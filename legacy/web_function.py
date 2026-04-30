import pyautogui as pg

import text2speech
import web_apps as wa
import web_Whatsapp
import web_Youtube
import web_chrome
import web_edge
import web_firefox
import web_gmail
import pyttsx3
engine = pyttsx3.init()

def web_open(app,input_str):
    
    if "whatsapp" in app:
        print(app)
        ip = web_Whatsapp.main()
        return ip
    
    if "youtube" in app:
        print(app)
        ip = web_Youtube.main(input_str)
        return ip
    
    if "chrome" in app:
        print(app)
        ip = web_chrome.main(input_str)
        return ip
    
    if "edge" in app:
        print(app)
        ip = web_edge.main(input_str)
        return ip
    
    if "firefox" in app:
        try:
            ip = web_firefox.main(input_str)  # This line calls web_firefox function with input_str as argument
            return ip  # This line returns the value of ip
        except Exception as e:  # This line captures any exception raised within the try block
            print("FireFox is not in this system", e)
            text2speech.text2speech(engine, "FireFox is not available")
    
    if "gmail" in app:
        print(app)
        ip = web_gmail.main()
        return ip
    
    if "telegram" in app:
        print(app)
        wa.open_telegram()
        return
    
    if "google calendar" in app:
        print(app)
        wa.open_google_calendar()
        return
    
    if "google maps" in app:
        print(app)
        wa.open_google_maps()
        return
    
    if "one drive" in app:
        print(app)
        wa.open_onedrive()
        return
    
    if "google drive" in app:
        print(app)
        wa.open_google_drive()
    
    if "teams" in app:
        print(app)
        wa.open_teams()
        return
    
    if "spotify" in app:
        print(app)
        wa.open_spotify()
        return
    
    if "chat gpt" in app:
        print(app)
        wa.open_chatgpt()
        return
    
    if "linkedin" in app:
        print(app)
        wa.open_linkedin()
        return
    
    if "github" in app:
        print(app)
        wa.open_github()
        return
