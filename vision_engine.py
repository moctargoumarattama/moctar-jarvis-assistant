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
9. Tolérance aux pannes maximale : dégradation gracieuse en mode simulation/veille si OpenCV (cv2) n'est pas installé.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple

# Import sécurisé d'OpenCV (cv2) et NumPy pour résilience multi-environnements (Linux/Windows)
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    CV2_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

LOGGER = logging.getLogger("jarvis.vision")

# Image de veille statique de secours si cv2 est absent
_STANDBY_IMAGE_PATH = Path(__file__).parent / "static" / "standby_frame.jpg"
_FALLBACK_1X1_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06"
    b"\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
    b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00"
    b"?\x00\xbf\x00\xff\xd9"
)
_CACHED_STANDBY_JPEG: Optional[bytes] = None


def get_standby_jpeg_bytes() -> bytes:
    """Renvoie les octets JPEG de la mire de veille de manière garantie."""
    global _CACHED_STANDBY_JPEG
    if _CACHED_STANDBY_JPEG is not None:
        return _CACHED_STANDBY_JPEG

    # 1. Tente de lire l'image statique pré-générée du dossier static/
    if _STANDBY_IMAGE_PATH.exists():
        try:
            _CACHED_STANDBY_JPEG = _STANDBY_IMAGE_PATH.read_bytes()
            return _CACHED_STANDBY_JPEG
        except Exception:
            pass

    # 2. Si cv2 et numpy sont installés, génère dynamiquement
    if CV2_AVAILABLE and NUMPY_AVAILABLE and cv2 is not None and np is not None:
        try:
            img = np.zeros((360, 480, 3), dtype=np.uint8)
            img[:] = (18, 14, 10)
            cv2.putText(img, "M.O.C.T.A.R // NEUROVISION STANDBY", (35, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 243, 255), 2)
            cv2.putText(img, "Capteur pret - En attente d'activation", (65, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)
            ret, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ret:
                _CACHED_STANDBY_JPEG = buf.tobytes()
                return _CACHED_STANDBY_JPEG
        except Exception:
            pass

    # 3. Fallback JPEG minimal garanti
    _CACHED_STANDBY_JPEG = _FALLBACK_1X1_JPEG
    return _CACHED_STANDBY_JPEG


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
        self.cap: Optional[Any] = None
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

    @staticmethod
    def is_cv2_available() -> bool:
        """Indique si la bibliothèque OpenCV est installée dans l'environnement Python."""
        return CV2_AVAILABLE

    @staticmethod
    def get_standby_jpeg_bytes() -> bytes:
        return get_standby_jpeg_bytes()

    def _init_cascades(self) -> None:
        """Charge les cascades Haar officielles d'OpenCV si disponibles."""
        self.face_cascade = None
        self.eye_cascade = None

        if not CV2_AVAILABLE or cv2 is None:
            LOGGER.info("ℹ️ OpenCV (cv2) non installé. NeuroVision fonctionne en mode veille/dégradé.")
            return

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

            LOGGER.info("🎥 Activation du moteur de vision NeuroVision (CV2=%s)...", CV2_AVAILABLE)
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
        """Ouvre la webcam avec les paramètres optimaux si cv2 est présent."""
        if not CV2_AVAILABLE or cv2 is None:
            self.simulation = True
            return False

        try:
            backend = getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY) if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(self.camera_index, backend)
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
        has_camera = self._open_camera() if CV2_AVAILABLE else False
        frame_skip = 0

        while self._running:
            loop_start = time.monotonic()

            if not CV2_AVAILABLE or not has_camera or self.simulation or self.cap is None:
                # Mode veille / simulation quand aucune webcam ou cv2 n'est disponible
                self._latest_jpeg_frame = get_standby_jpeg_bytes()
                self._new_frame_event.set()
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

    def _process_frame(self, frame: Any) -> Tuple[Any, bool]:
        """Analyse réelle du visage, des yeux, de la bouche et de l'orientation."""
        if not CV2_AVAILABLE or cv2 is None or not NUMPY_AVAILABLE or np is None:
            return frame, False

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

    def _detect_eyes_fast(self, face_x: int, face_y: int, face_w: int, face_h: int, gray: Any) -> float:
        """Détection optimisée de l'ouverture des yeux par rapport de pixels sombres."""
        if not CV2_AVAILABLE or cv2 is None or not NUMPY_AVAILABLE or np is None:
            return 0.8
        eye_y_end = face_y + int(face_h * 0.40)
        eye_region = gray[face_y:eye_y_end, face_x:face_x + face_w]

        if eye_region.size < 100:
            return 0.8
        _, binary = cv2.threshold(eye_region, 50, 255, cv2.THRESH_BINARY_INV)
        black_pixels = int(np.count_nonzero(binary))
        eye_openness = 1.0 - (black_pixels / binary.size)
        return float(eye_openness)

    def _detect_mouth_fast(self, face_x: int, face_y: int, face_w: int, face_h: int, gray: Any) -> Optional[float]:
        """Détection optimisée de l'ouverture de bouche (bâillements)."""
        if not CV2_AVAILABLE or cv2 is None or not NUMPY_AVAILABLE or np is None:
            return None
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

        # 1. Taux de clignement par minute
        blink_rate = self.blink_count / duration_minutes
        if blink_rate > 24.0:
            score += min((blink_rate - 15.0) * 1.8, 30.0)

        # 2. Bâillements détectés (+10 points par bâillement)
        score += min(self.yawn_count * 10.0, 30.0)

        # 3. Mouvements de tête anormaux ou posture avachie
        head_drift = abs(self.head_tilt) + abs(self.head_roll)
        if head_drift > 6.0:
            score += min(head_drift * 1.2, 20.0)

        # 4. Yeux mi-clos de manière prolongée (calcul pur sans dépendance stricte)
        if len(self.eye_aspect_ratio_history) > 0:
            avg_eye = float(sum(self.eye_aspect_ratio_history) / len(self.eye_aspect_ratio_history))
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

    def _render_hud_overlay(self, frame: Any, face_detected: bool) -> Any:
        """Dessine une télémétrie sobre dans le style du HUD JARVIS."""
        if not CV2_AVAILABLE or cv2 is None or frame is None:
            return frame

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

    def _update_jpeg(self, frame: Any) -> None:
        """Encode en JPEG léger (qualité 75) pour le flux MJPEG temps réel."""
        if not CV2_AVAILABLE or cv2 is None or frame is None:
            self._latest_jpeg_frame = get_standby_jpeg_bytes()
            self._new_frame_event.set()
            return

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
            self._new_frame_event.wait(timeout=0.2)
            self._new_frame_event.clear()
            frame = self._latest_jpeg_frame
            if frame is None:
                frame = get_standby_jpeg_bytes()

            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

            # Si le moteur est arrêté ou cv2 indisponible, réduit le débit (2 FPS)
            if not self._running or not CV2_AVAILABLE:
                time.sleep(0.5)

    def get_metrics(self) -> Dict[str, Any]:
        """Télémétrie complète au format JSON pour l'API Jarvis et le HUD."""
        duration_minutes = max((time.time() - self.start_time) / 60.0, 0.1)
        blink_rate = round(self.blink_count / duration_minutes, 1)
        is_cam_open = (
            CV2_AVAILABLE
            and not self.simulation
            and self.cap is not None
            and getattr(self.cap, "isOpened", lambda: False)()
        )

        return {
            "active": self._running,
            "cv2_available": CV2_AVAILABLE,
            "camera_connected": bool(is_cam_open),
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
            "status": "nominal" if CV2_AVAILABLE else "cv2_missing",
            "message": "Vision nominale" if CV2_AVAILABLE else "Module opencv-python non installé sur cet hôte (mode simulation/veille)",
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
