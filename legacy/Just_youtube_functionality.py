import speech_recognition as sr
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
import time

def recognize_input():
    recognizer = sr.Recognizer()

    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=2)
        recognizer.pause_threshold = 1.5

        print("Listening... Say something:")
        try:
            audio = recognizer.listen(source, timeout=10)
            text = recognizer.recognize_google(audio)
            print(f"User (Voice): {text}")
            return text.lower()
        except sr.UnknownValueError:
            print("Sorry, I couldn't understand the audio.")
            return None
        except sr.RequestError as e:
            print(f"Could not request results from Google Speech Recognition service; {e}")
            return None

def open_google():
    print("Opening Google...")
    driver = webdriver.Chrome()  # You need to have the ChromeDriver executable in your PATH or specify its path here
    driver.get("https://www.google.com/")
    return driver

def open_youtube(driver):
    print("Opening YouTube...")
    driver.get("https://www.youtube.com/")

def search_youtube(driver, query):
    search_box = driver.find_element("name", "search_query")
    search_box.clear()  # Clear the search box
    search_box.send_keys(query)
    search_box.send_keys(Keys.RETURN)

# Main program
web_driver = None
while True:
    user_input = recognize_input()

    if user_input:
        if "stop" in user_input:
            print("Exiting program...")
            break
        elif "open chrome" in user_input:
            web_driver = open_google()
        elif "open youtube" in user_input:
            web_driver = open_google()
            if web_driver:
                open_youtube(web_driver)
            else:
                print("Please open a web page first, for example, say 'open Chrome'.")
        elif "search for" in user_input and web_driver:
            query_start_index = user_input.find("search for") + len("search for")
            search_query = user_input[query_start_index:].strip()
            print(f"Searching YouTube for: {search_query}")
            search_youtube(web_driver, search_query)
        else:
            print("Invalid command. Please say 'open Google', 'open YouTube', or 'search for [query]'.")
