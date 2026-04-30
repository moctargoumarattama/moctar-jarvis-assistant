import pyautogui as pg
import text2speech as t2s
import speech2text as s2t
import pyttsx3
engine = pyttsx3.init()
import time

# Open Gmail in a web browser

def main():
    pg.hotkey('win', 'r')
    pg.write('chrome https://mail.google.com/mail/u/0/#inbox')
    pg.press('enter')
    pg.hotkey('win','up')
    time.sleep(5)
    while True:
        user_input = (s2t.voice2text("en")).lower()
        print(user_input)
        if user_input:
            if "stop" in user_input or "close youtube" in user_input:
                print("Exiting program...")
                t2s.text2speech(engine, "thank you sir")
                return
            else:
                return user_input

