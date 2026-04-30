import time

import speech_recognition as sr
import pyttsx3
import datetime
import re
import time

def speak(text):
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()

def listen():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.pause_threshold = 1
        audio = recognizer.listen(source)
        try:
            print("Recognizing...")
            query = recognizer.recognize_google(audio, language='en-in')
            print(f"User said: {query}\n")
            return query.lower()
        except sr.UnknownValueError:
            speak("Sorry, I could not understand what you said.")
            return ""
        except sr.RequestError as e:
            speak("Sorry, I am facing some technical issues.")
            return ""

def set_alarm():
    speak("What time would you like to set the alarm for?")
    alarm_time = listen()
    if alarm_time:
        # Using regular expression to extract time from user's input
        match = re.search(r'\b\d{1,2}:\d{2} [ap]m\b', alarm_time)
        if match:
            alarm_time = match.group()
            try:
                alarm_time = datetime.datetime.strptime(alarm_time, "%I:%M %p")
                current_time = datetime.datetime.now()
                alarm_time = alarm_time.replace(year=current_time.year, month=current_time.month, day=current_time.day)
                if alarm_time < current_time:
                    alarm_time += datetime.timedelta(days=1)
                delta_time = alarm_time - current_time
                seconds = delta_time.total_seconds()
                speak(f"Alarm set for {alarm_time.strftime('%I:%M %p')}.")
                return seconds
            except ValueError:
                speak("Sorry, I could not understand the time.")
        else:
            speak("Sorry, I could not understand the time format.")

if __name__ == "__main__":
    speak("Hello! I am your alarm assistant.")
    while True:
        speak("What would you like me to do?")
        command = listen()
        if "set alarm" in command:
            time_diff = set_alarm()
            if time_diff:
                time.sleep(time_diff)
                speak("Wake up! It's time.")
                break
        elif "exit" in command:
            speak("Goodbye!")
            break

