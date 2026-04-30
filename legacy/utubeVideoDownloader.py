
from pytube import YouTube

def download_video(url, save_path):
    try:
        yt = YouTube(url)
        stream = yt.streams.get_highest_resolution()
        stream.download(save_path)
        print("Download completed successfully.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

def main():
    url = input("Enter the URL of the video: ")
    save_path = input("Enter the path to save the video: ")
    download_video(url, save_path)

if __name__ == "__main__":
    main()




















# import pyautogui
# import time
# import os
#
# def open_paint():
#     # Open Paint using Windows Run dialog (Windows + R)
#     os.system("start mspaint")
#     time.sleep(2)  # Wait for Paint to open
#
# def draw_line():
#     # Move mouse to starting point of the line
#     pyautogui.moveTo(100, 100, duration=0.5)
#     pyautogui.mouseDown()  # Press left mouse button
#     time.sleep(1)  # Hold left mouse button for 1 second
#     # Move mouse to end point of the line
#     pyautogui.moveTo(300, 300, duration=1.5)
#     pyautogui.mouseUp()  # Release left mouse button
#
# def undo():
#     # Press Ctrl + Z to undo
#     pyautogui.hotkey('ctrl', 'z')
#
# def pick_red_color():
#     # Move mouse to color palette and select red color
#     pyautogui.moveTo(50, 200, duration=0.5)
#     pyautogui.click()
#
# def draw_square():
#     # Move mouse to starting point of the square
#     pyautogui.moveTo(200, 200, duration=0.5)
#     pyautogui.mouseDown()
#     time.sleep(1)
#     # Draw square
#     pyautogui.moveRel(100, 0, duration=0.5)  # Move right
#     pyautogui.moveRel(0, 100, duration=0.5)  # Move down
#     pyautogui.moveRel(-100, 0, duration=0.5)  # Move left
#     pyautogui.moveRel(0, -100, duration=0.5)  # Move up
#     pyautogui.mouseUp()
#
# def draw_rectangular_spiral():
#     # Move mouse to starting point of the spiral
#     pyautogui.moveTo(400, 400, duration=0.5)
#     pyautogui.mouseDown()
#     side_length = 10
#     for _ in range(20):
#         pyautogui.moveRel(side_length, 0, duration=0.5)  # Move right
#         pyautogui.moveRel(0, side_length, duration=0.5)  # Move down
#         side_length += 10
#         pyautogui.moveRel(-side_length, 0, duration=0.5)  # Move left
#         pyautogui.moveRel(0, -side_length, duration=0.5)  # Move up
#         side_length += 10
#     pyautogui.mouseUp()
#
# def main():
#     open_paint()
#     time.sleep(2)  # Wait for Paint to fully open
#     draw_line()
#     undo()
#     time.sleep(1)
#     pick_red_color()
#     draw_square()
#     draw_rectangular_spiral()
#
# if __name__ == "__main__":
#     main()
