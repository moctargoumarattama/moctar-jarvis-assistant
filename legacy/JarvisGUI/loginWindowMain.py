import sys
import csv
from PyQt5.QtWidgets import QWidget, QLineEdit, QApplication, QMessageBox
from loginWindowGUI import Ui_Widget
from PyQt5 import QtGui
from auth_security import hash_password, is_password_hash, verify_password


USER_DATA_FILE = 'user_data.csv'

class loginWindow(QWidget):
    def __init__(self):
        super(loginWindow, self).__init__()
        print("Setting up GUI")
        self.loginUI = Ui_Widget()
        self.loginUI.setupUi(self)

        self.loginUI.backBtn.clicked.connect(self.goToInitialPage)
        self.loginUI.NewUserBtn.clicked.connect(self.goToSignUpPage)  # Connect NewUserBtn to sign-up page

        self.loginUI.label.hide()
        self.loginUI.passwordEntry.setEchoMode(QLineEdit.Password)
        self.loginUI.loginBtn.clicked.connect(self.validateLogin)

        self.loginUI.retryBtn.clicked.connect(self.retryButton)
        self.loginUI.exitBtn.clicked.connect(self.close)

        self.loginUI.IllegalEntrymovie = QtGui.QMovie("D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/loginFailed.gif")
        self.loginUI.label.setScaledContents(True)
        self.loginUI.label.setMovie(self.loginUI.IllegalEntrymovie)

    def retryButton(self):
        self.loginUI.userNameEntry.clear()
        self.loginUI.passwordEntry.clear()
        self.stopMovie()

    def validateLogin(self):
        username = self.loginUI.userNameEntry.text()
        password = self.loginUI.passwordEntry.text()
        if self.checkCredentials(username, password):
            print("Login Success")
            self.goToMainPage()
            # Add code to go to main page
        else:
            self.playMovie()

    def checkCredentials(self, username, password):
        try:
            with open(USER_DATA_FILE, 'r', newline='') as file:
                rows = list(csv.reader(file))
        except FileNotFoundError:
            return False

        for row in rows:
            if len(row) < 2 or row[0] != username:
                continue

            if verify_password(password, row[1]):
                if not is_password_hash(row[1]):
                    row[1] = hash_password(password)
                    self.writeCredentials(rows)
                return True

        return False

    def writeCredentials(self, rows):
        with open(USER_DATA_FILE, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerows(rows)

    def playMovie(self):
        self.loginUI.label.show()
        self.loginUI.IllegalEntrymovie.start()

    def stopMovie(self):
        self.loginUI.label.hide()
        self.loginUI.IllegalEntrymovie.stop()

    def goToMainPage(self):
        from jarvisMAIN import LoginWindow
        self.showMain = LoginWindow()
        self.close()  # Close the current dialog
        self.showMain.show()

    def goToInitialPage(self):
        from main import mainFileNew
        self.showMainWindow = mainFileNew()
        self.close()  # Close the current dialog
        self.showMainWindow.show()

    def goToSignUpPage(self):
        from signUpImpl import mainFileNew  # Import the sign-up window class
        self.showSignUp = mainFileNew()
        self.close()  # Close the current dialog
        self.showSignUp.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = loginWindow()
    ui.show()
    sys.exit(app.exec_())
