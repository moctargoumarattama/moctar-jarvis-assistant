from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont
import sys
import math


class JarvisCore(QWidget):
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.angle = 0
        self.pulse = 0
        self.mode = "idle"  # idle / listening / speaking
        self.setMinimumSize(420, 420)
        self.setCursor(Qt.PointingHandCursor)

        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(25)

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def animate(self):
        self.angle = (self.angle + 3) % 360
        self.pulse = (self.pulse + 0.12) % (math.pi * 2)
        self.update()

    def mousePressEvent(self, event):
        self.clicked.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        painter.fillRect(self.rect(), QColor(0, 0, 0))

        pulse_value = int((math.sin(self.pulse) + 1) * 25)

        if self.mode == "listening":
            main_color = QColor(0, 255, 180)
            glow_alpha = 170 + pulse_value
            center_text = "LISTENING"
        elif self.mode == "speaking":
            main_color = QColor(255, 170, 0)
            glow_alpha = 170 + pulse_value
            center_text = "SPEAKING"
        else:
            main_color = QColor(0, 230, 255)
            glow_alpha = 110
            center_text = "M.O.C.T.A.R\nCLICK"

        # Glow extérieur
        for r in range(210, 260, 12):
            painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), 20), 8))
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Cercles HUD
        for i, radius in enumerate([70, 110, 150, 190]):
            alpha = max(50, glow_alpha - i * 30)
            painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), alpha), 2))
            painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        # Arcs animés
        painter.setPen(QPen(main_color, 4))
        painter.drawArc(cx - 185, cy - 185, 370, 370, self.angle * 16, 115 * 16)

        painter.setPen(QPen(QColor(0, 120, 255), 3))
        painter.drawArc(cx - 125, cy - 125, 250, 250, -self.angle * 16, 170 * 16)

        # Rayons
        painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), 120), 1))
        for i in range(0, 360, 30):
            a = math.radians(i + self.angle)
            x1 = cx + math.cos(a) * 80
            y1 = cy + math.sin(a) * 80
            x2 = cx + math.cos(a) * 190
            y2 = cy + math.sin(a) * 190
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Battement centre
        beat = 55 + pulse_value if self.mode in ["listening", "speaking"] else 55

        painter.setBrush(QColor(main_color.red(), main_color.green(), main_color.blue(), 80))
        painter.setPen(QPen(main_color, 3))
        painter.drawEllipse(cx - beat, cy - beat, beat * 2, beat * 2)

        painter.setPen(QColor(220, 255, 255))
        painter.setFont(QFont("Arial", 16, QFont.Bold))
        painter.drawText(self.rect(), Qt.AlignCenter, center_text)


class JarvisUI(QWidget):
    listen_requested = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowTitle("M.O.C.T.A.R - Iron Mode")
        self.resize(620, 720)
        self.setStyleSheet("""
            QWidget {
                background-color: #02070d;
                color: #00eaff;
            }
            QLabel {
                color: #00eaff;
            }
        """)

        self.title = QLabel("M.O.C.T.A.R")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("""
            font-size: 34px;
            font-weight: bold;
            letter-spacing: 6px;
            color: #00f7ff;
        """)

        self.core = JarvisCore()
        self.core.clicked.connect(self.listen_requested.emit)

        self.label = QLabel("Clique sur le cœur pour parler")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("""
            font-size: 22px;
            padding: 18px;
            border: 1px solid #00eaff;
            border-radius: 14px;
            background-color: rgba(0, 200, 255, 0.08);
        """)

        layout = QVBoxLayout()
        layout.addWidget(self.title)
        layout.addWidget(self.core)
        layout.addWidget(self.label)
        self.setLayout(layout)

    def update_text(self, text):
        self.label.setText(text)

    def set_mode(self, mode):
        self.core.set_mode(mode)


def run_ui():
    app = QApplication(sys.argv)
    ui = JarvisUI()
    ui.show()
    return app, ui