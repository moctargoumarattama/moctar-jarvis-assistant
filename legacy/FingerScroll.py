import cv2
import numpy as np
import pyautogui

# Function to perform scrolling based on hand gesture
def perform_scroll():
    pyautogui.scroll(10)  # Scroll up by 10 units (you can adjust this value)

# Main function to capture video and detect hand gestures
def main():
    cap = cv2.VideoCapture(0)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Unable to capture video.")
            break

        # Convert the frame to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Apply Gaussian blur
        blurred = cv2.GaussianBlur(gray, (15, 15), 0)

        # Detect edges using Canny
        edges = cv2.Canny(blurred, 50, 150)

        # Find contours
        contours, _ = cv2.findContours(edges.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Check if any contours are found
        if len(contours) > 0:
            # Get the largest contour
            max_contour = max(contours, key=cv2.contourArea)

            # Get the bounding box of the contour
            x, y, w, h = cv2.boundingRect(max_contour)

            # Check if the bounding box is big enough to be considered a hand
            if w * h > 10000:  # Adjust this threshold according to your needs
                # Draw bounding box around the hand
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                # Check if hand is in top portion of the frame (for scroll up)
                if y < frame.shape[0] // 2:
                    perform_scroll()

        cv2.imshow('Frame', frame)

        # Break the loop if 'q' is pressed
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # Release the capture
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
