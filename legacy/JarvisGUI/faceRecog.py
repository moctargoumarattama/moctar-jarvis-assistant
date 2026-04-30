import os
import sys
import cv2
import face_recognition
from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import pyqtSlot, QTimer
from faceRecogGUI import Ui_Widget

class faceRecog(QWidget):
    def __init__(self):
        super(faceRecog, self).__init__()
        print("Setting up GUI")
        self.faceUI = Ui_Widget()
        self.faceUI.setupUi(self)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.updateFrames)

        self.faceUI.exitBtn.clicked.connect(self.close)
        self.faceUI.loginBtn.clicked.connect(self.connectToLoginPage)
        self.faceUI.newUserBtn.clicked.connect(self.goToSignUpPage)

        self.capture = None  # Initialize the capture object

        self.startVideo()

    def startVideo(self):
        print("Encoding started")
        try:
            self.capture = cv2.VideoCapture(0)  # Open webcam
            if not self.capture.isOpened():
                print("Failed to open webcam")
                return
            self.timer.start(10)
        except Exception as e:
            print("Error occurred during video capture:", e)

    @pyqtSlot()
    def updateFrames(self):
        try:
            ret, self.image = self.capture.read()
            if ret:
                self.displayImage()
            else:
                print("Failed to capture frame")
        except Exception as e:
            print("Error occurred during frame update:", e)

    def displayImage(self):
        try:
            image = cv2.resize(self.image, (681, 491))
            image, detected_name = self.faceRec(image)

            qformat = QImage.Format_RGB888
            outImage = QImage(image, image.shape[1], image.shape[0], image.strides[0], qformat)
            outImage = outImage.rgbSwapped()

            self.faceUI.videoBack.setPixmap(QPixmap.fromImage(outImage))
            self.faceUI.videoBack.setScaledContents(True)

            if detected_name == "hari":
                self.connectToJarvisMainFile()
                self.timer.stop()

        except Exception as e:
            print("Error occurred during image display:", e)

    def faceRec(self, image):
        try:
            path = r'D:\BSc CSD Sem 6\Project\JarvisGUI\faceRecogGUI\faceImages'
            known_face_encodings = []
            known_face_names = []

            for filename in os.listdir(path):
                image_path = os.path.join(path, filename)
                img = face_recognition.load_image_file(image_path)
                face_encoding_list = face_recognition.face_encodings(img)
                if face_encoding_list:
                    face_encoding = face_encoding_list[0]
                    known_face_encodings.append(face_encoding)
                    known_face_names.append(os.path.splitext(filename)[0])

            if not known_face_encodings:
                print("No face encodings found.")
                return image, "Unknown"

            face_locations = face_recognition.face_locations(image)
            face_encodings = face_recognition.face_encodings(image, face_locations)

            detected_name = "Unknown"

            for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
                name = "Unknown"

                if True in matches:
                    first_match_index = matches.index(True)
                    name = known_face_names[first_match_index]
                    detected_name = name

                cv2.rectangle(image, (left, top), (right, bottom), (0, 255, 0), 2)
                cv2.putText(image, name, (left + 6, bottom - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            return image, detected_name

        except Exception as e:
            print("Error occurred during face recognition:", e)
            return image, "Unknown"

    def connectToJarvisMainFile(self):
        from subprocess import call
        python_executable = sys.executable
        self.timer.stop()  # Stop the timer
        self.capture.release()  # Release the capture object
        call([python_executable, "jarvisMAIN.py"])
        self.close()

    def connectToLoginPage(self):
        from loginWindowMain import loginWindow
        self.timer.stop()  # Stop the timer
        self.capture.release()  # Release the capture object
        self.showLoginWindow = loginWindow()
        self.close()
        self.showLoginWindow.show()

    def goToSignUpPage(self):
        from signUpImpl import mainFileNew  # Import the sign-up window class
        self.timer.stop()  # Stop the timer
        self.capture.release()  # Release the capture object
        self.showSignUp = mainFileNew()
        self.close()  # Close the current dialog
        self.showSignUp.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    try:
        ui = faceRecog()
        ui.show()
        sys.exit(app.exec_())
    except Exception as e:
        print("Error occurred:", e)
