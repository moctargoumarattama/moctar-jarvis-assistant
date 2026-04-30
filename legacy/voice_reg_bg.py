import speech_recognition as sr
import schedule
import datetime
import pyttsx3

def speak(text):
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()

def set_alarm(alarm_time):
    def job():
        speak("Wake up! It's time.")

    schedule.every().day.at(alarm_time).do(job)

def listen():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        # Adjust microphone sensitivity to reduce background noise
        recognizer.adjust_for_ambient_noise(source)

        print("Listening...")
        audio = recognizer.listen(source)

        try:
            print("Recognizing...")
            # Recognize speech from the audio
            query = recognizer.recognize_google(audio, language='en-in')
            print(f"User said: {query}\n")
            return query.lower()
        except sr.UnknownValueError:
            speak("Sorry, I could not understand what you said.")
            return ""
        except sr.RequestError as e:
            speak("Sorry, I am facing some technical issues.")
            return ""

if __name__ == "__main__":
    speak("Hello! I am your desktop voice assistant.")
    while True:
        speak("What would you like me to do?")
        command = listen()
        if command:
            process_command(command)
