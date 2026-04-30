import tkinter as tk
from tkinter import messagebox
import cv2
import os

class FaceRecognitionApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Face Recognition App")

        self.new_user_button = tk.Button(master, text="New User", command=self.capture_image)
        self.new_user_button.pack()

        self.login_button = tk.Button(master, text="Login", command=self.login)
        self.login_button.pack()

    def capture_image(self):
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

        cam = cv2.VideoCapture(0)
        while True:
            ret, frame = cam.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)

            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)
                roi_gray = gray[y:y + h, x:x + w]
                roi_color = frame[y:y + h, x:x + w]

                cv2.imwrite('captured_image.jpg', roi_color)

            cv2.imshow('Capture Image', frame)
            key = cv2.waitKey(1)
            if key == ord('q'):
                break
            elif key == ord('c'):
                if len(faces) == 1:
                    messagebox.showinfo("Success", "Image Captured Successfully!")
                    cam.release()
                    cv2.destroyAllWindows()
                    return
                else:
                    messagebox.showerror("Error", "Please make sure your face is clearly visible in the camera!")
                    cam.release()
                    cv2.destroyAllWindows()
                    return


    def histogram_distance(self, image1, image2):
        hist1 = cv2.calcHist([image1], [0], None, [256], [0, 256])
        hist2 = cv2.calcHist([image2], [0], None, [256], [0, 256])
        return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

    def login(self):
        if not os.path.exists('captured_image.jpg'):
            messagebox.showerror("Error", "No image available. Please capture an image first!")
            return

        known_image = cv2.imread("captured_image.jpg", cv2.IMREAD_GRAYSCALE)

        cam = cv2.VideoCapture(0)
        while True:
            ret, frame = cam.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)

            if len(faces) == 1:
                (x, y, w, h) = faces[0]
                roi_gray = gray[y:y + h, x:x + w]
                roi_gray_resized = cv2.resize(roi_gray, (known_image.shape[1], known_image.shape[0]))

                similarity = self.histogram_distance(known_image, roi_gray_resized)
                if similarity > 0.8:
                    print("Similarity:", similarity)
                    print("Login Successful!")
                    cam.release()
                    cv2.destroyAllWindows()
                    return
                else:
                    print("Similarity:", similarity)


            cv2.imshow('Login', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        messagebox.showerror("Error", "Failed to recognize face!")
        cam.release()
        cv2.destroyAllWindows()

def main():
    root = tk.Tk()
    app = FaceRecognitionApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()


