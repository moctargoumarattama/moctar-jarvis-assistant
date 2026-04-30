import speech_recognition as sr
import pyjokes

def recognize_speech():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    try:
        text = recognizer.recognize_google(audio).lower()
        return text
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        print("Speech recognition service unavailable")
        return ""

def tell_joke():
    joke = pyjokes.get_joke()
    print(joke)

while True:
    user_input = input("You (type or say 'tell me a joke'): ").lower()

    if "tell me a joke" in user_input:
        tell_joke()
    elif "tell me a joke" in recognize_speech():
        tell_joke()
    elif user_input == "exit":
        print("Exiting...")
        break
    else:
        print("I'm sorry, I didn't understand. You can ask me to tell you a joke!")
