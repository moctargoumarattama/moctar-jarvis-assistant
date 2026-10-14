import os
import time

target = r"C:\Users\hp\Desktop\MONOTORING WEB"

for _ in range(120):
    if not os.path.exists(target):
        break
    try:
        os.rmdir(target)
        print("MONOTORING WEB deleted successfully!")
        break
    except Exception:
        time.sleep(2)

