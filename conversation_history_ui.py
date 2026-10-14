"""
Interface graphique PyQt5 pour l'historique des conversations de JARVIS.

Fournit :
1. La navigation dans les sessions et thèmes de discussion récents.
2. La relecture intégrale des échanges passés (Utilisateur, Assistant, Outils).
3. La recherche par mots-clés ou filtrage en direct.
4. La reprise active d'un fil de conversation pour continuer le dialogue.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional

try:
    from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt5.QtGui import QColor, QFont
    from PyQt5.QtWidgets import (
        QDialog,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSplitter,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    PYQT_AVAILABLE = True
except Exception:
    PYQT_AVAILABLE = False

from assistant_memory import AssistantMemory

logger = logging.getLogger("jarvis.ui.history")

BG = "#02070d"
PANEL_BG = "#04131b"
FG = "#00eaff"
FG_LIGHT = "#c8f8ff"
FG_MUTED = "#607d8b"
BORDER = "#00eaff"
BORDER_MUTED = "rgba(0, 234, 255, 0.25)"
ACCENT_GREEN = "#00ffb4"
ACCENT_TEAL = "#7fffd4"
BTN_RESUME = "#007f75"
BTN_RESUME_HOVER = "#00a894"
BTN_NEW = "#005a6e"
BTN_DELETE = "#991b1b"

BASE_STYLE = f"""
QDialog {{
    background-color: {BG};
    color: {FG_LIGHT};
    font-family: Arial, sans-serif;
}}
QLabel {{
    color: {FG_LIGHT};
}}
QLineEdit {{
    background-color: #060e18;
    color: {FG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
}}
QLineEdit:focus {{
    border: 2px solid {ACCENT_GREEN};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: #02070d;
    width: 8px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: #005a6e;
    min-height: 20px;
    border-radius: 4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""

if PYQT_AVAILABLE:

    class SessionCard(QFrame):
        """Carte récapitulative cliquable représentant une session de conversation."""

        clicked = pyqtSignal(str)

        def __init__(
            self,
            session_data: Dict[str, Any],
            is_active: bool = False,
            parent: Optional[QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.session_id = session_data["session_id"]
            self.is_active = is_active
            self.setCursor(Qt.PointingHandCursor)
            self._build_ui(session_data)

        def _build_ui(self, data: Dict[str, Any]) -> None:
            self.setFrameShape(QFrame.StyledPanel)
            active_border = f"border: 2px solid {ACCENT_GREEN}; background-color: #003a4a;" if self.is_active else f"border: 1px solid {BORDER_MUTED}; background-color: {PANEL_BG};"
            self.setStyleSheet(
                f"""
                SessionCard {{
                    {active_border}
                    border-radius: 8px;
                    padding: 8px;
                }}
                SessionCard:hover {{
                    border: 1px solid {FG};
                    background-color: #07222c;
                }}
                """
            )

            layout = QVBoxLayout(self)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(4)

            # Ligne supérieure : Titre du sujet + badge actif
            top_row = QHBoxLayout()
            title_text = data.get("title") or f"Session {self.session_id[:8]}"
            title_lbl = QLabel(title_text)
            title_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {FG};")
            title_lbl.setWordWrap(True)
            top_row.addWidget(title_lbl, 1)

            if self.is_active:
                active_badge = QLabel("🟢 En cours")
                active_badge.setStyleSheet(
                    f"font-size: 11px; font-weight: bold; color: {ACCENT_GREEN}; "
                    "background: rgba(0, 255, 180, 0.15); border-radius: 4px; padding: 2px 6px;"
                )
                top_row.addWidget(active_badge)
            layout.addLayout(top_row)

            # Ligne médiane : Date et nombre de messages
            meta_row = QHBoxLayout()
            created_str = data.get("updated_at") or data.get("created_at") or ""
            if "T" in created_str:
                date_part, time_part = created_str.split("T")
                time_display = f"{date_part} {time_part[:5]}"
            else:
                time_display = created_str[:16]

            date_lbl = QLabel(f"🕒 {time_display}")
            date_lbl.setStyleSheet(f"font-size: 11px; color: {FG_MUTED};")
            meta_row.addWidget(date_lbl)

            count = data.get("message_count", 0)
            count_lbl = QLabel(f"💬 {count} msg{'s' if count > 1 else ''}")
            count_lbl.setStyleSheet(f"font-size: 11px; color: {ACCENT_TEAL}; font-weight: bold;")
            meta_row.addStretch()
            meta_row.addWidget(count_lbl)
            layout.addLayout(meta_row)

            # Aperçu du dernier message
            last_msg = data.get("last_message", "")
            if last_msg:
                preview_text = last_msg.replace("\n", " ")
                if len(preview_text) > 65:
                    preview_text = f"{preview_text[:65]}..."
                preview_lbl = QLabel(preview_text)
                preview_lbl.setStyleSheet("font-size: 11px; color: #90a4ae; font-style: italic;")
                preview_lbl.setWordWrap(True)
                layout.addWidget(preview_lbl)

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.LeftButton:
                self.clicked.emit(self.session_id)
            super().mousePressEvent(event)


    class ChatBubble(QFrame):
        """Bulle de dialogue stylisée pour l'affichage chronologique des échanges."""

        def __init__(self, message_data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self._build_ui(message_data)

        def _build_ui(self, msg: Dict[str, Any]) -> None:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            timestamp = msg.get("timestamp", "")
            tool_calls = msg.get("tool_calls")
            time_display = timestamp.split("T")[-1][:8] if "T" in timestamp else timestamp

            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(6)

            header_row = QHBoxLayout()

            if role == "user":
                role_text = "👤 Vous"
                role_color = FG
                bubble_bg = "#002b38"
                bubble_border = f"border: 1px solid {BORDER};"
                self.setStyleSheet(
                    f"ChatBubble {{ background-color: {bubble_bg}; {bubble_border} border-radius: 12px; margin-left: 60px; margin-right: 6px; }}"
                )
            elif role == "assistant":
                role_text = "🤖 M.O.C.T.A.R"
                role_color = ACCENT_TEAL
                bubble_bg = "#051c24"
                bubble_border = "border: 1px solid #00a894;"
                self.setStyleSheet(
                    f"ChatBubble {{ background-color: {bubble_bg}; {bubble_border} border-radius: 12px; margin-left: 6px; margin-right: 60px; }}"
                )
            elif role == "tool":
                role_text = f"⚙️ Résultat Outil ({msg.get('tool_call_id', 'action')})"
                role_color = "#ffb74d"
                bubble_bg = "#0b141d"
                bubble_border = "border: 1px dashed #78909c;"
                self.setStyleSheet(
                    f"ChatBubble {{ background-color: {bubble_bg}; {bubble_border} border-radius: 8px; margin-left: 20px; margin-right: 20px; }}"
                )
            else:
                role_text = "ℹ️ Système"
                role_color = FG_MUTED
                bubble_bg = "#07121a"
                bubble_border = "border: 1px solid #37474f;"
                self.setStyleSheet(
                    f"ChatBubble {{ background-color: {bubble_bg}; {bubble_border} border-radius: 8px; margin: 4px; }}"
                )

            role_lbl = QLabel(role_text)
            role_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {role_color};")
            header_row.addWidget(role_lbl)

            if time_display:
                time_lbl = QLabel(time_display)
                time_lbl.setStyleSheet("font-size: 11px; color: #546e7a;")
                header_row.addStretch()
                header_row.addWidget(time_lbl)
            layout.addLayout(header_row)

            if content:
                content_lbl = QLabel(content)
                content_lbl.setStyleSheet("font-size: 13px; color: #e0f7fa; line-height: 1.4;")
                content_lbl.setWordWrap(True)
                content_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                layout.addWidget(content_lbl)

            if tool_calls:
                tc_box = QFrame()
                tc_box.setStyleSheet(
                    "background: rgba(0, 234, 255, 0.05); border: 1px solid rgba(0, 234, 255, 0.2); border-radius: 6px; padding: 6px;"
                )
                tc_layout = QVBoxLayout(tc_box)
                tc_layout.setContentsMargins(6, 4, 6, 4)
                tc_title = QLabel("🛠️ Appel d'outil ReAct :")
                tc_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #ffb74d;")
                tc_layout.addWidget(tc_title)
                for tc in tool_calls:
                    tc_text = f"• {tc.get('name', 'action')} : {json.dumps(tc.get('arguments', {}), ensure_ascii=False)}"
                    tc_lbl = QLabel(tc_text)
                    tc_lbl.setStyleSheet("font-size: 11px; color: #b0bec5; font-family: Consolas, monospace;")
                    tc_lbl.setWordWrap(True)
                    tc_layout.addWidget(tc_lbl)
                layout.addWidget(tc_box)


    class ConversationHistoryWindow(QDialog):
        """Fenêtre modale complète de navigation, relecture et reprise des conversations JARVIS."""

        session_resumed = pyqtSignal(str)

        def __init__(
            self,
            parent: Optional[QWidget] = None,
            memory: Optional[AssistantMemory] = None,
            active_session_id: Optional[str] = None,
        ) -> None:
            super().__init__(parent)
            self.memory = memory or AssistantMemory()
            self.active_session_id = active_session_id or "default"
            self.selected_session_id: Optional[str] = self.active_session_id

            self.setWindowTitle("Historique des Conversations & Sujets - M.O.C.T.A.R")
            self.resize(1020, 780)
            self.setMinimumSize(850, 600)
            self.setStyleSheet(BASE_STYLE)

            self._build_ui()
            self._load_sessions()

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(18, 16, 18, 16)
            root.setSpacing(12)

            # Header principal
            header_layout = QHBoxLayout()
            title_vbox = QVBoxLayout()
            main_title = QLabel("Historique des Conversations")
            main_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #00eaff;")
            subtitle = QLabel("Parcourez vos sessions, relisez les échanges passés et reprenez un fil de discussion.")
            subtitle.setStyleSheet(f"font-size: 12px; color: {FG_MUTED};")
            title_vbox.addWidget(main_title)
            title_vbox.addWidget(subtitle)
            header_layout.addLayout(title_vbox, 1)

            btn_new = QPushButton("➕ Nouvelle session")
            btn_new.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {BTN_NEW};
                    color: #ffffff;
                    border: 1px solid {BORDER};
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #007a94;
                }}
                """
            )
            btn_new.clicked.connect(self._on_new_session)
            header_layout.addWidget(btn_new)
            root.addLayout(header_layout)

            # Splitter Master-Detail
            splitter = QSplitter(Qt.Horizontal)
            splitter.setStyleSheet(
                """
                QSplitter::handle {
                    background-color: rgba(0, 234, 255, 0.15);
                    width: 3px;
                }
                """
            )

            # -------------------------------------------------------------
            # Panneau Gauche : Recherche et Liste des Sessions
            # -------------------------------------------------------------
            left_panel = QWidget()
            left_vbox = QVBoxLayout(left_panel)
            left_vbox.setContentsMargins(0, 0, 8, 0)
            left_vbox.setSpacing(8)

            self.search_input = QLineEdit()
            self.search_input.setPlaceholderText("🔍 Rechercher par mot-clé, sujet...")
            self.search_input.setClearButtonEnabled(True)
            self.search_input.textChanged.connect(self._on_search_changed)
            left_vbox.addWidget(self.search_input)

            self.sessions_scroll = QScrollArea()
            self.sessions_scroll.setWidgetResizable(True)
            self.sessions_scroll_content = QWidget()
            self.sessions_vbox = QVBoxLayout(self.sessions_scroll_content)
            self.sessions_vbox.setContentsMargins(0, 4, 4, 4)
            self.sessions_vbox.setSpacing(8)
            self.sessions_vbox.addStretch()
            self.sessions_scroll.setWidget(self.sessions_scroll_content)
            left_vbox.addWidget(self.sessions_scroll, 1)

            splitter.addWidget(left_panel)

            # -------------------------------------------------------------
            # Panneau Droit : Détails et Relecture des Échanges
            # -------------------------------------------------------------
            right_panel = QWidget()
            right_vbox = QVBoxLayout(right_panel)
            right_vbox.setContentsMargins(8, 0, 0, 0)
            right_vbox.setSpacing(10)

            # Barre d'actions de la session sélectionnée
            self.detail_header = QFrame()
            self.detail_header.setStyleSheet(
                f"background: {PANEL_BG}; border: 1px solid {BORDER_MUTED}; border-radius: 8px; padding: 6px;"
            )
            dh_layout = QHBoxLayout(self.detail_header)
            dh_layout.setContentsMargins(10, 8, 10, 8)

            detail_title_vbox = QVBoxLayout()
            self.detail_title_lbl = QLabel("Sélectionnez une session")
            self.detail_title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #00eaff;")
            self.detail_meta_lbl = QLabel("")
            self.detail_meta_lbl.setStyleSheet(f"font-size: 11px; color: {FG_MUTED};")
            detail_title_vbox.addWidget(self.detail_title_lbl)
            detail_title_vbox.addWidget(self.detail_meta_lbl)
            dh_layout.addLayout(detail_title_vbox, 1)

            self.btn_resume = QPushButton("▶️ Reprendre cette discussion")
            self.btn_resume.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {BTN_RESUME};
                    color: #ffffff;
                    border: 1px solid {ACCENT_GREEN};
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-size: 13px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {BTN_RESUME_HOVER};
                }}
                QPushButton:disabled {{
                    background-color: #263238;
                    color: #607d8b;
                    border: 1px solid #37474f;
                }}
                """
            )
            self.btn_resume.clicked.connect(self._on_resume_session)
            dh_layout.addWidget(self.btn_resume)

            self.btn_delete = QPushButton("🗑️ Supprimer")
            self.btn_delete.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {BTN_DELETE};
                    color: #ffffff;
                    border: 1px solid #ef5350;
                    border-radius: 8px;
                    padding: 8px 14px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #b71c1c;
                }}
                QPushButton:disabled {{
                    background-color: #263238;
                    color: #607d8b;
                    border: 1px solid #37474f;
                }}
                """
            )
            self.btn_delete.clicked.connect(self._on_delete_session)
            dh_layout.addWidget(self.btn_delete)
            right_vbox.addWidget(self.detail_header)

            # Zone de relecture des messages (Chat)
            self.chat_scroll = QScrollArea()
            self.chat_scroll.setWidgetResizable(True)
            self.chat_scroll_content = QWidget()
            self.chat_vbox = QVBoxLayout(self.chat_scroll_content)
            self.chat_vbox.setContentsMargins(6, 6, 6, 6)
            self.chat_vbox.setSpacing(10)
            self.chat_vbox.addStretch()
            self.chat_scroll.setWidget(self.chat_scroll_content)
            right_vbox.addWidget(self.chat_scroll, 1)

            splitter.addWidget(right_panel)
            splitter.setSizes([340, 680])
            root.addWidget(splitter, 1)

            # Pied de page avec bouton Fermer
            footer_layout = QHBoxLayout()
            self.status_msg = QLabel("")
            self.status_msg.setStyleSheet(f"color: {ACCENT_GREEN}; font-size: 12px; font-weight: bold;")
            footer_layout.addWidget(self.status_msg, 1)

            btn_close = QPushButton("Fermer")
            btn_close.setStyleSheet(
                """
                QPushButton {
                    background-color: #1e293b;
                    color: #e2e8f0;
                    border: 1px solid #475569;
                    border-radius: 8px;
                    padding: 8px 20px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #334155;
                }
                """
            )
            btn_close.clicked.connect(self.accept)
            footer_layout.addWidget(btn_close)
            root.addLayout(footer_layout)

        def _clear_layout(self, layout: QVBoxLayout) -> None:
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

        def _load_sessions(self, search_query: Optional[str] = None) -> None:
            self._clear_layout(self.sessions_vbox)

            sessions = self.memory.list_sessions(limit=60, search_query=search_query)

            if not sessions:
                empty_lbl = QLabel("Aucune session trouvée." if search_query else "Aucun historique pour l'instant.")
                empty_lbl.setStyleSheet(f"color: {FG_MUTED}; font-size: 13px; padding: 20px;")
                empty_lbl.setAlignment(Qt.AlignCenter)
                self.sessions_vbox.addWidget(empty_lbl)
                self.sessions_vbox.addStretch()
                self._load_conversation_messages(None)
                return

            # Si la session couramment sélectionnée n'est pas dans la liste, sélectionner la première
            session_ids = [s["session_id"] for s in sessions]
            if self.selected_session_id not in session_ids:
                self.selected_session_id = session_ids[0]

            for s_data in sessions:
                is_active = (s_data["session_id"] == self.active_session_id)
                card = SessionCard(s_data, is_active=is_active)
                card.clicked.connect(self._on_select_session)
                self.sessions_vbox.addWidget(card)

            self.sessions_vbox.addStretch()
            self._load_conversation_messages(self.selected_session_id)

        def _on_select_session(self, session_id: str) -> None:
            self.selected_session_id = session_id
            self._load_conversation_messages(session_id)
            self._load_sessions(self.search_input.text().strip() or None)

        def _load_conversation_messages(self, session_id: Optional[str]) -> None:
            self._clear_layout(self.chat_vbox)

            if not session_id:
                self.detail_title_lbl.setText("Sélectionnez une session")
                self.detail_meta_lbl.setText("")
                self.btn_resume.setEnabled(False)
                self.btn_delete.setEnabled(False)
                self.chat_vbox.addStretch()
                return

            sessions = self.memory.list_sessions(limit=100)
            target_meta = next((s for s in sessions if s["session_id"] == session_id), None)
            title = target_meta.get("title") if target_meta else f"Session {session_id[:8]}"
            count = target_meta.get("message_count", 0) if target_meta else 0
            time_info = target_meta.get("updated_at", "") if target_meta else ""

            self.detail_title_lbl.setText(title)
            self.detail_meta_lbl.setText(f"Identifiant : {session_id} | Total échanges : {count} | {time_info}")
            self.btn_resume.setEnabled(True)
            self.btn_delete.setEnabled(True)

            if session_id == self.active_session_id:
                self.btn_resume.setText("🟢 Session actuellement active")
                self.btn_resume.setEnabled(False)
            else:
                self.btn_resume.setText("▶️ Reprendre ce fil")
                self.btn_resume.setEnabled(True)

            messages = self.memory.get_session_messages(session_id)
            if not messages:
                empty_chat = QLabel("Cette session ne contient aucun message.")
                empty_chat.setStyleSheet(f"color: {FG_MUTED}; font-size: 13px; padding: 30px;")
                empty_chat.setAlignment(Qt.AlignCenter)
                self.chat_vbox.addWidget(empty_chat)
                self.chat_vbox.addStretch()
                return

            for msg in messages:
                bubble = ChatBubble(msg)
                self.chat_vbox.addWidget(bubble)

            self.chat_vbox.addStretch()
            # Faire défiler vers le bas (messages récents)
            QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ))

        def _on_search_changed(self, text: str) -> None:
            query = text.strip() or None
            self._load_sessions(search_query=query)

        def _on_new_session(self) -> None:
            now_iso = datetime.now().isoformat(timespec="seconds")
            new_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            title = f"Nouvelle discussion du {now_iso[:10]}"
            self.memory.create_or_update_session(new_id, title=title)
            self.active_session_id = new_id
            self.selected_session_id = new_id
            self.session_resumed.emit(new_id)
            self.status_msg.setText("✨ Nouvelle session démarrée et active !")
            self._load_sessions()

        def _on_resume_session(self) -> None:
            if not self.selected_session_id:
                return
            self.active_session_id = self.selected_session_id
            self.session_resumed.emit(self.selected_session_id)
            self.status_msg.setText(f"✅ Session '{self.selected_session_id[:12]}' reprise avec succès !")
            self._load_sessions(self.search_input.text().strip() or None)

        def _on_delete_session(self) -> None:
            if not self.selected_session_id:
                return
            reply = QMessageBox.question(
                self,
                "Confirmation de suppression",
                "Êtes-vous sûr de vouloir supprimer définitivement cette conversation et tous ses messages ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.memory.delete_session(self.selected_session_id)
                self.status_msg.setText("🗑️ Session supprimée.")
                self.selected_session_id = None
                self._load_sessions(self.search_input.text().strip() or None)


    def open_conversation_history(
        parent: Optional[QWidget] = None,
        memory: Optional[AssistantMemory] = None,
        active_session_id: Optional[str] = None,
        on_resume_callback: Optional[callable] = None,
    ) -> None:
        """Ouvre la fenêtre modale d'historique des conversations."""
        window = ConversationHistoryWindow(
            parent=parent,
            memory=memory,
            active_session_id=active_session_id,
        )
        if on_resume_callback:
            window.session_resumed.connect(on_resume_callback)
        window.exec_()

else:

    def open_conversation_history(
        parent: Optional[QWidget] = None,
        memory: Optional[AssistantMemory] = None,
        active_session_id: Optional[str] = None,
        on_resume_callback: Optional[callable] = None,
    ) -> None:
        logger.warning("PyQt5 indisponible - impossible d'afficher l'interface d'historique des conversations.")

