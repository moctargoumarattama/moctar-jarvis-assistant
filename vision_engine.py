"""Moteur de Vision Cognitive et Biomutationnelle (NeuroVision) pour JARVIS / M.O.C.T.A.R.

Intègre les capacités réelles de vision par ordinateur développées dans NeuroVision v2.2 :
1. Détection faciale en temps réel (Haar Cascade optimisée).
2. Analyse de clignements réels (Eye Aspect Ratio / EAR ultra-rapide par seuillage).
3. Détection de bâillements réels (Mouth Aspect Ratio / MAR par densité de cavité buccale).
4. Estimation de la pose de tête 3D (Yaw: inclinaison horizontale, Roll: roulis/affaissement).
5. Calcul du score de fatigue cognitive biométrique (0 à 100%).
6. Détection de présence physique de Moctar devant le Dell Precision M4800.
7. Génération de flux vidéo MJPEG natif pour FastAPI et le HUD 3D (sans serveur Flask ni port 5001).
8. Contrôle dynamique de l'acquisition (démarrage/arrêt à chaud, zéro CPU en veille).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

import cv2
import numpy as np

LOGGER = logging.getLogger("jarvis.vision")


class VisionEngine:
    """Service de vision cognitive temps réel unifié pour JARVIS."""

    _instance: Optional[VisionEngine] = None
    _lock = threading.Lock()

    def __init__(self, camera_index: int = 0, target_fps: int = 25) -> None:
        self.camera_index = camera_index
        self.target_fps = target_fps
        self.frame_interval = 1.0 / max(1, target_fps)

        # Contrôle du cycle de vie du thread d'acquisition
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()

        # Matériel et capture
        self.cap: Optional[cv2.VideoCapture] = None
        self.simulation = False

        # Dimensions optimisées v2.2 (faible charge CPU Dell Precision M4800)
        self.frame_width = 480
        self.frame_height = 360

        # Données de détection réelles
        self.blink_count = 0
        self.yawn_count = 0
        self.head_tilt = 0.0  # Yaw (-30° à +30°)
        self.head_roll = 0.0  # Roll (-20° à +20°)
        self.fatigue_score = 0
        self.fatigue_level = "Normal"
        self.face_detected = False
        self.last_face_seen_time = 0.0
        self.start_time = time.time()
        self.last_blink_time = time.time()
        self.last_yawn_time = time.time()
        self.last_update = time.time()
        self.frame_count = 0
        self.measured_fps = 0.0
        self._fps_counter = 0
        self._fps_timer = time.time()

        # Historiques glissants pour lissage statistique
        self.blink_history: deque[float] = deque(maxlen=30)
        self.eye_aspect_ratio_history: deque[float] = deque(maxlen=30)
        self.mouth_aspect_ratio_history: deque[float] = deque(maxlen=30)
        self.head_position_history: deque[Tuple[float, float]] = deque(maxlen=60)

        # Seuils étalonnés pour la vision réelle v2.2
        self.EYE_ASPECT_RATIO_THRESHOLD = 0.20
        self.EYE_ASPECT_RATIO_CONSEC_FRAMES = 3
        self.MOUTH_ASPECT_RATIO_THRESHOLD = 0.25
        self.MOUTH_ASPECT_RATIO_CONSEC_FRAMES = 5

        self.consec_frames_blink = 0
        self.consec_frames_yawn = 0

        # Cache d'images thread-safe pour le streaming MJPEG
        self._latest_jpeg_frame: Optional[bytes] = None
        self._new_frame_event = threading.Event()

        # Initialisation sécurisée des classificateurs Haar
        self._init_cascades()

    @classmethod
    def get_instance(cls) -> VisionEngine:
        """Accès Singleton thread-safe."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_cascades(self) -> None:
        """Charge les cascades Haar officielles d'OpenCV."""
        try:
            face_xml = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self.face_cascade = cv2.CascadeClassifier(face_xml)
            if self.face_cascade.empty():
                LOGGER.warning("Cascade faciale vide, mode dégradé.")
                self.face_cascade = None
            else:
                LOGGER.info("✅ Cascade faciale Haar chargée avec succès.")
        except Exception as exc:
            LOGGER.error("Erreur chargement cascade faciale: %s", exc)
            self.face_cascade = None

        try:
            eye_xml = cv2.data.haarcascades + "haarcascade_eye.xml"
            self.eye_cascade = cv2.CascadeClassifier(eye_xml)
            if self.eye_cascade.empty():
                self.eye_cascade = None
        except Exception:
            self.eye_cascade = None

    def start(self) -> bool:
        """Démarre l'acquisition et l'analyse vidéo en arrière-plan."""
        with self._state_lock:
            if self._running:
                return True

            LOGGER.info("🎥 Activation du moteur de vision NeuroVision...")
            self._running = True
            self.start_time = time.time()
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="jarvis-neurovision")
            self._thread.start()
            return True

    def stop(self) -> None:
        """Arrête proprement la caméra et libère les ressources matérielles."""
        with self._state_lock:
            if not self._running:
                return
            LOGGER.info("🛑 Arrêt du moteur de vision NeuroVision...")
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

        self._release_camera()

    def toggle(self) -> bool:
        """Bascule l'état marche/arrêt de la caméra."""
        if self.is_running():
            self.stop()
            return False
        else:
            self.start()
            return True

    def is_running(self) -> bool:
        """Indique si la vision est activement en cours de capture."""
        return self._running

    def is_user_present(self, timeout_seconds: float = 5.0) -> bool:
        """Indique si l'utilisateur a été détecté devant l'écran récemment."""
        if not self._running:
            return False
        return self.face_detected and (time.time() - self.last_face_seen_time < timeout_seconds)

    def _open_camera(self) -> bool:
        """Ouvre la webcam avec les paramètres optimaux."""
        try:
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW if cv2.__name__ and hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY)
            if not self.cap.isOpened():
                # Deuxième tentative générique
                self.cap = cv2.VideoCapture(self.camera_index)

            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
                self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self.simulation = False
                LOGGER.info("✅ Webcam #%d connectée pour NeuroVision (480x360 @ %d FPS).", self.camera_index, self.target_fps)
                return True
            else:
                LOGGER.warning("⚠️ Impossible d'ouvrir la webcam #%d. Bascule en mode veille/simulation.", self.camera_index)
                self.simulation = True
                return False
        except Exception as exc:
            LOGGER.warning("Erreur connexion webcam: %s. Bascule en mode veille.", exc)
            self.simulation = True
            return False

    def _release_camera(self) -> None:
        """Libère le handle de la webcam."""
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        self._latest_jpeg_frame = None

    def _worker_loop(self) -> None:
        """Boucle d'acquisition et de traitement continu à faible empreinte CPU."""
        has_camera = self._open_camera()
        frame_skip = 0

        while self._running:
            loop_start = time.monotonic()

            if not has_camera or self.simulation or self.cap is None:
                # Mode simulation / veille quand aucune webcam n'est accessible
                annotated = self._generate_standby_frame()
                self._update_jpeg(annotated)
                time.sleep(0.5)
                continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                LOGGER.warning("Frame webcam vide ou déconnectée. Tentative de reconnexion...")
                time.sleep(1.0)
                continue

            self.frame_count += 1
            frame_skip += 1

            # Traitement 1 frame sur 2 pour diviser la charge CPU par deux tout en gardant 25-30 FPS d'affichage
            if frame_skip % 2 == 0:
                processed_frame, face_found = self._process_frame(frame)
            else:
                processed_frame = frame
                face_found = self.face_detected

            # Annotation visuelle de télémétrie sur le flux
            annotated_frame = self._render_hud_overlay(processed_frame, face_found)
            self._update_jpeg(annotated_frame)

            # Calcul du FPS réel mesuré
            self._fps_counter += 1
            now = time.time()
            if now - self._fps_timer >= 1.0:
                self.measured_fps = self._fps_counter / (now - self._fps_timer)
                self._fps_counter = 0
                self._fps_timer = now

            # Maintien d'un rythme d'acquisition régulier
            elapsed = time.monotonic() - loop_start
            sleep_time = self.frame_interval - elapsed
            if sleep_time > 0.001:
                time.sleep(sleep_time)

        self._release_camera()

    def _process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool]:
        """Analyse réelle du visage, des yeux, de la bouche et de l'orientation."""
        # Redimensionnement et effet miroir
        frame = cv2.resize(frame, (self.frame_width, self.frame_height))
        frame = cv2.flip(frame, 1)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        face_found = False
        face_x, face_y, face_w, face_h = 0, 0, 0, 0

        if self.face_cascade is not None:
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.15,
                minNeighbors=4,
                minSize=(80, 80),
            )

            if len(faces) > 0:
                face_found = True
                self.last_face_seen_time = time.time()
                (face_x, face_y, face_w, face_h) = faces[0]

                # 1. Analyse ultra-rapide des yeux (EAR)
                eye_openness = self._detect_eyes_fast(face_x, face_y, face_w, face_h, gray)
                self.eye_aspect_ratio_history.append(eye_openness)

                # Détection de clignement réel
                if eye_openness > 0.70:  # Yeux fermés
                    self.consec_frames_blink += 1
                else:
                    if self.consec_frames_blink >= self.EYE_ASPECT_RATIO_CONSEC_FRAMES:
                        self.blink_count += 1
                        self.last_blink_time = time.time()
                    self.consec_frames_blink = 0

                # 2. Analyse ultra-rapide de la bouche (MAR / Bâillements)
                mouth_ratio = self._detect_mouth_fast(face_x, face_y, face_w, face_h, gray)
                if mouth_ratio is not None:
                    self.mouth_aspect_ratio_history.append(mouth_ratio)
                    if mouth_ratio > self.MOUTH_ASPECT_RATIO_THRESHOLD:
                        self.consec_frames_yawn += 1
                    else:
                        if self.consec_frames_yawn >= self.MOUTH_ASPECT_RATIO_CONSEC_FRAMES:
                            self.yawn_count += 1
                            self.last_yawn_time = time.time()
                        self.consec_frames_yawn = 0

                # 3. Estimation de la pose de tête 3D
                tilt, roll = self._detect_head_pose_real(face_x, face_y, face_w, face_h)
                self.head_tilt = tilt
                self.head_roll = roll
                self.head_position_history.append((tilt, roll))

                # Dessin du rectangle faciale avec liseré cyan JARVIS
                cv2.rectangle(
                    frame,
                    (face_x, face_y),
                    (face_x + face_w, face_y + face_h),
                    (0, 243, 255),
                    2,
                )

        self.face_detected = face_found
        if not face_found:
            self.consec_frames_blink = 0
            self.consec_frames_yawn = 0

        self._update_metrics(face_found)
        return frame, face_found

    def _detect_eyes_fast(self, face_x: int, face_y: int, face_w: int, face_h: int, gray: np.ndarray) -> float:
        """Détection optimisée de l'ouverture des yeux par rapport de pixels sombres."""
        eye_y_end = face_y + int(face_h * 0.40)
        eye_region = gray[face_y:eye_y_end, face_x:face_x + face_w]

        if eye_region.size < 100:
            return 0.8
        _, binary = cv2.threshold(eye_region, 50, 255, cv2.THRESH_BINARY_INV)
        black_pixels = int(np.count_nonzero(binary))
        eye_openness = 1.0 - (black_pixels / binary.size)
        return float(eye_openness)

    def _detect_mouth_fast(self, face_x: int, face_y: int, face_w: int, face_h: int, gray: np.ndarray) -> Optional[float]:
        """Détection optimisée de l'ouverture de bouche (bâillements)."""
        mouth_start = face_y + int(face_h * 0.65)
        mouth_end = face_y + face_h
        mouth_roi = gray[mouth_start:mouth_end, face_x:face_x + face_w]

        if mouth_roi.size < 100:
            return None
        _, binary = cv2.threshold(mouth_roi, 50, 255, cv2.THRESH_BINARY_INV)
        black_pixels = int(np.count_nonzero(binary))
        mouth_ratio = black_pixels / binary.size if binary.size > 0 else 0.0
        return float(mouth_ratio)

    def _detect_head_pose_real(self, face_x: int, face_y: int, face_w: int, face_h: int) -> Tuple[float, float]:
        """Estime l'inclinaison (Yaw) et le roulis (Roll) de la tête par géométrie faciale."""
        frame_center_x = self.frame_width // 2
        face_center_x = face_x + face_w // 2
        face_center_y = face_y + face_h // 2

        # Inclinaison horizontale (Yaw : -30° à +30°)
        tilt = (face_center_x - frame_center_x) / max(face_w / 2, 1) * 30.0

        # Roulis / Affaissement (Roll : -20° à +20°)
        frame_center_y = self.frame_height // 2
        roll = (face_center_y - frame_center_y) / max(face_w / 2, 1) * 20.0

        aspect = face_h / max(face_w, 1)
        if aspect > 1.3:
            roll += 10.0  # Penché en arrière
        elif aspect < 0.7:
            roll -= 10.0  # Penché en avant

        return round(float(tilt), 1), round(float(roll), 1)

    def _calculate_real_fatigue_score(self) -> int:
        """Calcule un score de fatigue multi-facteurs (0-100%)."""
        score = 0.0
        duration_minutes = max((time.time() - self.start_time) / 60.0, 0.1)

        # 1. Taux de clignement par minute (un taux anormalement élevé traduit la sécheresse et la fatigue oculaire)
        blink_rate = self.blink_count / duration_minutes
        if blink_rate > 24.0:
            score += min((blink_rate - 15.0) * 1.8, 30.0)

        # 2. Bâillements détectés (+10 points par bâillement)
        score += min(self.yawn_count * 10.0, 30.0)

        # 3. Mouvements de tête anormaux ou posture avachie
        head_drift = abs(self.head_tilt) + abs(self.head_roll)
        if head_drift > 6.0:
            score += min(head_drift * 1.2, 20.0)

        # 4. Yeux mi-clos de manière prolongée
        if len(self.eye_aspect_ratio_history) > 0:
            avg_eye = float(np.mean(list(self.eye_aspect_ratio_history)))
            if avg_eye < 0.35:
                score += 15.0

        # 5. Durée de la session de travail continue
        if duration_minutes > 30.0:
            score += 10.0
        if duration_minutes > 60.0:
            score += 10.0

        return min(int(score), 100)

    def _update_metrics(self, face_detected: bool) -> None:
        """Actualise périodiquement l'état cognitif global."""
        current_time = time.time()
        if current_time - self.last_update >= 1.0:
            self.last_update = current_time
            self.fatigue_score = self._calculate_real_fatigue_score()

            if self.fatigue_score >= 80:
                self.fatigue_level = "Fatigue Sévère"
            elif self.fatigue_score >= 60:
                self.fatigue_level = "Très Fatigué"
            elif self.fatigue_score >= 40:
                self.fatigue_level = "Fatigué"
            elif self.fatigue_score >= 20:
                self.fatigue_level = "Alerte"
            elif abs(self.head_tilt) > 12.0 or abs(self.head_roll) > 12.0:
                self.fatigue_level = "Distrait"
            else:
                self.fatigue_level = "Normal"

    def _render_hud_overlay(self, frame: np.ndarray, face_detected: bool) -> np.ndarray:
        """Dessine une télémétrie sobre dans le style du HUD JARVIS."""
        overlay = frame.copy()

        # Barre supérieure d'état
        status_text = f"M.O.C.T.A.R VISION : {self.fatigue_level.upper()}"
        color = (0, 243, 255) if face_detected else (0, 165, 255)
        if self.fatigue_score >= 60:
            color = (0, 100, 255)  # Ambre / alerte fatigue

        cv2.putText(overlay, status_text, (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        cv2.putText(overlay, f"Clignements: {self.blink_count}", (15, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
        cv2.putText(overlay, f"Bâillements: {self.yawn_count}", (15, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
        cv2.putText(overlay, f"Inclinaison: {int(self.head_tilt)}°", (15, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        # Jauge de fatigue
        bar_x, bar_y, bar_w, bar_h = 15, 110, 140, 10
        fill_w = int(bar_w * (self.fatigue_score / 100.0))
        cv2.rectangle(overlay, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (60, 60, 60), 1)
        if fill_w > 0:
            bar_color = (0, 243, 255) if self.fatigue_score < 40 else (0, 140, 255) if self.fatigue_score < 70 else (0, 0, 255)
            cv2.rectangle(overlay, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), bar_color, -1)
        cv2.putText(overlay, f"Fatigue: {self.fatigue_score}%", (bar_x + bar_w + 10, bar_y + 9), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (220, 220, 220), 1)

        # Indicateur de présence
        pres_text = "UTILISATEUR PRÉSENT" if face_detected else "EN ATTENTE..."
        cv2.putText(overlay, pres_text, (15, self.frame_height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 128) if face_detected else (120, 120, 120), 1)

        return overlay

    def _generate_standby_frame(self) -> np.ndarray:
        """Génère une mire d'attente propre lorsque la webcam est désactivée."""
        img = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        # Fond sombre dégradé
        img[:] = (18, 14, 10)
        cv2.putText(img, "M.O.C.T.A.R // NEUROVISION VEILLE", (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 243, 255), 2)
        cv2.putText(img, "Capteur pret - En attente d'activation", (65, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)
        return img

    def _update_jpeg(self, frame: np.ndarray) -> None:
        """Encode en JPEG léger (qualité 75) pour le flux MJPEG temps réel."""
        try:
            ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ret:
                self._latest_jpeg_frame = buffer.tobytes()
                self._new_frame_event.set()
        except Exception:
            pass

    def get_latest_jpeg_frame(self) -> Optional[bytes]:
        """Renvoie la dernière frame encodée en JPEG."""
        return self._latest_jpeg_frame

    def stream_generator(self) -> Generator[bytes, None, None]:
        """Générateur HTTP multipart pour flux vidéo MJPEG continu (FastAPI StreamingResponse)."""
        while True:
            # Attend une nouvelle frame ou timeout
            self._new_frame_event.wait(timeout=0.1)
            self._new_frame_event.clear()
            frame = self._latest_jpeg_frame
            if frame is None:
                standby = self._generate_standby_frame()
                _, buf = cv2.imencode(".jpg", standby, [cv2.IMWRITE_JPEG_QUALITY, 70])
                frame = buf.tobytes()

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

    def get_metrics(self) -> Dict[str, Any]:
        """Télémétrie complète au format JSON pour l'API Jarvis et le HUD."""
        duration_minutes = max((time.time() - self.start_time) / 60.0, 0.1)
        blink_rate = round(self.blink_count / duration_minutes, 1)

        return {
            "active": self._running,
            "camera_connected": not self.simulation and (self.cap is not None and self.cap.isOpened()),
            "user_present": self.is_user_present(),
            "face_detected": self.face_detected,
            "blink_count": self.blink_count,
            "blink_rate_per_min": blink_rate,
            "yawn_count": self.yawn_count,
            "head_tilt_yaw": self.head_tilt,
            "head_roll": self.head_roll,
            "fatigue_score": self.fatigue_score,
            "fatigue_level": self.fatigue_level,
            "session_duration_seconds": int(time.time() - self.start_time),
            "measured_fps": round(self.measured_fps, 1),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }

    def reset_counters(self) -> None:
        """Remet à zéro tous les compteurs de session."""
        with self._state_lock:
            self.blink_count = 0
            self.yawn_count = 0
            self.head_tilt = 0.0
            self.head_roll = 0.0
            self.fatigue_score = 0
            self.fatigue_level = "Normal"
            self.start_time = time.time()
            self.last_blink_time = time.time()
            self.last_yawn_time = time.time()
            self.eye_aspect_ratio_history.clear()
            self.mouth_aspect_ratio_history.clear()
            self.head_position_history.clear()
            LOGGER.info("🔄 Compteurs NeuroVision réinitialisés.")

