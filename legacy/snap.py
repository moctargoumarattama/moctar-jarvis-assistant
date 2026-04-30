import cv2
import pyautogui

def detect_snapping():
    cap = cv2.VideoCapture(0)  # Open the default camera (usually the webcam)

    if not cap.isOpened():
        print("Error: Unable to open webcam.")
        return

    print("Listening for snapping sound... Press 'q' to quit.")

    prev_frame = None

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Error: Unable to capture frame.")
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if prev_frame is not None:
            # Calculate absolute difference between current and previous frame
            diff = cv2.absdiff(gray, prev_frame)
            mean_diff = diff.mean()

            # If the mean difference exceeds a threshold, consider it as snapping
            if mean_diff > 10:  # Adjust threshold as needed
                print("Snapping detected!")
                minimize_window()
                break

        prev_frame = gray

        cv2.imshow("Frame", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

def minimize_window():
    # Simulate keyboard shortcut to minimize the window
    pyautogui.hotkey('winleft', 'down')

if __name__ == "__main__":
    detect_snapping()
