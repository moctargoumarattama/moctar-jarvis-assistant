import speech_recognition as sr
import pyttsx3

def recognize_speech():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)  # Adjust for ambient noise
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio).lower()
        return text
    except sr.UnknownValueError:
        return ""  # Return empty string if speech is not recognized
    except sr.RequestError:
        print("Speech recognition service unavailable")
        return ""

def switch_voice(engine, voice_id):
    # Get the list of available voices
    voices = engine.getProperty('voices')
    for voice in voices:
        if voice.id == voice_id:
            engine.setProperty('voice', voice.id)
            break

def main():
    engine = pyttsx3.init()

    while True:
        user_input = recognize_speech()

        if "switch to jarvis" in user_input:
            switch_voice(engine, 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0')
            engine.say("Voice switched to male.")
            engine.runAndWait()
        elif "switch to friday" in user_input:
            switch_voice(engine, 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_ZIRA_11.0')
            engine.say("Voice switched to female.")
            engine.runAndWait()
        elif user_input == "exit":
            print("Exiting...")
            break
        else:
            engine.say("I'm sorry, I didn't understand.")
            engine.runAndWait()

if __name__ == "__main__":
    main()
