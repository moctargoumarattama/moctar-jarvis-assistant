import subprocess
import pyautogui as pg
import time
def open_windows_search(name):
    try:
        subprocess.run(["start", "search-ms:"], shell=True)
        time.sleep(1)
        pg.write(name)
        time.sleep(1)
        pg.press('enter')
    except Exception as e:
        print("Error:", e)
