import logging
import time

try:
    import speech_recognition as sr
except Exception:
    class _SRFallback:
        class WaitTimeoutError(Exception):
            pass

        class UnknownValueError(Exception):
            pass

        class RequestError(Exception):
            pass

        class Recognizer:
            def adjust_for_ambient_noise(self, source, duration=1):
                return None

            def listen(self, source, timeout=5, phrase_time_limit=6):
                raise _SRFallback.WaitTimeoutError()

            def recognize_google(self, audio, language="fr-FR"):
                return ""

        class Microphone:
            def __init__(self, device_index=None):
                self.device_index = device_index

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

    sr = _SRFallback()

import config


DEFAULT_LANGUAGE = config.MIC_SETTINGS["default_language"]
TIMEOUT = config.MIC_SETTINGS["timeout"]
PHRASE_LIMIT = config.MIC_SETTINGS["phrase_limit"]
AMBIENT_DURATION = config.MIC_SETTINGS["ambient_duration"]
MIC_INDEX = config.MIC_SETTINGS["device_index"]
ATTEMPTS = max(1, config.MIC_SETTINGS["attempts"])
CALIBRATION_INTERVAL = max(0, config.MIC_SETTINGS["calibration_interval"])

recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = config.MIC_SETTINGS["pause_threshold"]
recognizer.phrase_threshold = config.MIC_SETTINGS["phrase_threshold"]
recognizer.non_speaking_duration = config.MIC_SETTINGS["non_speaking_duration"]
_last_calibration_at = 0.0
logger = logging.getLogger("jarvis.speech")


def _calibrate_if_needed(source):
    global _last_calibration_at

    now = time.monotonic()
    if now - _last_calibration_at < CALIBRATION_INTERVAL:
        return

    recognizer.adjust_for_ambient_noise(source, duration=config.MIC_SETTINGS["ambient_duration"])
    recognizer.energy_threshold *= config.MIC_SETTINGS["energy_threshold_ratio"]
    _last_calibration_at = now


def _recognize_once(language):
    with sr.Microphone(device_index=MIC_INDEX) as source:
        _calibrate_if_needed(source)
        print("Parle maintenant...")
        audio = recognizer.listen(
            source,
            timeout=config.MIC_SETTINGS["timeout"],
            phrase_time_limit=config.MIC_SETTINGS["phrase_limit"],
        )

    print("Transcription...")
    text = recognizer.recognize_google(audio, language=language)
    return text.strip().lower()


def speech_to_text(language=DEFAULT_LANGUAGE):
    last_reason = "aucun son"
    for attempt in range(ATTEMPTS):
        try:
            text = _recognize_once(language)
            if text:
                print("Tu as dit :", text)
                return text
            last_reason = "transcription vide"
        except sr.WaitTimeoutError:
            last_reason = "aucun son dans le delai"
        except sr.UnknownValueError:
            last_reason = "parole incomprehensible"
        except sr.RequestError as exc:
            last_reason = f"service de transcription indisponible: {exc}"
            logger.warning(last_reason)
            break
        except Exception as exc:
            last_reason = f"micro indisponible: {exc}"
            logger.exception("Erreur pendant la capture audio")
            break

        if attempt + 1 < ATTEMPTS:
            print("Je n'ai pas bien entendu, essaie encore...")

    print(f"Ecoute echouee: {last_reason}")
    return ""


def french_speech_to_text():
    return speech_to_text("fr-FR")


def english_speech_to_text():
    return speech_to_text("en-US")


def tamil_speech_to_text():
    return speech_to_text("ta-IN")


def voice2text(lang="fr"):
    if lang == "fr":
        return french_speech_to_text()
    if lang == "ta":
        return tamil_speech_to_text()
    return english_speech_to_text()
