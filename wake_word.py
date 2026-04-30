"""Deprecated Picovoice wake-word backend kept for compatibility only.

The active runtime now uses `wake_word_simple.py` for local-first soft wake-word
detection without external keys.
"""

import logging
import os
import struct
import threading
import time

from dotenv import load_dotenv

import config


DEPRECATED_NOTICE = "Module wake_word.py deprecated; the active runtime uses wake_word_simple.py."


def get_picovoice_access_key():
    load_dotenv()
    return os.getenv("PICOVOICE_ACCESS_KEY", "").strip()


class WakeWordService:
    def __init__(
        self,
        access_key=None,
        keyword=None,
        custom_keyword_path=None,
        cooldown_seconds=None,
        logger=None,
    ):
        self.logger = logger or logging.getLogger("jarvis")
        self.access_key = access_key if access_key is not None else get_picovoice_access_key()
        self.keyword = keyword or config.WAKE_WORD_SETTINGS["builtin_keyword"]
        self.custom_keyword_path = (
            custom_keyword_path
            if custom_keyword_path is not None
            else config.WAKE_WORD_SETTINGS["custom_keyword_path"]
        )
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else config.WAKE_WORD_SETTINGS["cooldown_seconds"]
        )
        self.enabled = False
        self._status_message = DEPRECATED_NOTICE
        self._stop_event = threading.Event()
        self._detected_event = threading.Event()
        self._thread = None
        self._last_detection_at = 0.0
        self._backend_modules = None

    def start(self):
        if not self.access_key:
            self.enabled = False
            self._status_message = "Wake word desactive : PICOVOICE_ACCESS_KEY absent."
            return self._status_message

        try:
            self._start_backend()
        except Exception as exc:
            self.enabled = False
            self._status_message = f"Wake word indisponible : {exc}"
            self.logger.warning(self._status_message)
            return self._status_message

        self.enabled = True
        self._status_message = f"Wake word actif : {self.keyword}."
        self.logger.info(self._status_message)
        return self._status_message

    def _start_backend(self):
        self._backend_modules = self._load_backend_modules()
        self._thread = threading.Thread(
            target=self._listen_loop,
            name="wake-word-listener",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _load_backend_modules():
        try:
            import pyaudio
            import pvporcupine
        except Exception as exc:
            raise RuntimeError(f"dependances Porcupine indisponibles ({exc})") from exc

        return pyaudio, pvporcupine

    def _build_porcupine(self, pvporcupine_module):
        if self.custom_keyword_path:
            return pvporcupine_module.create(
                access_key=self.access_key,
                keyword_paths=[self.custom_keyword_path],
            )

        return pvporcupine_module.create(
            access_key=self.access_key,
            keywords=[self.keyword],
        )

    def _listen_loop(self):
        pyaudio_module, pvporcupine_module = self._backend_modules
        porcupine = None
        pa = None
        stream = None

        try:
            porcupine = self._build_porcupine(pvporcupine_module)
            pa = pyaudio_module.PyAudio()
            stream = pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio_module.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length,
            )

            while not self._stop_event.is_set():
                pcm = stream.read(
                    porcupine.frame_length,
                    exception_on_overflow=False,
                )
                pcm = struct.unpack_from(
                    "h" * porcupine.frame_length,
                    pcm,
                )
                if porcupine.process(pcm) >= 0:
                    now_value = time.monotonic()
                    if now_value - self._last_detection_at >= self.cooldown_seconds:
                        self._last_detection_at = now_value
                        self._detected_event.set()
        except Exception as exc:
            self.enabled = False
            self._status_message = f"Wake word indisponible : {exc}"
            self.logger.warning(self._status_message)
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            if pa:
                pa.terminate()
            if porcupine:
                porcupine.delete()

    def consume_detection(self):
        if not self._detected_event.is_set():
            return False

        self._detected_event.clear()
        return True

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
