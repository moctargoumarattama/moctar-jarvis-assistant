import os
import sys
import cv2
import face_recognition
from PyQt5.QtWidgets import QWidget, QApplication, QMessageBox
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import pyqtSlot, QTimer
from newUserFaceRecGUI import Ui_Newface
from signUpImpl import mainFileNew  # Import the SignUpDialog class

class newFaceRecog(QWidget):
    def __init__(self):
        super(newFaceRecog, self).__init__()
        self.faceNewUI = Ui_Newface()
        self.faceNewUI.setupUi(self)
        self.faceNewUI.exitBtn.clicked.connect(self.close)
        self.faceNewUI.loginBtn.clicked.connect(self.connectToLoginPage)
        self.faceNewUI.captureBtn.clicked.connect(self.captureImage)

        # Create an instance of the SignUpDialog class
        self.signup_dialog = mainFileNew()

        self.startVideo()

    @pyqtSlot()
    def startVideo(self):
        try:
            self.capture = cv2.VideoCapture(0)
            if not self.capture.isOpened():
                print("Failed to open webcam")
                return
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.updateFrames)
            self.timer.start(10)
        except Exception as e:
            print("Error occurred during video capture:", e)

    def updateFrames(self):
        try:
            ret, self.image = self.capture.read()
            if ret:
                self.displayImage(self.image)
            else:
                print("Failed to capture frame")
        except Exception as e:
            print("Error occurred during frame update:", e)

    def displayImage(self, image):
        try:
            image = cv2.resize(image, (681, 491))
            image, _ = self.faceRec(image)
            qformat = QImage.Format_RGB888
            outImage = QImage(image, image.shape[1], image.shape[0], image.strides[0], qformat)
            outImage = outImage.rgbSwapped()
            self.faceNewUI.videBack.setPixmap(QPixmap.fromImage(outImage))
            self.faceNewUI.videBack.setScaledContents(True)
        except Exception as e:
            print("Error occurred during image display:", e)

    def faceRec(self, image):
        try:
            face_locations = face_recognition.face_locations(image)
            for (top, right, bottom, left) in face_locations:
                cv2.rectangle(image, (left, top), (right, bottom), (0, 255, 0), 2)
            return image, face_locations
        except Exception as e:
            print("Error occurred during face recognition:", e)

    def captureImage(self):
        try:
            # Open the CSV file in read mode and read the last line to get the username
            with open('user_data.csv', 'r') as file:
                lines = file.readlines()
                last_line = lines[-1].strip()  # Get the last line
                last_username = last_line.split(',')[0]  # Extract the username

            # Save the captured image with the last username as filename
            image_path = os.path.join('D:\BSc CSD Sem 6\Project\JarvisGUI\FaceRecogGUI\\faceImages', f'{last_username}.jpg')
            cv2.imwrite(image_path, self.image)
            print("Image captured and saved successfully!")

            # Display a message box indicating successful capture and save
            self.showMessageBox("Success", "Image captured and saved successfully!")

        except Exception as e:
            print("Error occurred while capturing and saving image:", e)

    def connectToLoginPage(self):
        from loginWindowMain import loginWindow
        self.showLoginWindow = loginWindow()
        self.timer.stop()  # Stop the timer
        self.capture.release()  # Release the capture object
        self.close()  # Close the current dialog
        self.showLoginWindow.show()

    def showMessageBox(self, title, message):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle(title)
        msg.setStyleSheet("color: white; background-color: #333333; font-size: 12pt;")
        msg.setText(message)
        msg.exec_()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    try:
        ui = newFaceRecog()
        ui.show()
        sys.exit(app.exec_())
    except Exception as e:
        print("Error occurred:", e)
