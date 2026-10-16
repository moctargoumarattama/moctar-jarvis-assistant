#!/usr/bin/env python3
"""Agent vocal natif M.O.C.T.A.R pour le PC portable HP sous Windows.

Fonctionnalités :
- Capture continue du microphone physique via `sounddevice` ou `pyaudio`
  avec détection d'activité vocale (seuil RMS + silence de fin de phrase).
- Envoi de l'enregistrement WAV 16 kHz mono à `/api/voice/transcribe`
  (par défaut `https://moctar.klasora.com/api/voice/transcribe`).
- Détection des wake words : "Moctar", "Yo Moctar", "ELMo" (et variantes).
- Interrogation de l'AgentCore via `/api/jarvis/query`.
- Téléchargement de la synthèse vocale MP3 via `/api/voice/tts` (`edge-tts`)
  et lecture sur les haut-parleurs du PC HP via `miniaudio` ou `pygame`.
"""
from __future__ import annotations

import argparse
import io
import logging
import math
import os
import re
import struct
import tempfile
import time
import unicodedata
import wave
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import requests

LOGGER = logging.getLogger("moctar.hp_agent")

DEFAULT_SERVER_URL = os.environ.get("MOCTAR_SERVER_URL", "https://moctar.klasora.com").rstrip("/")
DEFAULT_VOICE = os.environ.get("MOCTAR_TTS_VOICE", "fr-FR-HenriNeural")
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_S = 0.1  # 100 ms par bloc
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_S)
DEFAULT_RMS_THRESHOLD = float(os.environ.get("MOCTAR_RMS_THRESHOLD", "0.018"))
SILENCE_DURATION_S = 0.85
MIN_PHRASE_S = 0.35
MAX_PHRASE_S = 14.0

WAKE_PREFIXES = (
    "yo moctar",
    "hey moctar",
    "ok moctar",
    "salut moctar",
    "dis moctar",
    "moctar",
    "hey elmo",
    "hey el mo",
    "el mo",
    "elmo",
)

DEFAULT_GREETING = "Oui Moctar, je suis à ton écoute."


def normalize_text(value: str) -> str:
    """Normalise une chaîne (sans accents, minuscules) pour la détection du wake word."""
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.lower().strip()


def compute_rms(pcm16_bytes: bytes) -> float:
    """Calcule l'énergie RMS normalisée [0.0, 1.0] d'un tampon PCM 16-bit signé."""
    if not pcm16_bytes or len(pcm16_bytes) < 2:
        return 0.0
    count = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{count}h", pcm16_bytes[: count * 2])
    mean_sq = sum((s / 32768.0) ** 2 for s in samples) / count
    return math.sqrt(mean_sq)


def encode_pcm16_wav(pcm16_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode un flux PCM 16-bit mono dans un conteneur WAV standard en mémoire."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16_bytes)
    return buffer.getvalue()


def extract_wake_command(transcript: str) -> tuple[bool, str]:
    """Vérifie si la phrase commence par 'Moctar', 'Yo Moctar' ou 'ELMo'.

    Retourne `(matched, command_text)`. Si seul le wake word est prononcé,
    `command_text` est `""`.
    """
    raw = str(transcript or "").strip()
    if not raw:
        return False, ""
    tokens = list(re.finditer(r"[\wÀ-ÿ]+", raw, flags=re.UNICODE))
    if not tokens:
        return False, ""
    norm_words = [normalize_text(m.group(0)) for m in tokens]

    for prefix in sorted(WAKE_PREFIXES, key=lambda item: len(item.split()), reverse=True):
        prefix_words = prefix.split()
        if norm_words[: len(prefix_words)] == prefix_words:
            end_pos = tokens[len(prefix_words) - 1].end()
            remainder = raw[end_pos:].lstrip(" ,.:;!?…—–-")
            return True, remainder.strip()
    return False, ""


def play_mp3_bytes(mp3_bytes: bytes) -> bool:
    """Joue un flux audio MP3 sur les haut-parleurs du PC HP via miniaudio ou pygame."""
    if not mp3_bytes:
        return False

    # 1. Priorité à miniaudio
    try:
        import miniaudio  # type: ignore

        decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        with miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=decoded.nchannels,
            sample_rate=decoded.sample_rate,
        ) as device:
            stream = miniaudio.stream_raw_pcm_memory(
                decoded.samples, decoded.nchannels, decoded.sample_width
            )
            device.start(stream)
            duration_s = len(decoded.samples) / float(decoded.sample_rate * decoded.nchannels)
            time.sleep(max(0.1, duration_s + 0.08))
        return True
    except Exception as exc:
        LOGGER.debug("miniaudio non utilisé (%s), bascule sur pygame.", exc)

    # 2. Fallback sur pygame.mixer
    try:
        import pygame  # type: ignore

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        with tempfile.NamedTemporaryFile(prefix="moctar-tts-", suffix=".mp3", delete=False) as tmp:
            tmp.write(mp3_bytes)
            tmp_path = Path(tmp.name)
        try:
            pygame.mixer.music.load(str(tmp_path))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            pygame.mixer.music.unload()
            return True
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        LOGGER.warning("Impossible de jouer l'audio MP3 via miniaudio/pygame : %s", exc)
        return False


class MoctarHPCompanionAgent:
    """Démon vocal compagnon M.O.C.T.A.R pour le poste client Windows HP."""

    def __init__(
        self,
        server_url: str = DEFAULT_SERVER_URL,
        voice: str = DEFAULT_VOICE,
        rms_threshold: float = DEFAULT_RMS_THRESHOLD,
        session: Optional[requests.Session] = None,
        audio_player: Callable[[bytes], bool] = play_mp3_bytes,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.voice = voice
        self.rms_threshold = rms_threshold
        self.session = session or requests.Session()
        self.audio_player = audio_player
        self.followup_until = 0.0

    @property
    def transcribe_url(self) -> str:
        return f"{self.server_url}/api/voice/transcribe"

    @property
    def query_url(self) -> str:
        return f"{self.server_url}/api/jarvis/query"

    @property
    def tts_url(self) -> str:
        return f"{self.server_url}/api/voice/tts"

    def transcribe_wav(self, wav_bytes: bytes) -> str:
        """Envoie l'audio WAV capturé au serveur `/api/voice/transcribe`."""
        response = self.session.post(
            self.transcribe_url,
            data=wav_bytes,
            headers={"Content-Type": "audio/wav"},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload.get("text") or "").strip()

    def query_agent(self, prompt: str) -> dict[str, Any]:
        """Interroge AgentCore sur le serveur et ouvre les URL clientes si demandé."""
        response = self.session.post(
            self.query_url,
            json={"prompt": prompt, "source": "hp_companion_voice"},
            headers={"Content-Type": "application/json"},
            timeout=35,
        )
        response.raise_for_status()
        data = response.json()
        target_url = data.get("url") if data.get("action") == "open_browser" else None
        if not target_url and isinstance(data.get("tools_used"), list):
            for item in data["tools_used"]:
                tool_data = item.get("data") if isinstance(item, dict) else None
                if isinstance(tool_data, dict) and tool_data.get("action") == "open_browser" and tool_data.get("url"):
                    target_url = tool_data["url"]
                    break
        if target_url:
            try:
                webbrowser.open(target_url, new=2)
            except Exception as exc:
                LOGGER.warning("Ouverture navigateur impossible : %s", exc)
        return data

    def fetch_tts(self, text: str) -> bytes:
        """Télécharge le flux MP3 généré par edge-tts depuis `/api/voice/tts`."""
        response = self.session.post(
            self.tts_url,
            json={"text": text, "voice": self.voice},
            headers={"Content-Type": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        return response.content

    def speak(self, text: str) -> bool:
        """Synthétise et joue vocalement la réponse de M.O.C.T.A.R sur les haut-parleurs HP."""
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        mp3_bytes = self.fetch_tts(cleaned)
        return self.audio_player(mp3_bytes)

    def handle_phrase_audio(self, wav_bytes: bytes) -> Optional[str]:
        """Traite une phrase audio complète : transcription -> wake word -> AgentCore -> TTS."""
        try:
            transcript = self.transcribe_wav(wav_bytes)
        except Exception as exc:
            LOGGER.warning("Transcription échouée : %s", exc)
            return None

        if not transcript:
            return None

        LOGGER.info("🎙️ Entendu : %s", transcript)
        matched, command = extract_wake_command(transcript)
        in_followup = time.monotonic() < self.followup_until

        if not matched and not in_followup:
            return None

        active_command = command if matched else transcript.strip()
        if matched and not active_command:
            self.followup_until = time.monotonic() + 15.0
            self.speak(DEFAULT_GREETING)
            return DEFAULT_GREETING

        try:
            agent_result = self.query_agent(active_command)
            reply = str(agent_result.get("reply") or "Action exécutée, Moctar.").strip()
        except Exception as exc:
            LOGGER.error("Erreur AgentCore : %s", exc)
            reply = "Désolé Moctar, la liaison avec le serveur central est momentanément perturbée."

        LOGGER.info("🤖 M.O.C.T.A.R : %s", reply)
        self.speak(reply)
        self.followup_until = time.monotonic() + 15.0
        return reply

    def _iter_mic_chunks_sounddevice(self):
        import numpy as np
        import sounddevice as sd  # type: ignore

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SAMPLES) as stream:
            while True:
                frames, _ = stream.read(CHUNK_SAMPLES)
                yield np.asarray(frames, dtype=np.int16).tobytes()

    def _iter_mic_chunks_pyaudio(self):
        import pyaudio  # type: ignore

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SAMPLES,
        )
        try:
            while True:
                yield stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def iter_microphone_chunks(self):
        """Fournit des blocs PCM 16-bit continus via sounddevice (prioritaire) ou pyaudio."""
        try:
            import sounddevice  # noqa: F401

            LOGGER.info("Capture microphone initialisée via sounddevice.")
            yield from self._iter_mic_chunks_sounddevice()
            return
        except ImportError:
            pass
        LOGGER.info("Capture microphone initialisée via PyAudio.")
        yield from self._iter_mic_chunks_pyaudio()

    def run_forever(self) -> None:
        """Boucle principale d'écoute continue avec détection d'activité vocale (RMS)."""
        LOGGER.info(
            "Démon compagnon M.O.C.T.A.R actif -> %s (seuil RMS=%.3f, voix=%s)",
            self.server_url,
            self.rms_threshold,
            self.voice,
        )
        active_frames: list[bytes] = []
        pre_roll: list[bytes] = []
        speaking = False
        silence_chunks = 0
        max_silence_chunks = max(1, int(SILENCE_DURATION_S / CHUNK_DURATION_S))
        min_speech_chunks = max(1, int(MIN_PHRASE_S / CHUNK_DURATION_S))
        max_speech_chunks = max(10, int(MAX_PHRASE_S / CHUNK_DURATION_S))

        for chunk in self.iter_microphone_chunks():
            rms = compute_rms(chunk)
            if not speaking:
                pre_roll.append(chunk)
                if len(pre_roll) > 4:
                    pre_roll.pop(0)
                if rms >= self.rms_threshold:
                    speaking = True
                    silence_chunks = 0
                    active_frames = list(pre_roll)
            else:
                active_frames.append(chunk)
                if rms < self.rms_threshold:
                    silence_chunks += 1
                else:
                    silence_chunks = 0

                if silence_chunks >= max_silence_chunks or len(active_frames) >= max_speech_chunks:
                    if len(active_frames) >= min_speech_chunks:
                        wav_bytes = encode_pcm16_wav(b"".join(active_frames), SAMPLE_RATE)
                        self.handle_phrase_audio(wav_bytes)
                    active_frames.clear()
                    pre_roll.clear()
                    speaking = False
                    silence_chunks = 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compagnon vocal natif M.O.C.T.A.R pour PC HP Windows")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="URL du serveur M.O.C.T.A.R (ex: https://moctar.klasora.com)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Voix Edge-TTS (ex: fr-FR-HenriNeural)")
    parser.add_argument("--rms-threshold", type=float, default=DEFAULT_RMS_THRESHOLD, help="Seuil RMS de détection vocale")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    agent = MoctarHPCompanionAgent(
        server_url=args.server,
        voice=args.voice,
        rms_threshold=args.rms_threshold,
    )
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        LOGGER.info("Arrêt du démon vocal M.O.C.T.A.R demandé par l'utilisateur.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

