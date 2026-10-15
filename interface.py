import math
import sys

import config

try:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal
    from PyQt5.QtGui import QColor, QFont, QPainter, QPen
    from PyQt5.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False


if PYQT_AVAILABLE:
    class JarvisCore(QWidget):
        clicked = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.angle = 0
            self.pulse = 0
            self.mode = "idle"
            self.setMinimumSize(420, 420)
            self.setCursor(Qt.PointingHandCursor)

            self.timer = QTimer()
            self.timer.timeout.connect(self.animate)
            self.timer.start(33)

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

            for r in range(210, 260, 12):
                painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), 20), 8))
                painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

            for i, radius in enumerate([70, 110, 150, 190]):
                alpha = max(50, glow_alpha - i * 30)
                painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), alpha), 2))
                painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

            painter.setPen(QPen(main_color, 4))
            painter.drawArc(cx - 185, cy - 185, 370, 370, self.angle * 16, 115 * 16)

            painter.setPen(QPen(QColor(0, 120, 255), 3))
            painter.drawArc(cx - 125, cy - 125, 250, 250, -self.angle * 16, 170 * 16)

            painter.setPen(QPen(QColor(main_color.red(), main_color.green(), main_color.blue(), 120), 1))
            for i in range(0, 360, 30):
                a = math.radians(i + self.angle)
                x1 = cx + math.cos(a) * 80
                y1 = cy + math.sin(a) * 80
                x2 = cx + math.cos(a) * 190
                y2 = cy + math.sin(a) * 190
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

            beat = 55 + pulse_value if self.mode in ["listening", "speaking"] else 55

            painter.setBrush(QColor(main_color.red(), main_color.green(), main_color.blue(), 80))
            painter.setPen(QPen(main_color, 3))
            painter.drawEllipse(cx - beat, cy - beat, beat * 2, beat * 2)

            painter.setPen(QColor(220, 255, 255))
            painter.setFont(QFont("Arial", 16, QFont.Bold))
            painter.drawText(self.rect(), Qt.AlignCenter, center_text)


    class ListeningPopup(QWidget):
        """Small always-on-top listening indicator with animated wave rings and one-click dismiss."""
        cancel_requested = pyqtSignal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setFixedSize(210, 210)
            self.setStyleSheet("background: #02070d; border: 2px solid #00ffb4; border-radius: 105px;")
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Écoute en cours - Cliquez pour annuler")

            self.core = JarvisCore()
            self.core.setMinimumSize(200, 200)
            self.core.set_mode("listening")
            # Stopper le timer par défaut tant que la popup est masquée
            if hasattr(self.core, "timer"):
                self.core.timer.stop()
            self.core.clicked.connect(self._on_dismiss)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(4, 4, 4, 4)
            layout.addWidget(self.core)

            # Petit bouton rond d'annulation en haut à droite
            self.close_btn = QPushButton("✕", self)
            self.close_btn.setGeometry(170, 14, 26, 26)
            self.close_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(220, 38, 38, 0.7);
                    color: #ffffff;
                    border: 1px solid #ef4444;
                    border-radius: 13px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(239, 68, 68, 0.95);
                }
            """)
            self.close_btn.clicked.connect(self._on_dismiss)

        def _on_dismiss(self):
            self.cancel_requested.emit()
            self.hide()

        def mousePressEvent(self, event):
            self._on_dismiss()
            super().mousePressEvent(event)

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Escape:
                self._on_dismiss()
            else:
                super().keyPressEvent(event)

        def show_for(self, parent):
            center = parent.frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
            if hasattr(self.core, "timer") and not self.core.timer.isActive():
                self.core.timer.start(33)
            self.show()
            self.raise_()

        def hide(self):
            if hasattr(self.core, "timer") and self.core.timer.isActive():
                self.core.timer.stop()
            super().hide()


    class JarvisUI(QWidget):
        listen_requested = pyqtSignal()
        listen_cancelled = pyqtSignal()
        scheduler_requested = pyqtSignal()
        history_requested = pyqtSignal()
        ai_status_requested = pyqtSignal()
        manual_command_requested = pyqtSignal(str)
        reset_requested = pyqtSignal()

        def __init__(self):
            super().__init__()

            self.setWindowTitle("M.O.C.T.A.R - Iron Mode")
            self.resize(660, 790)
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
            self.core.setMinimumSize(340, 340)
            self.core.clicked.connect(self.listen_requested.emit)
            self.listening_popup = ListeningPopup()
            self.listening_popup.cancel_requested.connect(self.listen_cancelled.emit)

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

            self.scheduler_btn = QPushButton("📅 Planificateur de tâches")
            self.scheduler_btn.setStyleSheet("""
                QPushButton {
                    background-color: #003a4a;
                    color: #00eaff;
                    border: 1px solid #00eaff;
                    border-radius: 10px;
                    padding: 10px 22px;
                    font-size: 15px;
                    font-weight: bold;
                    letter-spacing: 1px;
                }
                QPushButton:hover {
                    background-color: #005a6e;
                    color: #ffffff;
                }
            """)
            self.scheduler_btn.clicked.connect(self.scheduler_requested.emit)

            self.history_btn = QPushButton("💬 Historique des conversations")
            self.history_btn.setStyleSheet("""
                QPushButton {
                    background-color: #003a4a;
                    color: #00eaff;
                    border: 1px solid #00eaff;
                    border-radius: 10px;
                    padding: 10px 22px;
                    font-size: 15px;
                    font-weight: bold;
                    letter-spacing: 1px;
                }
                QPushButton:hover {
                    background-color: #005a6e;
                    color: #ffffff;
                }
            """)
            self.history_btn.clicked.connect(self.history_requested.emit)

            self.ai_status_btn = QPushButton()
            self.ai_status_btn.setStyleSheet("""
                QPushButton {
                    background-color: #06232b;
                    color: #7fffd4;
                    border: 1px solid #00eaff;
                    border-radius: 10px;
                    padding: 10px 22px;
                    font-size: 14px;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                }
                QPushButton:hover {
                    background-color: #0a3a45;
                    color: #ffffff;
                }
            """)
            self.ai_status_btn.clicked.connect(self.ai_status_requested.emit)
            self.ai_status_message = ""
            self.set_ai_status(config.gemini_model, config.AI_SETTINGS.get("base_url", "https://generativelanguage.googleapis.com"), online=bool(config.GEMINI_API_KEY))

            self.manual_label = QLabel("Commande manuelle")
            self.manual_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #7fffd4;")

            self.manual_input = QLineEdit()
            self.manual_input.setPlaceholderText("Ecris ta demande ici...")
            self.manual_input.setClearButtonEnabled(True)
            self.manual_input.setStyleSheet("""
                QLineEdit {
                    background-color: #04131b;
                    color: #ffffff;
                    border: 1px solid #00eaff;
                    border-radius: 10px;
                    padding: 11px 14px;
                    font-size: 15px;
                }
                QLineEdit:focus {
                    border: 2px solid #00ffb4;
                }
            """)

            self.send_btn = QPushButton("Envoyer")
            self.send_btn.setStyleSheet("""
                QPushButton {
                    background-color: #007f75;
                    color: #ffffff;
                    border: 1px solid #00ffb4;
                    border-radius: 10px;
                    padding: 11px 18px;
                    font-size: 15px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #00a894; }
            """)

            self.reset_btn = QPushButton("Reinitialiser")
            self.reset_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4a2415;
                    color: #ffd7b5;
                    border: 1px solid #ff9f43;
                    border-radius: 10px;
                    padding: 10px 18px;
                    font-size: 14px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #71351c;
                    color: #ffffff;
                }
            """)

            self.manual_input.returnPressed.connect(self._submit_manual_command)
            self.send_btn.clicked.connect(self._submit_manual_command)
            self.reset_btn.clicked.connect(self._reset_from_ui)

            manual_row = QHBoxLayout()
            manual_row.addWidget(self.manual_input, 1)
            manual_row.addWidget(self.send_btn)

            layout = QVBoxLayout()
            layout.addWidget(self.title)
            layout.addWidget(self.core, 1)
            layout.addWidget(self.label)
            layout.addWidget(self.manual_label)
            layout.addLayout(manual_row)
            layout.addWidget(self.reset_btn)
            layout.addWidget(self.ai_status_btn)
            layout.addWidget(self.scheduler_btn)
            layout.addWidget(self.history_btn)
            self.setLayout(layout)

        def _submit_manual_command(self):
            command = self.manual_input.text().strip()
            if not command:
                self.update_text("Ecris d'abord une commande.")
                self.manual_input.setFocus()
                return
            self.manual_command_requested.emit(command)
            self.manual_input.clear()

        def _reset_from_ui(self):
            self.manual_input.clear()
            self.reset_requested.emit()

        def update_text(self, text):
            self.label.setText(text)

        def set_ai_status(self, model, base_url, online=True):
            engine = "Gemini Cloud" if online else "Mode autonome"
            self.ai_status_message = f"Etat IA: {engine} | modele {model} | {base_url}"
            self.ai_status_btn.setText(f"IA: {engine} | {model}")

        def set_mode(self, mode):
            self.core.set_mode(mode)

        def show_listening_popup(self):
            self.listening_popup.show_for(self)

        def hide_listening_popup(self):
            self.listening_popup.hide()


    def run_ui():
        app = QApplication(sys.argv)
        ui = JarvisUI()
        ui.show()
        return app, ui
else:
    class _DummySignal:
        def connect(self, _callback):
            return None

    class _DummyApp:
        def processEvents(self):
            return None

    class _DummyUI:
        def __init__(self):
            self.listen_requested = _DummySignal()
            self.scheduler_requested = _DummySignal()
            self.ai_status_requested = _DummySignal()
            self.manual_command_requested = _DummySignal()
            self.reset_requested = _DummySignal()
            self.ai_status_message = ""

        def update_text(self, _text):
            return None

        def set_ai_status(self, *_args, **_kwargs):
            return None

        def set_mode(self, _mode):
            return None

        def show_listening_popup(self):
            return None

        def hide_listening_popup(self):
            return None

    def run_ui():
        return _DummyApp(), _DummyUI()
