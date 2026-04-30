import time

import speech_recognition as sr

import config


DEFAULT_LANGUAGE = config.MIC_SETTINGS["default_language"]
TIMEOUT = config.MIC_SETTINGS["timeout"]
PHRASE_LIMIT = config.MIC_SETTINGS["phrase_limit"]
AMBIENT_DURATION = config.MIC_SETTINGS["ambient_duration"]
MIC_INDEX = config.MIC_SETTINGS["device_index"]

recognizer = sr.Recognizer()


def speech_to_text(language=DEFAULT_LANGUAGE):
    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            print("Parle maintenant...")
            recognizer.adjust_for_ambient_noise(source, duration=AMBIENT_DURATION)
            audio = recognizer.listen(
                source,
                timeout=TIMEOUT,
                phrase_time_limit=PHRASE_LIMIT,
            )
    except sr.WaitTimeoutError:
        print("Aucun son detecte")
        time.sleep(1)
        return ""
    except Exception as exc:
        print(f"Erreur micro : {exc}")
        time.sleep(2)
        return ""

    try:
        print("Transcription...")
        text = recognizer.recognize_google(audio, language=language)
        print("Tu as dit :", text)
        return text.lower()
    except sr.UnknownValueError:
        print("Je n'ai pas compris")
        time.sleep(1)
        return ""
    except sr.RequestError as exc:
        print(f"Erreur API Google : {exc}")
        time.sleep(2)
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
