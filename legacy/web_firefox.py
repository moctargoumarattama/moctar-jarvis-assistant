from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
import time
import speech2text as s2t
import text2speech as t2s
import pyttsx3
engine = pyttsx3.init()
import pyautogui

# Initialize global variables
driver = None
engine = pyttsx3.init()

# Function to activate the Firefox window
def activate_firefox_window():
    pyautogui.click(x=300, y=300)  # Click somewhere on the screen to activate Firefox

def initialize_firefox_driver():
    global driver
    driver = webdriver.Firefox()

def web_search(query):
    try:
        t2s.text2speech(engine, "Yes sir")
        search_box = driver.find_element(By.NAME,"q")
        search_box.clear()
        search_box.send_keys(query)
        search_box.submit()
    except Exception as e:
        print("An error occurred during web search:", e)
        t2s.text2speech(engine, "Sorry, I couldn't perform the search.")

def navigate_to_website(url):
    try:
        driver.get(url)
    except Exception as e:
        print("An error occurred during website navigation:", e)
        t2s.text2speech(engine, "Sorry, I couldn't navigate to the website.")

def close_current_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'w')

def open_new_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 't')

# Function to move to the next tab
def move_to_next_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'tab')

def close_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'w')

def close_previous_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'shift', 'tab')
    pyautogui.hotkey('ctrl', 'w')

# Function to move to the previous tab for Windows
def previous_tab():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'shift', 'tab')

# Function to close all tabs
def close_all_tabs():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'shift', 'w')

def open_new_window():
    activate_firefox_window()
    pyautogui.hotkey('ctrl', 'n')

# Driver program
def main(ip):
    initialize_firefox_driver()
    t2s.text2speech(engine, "opening firefox")
    driver.get("https://www.google.com/")
    while True:
        print("firefox control")
        command = (s2t.voice2text("en")).lower()
        print("firefox control"+command)
        if command == "exit" or "close all tabs" in command:
            driver.quit()
            print("Exiting program...")
            break

        elif "search" in command:
            query = command.replace("search", "")
            web_search(query)
        elif command == "navigate_to_website":
            url = input("Enter website URL: ")
            navigate_to_website(url)
        elif "new tab" in command:
            open_new_tab()
        elif "close tab" in command:
            close_tab()
        elif "previous tab" in  command:
            previous_tab()
        elif "close previous tab" in  command:
            close_previous_tab()
        elif "next tab" in command:
            move_to_next_tab()
        elif "new window" in command:
            open_new_window()
        else:
            return command

