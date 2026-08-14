"""
PyQt5 Scheduler Window — Planificateur de tâches automatisées.

Features:
- Form to create tasks (title, platform, target, message, frequency, send_time)
- AI-enhance button to polish the message via LLM
- Task list with status badges
- Confirm & Send button for pending tasks
- Delete button
"""

import logging

try:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal
    from PyQt5.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QTextEdit,
        QTimeEdit,
        QVBoxLayout,
        QWidget,
    )
    from PyQt5.QtCore import QTime
    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False

from scheduler import scheduler_store as store
from scheduler import scheduler_core

logger = logging.getLogger("jarvis.scheduler.ui")

# --------------------------------------------------------------------------- #
#  Style constants                                                             #
# --------------------------------------------------------------------------- #

BG = "#02070d"
FG = "#00eaff"
FG2 = "#c8f8ff"
ACCENT = "#00c8ff"
BTN_CONFIRM = "#00c853"
BTN_DELETE = "#c62828"
BTN_AI = "#7b1fa2"
PANEL_BG = "rgba(0,200,255,0.06)"
BORDER = "#00eaff"

BASE_STYLE = f"""
QWidget {{ background-color: {BG}; color: {FG}; font-family: Arial; }}
QLabel  {{ color: {FG2}; }}
QLineEdit, QTextEdit, QComboBox, QTimeEdit, QSpinBox {{
    background-color: #060e18;
    color: {FG2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
}}
QComboBox QAbstractItemView {{
    background-color: #060e18;
    color: {FG2};
    selection-background-color: {ACCENT};
}}
QPushButton {{
    background-color: {ACCENT};
    color: #02070d;
    border: none;
    border-radius: 8px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton:hover {{ background-color: #33d4ff; }}
QPushButton:disabled {{ background-color: #1a3a4a; color: #446670; }}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 10px;
    font-size: 14px;
    font-weight: bold;
    color: {FG};
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: -7px;
}}
QScrollArea {{ border: none; }}
"""

WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
PLATFORMS = ["whatsapp", "facebook", "tiktok"]
PLATFORM_HINTS = {
    "whatsapp": "Ex: +2236XXXXXXXX  (numéro avec indicatif)",
    "facebook": "Ex: page_id ou group_id (trouvé dans l'URL Facebook)",
    "tiktok": "Laissez vide — TikTok s'ouvre dans le navigateur",
}

STATUS_LABELS = {
    "active": ("Actif", "#00c853"),
    "pending_confirmation": ("⏳ En attente", "#ffb300"),
    "error": ("❌ Erreur", "#c62828"),
    "paused": ("⏸ Pause", "#546e7a"),
}


if PYQT_AVAILABLE:

    # ----------------------------------------------------------------------- #
    #  Task card widget                                                        #
    # ----------------------------------------------------------------------- #

    class TaskCard(QWidget):
        confirm_requested = pyqtSignal(int)
        delete_requested = pyqtSignal(int)

        def __init__(self, task: dict, parent=None):
            super().__init__(parent)
            self.task_id = task["id"]
            self._build(task)

        def _build(self, task: dict):
            self.setStyleSheet(f"""
                QWidget#TaskCard {{
                    background-color: {PANEL_BG};
                    border: 1px solid #003a4a;
                    border-radius: 10px;
                }}
            """)
            self.setObjectName("TaskCard")

            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)

            # Info section
            info = QVBoxLayout()
            title_row = QHBoxLayout()

            title_lbl = QLabel(f"<b>{task['title']}</b>")
            title_lbl.setStyleSheet("font-size:15px; color:#00eaff;")
            title_row.addWidget(title_lbl)

            platform_icon = {"whatsapp": "📱", "facebook": "📘", "tiktok": "🎵"}.get(
                task["platform"], "📡"
            )
            plat_lbl = QLabel(f"{platform_icon} {task['platform'].capitalize()}")
            plat_lbl.setStyleSheet("font-size:13px; color:#80deea; margin-left:10px;")
            title_row.addWidget(plat_lbl)

            status_text, status_color = STATUS_LABELS.get(
                task["status"], (task["status"], FG)
            )
            status_lbl = QLabel(status_text)
            status_lbl.setStyleSheet(
                f"font-size:12px; color:{status_color}; margin-left:10px; font-weight:bold;"
            )
            title_row.addWidget(status_lbl)
            title_row.addStretch()
            info.addLayout(title_row)

            freq_str = (
                f"Quotidien à {task['send_time']}"
                if task["frequency"] == "daily"
                else (
                    f"{WEEKDAYS_FR[int(task['weekday'])]} à {task['send_time']}"
                    if task.get("weekday") is not None
                    else f"Hebdomadaire à {task['send_time']} (jour non défini)"
                )
            )
            if task.get("target"):
                freq_str += f"  |  Destinataire: {task['target']}"
            meta_lbl = QLabel(freq_str)
            meta_lbl.setStyleSheet("font-size:12px; color:#607d8b;")
            info.addWidget(meta_lbl)

            msg_preview = (task["message"][:80] + "…") if len(task["message"]) > 80 else task["message"]
            msg_lbl = QLabel(msg_preview)
            msg_lbl.setStyleSheet("font-size:12px; color:#b0bec5; font-style:italic;")
            msg_lbl.setWordWrap(True)
            info.addWidget(msg_lbl)

            if task.get("last_sent"):
                sent_lbl = QLabel(f"Dernier envoi: {task['last_sent']}")
                sent_lbl.setStyleSheet("font-size:11px; color:#37474f;")
                info.addWidget(sent_lbl)

            layout.addLayout(info, stretch=1)

            # Buttons
            btn_layout = QVBoxLayout()
            btn_layout.setSpacing(6)

            if task["status"] == "pending_confirmation":
                btn_confirm = QPushButton("✅ Confirmer & Envoyer")
                btn_confirm.setStyleSheet(
                    f"background-color:{BTN_CONFIRM}; color:#fff; border-radius:6px;"
                    "padding:6px 14px; font-weight:bold;"
                )
                btn_confirm.clicked.connect(lambda: self.confirm_requested.emit(self.task_id))
                btn_layout.addWidget(btn_confirm)

            btn_del = QPushButton("🗑 Supprimer")
            btn_del.setStyleSheet(
                f"background-color:{BTN_DELETE}; color:#fff; border-radius:6px;"
                "padding:6px 14px; font-weight:bold;"
            )
            btn_del.clicked.connect(lambda: self.delete_requested.emit(self.task_id))
            btn_layout.addWidget(btn_del)
            btn_layout.addStretch()

            layout.addLayout(btn_layout)

    # ----------------------------------------------------------------------- #
    #  Main Scheduler Window                                                   #
    # ----------------------------------------------------------------------- #

    class SchedulerWindow(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("📅 Planificateur de tâches — Moctar Jarvis")
            self.resize(820, 700)
            self.setStyleSheet(BASE_STYLE)
            self._build_ui()
            self._refresh_task_list()

            # Auto-refresh every 30 s to pick up pending confirmations
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh_task_list)
            self._timer.start(30_000)

        # ------------------------------------------------------------------- #
        #  UI construction                                                     #
        # ------------------------------------------------------------------- #

        def _build_ui(self):
            root = QVBoxLayout(self)
            root.setSpacing(10)
            root.setContentsMargins(16, 16, 16, 16)

            header = QLabel("📅 Planificateur de Tâches Automatisées")
            header.setStyleSheet("font-size:22px; font-weight:bold; color:#00eaff; margin-bottom:4px;")
            header.setAlignment(Qt.AlignCenter)
            root.addWidget(header)

            sub = QLabel(
                "Planifiez des publications WhatsApp • Facebook • TikTok — "
                "avec messages améliorés par l'IA"
            )
            sub.setStyleSheet("font-size:12px; color:#607d8b;")
            sub.setAlignment(Qt.AlignCenter)
            root.addWidget(sub)

            # Form
            form_group = QGroupBox("➕ Nouvelle tâche")
            form_layout = QFormLayout(form_group)
            form_layout.setSpacing(8)

            self._f_title = QLineEdit()
            self._f_title.setPlaceholderText("Ex: Post quotidien motivation")
            form_layout.addRow("Titre :", self._f_title)

            self._f_platform = QComboBox()
            for p in PLATFORMS:
                icon = {"whatsapp": "📱", "facebook": "📘", "tiktok": "🎵"}[p]
                self._f_platform.addItem(f"{icon} {p.capitalize()}", p)
            self._f_platform.currentIndexChanged.connect(self._on_platform_change)
            form_layout.addRow("Plateforme :", self._f_platform)

            self._f_target = QLineEdit()
            self._f_target.setPlaceholderText(PLATFORM_HINTS["whatsapp"])
            form_layout.addRow("Destinataire :", self._f_target)

            self._f_message = QTextEdit()
            self._f_message.setPlaceholderText(
                "Écrivez votre message ici...\n"
                "Cliquez sur 'Améliorer avec l'IA' pour le rendre plus engageant."
            )
            self._f_message.setFixedHeight(90)
            form_layout.addRow("Message :", self._f_message)

            # AI enhance button
            btn_ai = QPushButton("✨ Améliorer avec l'IA")
            btn_ai.setStyleSheet(
                f"background-color:{BTN_AI}; color:#fff; border-radius:6px; padding:6px 14px;"
            )
            btn_ai.clicked.connect(self._on_ai_enhance)
            form_layout.addRow("", btn_ai)

            self._f_frequency = QComboBox()
            self._f_frequency.addItem("📅 Quotidien", "daily")
            self._f_frequency.addItem("📆 Hebdomadaire", "weekly")
            self._f_frequency.currentIndexChanged.connect(self._on_frequency_change)
            form_layout.addRow("Fréquence :", self._f_frequency)

            self._f_weekday_label = QLabel("Jour de la semaine :")
            self._f_weekday = QComboBox()
            for i, day in enumerate(WEEKDAYS_FR):
                self._f_weekday.addItem(day, i)
            self._f_weekday.hide()
            self._f_weekday_label.hide()
            form_layout.addRow(self._f_weekday_label, self._f_weekday)

            self._f_time = QTimeEdit()
            self._f_time.setTime(QTime(9, 0))
            self._f_time.setDisplayFormat("HH:mm")
            form_layout.addRow("Heure d'envoi :", self._f_time)

            self._f_ai_flag = QComboBox()
            self._f_ai_flag.addItem("Non — garder le message tel quel", False)
            self._f_ai_flag.addItem("Oui — améliorer avec l'IA à chaque envoi", True)
            form_layout.addRow("Re-améliorer à chaque envoi :", self._f_ai_flag)

            btn_add = QPushButton("➕ Ajouter la tâche")
            btn_add.clicked.connect(self._on_add_task)
            form_layout.addRow("", btn_add)

            root.addWidget(form_group)

            # Task list
            list_group = QGroupBox("📋 Tâches planifiées")
            list_layout = QVBoxLayout(list_group)

            refresh_btn = QPushButton("🔄 Actualiser")
            refresh_btn.setFixedWidth(120)
            refresh_btn.clicked.connect(self._refresh_task_list)
            list_layout.addWidget(refresh_btn, alignment=Qt.AlignRight)

            self._task_scroll_area = QScrollArea()
            self._task_scroll_area.setWidgetResizable(True)
            self._task_container = QWidget()
            self._task_vbox = QVBoxLayout(self._task_container)
            self._task_vbox.setAlignment(Qt.AlignTop)
            self._task_scroll_area.setWidget(self._task_container)
            list_layout.addWidget(self._task_scroll_area)

            root.addWidget(list_group, stretch=1)

        # ------------------------------------------------------------------- #
        #  Slots                                                               #
        # ------------------------------------------------------------------- #

        def _on_platform_change(self, _idx):
            platform = self._f_platform.currentData()
            self._f_target.setPlaceholderText(PLATFORM_HINTS.get(platform, ""))

        def _on_frequency_change(self, _idx):
            is_weekly = self._f_frequency.currentData() == "weekly"
            self._f_weekday.setVisible(is_weekly)
            self._f_weekday_label.setVisible(is_weekly)

        def _on_ai_enhance(self):
            message = self._f_message.toPlainText().strip()
            if not message:
                QMessageBox.warning(self, "Message vide", "Écrivez d'abord un message.")
                return
            platform = self._f_platform.currentData()
            title = self._f_title.text().strip()
            enhanced = scheduler_core.enhance_message_with_ai(message, platform, title)
            self._f_message.setPlainText(enhanced)

        def _on_add_task(self):
            title = self._f_title.text().strip()
            platform = self._f_platform.currentData()
            target = self._f_target.text().strip()
            message = self._f_message.toPlainText().strip()
            frequency = self._f_frequency.currentData()
            weekday = self._f_weekday.currentData() if frequency == "weekly" else None
            send_time = self._f_time.time().toString("HH:mm")
            ai_enhanced = bool(self._f_ai_flag.currentData())

            if not title:
                QMessageBox.warning(self, "Champ manquant", "Veuillez entrer un titre.")
                return
            if not message:
                QMessageBox.warning(self, "Champ manquant", "Veuillez entrer un message.")
                return
            if platform != "tiktok" and not target:
                QMessageBox.warning(
                    self, "Champ manquant",
                    f"Veuillez entrer le destinataire {platform}."
                )
                return

            store.create_task(
                title=title,
                platform=platform,
                target=target,
                message=message,
                frequency=frequency,
                weekday=weekday,
                send_time=send_time,
                ai_enhanced=ai_enhanced,
            )

            # Reset form
            self._f_title.clear()
            self._f_message.clear()
            self._f_target.clear()
            self._refresh_task_list()
            QMessageBox.information(self, "Tâche créée", f"✅ Tâche '{title}' ajoutée avec succès!")

        def _on_confirm_send(self, task_id: int):
            ok, detail = scheduler_core.confirm_and_send(task_id)
            if ok:
                QMessageBox.information(self, "Envoi réussi", f"✅ {detail}")
            else:
                QMessageBox.warning(self, "Erreur d'envoi", f"❌ {detail}")
            self._refresh_task_list()

        def _on_delete_task(self, task_id: int):
            reply = QMessageBox.question(
                self, "Confirmation",
                "Supprimer cette tâche ?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                store.delete_task(task_id)
                self._refresh_task_list()

        def _refresh_task_list(self):
            # Clear existing cards
            while self._task_vbox.count():
                child = self._task_vbox.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            tasks = store.list_tasks()
            if not tasks:
                empty_lbl = QLabel("Aucune tâche planifiée. Ajoutez-en une ci-dessus ↑")
                empty_lbl.setStyleSheet("color:#546e7a; font-size:14px; padding:20px;")
                empty_lbl.setAlignment(Qt.AlignCenter)
                self._task_vbox.addWidget(empty_lbl)
                return

            for task in tasks:
                card = TaskCard(task)
                card.confirm_requested.connect(self._on_confirm_send)
                card.delete_requested.connect(self._on_delete_task)
                self._task_vbox.addWidget(card)

    # ----------------------------------------------------------------------- #
    #  Launcher                                                                #
    # ----------------------------------------------------------------------- #

    def open_scheduler(parent=None):
        """Open the scheduler window. Safe to call from main thread."""
        win = SchedulerWindow(parent)
        win.exec_()

else:
    def open_scheduler(parent=None):
        logger.warning("PyQt5 not available — scheduler UI cannot be shown.")
