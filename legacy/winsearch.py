import pyautogui as pg
import time
from fuzzywuzzy import fuzz
import subprocess
import psutil

def os_open(app):
    pg.press('win')
    time.sleep(1)
    pg.write(app)
    pg.write(" ")
    time.sleep(1)
    pg.press('enter')
    time.sleep(1)

    #maximize
    pg.hotkey('win','up')

def close_application(app):
    for proc in psutil.process_iter(['pid', 'name']):
        # Check if the process name contains "app"
        if app in proc.info['name'].lower():
            # Terminate the process
            proc.terminate()
            print("App terminated successfully.")
            return
    print("App not found.")

    running_processes = psutil.process_iter()
