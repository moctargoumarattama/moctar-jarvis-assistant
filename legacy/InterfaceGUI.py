import speech_recognition as sr
import pyttsx3
import pygame
import threading

def speech_to_text():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)
    try:
        text = recognizer.recognize_google(audio)
        print("You said:", text)
        return text.lower()
    except sr.UnknownValueError:
        print("Sorry, I could not understand what you said.")
        return ""

def show_animation():
    pygame.init()
    pygame.mixer.init()
    pygame.mixer.music.load("windows_notification.mp3")  # You need to replace "windows_notify.wav" with the path to your Windows notification sound file
    pygame.mixer.music.play()
    pygame.mixer.music.set_volume(1.0)

    print("Animation triggered!")

def main():
    while True:
        text = speech_to_text()
        if "hey jarvis" in text:
            thread = threading.Thread(target=show_animation)
            thread.start()
            thread.join()

if __name__ == "__main__":
    main()
