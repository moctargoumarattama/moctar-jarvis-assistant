import pyautogui as pg
import time
from fuzzywuzzy import fuzz
import subprocess
import psutil

def os_open(app):
    pg.press('win')
    time.sleep(1)
    pg.write(app)
    time.sleep(1)
    pg.press('enter')
    time.sleep(1)

    pg.hotkey('win','up')
    
def close_application(app):
    running_processes = psutil.process_iter()
    for process in running_processes:
        ratio = fuzz.ratio((process.name()).replace(".exe",""), app)
        if ratio >= 70:  # You can adjust the threshold as needed
            pname = process.name()
            print("Strings are approximately the same.")
        else:
            print("Strings are different.")
    try:
        # Run taskkill command to close the application
        result = subprocess.run(['taskkill', '/F', '/IM', pname], capture_output=True, text=True)
        # Check if the command was successful
        if result.returncode == 0:
            print(f"Successfully closed {app}.")
        else:
            print(f"Failed to close {app}.")
            print("Error:", result.stderr)
    except Exception as e:
        print("An error occurred:", e)

def minimize(app):
    running_processes = psutil.process_iter()
    pname=""
    for process in running_processes:

        ratio = fuzz.ratio((process.name()).replace(".exe",""), app)
        if ratio >= 50:  # You can adjust the threshold as needed
            pname = process.name()
            print("Strings are approximately the same.")
            break
        else:
            print("Strings are different.")
    if pname != "":

        try:
             # Run taskkill command to close the application
            powershell_cmd = "$wshell = New-Object -ComObject wscript.shell;$wshell.AppActivate('"+pname+"');sleep 1;$wshell.SendKeys('%{SPACE}n')"
            subprocess.run(["powershell", "-Command", powershell_cmd])

        except Exception as e:
            print("An error occurred:", e)
