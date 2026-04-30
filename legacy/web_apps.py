import pyautogui as pg
import text2speech as t2s
import speech2text as s2t
import pyttsx3
engine = pyttsx3.init()
import time
# Open Google Maps in a web browser
def open_google_maps():
    pg.hotkey('win', 'r')
    pg.write('chrome https://www.google.com/maps')
    pg.press('enter')

# Open Google Calendar in a web browser
def open_google_calendar():
    pg.hotkey('win', 'r')
    pg.write('chrome https://calendar.google.com')
    pg.press('enter')

# Open LinkedIn in a web browser
def open_linkedin():
    pg.hotkey('win', 'r')
    pg.write('chrome https://www.linkedin.com')
    pg.press('enter')

# Open Telegram desktop app
def open_telegram():
    pg.hotkey('win', 'r')
    # pg.write('telegram')
    pg.write('chrome https://web.telegram.org/k/')
    pg.press('enter')

# Open OneDrive in a web browser
def open_onedrive():
    pg.hotkey('win', 'r')
    pg.write('chrome https://onedrive.live.com')
    pg.press('enter')
    
# Open GitHub in a web browser
def open_github():
    pg.hotkey('win', 'r')
    pg.write('chrome https://github.com')
    pg.press('enter')


# Open Google Drive in a web browser
def open_google_drive():
    pg.hotkey('win', 'r')
    pg.write('chrome https://drive.google.com')
    pg.press('enter')

# Open ChatGPT web interface
def open_chatgpt():
    pg.hotkey('win', 'r')
    pg.write('chrome https://www.chatgpt.com')
    pg.press('enter')

# Open Telegram web interface
def open_telegram_web():
    pg.hotkey('win', 'r')
    pg.write('chrome https://web.telegram.org')
    pg.press('enter')

# Open Microsoft Teams desktop app
def open_teams():
    pg.hotkey('win')
    pg.write('teams')
    pg.press('enter')

# Open Spotify desktop app
def open_spotify():
    pg.hotkey('win', 'r')
    pg.write('spotify')
    pg.press('enter')

