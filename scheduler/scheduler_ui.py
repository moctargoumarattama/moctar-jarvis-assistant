"""
PyQt5 scheduler window for social and messaging tasks.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from PyQt5.QtCore import QObject, QThread, Qt, QTime, QTimer, pyqtSignal
    from PyQt5.QtWidgets import (
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QTextEdit,
        QTimeEdit,
        QVBoxLayout,
        QWidget,
    )

    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False

from scheduler import scheduler_core, scheduler_store as store

logger = logging.getLogger("jarvis.scheduler.ui")

BG = "#02070d"
FG = "#00eaff"
FG2 = "#c8f8ff"
ACCENT = "#00c8ff"
BTN_CONFIRM = "#00c853"
BTN_DELETE = "#c62828"
BTN_AI = "#7b1fa2"
BTN_MANUAL = "#1565c0"
PANEL_BG = "rgba(0,200,255,0.06)"
BORDER = "#00eaff"

BASE_STYLE = f"""
QWidget {{ background-color: {BG}; color: {FG}; font-family: Arial; }}
QLabel  {{ color: {FG2}; }}
QLineEdit, QTextEdit, QComboBox, QTimeEdit {{
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
PLATFORMS = ["whatsapp", "facebook"]
PLATFORM_HINTS = {
    "whatsapp": "Ex: +2236XXXXXXXX (numero avec indicatif international)",
    "facebook": "Ex: mon.groupe ou groups/123456789",
}

STATUS_LABELS = {
    "active": ("Actif", "#00c853"),
    "pending_confirmation": ("En attente", "#ffb300"),
    "awaiting_manual_action": ("Action manuelle", "#42a5f5"),
    "error": ("Erreur", "#c62828"),
    "paused": ("Pause", "#546e7a"),
}


if PYQT_AVAILABLE:

    class AIEnhanceWorker(QObject):
        finished = pyqtSignal(str)
        failed = pyqtSignal(str)

        def __init__(self, message: str, platform: str, title: str, media_paths):
            super().__init__()
            self.message = message
            self.platform = platform
            self.title = title
            self.media_paths = list(media_paths or [])

        def run(self) -> None:
            try:
                result = scheduler_core.enhance_message_with_ai(
                    self.message,
                    self.platform,
                    self.title,
                    self.media_paths,
                )
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.finished.emit(result)


    class TaskCard(QWidget):
        confirm_requested = pyqtSignal(int)
        mark_sent_requested = pyqtSignal(int)
        delete_requested = pyqtSignal(int)

        def __init__(self, task: dict, parent=None):
            super().__init__(parent)
            self.task_id = task["id"]
            self._build(task)

        def _build(self, task: dict) -> None:
            self.setObjectName("TaskCard")
            self.setStyleSheet(
                f"""
                QWidget#TaskCard {{
                    background-color: {PANEL_BG};
                    border: 1px solid #003a4a;
                    border-radius: 10px;
                }}
                """
            )

            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)

            info = QVBoxLayout()
            title_row = QHBoxLayout()

            title_lbl = QLabel(f"<b>{task['title']}</b>")
            title_lbl.setStyleSheet("font-size:15px; color:#00eaff;")
            title_row.addWidget(title_lbl)

            platform_lbl = QLabel(task["platform"].capitalize())
            platform_lbl.setStyleSheet("font-size:13px; color:#80deea; margin-left:10px;")
            title_row.addWidget(platform_lbl)

            status_text, status_color = STATUS_LABELS.get(task["status"], (task["status"], FG))
            status_lbl = QLabel(status_text)
            status_lbl.setStyleSheet(
                f"font-size:12px; color:{status_color}; margin-left:10px; font-weight:bold;"
            )
            title_row.addWidget(status_lbl)
            title_row.addStretch()
            info.addLayout(title_row)

            if task["frequency"] == "daily":
                freq_str = f"Quotidien a {task['send_time']}"
            elif task.get("weekday") is not None:
                freq_str = f"{WEEKDAYS_FR[int(task['weekday'])]} a {task['send_time']}"
            else:
                freq_str = f"Hebdomadaire a {task['send_time']}"
            if task.get("target"):
                freq_str += f" | Destinataire: {task['target']}"

            meta_lbl = QLabel(freq_str)
            meta_lbl.setStyleSheet("font-size:12px; color:#607d8b;")
            info.addWidget(meta_lbl)

            msg_preview = task["message"] or "(aucun texte)"
            if len(msg_preview) > 100:
                msg_preview = f"{msg_preview[:100]}..."
            msg_lbl = QLabel(msg_preview)
            msg_lbl.setStyleSheet("font-size:12px; color:#b0bec5; font-style:italic;")
            msg_lbl.setWordWrap(True)
            info.addWidget(msg_lbl)

            media_paths = task.get("media_paths", [])
            if media_paths:
                media_names = [Path(path).name for path in media_paths[:3]]
                extra_count = max(len(media_paths) - len(media_names), 0)
                media_text = ", ".join(media_names)
                if extra_count:
                    media_text += f" +{extra_count} autre(s)"
                media_lbl = QLabel(f"Medias ({len(media_paths)}): {media_text}")
                media_lbl.setStyleSheet("font-size:11px; color:#90caf9;")
                media_lbl.setWordWrap(True)
                info.addWidget(media_lbl)

            if task["status"] == "awaiting_manual_action":
                manual_lbl = QLabel(
                    "Finalisez l'envoi dans le navigateur puis marquez la tache comme envoyee."
                )
                manual_lbl.setStyleSheet("font-size:11px; color:#90caf9;")
                manual_lbl.setWordWrap(True)
                info.addWidget(manual_lbl)

            if task.get("last_sent"):
                sent_lbl = QLabel(f"Dernier envoi: {task['last_sent']}")
                sent_lbl.setStyleSheet("font-size:11px; color:#37474f;")
                info.addWidget(sent_lbl)

            layout.addLayout(info, stretch=1)

            btn_layout = QVBoxLayout()
            btn_layout.setSpacing(6)

            if task["status"] == "pending_confirmation":
                btn_confirm = QPushButton("Confirmer et envoyer")
                btn_confirm.setStyleSheet(
                    f"background-color:{BTN_CONFIRM}; color:#fff; border-radius:6px;"
                    "padding:6px 14px; font-weight:bold;"
                )
                btn_confirm.clicked.connect(lambda: self.confirm_requested.emit(self.task_id))
                btn_layout.addWidget(btn_confirm)
            elif task["status"] == "awaiting_manual_action":
                btn_mark_sent = QPushButton("Marquer envoye")
                btn_mark_sent.setStyleSheet(
                    f"background-color:{BTN_MANUAL}; color:#fff; border-radius:6px;"
                    "padding:6px 14px; font-weight:bold;"
                )
                btn_mark_sent.clicked.connect(lambda: self.mark_sent_requested.emit(self.task_id))
                btn_layout.addWidget(btn_mark_sent)

            btn_del = QPushButton("Supprimer")
            btn_del.setStyleSheet(
                f"background-color:{BTN_DELETE}; color:#fff; border-radius:6px;"
                "padding:6px 14px; font-weight:bold;"
            )
            btn_del.clicked.connect(lambda: self.delete_requested.emit(self.task_id))
            btn_layout.addWidget(btn_del)
            btn_layout.addStretch()

            layout.addLayout(btn_layout)


    class SchedulerWindow(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._ai_thread = None
            self._ai_worker = None
            self._selected_media_paths = []
            self.setWindowTitle("Planificateur de taches - Moctar Jarvis")
            self.resize(840, 760)
            self.setStyleSheet(BASE_STYLE)
            self._build_ui()
            self._refresh_task_list()

            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh_task_list)
            self._timer.start(30_000)

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setSpacing(10)
            root.setContentsMargins(16, 16, 16, 16)

            header = QLabel("Planificateur de Taches Automatisees")
            header.setStyleSheet(
                "font-size:22px; font-weight:bold; color:#00eaff; margin-bottom:4px;"
            )
            header.setAlignment(Qt.AlignCenter)
            root.addWidget(header)

            sub = QLabel(
                "Planifiez des publications WhatsApp et Facebook avec messages ameliores par l'IA"
            )
            sub.setStyleSheet("font-size:12px; color:#607d8b;")
            sub.setAlignment(Qt.AlignCenter)
            root.addWidget(sub)

            form_group = QGroupBox("Nouvelle tache")
            form_layout = QFormLayout(form_group)
            form_layout.setSpacing(8)

            self._f_title = QLineEdit()
            self._f_title.setPlaceholderText("Ex: Post quotidien motivation")
            form_layout.addRow("Titre :", self._f_title)

            self._f_platform = QComboBox()
            for platform in PLATFORMS:
                self._f_platform.addItem(platform.capitalize(), platform)
            self._f_platform.currentIndexChanged.connect(self._on_platform_change)
            form_layout.addRow("Plateforme :", self._f_platform)

            self._f_target = QLineEdit()
            self._f_target.setPlaceholderText(PLATFORM_HINTS["whatsapp"])
            form_layout.addRow("Destinataire :", self._f_target)

            self._f_message = QTextEdit()
            self._f_message.setPlaceholderText(
                "Ecrivez votre message ici.\nCliquez sur 'Ameliorer avec l'IA' pour le retravailler."
            )
            self._f_message.setFixedHeight(90)
            form_layout.addRow("Message :", self._f_message)

            media_widget = QWidget()
            media_layout = QHBoxLayout(media_widget)
            media_layout.setContentsMargins(0, 0, 0, 0)
            media_layout.setSpacing(6)

            self._f_media_path = QLineEdit()
            self._f_media_path.setReadOnly(True)
            self._f_media_path.setPlaceholderText(
                "Optionnel: choisir une ou plusieurs photos/videos"
            )
            media_layout.addWidget(self._f_media_path, stretch=1)

            btn_media = QPushButton("Choisir")
            btn_media.clicked.connect(self._on_pick_media)
            media_layout.addWidget(btn_media)

            btn_clear_media = QPushButton("Retirer")
            btn_clear_media.clicked.connect(self._on_clear_media)
            media_layout.addWidget(btn_clear_media)

            form_layout.addRow("Media :", media_widget)

            self._btn_ai = QPushButton("Ameliorer avec l'IA")
            self._btn_ai.setStyleSheet(
                f"background-color:{BTN_AI}; color:#fff; border-radius:6px; padding:6px 14px;"
            )
            self._btn_ai.clicked.connect(self._on_ai_enhance)
            form_layout.addRow("", self._btn_ai)

            self._f_frequency = QComboBox()
            self._f_frequency.addItem("Quotidien", "daily")
            self._f_frequency.addItem("Hebdomadaire", "weekly")
            self._f_frequency.currentIndexChanged.connect(self._on_frequency_change)
            form_layout.addRow("Frequence :", self._f_frequency)

            self._f_weekday_label = QLabel("Jour de la semaine :")
            self._f_weekday = QComboBox()
            for index, day in enumerate(WEEKDAYS_FR):
                self._f_weekday.addItem(day, index)
            self._f_weekday.hide()
            self._f_weekday_label.hide()
            form_layout.addRow(self._f_weekday_label, self._f_weekday)

            self._f_time = QTimeEdit()
            self._f_time.setTime(QTime(9, 0))
            self._f_time.setDisplayFormat("HH:mm")
            form_layout.addRow("Heure d'envoi :", self._f_time)

            self._f_ai_flag = QComboBox()
            self._f_ai_flag.addItem("Non - garder le message tel quel", False)
            self._f_ai_flag.addItem("Oui - reameliorer a chaque envoi", True)
            form_layout.addRow("Reameliorer a chaque envoi :", self._f_ai_flag)

            btn_add = QPushButton("Ajouter la tache")
            btn_add.clicked.connect(self._on_add_task)
            form_layout.addRow("", btn_add)

            root.addWidget(form_group)

            list_group = QGroupBox("Taches planifiees")
            list_layout = QVBoxLayout(list_group)

            refresh_btn = QPushButton("Actualiser")
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

        def _on_platform_change(self, _idx) -> None:
            platform = self._f_platform.currentData()
            self._f_target.setPlaceholderText(PLATFORM_HINTS.get(platform, ""))

        def _on_frequency_change(self, _idx) -> None:
            is_weekly = self._f_frequency.currentData() == "weekly"
            self._f_weekday.setVisible(is_weekly)
            self._f_weekday_label.setVisible(is_weekly)

        def _on_pick_media(self) -> None:
            file_paths, _selected_filter = QFileDialog.getOpenFileNames(
                self,
                "Choisir des medias",
                "",
                "Images et videos (*.jpg *.jpeg *.png *.webp *.gif *.bmp *.mp4 *.mov *.avi *.mkv *.webm);;Tous les fichiers (*.*)",
            )
            if file_paths:
                self._selected_media_paths = list(file_paths)
                self._refresh_media_preview()

        def _on_clear_media(self) -> None:
            self._selected_media_paths = []
            self._refresh_media_preview()

        def _refresh_media_preview(self) -> None:
            if not self._selected_media_paths:
                self._f_media_path.clear()
                return
            names = [Path(path).name for path in self._selected_media_paths[:3]]
            summary = ", ".join(names)
            if len(self._selected_media_paths) > 3:
                summary += f" +{len(self._selected_media_paths) - 3} autre(s)"
            self._f_media_path.setText(summary)

        def _set_ai_busy(self, busy: bool) -> None:
            self._btn_ai.setDisabled(busy)
            self._btn_ai.setText("Amelioration..." if busy else "Ameliorer avec l'IA")

        def _cleanup_ai_thread(self) -> None:
            if self._ai_worker is not None:
                self._ai_worker.deleteLater()
                self._ai_worker = None
            if self._ai_thread is not None:
                self._ai_thread.deleteLater()
                self._ai_thread = None

        def _on_ai_enhance_finished(self, enhanced: str) -> None:
            self._set_ai_busy(False)
            self._f_message.setPlainText(enhanced)

        def _on_ai_enhance_failed(self, detail: str) -> None:
            self._set_ai_busy(False)
            QMessageBox.warning(self, "Erreur IA", f"Impossible d'utiliser l'IA : {detail}")

        def _on_ai_enhance(self) -> None:
            message = self._f_message.toPlainText().strip()
            title = self._f_title.text().strip()
            if not (message or title or self._selected_media_paths):
                QMessageBox.warning(
                    self,
                    "Contenu vide",
                    "Ajoutez un texte, un titre ou un media avant d'utiliser l'IA.",
                )
                return
            if self._ai_thread is not None:
                return

            platform = self._f_platform.currentData()
            self._set_ai_busy(True)

            self._ai_thread = QThread(self)
            self._ai_worker = AIEnhanceWorker(
                message,
                platform,
                title,
                list(self._selected_media_paths),
            )
            self._ai_worker.moveToThread(self._ai_thread)

            self._ai_thread.started.connect(self._ai_worker.run)
            self._ai_worker.finished.connect(self._on_ai_enhance_finished)
            self._ai_worker.failed.connect(self._on_ai_enhance_failed)
            self._ai_worker.finished.connect(self._ai_thread.quit)
            self._ai_worker.failed.connect(self._ai_thread.quit)
            self._ai_thread.finished.connect(self._cleanup_ai_thread)
            self._ai_thread.start()

        def _on_add_task(self) -> None:
            title = self._f_title.text().strip()
            platform = self._f_platform.currentData()
            target = self._f_target.text().strip()
            message = self._f_message.toPlainText().strip()
            media_paths = list(self._selected_media_paths)
            frequency = self._f_frequency.currentData()
            weekday = self._f_weekday.currentData() if frequency == "weekly" else None
            send_time = self._f_time.time().toString("HH:mm")
            ai_enhanced = bool(self._f_ai_flag.currentData())

            if not title:
                QMessageBox.warning(self, "Champ manquant", "Veuillez entrer un titre.")
                return
            if not message and not media_paths:
                QMessageBox.warning(
                    self,
                    "Champ manquant",
                    "Veuillez entrer un message ou choisir un media.",
                )
                return
            if not target:
                QMessageBox.warning(
                    self,
                    "Champ manquant",
                    f"Veuillez entrer le destinataire {platform}.",
                )
                return

            store.create_task(
                title=title,
                platform=platform,
                target=target,
                message=message,
                media_paths=media_paths,
                frequency=frequency,
                weekday=weekday,
                send_time=send_time,
                ai_enhanced=ai_enhanced,
            )

            self._f_title.clear()
            self._f_message.clear()
            self._f_target.clear()
            self._selected_media_paths = []
            self._refresh_media_preview()
            self._refresh_task_list()
            QMessageBox.information(self, "Tache creee", f"Tache '{title}' ajoutee avec succes.")

        def _on_confirm_send(self, task_id: int) -> None:
            ok, detail = scheduler_core.confirm_and_send(task_id)
            updated_task = store.get_task(task_id)
            if ok:
                if updated_task and updated_task["status"] == "awaiting_manual_action":
                    QMessageBox.information(self, "Action manuelle requise", detail)
                else:
                    QMessageBox.information(self, "Envoi reussi", detail)
            else:
                QMessageBox.warning(self, "Erreur d'envoi", detail)
            self._refresh_task_list()

        def _on_mark_sent(self, task_id: int) -> None:
            ok, detail = scheduler_core.mark_task_sent(task_id)
            if ok:
                QMessageBox.information(self, "Tache finalisee", detail)
            else:
                QMessageBox.warning(self, "Erreur", detail)
            self._refresh_task_list()

        def _on_delete_task(self, task_id: int) -> None:
            reply = QMessageBox.question(
                self,
                "Confirmation",
                "Supprimer cette tache ?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                store.delete_task(task_id)
                self._refresh_task_list()

        def _refresh_task_list(self) -> None:
            while self._task_vbox.count():
                child = self._task_vbox.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            tasks = store.list_tasks()
            if not tasks:
                empty_lbl = QLabel("Aucune tache planifiee. Ajoutez-en une ci-dessus.")
                empty_lbl.setStyleSheet("color:#546e7a; font-size:14px; padding:20px;")
                empty_lbl.setAlignment(Qt.AlignCenter)
                self._task_vbox.addWidget(empty_lbl)
                return

            for task in tasks:
                card = TaskCard(task)
                card.confirm_requested.connect(self._on_confirm_send)
                card.mark_sent_requested.connect(self._on_mark_sent)
                card.delete_requested.connect(self._on_delete_task)
                self._task_vbox.addWidget(card)


    def open_scheduler(parent=None):
        """Open the scheduler window."""
        window = SchedulerWindow(parent)
        window.exec_()


else:

    def open_scheduler(parent=None):
        logger.warning("PyQt5 not available - scheduler UI cannot be shown.")
