from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_SignUp(object):
    def setupUi(self, Widget):
        Widget.setObjectName("Widget")
        Widget.resize(1080, 720)
        Widget.setStyleSheet("background-color: rgb(0, 0, 0);")
        self.frame = QtWidgets.QFrame(Widget)
        self.frame.setGeometry(QtCore.QRect(210, 130, 681, 551))
        self.frame.setStyleSheet("color: rgb(255, 255, 255);\n"
                                 "border-color: rgb(255, 255, 255);\n"
                                 "border-radius: 30px;\n"
                                 "border-width: 5px 5px 5px 5px;\n"
                                 "border-style: solid;")
        self.frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.frame.setFrameShadow(QtWidgets.QFrame.Raised)
        self.frame.setObjectName("frame")
        self.exitBtn = QtWidgets.QPushButton(self.frame)
        self.exitBtn.setGeometry(QtCore.QRect(490, 420, 141, 61))
        self.exitBtn.setStyleSheet("border-image: url(D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/exitButton.png);")
        self.exitBtn.setText("")
        self.exitBtn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.exitBtn.setObjectName("exitBtn")
        self.backBtn = QtWidgets.QPushButton(self.frame)
        self.backBtn.setGeometry(QtCore.QRect(310, 420, 141, 61))
        self.backBtn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.backBtn.setStyleSheet("border-image: url(D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/backButton.png);")
        self.backBtn.setText("")
        self.backBtn.setObjectName("backBtn")
        self.userNameEntry = QtWidgets.QLineEdit(self.frame)
        self.userNameEntry.setGeometry(QtCore.QRect(110, 40, 481, 81))
        self.userNameEntry.setStyleSheet("font: 15pt \"Segoe UI\";\n"
                                         "padding-left: 10px;")
        self.userNameEntry.setObjectName("userNameEntry")
        self.passwordEntry = QtWidgets.QLineEdit(self.frame)
        self.passwordEntry.setGeometry(QtCore.QRect(110, 150, 481, 81))
        self.passwordEntry.setStyleSheet("font: 15pt \"Segoe UI\";\n"
                                          "padding-left: 10px;\n"
                                          "background-color: rgb(0, 0, 0);")
        self.passwordEntry.setObjectName("passwordEntry")
        self.passwordEntry.setEchoMode(QtWidgets.QLineEdit.Password)  # Set echo mode to Password
        self.SignupBtn = QtWidgets.QPushButton(self.frame)
        self.SignupBtn.setGeometry(QtCore.QRect(100, 420, 171, 61))
        self.SignupBtn.setStyleSheet("border-image: url(D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/signUp.png);")
        self.SignupBtn.setText("")
        self.SignupBtn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.SignupBtn.setObjectName("SignupBtn")
        self.ConfirmpasswordEntry = QtWidgets.QLineEdit(self.frame)
        self.ConfirmpasswordEntry.setGeometry(QtCore.QRect(110, 270, 481, 81))
        self.ConfirmpasswordEntry.setStyleSheet("font: 15pt \"Segoe UI\";\n"
                                                 "padding-left: 10px;\n"
                                                 "background-color: rgb(0, 0, 0);")
        self.ConfirmpasswordEntry.setObjectName("ConfirmpasswordEntry")
        self.ConfirmpasswordEntry.setEchoMode(QtWidgets.QLineEdit.Password)  # Set echo mode to Password
        self.logo = QtWidgets.QLabel(Widget)
        self.logo.setGeometry(QtCore.QRect(90, -90, 561, 81))
        self.logo.setText("")
        self.logo.setPixmap(QtGui.QPixmap("D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/logo.png"))
        self.logo.setScaledContents(True)
        self.logo.setObjectName("logo")
        self.logo_2 = QtWidgets.QLabel(Widget)
        self.logo_2.setGeometry(QtCore.QRect(280, 40, 561, 81))
        self.logo_2.setText("")
        self.logo_2.setPixmap(QtGui.QPixmap("D:/BSc CSD Sem 6/Project/JarvisGUI/JarvisImages/logo.png"))
        self.logo_2.setScaledContents(True)
        self.logo_2.setObjectName("logo_2")

        self.retranslateUi(Widget)
        QtCore.QMetaObject.connectSlotsByName(Widget)

    def retranslateUi(self, Widget):
        _translate = QtCore.QCoreApplication.translate
        Widget.setWindowTitle(_translate("Widget", "Widget"))
        self.userNameEntry.setPlaceholderText(_translate("Widget", "USERNAME"))
        self.passwordEntry.setPlaceholderText(_translate("Widget", "PASSWORD"))
        self.ConfirmpasswordEntry.setPlaceholderText(_translate("Widget", "CONFIRM PASSWORD"))


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)
    Widget = QtWidgets.QWidget()
    ui = Ui_SignUp()
    ui.setupUi(Widget)
    Widget.show()
    sys.exit(app.exec_())
