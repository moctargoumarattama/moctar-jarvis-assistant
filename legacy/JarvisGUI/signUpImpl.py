import sys
import csv
from PyQt5.QtWidgets import QDialog, QApplication, QMessageBox
from signUpGUI import Ui_SignUp
from auth_security import hash_password


USER_DATA_FILE = 'user_data.csv'

class mainFileNew(QDialog):
    def __init__(self):
        super(mainFileNew, self).__init__()
        print("Setting up GUI")
        self.firstUI = Ui_SignUp()
        self.firstUI.setupUi(self)

        self.username = None  # Initialize username attribute

        self.firstUI.exitBtn.clicked.connect(self.close)
        self.firstUI.SignupBtn.clicked.connect(self.saveUserData)
        self.firstUI.backBtn.clicked.connect(self.goToMainPage)

    def getUserNameEntry(self):
        return self.firstUI.userNameEntry

    def saveUserData(self):
        self.username = self.firstUI.userNameEntry.text()  # Set the username attribute
        password = self.firstUI.passwordEntry.text()
        confirm_password = self.firstUI.ConfirmpasswordEntry.text()

        if password != confirm_password:
            self.showMessageBox("Warning", "Passwords do not match. Please try again.")
            return

        # Check if the username already exists
        if self.checkUsernameExists(self.username):
            self.showMessageBox("Warning", "Username already exists. Please choose a different username.")
            return

        # If username is not found, save the user data
        with open(USER_DATA_FILE, 'a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([self.username, hash_password(password)])

        self.showMessageBox("Success", "User registered successfully.")

        from newUserPyFaceRecogFile import newFaceRecog
        self.shownewFaceRecogWindow = newFaceRecog()
        self.close()  # Close the current dialog
        self.shownewFaceRecogWindow.show()

    def checkUsernameExists(self, username):
        try:
            with open(USER_DATA_FILE, 'r', newline='') as file:
                reader = csv.reader(file)
                for row in reader:
                    if row and row[0] == username:
                        return True
        except FileNotFoundError:
            return False
        return False

    def goToMainPage(self):
        from main import mainFileNew
        self.showMainWindow = mainFileNew()
        self.close()  # Close the current dialog
        self.showMainWindow.show()

    def showMessageBox(self, title, message):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle(title)
        msg.setStyleSheet("color: white; background-color: #333333; font-size: 12pt;")
        msg.setText(message)
        msg.exec_()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = mainFileNew()
    ui.show()
    sys.exit(app.exec_())
