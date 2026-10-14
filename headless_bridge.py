from __future__ import annotations

"""
Bridge headless pour M.O.C.T.A.R / JARVIS.

Fournit une interface Python propre, asynchrone et thread-safe pour exécuter
l'agent autonome ReAct sans aucune dépendance à PyQt5.
Conçu pour intégration directe dans des serveurs web (FastAPI, Flask, etc.)
ou des daemons d'arrière-plan.
"""

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import actions
import ai_brain
from ai_brain import AIBrain
from agent_core import AgentCore
from assistant_memory import AssistantMemory
from assistant_security import SecurityPolicyEngine
import config

logger = logging.getLogger("jarvis.headless")


def select_tools_for_prompt(prompt: str) -> Tuple[str, ...]:
    """Sélectionne un ensemble pertinent et réduit d'outils selon l'intention du prompt.

    Évite de saturer les modèles légers locaux (ex: qwen2.5:1.5b sur CPU) avec les 46 outils.
    """
    p = (prompt or "").lower()
    tools: set[str] = set()

    # Mots-clés Énergie / Solaire / Batterie
    if any(k in p for k in ("energie", "énergie", "solaire", "panneau", "batterie", "consommation", "kw", "kwh", "onduleur", "autonomie", "audit")):
        tools.update([
            "calculate_solar_sizing",
            "calculate_battery_sizing",
            "calculate_inverter_sizing",
            "calculate_consumption",
            "calculate_energy_cost",
        ])

    # Mots-clés IoT / Domotique / Capteurs / Relais
    if any(k in p for k in ("iot", "capteur", "relais", "salon", "lumiere", "lumière", "temperature", "température", "domotique", "switch")):
        tools.update([
            "get_iot_status",
            "get_iot_temperature",
            "set_relay_state",
        ])

    # Mots-clés Maintenance / Git / Projets / Code
    if any(k in p for k in ("git", "commit", "push", "pull", "branche", "projet", "vscode", "serveur", "code", "repo", "maintenance")):
        tools.update([
            "git_status",
            "git_pull",
            "git_log",
            "prepare_commit",
            "open_project",
            "maintenance_checklist",
        ])

    # Mots-clés Tâches / Notes / Rappels / Mémoire
    if any(k in p for k in ("tache", "tâche", "todo", "note", "rappel", "rappelle", "agenda", "memoire", "mémoire", "souviens", "recherche")):
        tools.update([
            "create_note",
            "add_todo",
            "list_todos",
            "list_todos_for_day",
            "search_personal_data",
            "schedule_reminder",
            "get_upcoming_reminders",
            "store_memory_fact",
            "search_memory_facts",
        ])

    # Mots-clés Télémétrie matérielle / Système / CPU / RAM / Disques / Ressources
    if any(k in p for k in ("systeme", "système", "cpu", "ram", "disque", "stockage", "temperature", "température", "ressource", "infrastructure", "materiel", "matériel")):
        tools.add("get_system_telemetry")

    # Mots-clés Système / Heure / Date / Volume / Apps
    if any(k in p for k in ("heure", "date", "jour", "volume", "son", "ouvre", "ferme", "capture", "ecran", "écran")):
        tools.update([
            "get_time_response",
            "get_date_response",
            "get_battery_response",
            "open_app",
            "close_app",
            "change_volume",
        ])

    # Mots-clés Web / Musique / Recherche
    if any(k in p for k in ("musique", "chanson", "youtube", "google", "cherche", "site", "web")):
        tools.update([
            "play_music",
            "control_music",
            "search_google",
            "search_youtube",
            "open_site",
        ])

    # Si aucun domaine spécifique détecté, restreindre aux 5 outils les plus courants
    if not tools:
        return (
            "get_time_response",
            "get_date_response",
            "get_iot_status",
            "search_personal_data",
            "list_todos",
        )

    return tuple(sorted(tools))


class JarvisHeadlessBridge:
    """Passerelle serveur headless pour l'agent autonome JARVIS."""

    def __init__(
        self,
        base_dir: Optional[Path | str] = None,
        db_path: Optional[Path | str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.base_dir = Path(base_dir or config.APP_ROOT).resolve()
        if str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path or (self.base_dir / "data" / "jarvis_memory.db")).resolve()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Inférence IA locale Ollama
        self.model = model or config.AI_SETTINGS.get("default_model", "qwen3:8b")
        self.base_url = base_url or config.AI_SETTINGS.get("base_url", "http://localhost:11434")
        self.brain = AIBrain(model=self.model, base_url=self.base_url)

        # Mémoire SQLite
        self.memory = AssistantMemory(db_path=self.db_path)

        # Moteur de sécurité
        self.security = SecurityPolicyEngine()

        # Orchestrateur ReAct
        self.agent = AgentCore(
            security=self.security,
            ai_brain_instance=self.brain,
            memory=self.memory,
        )

        self.active_session_id = f"session_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:4]}"
        self._lock = threading.Lock()
        logger.info(
            "JarvisHeadlessBridge initialisé (Modèle: %s @ %s, DB: %s, Outils: %d)",
            self.model,
            self.base_url,
            self.db_path,
            len(actions.TOOL_DISPATCHER),
        )

    def execute_prompt(
        self,
        user_input: str,
        source: str = "web_text",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exécute un prompt utilisateur de manière synchrone et thread-safe.

        Returns:
            Dict structuré contenant :
                - status: 'success' ou 'error'
                - reply: réponse finale formulée par l'assistant
                - thought_chain: liste des étapes de réflexion ReAct
                - tools_used: liste des outils appelés avec arguments et observations
                - timestamp: horodatage ISO de l'exécution
                - session_id: identifiant de session
                - source: 'web_text' ou 'web_voice'
        """
        user_input = (user_input or "").strip()
        timestamp = datetime.now().isoformat()
        current_session = session_id or self.active_session_id

        if not user_input:
            return {
                "status": "error",
                "reply": "Le prompt est vide.",
                "thought_chain": [],
                "tools_used": [],
                "timestamp": timestamp,
                "session_id": current_session,
                "source": source,
            }

        with self._lock:
            thought_chain: List[str] = []
            tools_used: List[Dict[str, Any]] = []

            # Interception des pensées intermédiaires du LLM
            orig_call_llm = self.agent._call_llm

            def hooked_call_llm(messages, tools=None, think=None):
                resp = orig_call_llm(messages, tools=tools, think=think)
                content = resp.get("content") or ""
                if content.strip():
                    thought_chain.append(content.strip())
                return resp

            # Interception des outils appelés et de leurs résultats
            orig_execute_tool = actions.execute_tool

            def hooked_execute_tool(tool_name, arguments=None):
                res = orig_execute_tool(tool_name, arguments)
                tools_used.append({
                    "tool": tool_name,
                    "arguments": arguments or {},
                    "success": bool(res.get("success", False)),
                    "message": res.get("message", ""),
                    "data": res.get("data"),
                    "error": res.get("error"),
                })
                return res

            import agent_core
            orig_agent_execute_tool = getattr(agent_core, "execute_tool", actions.execute_tool)
            agent_core.execute_tool = hooked_execute_tool
            actions.execute_tool = hooked_execute_tool

            def local_fallback() -> str:
                from intent_engine import detect_intent
                detected = detect_intent(user_input)
                intent = detected.get("intent")
                if intent == "time":
                    from actions.system_actions import get_time_response
                    res = get_time_response()
                    return res.get("message") if isinstance(res, dict) else str(res)
                elif intent == "date":
                    from actions.system_actions import get_date_response
                    res = get_date_response()
                    return res.get("message") if isinstance(res, dict) else str(res)
                elif intent in {"greetings", "hello"}:
                    return "Bonjour monsieur. Je suis prêt. En quoi puis-je vous aider ?"
                elif intent and intent in actions.TOOL_DISPATCHER:
                    tool_res = actions.execute_tool(intent, detected.get("slots") or {})
                    return tool_res.get("message") if isinstance(tool_res, dict) else str(tool_res)
                return "Bonjour monsieur. Je suis prêt et à votre écoute."

            status = "success"
            try:
                targeted_tools = select_tools_for_prompt(user_input)
                agent_result = self.agent.run(
                    prompt=user_input,
                    session_id=current_session,
                    tool_names=targeted_tools,
                    fallback=local_fallback,
                )
                if hasattr(agent_result, "response"):
                    reply = agent_result.response
                elif isinstance(agent_result, dict):
                    reply = agent_result.get("response") or agent_result.get("message") or str(agent_result)
                else:
                    reply = str(agent_result or "").strip()

                if not reply:
                    reply = local_fallback()
            except Exception as exc:
                logger.exception("Erreur exécution agent headless : %s", exc)
                reply = f"Erreur lors de l'exécution de la requête : {exc}"
                status = "error"
            finally:
                self.agent._call_llm = orig_call_llm
                actions.execute_tool = orig_execute_tool
                agent_core.execute_tool = orig_agent_execute_tool

            return {
                "status": status,
                "reply": reply,
                "thought_chain": thought_chain,
                "tools_used": tools_used,
                "timestamp": timestamp,
                "session_id": current_session,
                "source": source,
            }

    async def execute_prompt_async(
        self,
        user_input: str,
        source: str = "web_text",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Version asynchrone non-bloquante pour boucles asyncio / FastAPI."""
        return await asyncio.to_thread(self.execute_prompt, user_input, source, session_id)

    def get_status(self) -> Dict[str, Any]:
        """Renvoie l'état du moteur IA (Ollama en ligne/hors-ligne, modèle, outils, mémoire)."""
        ai_stat = ai_brain.get_ai_status()
        mem_counts = {"memories": 0, "messages": 0, "sessions": 0}
        try:
            with self.memory._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM memories")
                mem_counts["memories"] = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM conversation_history")
                mem_counts["messages"] = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM conversation_sessions")
                mem_counts["sessions"] = cursor.fetchone()[0]
        except Exception as exc:
            logger.warning("Impossible de lire les compteurs mémoire : %s", exc)

        return {
            "status": "ready" if ai_stat.get("online") else "degraded",
            "model": self.model,
            "base_url": self.base_url,
            "ollama_online": bool(ai_stat.get("online")),
            "tools_count": len(actions.TOOL_DISPATCHER),
            "tools": list(actions.TOOL_DISPATCHER.keys()),
            "memory": {
                "db_path": str(self.db_path),
                "total_memories": mem_counts["memories"],
                "total_messages": mem_counts["messages"],
                "total_sessions": mem_counts["sessions"],
            },
            "active_session": self.active_session_id,
            "timestamp": datetime.now().isoformat(),
        }

    def get_history(self, limit: int = 20, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Renvoie les dernières interactions enregistrées dans la base SQLite."""
        target_session = session_id or self.active_session_id
        items: List[Dict[str, Any]] = []
        try:
            with self.memory._get_connection() as conn:
                cursor = conn.cursor()
                if session_id:
                    cursor.execute(
                        """
                        SELECT id, session_id, timestamp, role, content, tool_calls, tool_call_id
                        FROM conversation_history
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (target_session, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, session_id, timestamp, role, content, tool_calls, tool_call_id
                        FROM conversation_history
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                rows = cursor.fetchall()
                for r in reversed(rows):
                    tool_calls = None
                    if r[5]:
                        try:
                            tool_calls = json.loads(r[5])
                        except Exception:
                            tool_calls = r[5]
                    items.append({
                        "id": r[0],
                        "session_id": r[1],
                        "timestamp": r[2],
                        "role": r[3],
                        "content": r[4],
                        "tool_calls": tool_calls,
                        "tool_call_id": r[6],
                    })
        except Exception as exc:
            logger.error("Erreur récupération historique : %s", exc)
        return items

    def clear_history(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Réinitialise le contexte court-terme de la conversation active."""
        target_session = session_id or self.active_session_id
        self.memory.clear_session(target_session)
        old_session = self.active_session_id
        self.active_session_id = f"session_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:4]}"
        return {
            "status": "success",
            "message": f"Contexte de session réinitialisé.",
            "cleared_session_id": old_session,
            "new_session_id": self.active_session_id,
        }
