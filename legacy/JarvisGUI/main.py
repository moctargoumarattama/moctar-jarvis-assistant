import sys
from PyQt5.QtWidgets import QDialog, QApplication
from PyQt5 import QtGui
from mainFileNew import Ui_Dialog

class mainFileNew(QDialog):
    def __init__(self):
        super(mainFileNew, self).__init__()
        print("Setting up GUI")
        self.firstUI = Ui_Dialog()
        self.firstUI.setupUi(self)

        self.firstUI.movie = QtGui.QMovie("D:\BSc CSD Sem 6\Project\JarvisGUI\JarvisImages\samplegui3.gif")
        self.firstUI.gif1.setMovie(self.firstUI.movie)
        self.firstUI.movie.start()

        self.firstUI.exitBtn.clicked.connect(self.close)
        self.firstUI.startBtn.clicked.connect(self.connectToFaceRecognition)
        self.firstUI.loginBtn.clicked.connect(self.connectToLoginPage)


    def connectToFaceRecognition(self):
        from faceRecog import faceRecog
        self.showFaceRecogWindow = faceRecog()
        self.close()  # Close the current dialog
        self.showFaceRecogWindow.show()

    def connectToLoginPage(self):
        from loginWindowMain import loginWindow
        self.showLoginWindow = loginWindow()
        self.close()  # Close the current dialog
        self.showLoginWindow.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = mainFileNew()
    ui.show()
    sys.exit(app.exec_())
