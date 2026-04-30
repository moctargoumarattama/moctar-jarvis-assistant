import speech_recognition as sr
from googletrans import Translator

def tamil_speech_to_text():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Speak something in Tamil...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)

    try:
        print("Transcribing...")
        # Using Google Speech Recognition for Tamil
        text = recognizer.recognize_google(audio, language="ta-IN")
        print("You said:", text)
        return text
    except sr.UnknownValueError:
        print("Sorry, I couldn't understand what you said.")
    except sr.RequestError as e:
        print("Could not request results from Google Speech Recognition service; {0}".format(e))

def translate_tamil_to_english(text):
    translator = Translator()
    translated_text = translator.translate(text, src='ta', dest='en')
    return translated_text.text

if __name__ == "__main__":
    text=tamil_speech_to_text()
    tamil_text = text
    translated_text = translate_tamil_to_english(tamil_text)
    print("Translated text:", translated_text)
