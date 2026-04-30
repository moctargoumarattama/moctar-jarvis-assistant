import speech_recognition as sr
import pyautogui

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

def minimize_window():
    pyautogui.hotkey('win', 'down')  # Minimize window shortcut

def main():
    while True:
        user_input = recognize_speech()

        if "minimize window" in user_input:
            minimize_window()
        elif user_input == "exit":
            print("Exiting...")
            break
        else:
            print("Command not recognized. Please try again.")

if __name__ == "__main__":
    main()
