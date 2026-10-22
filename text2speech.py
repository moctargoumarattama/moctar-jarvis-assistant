"""Module de synthese vocale pour Jarvis.

Prend en charge :
1. Edge-TTS haute definition (voix neuronale francaise Henri / Denise, 100% gratuit, sans cle API).
2. Fallback automatique transparent vers pyttsx3 (SAPI5 local hors-ligne).
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

import config

logger = logging.getLogger("jarvis")

_EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    pass

_PYGAME_AVAILABLE = False
try:
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    pass


def switch_voice(engine, voice_id):
    """Change la voix pyttsx3 locale."""
    try:
        if engine is not None:
            voices = engine.getProperty("voices")
            for voice in voices:
                if voice.id == voice_id:
                    engine.setProperty("voice", voice.id)
                    break
        if "DAVID" in str(voice_id):
            text2speech(engine, "Voice change to Male")
        else:
            text2speech(engine, "Voice change to Female")
    except Exception as exc:
        logger.warning("Could not switch pyttsx3 voice: %s", exc)


def _play_audio_file(file_path: str):
    """Joue un fichier audio MP3 de maniere fluide via pygame."""
    if not _PYGAME_AVAILABLE:
        raise RuntimeError("pygame not available for audio playback")
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.02)
        pygame.mixer.music.unload()
    except Exception as exc:
        logger.warning("Pygame audio playback failed: %s", exc)
        raise


def _speak_with_edge_tts(text: str) -> bool:
    """Tente de generer et jouer la voix neuronale avec Edge-TTS."""
    if not _EDGE_TTS_AVAILABLE or not _PYGAME_AVAILABLE:
        return False

    primary_voice = config.TTS_SETTINGS.get("voice", "fr-FR-RemyMultilingualNeural")
    fallback_voice = config.TTS_SETTINGS.get("fallback_voice", "fr-FR-HenriNeural")
    rate = config.TTS_SETTINGS.get("rate", "+0%")
    pitch = config.TTS_SETTINGS.get("pitch", "+0Hz")

    clean_text = text.strip()
    if not clean_text:
        return True

    # Essai 1 : Voix masculine moderne principale, puis repli sur la voix secondaire
    candidate_voices = [primary_voice]
    if fallback_voice and fallback_voice != primary_voice:
        candidate_voices.append(fallback_voice)

    for candidate in candidate_voices:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                temp_path = tmp.name

            async def _synthesize():
                communicate = edge_tts.Communicate(clean_text, candidate, rate=rate, pitch=pitch)
                await communicate.save(temp_path)

            asyncio.run(_synthesize())
            _play_audio_file(temp_path)
            return True
        except Exception as exc:
            logger.warning("Edge-TTS (%s) synthesis failed (%s)", candidate, exc)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    logger.warning("All Edge-TTS voice candidates failed, falling back to pyttsx3")
    return False


def text2speech(engine, text):
    """Synthese vocale hybride HD (Edge-TTS avec repli automatique pyttsx3)."""
    text = (text or "").strip()
    if not text:
        return

    engine_pref = config.TTS_SETTINGS.get("engine", "edge-tts")
    if engine_pref == "edge-tts":
        if _speak_with_edge_tts(text):
            return

    # Fallback ou choix explicite pyttsx3
    try:
        if engine is not None:
            engine.say(text)
            engine.runAndWait()
    except Exception as exc:
        logger.error("pyttsx3 speech failed: %s", exc)
