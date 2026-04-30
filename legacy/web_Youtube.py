from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
import time
import text2speech as t2s
import speech2text as s2t
import pyttsx3
engine = pyttsx3.init()

def open_google():
    try:
        print("Opening Google...")
        driver = webdriver.Chrome()  # You need to have the ChromeDriver executable in your PATH or specify its path here
        driver.get("https://www.google.com/")
        return driver
    except Exception as e:
        print("Error occurred while playing the video:", e)

def open_youtube(driver):
    try:
        print("Opening YouTube...")
        driver.get("https://www.youtube.com/")
    except Exception as e:
        print("Error occurred while playing the video:", e)

def search_youtube(driver, query):
    try:
        search_box = driver.find_element("name", "search_query")
        search_box.clear()  # Clear the search box
        search_box.send_keys(query)
        search_box.send_keys(Keys.RETURN)
    except Exception as e:
        print("Error occurred while playing the video:", e)
    


# Main program
def play_youtube_video(driver,query):
    try:
        search_box = driver.find_element("name", "search_query")
        search_box.clear()  # Clear the search box
        search_box.send_keys(query)
        search_box.send_keys(Keys.RETURN)
        time.sleep(2)
        first_video = driver.find_element(By.ID,'video-title')
        first_video.click()
        print("Playing the first video...")
    except Exception as e:
        print("Error occurred while playing the video:", e)


# Modify the main function to include playing a video
def main(ip):
    web_driver = open_google()
    open_youtube(web_driver)
    while True:
        print("youtube control")
        user_input = (s2t.voice2text("en")).lower()
        print("youtube control"+user_input)
        if user_input:
            if "stop" in user_input or "close youtube" in user_input:
                print("Exiting program...")
                return
                break
            
            elif "search for" in user_input and web_driver:
                query_start_index = user_input.find("search for") + len("search for")
                search_query = user_input[query_start_index:].strip()
                print(f"Searching YouTube for: {search_query}")
                search_youtube(web_driver, search_query)
            elif "play" in user_input and web_driver:
                query_start_index = user_input.find("play") + len("play")
                search_query = user_input[query_start_index:].strip()
                print(f"Searching YouTube for: {search_query}")
                play_youtube_video(web_driver, search_query)
               

            else:
                return user_input
        