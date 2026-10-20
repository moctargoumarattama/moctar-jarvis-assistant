"""Démon d'Écoute Native Système Arrière-Plan pour le Dell Precision M4800.

Permet à Moctar d'interpeller son copilote à tout moment, même si le navigateur Chrome est fermé :
1. Déclenchement via raccourci global OS (ex: Super+Espace via pipe IPC /tmp/moctar_voice.pipe).
2. Déclenchement direct via l'endpoint /api/voice/trigger.
3. Capture audio matérielle via arecord / ALSA / PulseAudio.
4. Synthèse et restitution vocale directe sur les haut-parleurs du Dell Precision M4800 (paplay / aplay / mpv / ffplay).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Optional

LOGGER = logging.getLogger("moctar.native_voice_daemon")

_DEFAULT_PIPE_PATH = Path("/tmp/moctar_voice.pipe")


def is_linux_environment() -> bool:
    """Vérifie si le système actuel est Linux/Ubuntu."""
    return os.name == "posix" and sys_platform() == "linux"


def sys_platform() -> str:
    import sys
    return sys.platform.lower()


def find_audio_recorder() -> Optional[str]:
    """Trouve un utilitaire de capture audio disponible sur l'hôte."""
    for tool in ("arecord", "rec", "ffmpeg"):
        path = shutil.which(tool)
        if path:
            return path
    return None


def find_audio_player() -> Optional[str]:
    """Trouve un lecteur audio disponible sur l'hôte pour la sortie haut-parleurs."""
    for tool in ("paplay", "aplay", "mpv", "ffplay", "mpg123"):
        path = shutil.which(tool)
        if path:
            return path
    return None


def play_local_audio(file_path: Path | str) -> bool:
    """Joue un fichier audio localement sur la carte son du Dell M4800."""
    p = str(file_path)
    player = find_audio_player()
    if not player:
        LOGGER.warning("Aucun lecteur audio système trouvé pour jouer %s", p)
        return False
    try:
        player_name = Path(player).name
        cmd = [player, p]
        if player_name in ("mpv", "ffplay"):
            cmd = [player, "--nodisp", "--autoexit", p]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return True
    except Exception as exc:
        LOGGER.warning("Erreur lors de la lecture audio locale avec %s : %s", player, exc)
        return False


def record_system_audio_wav(duration_seconds: float = 4.0, sample_rate: int = 16000) -> Optional[bytes]:
    """Enregistre un échantillon audio mono 16-bit PCM depuis le microphone matériel."""
    recorder = find_audio_recorder()
    if not recorder:
        LOGGER.debug("arecord/rec/ffmpeg absent pour la capture native.")
        return None

    recorder_name = Path(recorder).name
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if recorder_name == "arecord":
            cmd = [
                recorder,
                "-q",
                "-f", "S16_LE",
                "-r", str(sample_rate),
                "-c", "1",
                "-d", str(int(round(duration_seconds))),
                str(tmp_path),
            ]
        elif recorder_name == "ffmpeg":
            cmd = [
                recorder,
                "-y",
                "-f", "pulse",
                "-i", "default",
                "-t", str(duration_seconds),
                "-ar", str(sample_rate),
                "-ac", "1",
                str(tmp_path),
            ]
        else:
            return None

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=duration_seconds + 3.0)
        if tmp_path.exists() and tmp_path.stat().st_size > 44:
            return tmp_path.read_bytes()
    except Exception as exc:
        LOGGER.warning("Échec de l'enregistrement système natif : %s", exc)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
    return None


class NativeVoiceDaemon:
    """Démon d'écoute en arrière-plan pour le Dell Precision M4800."""

    _instance: Optional[NativeVoiceDaemon] = None

    def __init__(
        self,
        pipe_path: Path = _DEFAULT_PIPE_PATH,
        on_audio_captured: Optional[Callable[[bytes], Any]] = None,
    ) -> None:
        self.pipe_path = pipe_path
        self.on_audio_captured = on_audio_captured
        self.running = False
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> NativeVoiceDaemon:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self) -> None:
        """Démarre le listener d'arrière-plan sur un thread démon non bloquant."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="native-voice-daemon", daemon=True)
        self._thread.start()
        LOGGER.info("NativeVoiceDaemon démarré (pipe: %s).", self.pipe_path)

    def stop(self) -> None:
        """Arrête le listener système."""
        self.running = False

    def _setup_fifo(self) -> None:
        """Crée le pipe nommé sous Linux si absent."""
        if not is_linux_environment():
            return
        try:
            if not self.pipe_path.exists():
                os.mkfifo(str(self.pipe_path), mode=0o666)
        except Exception as exc:
            LOGGER.debug("Impossible d'initialiser le FIFO %s : %s", self.pipe_path, exc)

    def _run_loop(self) -> None:
        """Boucle d'écoute des signaux d'activation externes (raccourcis clavier OS)."""
        self._setup_fifo()
        while self.running:
            try:
                if is_linux_environment() and self.pipe_path.exists():
                    # Lecture bloquante avec timeout
                    with open(str(self.pipe_path), "r", encoding="utf-8") as f:
                        line = f.readline().strip()
                        if line and self.running:
                            LOGGER.info("⚡ [NATIVE TRIGGER]: Signal reçu via pipe ('%s')", line)
                            self.trigger_interaction_flow()
                else:
                    time.sleep(1.0)
            except Exception as exc:
                time.sleep(1.0)

    def trigger_interaction_flow(self, duration_seconds: float = 4.0) -> Optional[Dict[str, Any]]:
        """Déclenche la capture audio du micro, la transcription et la réponse vocale locale."""
        LOGGER.info("🎙️ [NATIVE DAEMON]: Déclenchement de la capture audio système (%ss)...", duration_seconds)
        wav_bytes = record_system_audio_wav(duration_seconds=duration_seconds)
        if not wav_bytes:
            LOGGER.warning("Aucun audio capturé lors du déclenchement natif.")
            return None

        if self.on_audio_captured:
            try:
                res = self.on_audio_captured(wav_bytes)
                if isinstance(res, dict) and res.get("audio_path"):
                    play_local_audio(res["audio_path"])
                return res
            except Exception as exc:
                LOGGER.error("Erreur lors du traitement de l'audio natif : %s", exc)
        return None

