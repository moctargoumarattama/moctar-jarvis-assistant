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
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
from uuid import uuid4

import actions
import ai_brain
from ai_brain import AIBrain
from agent_core import AgentCore
from assistant_memory import AssistantMemory
from assistant_security import SecurityPolicyEngine
import config

logger = logging.getLogger("jarvis.headless")


def _is_system_status_prompt(prompt: str) -> bool:
    normalized = unicodedata.normalize("NFKD", prompt.casefold())
    words = set(re.findall(r"[a-z0-9]+", normalized.encode("ascii", "ignore").decode()))
    return bool(words.intersection({
        "etat", "statut", "status", "infrastructure", "systeme", "system",
        "cpu", "ram", "disque", "disques", "stockage", "telemetrie",
        "ressource", "ressources", "materiel",
    }))


def _format_telemetry_report(result: Dict[str, Any]) -> str:
    """Bilan local utilisable même si l'inférence CPU dépasse le budget d'attente."""
    data = result.get("data") or {}
    memory = data.get("memory") or {}
    disk = data.get("disk") or {}
    disk_percent = disk.get("percent_used", disk.get("percent"))

    def measured(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def number(value):
        return f"{value:g}" if measured(value) else "indisponible"

    metrics = (
        ("CPU", data.get("cpu_percent"), 85, 95, "Identifier les processus gourmands et répartir les calculs."),
        ("RAM", memory.get("percent"), 80, 90, "Réduire les services non essentiels et surveiller la mémoire disponible."),
        ("Disque", disk_percent, 80, 90, "Contrôler la croissance des journaux et planifier la libération d'espace après sauvegarde."),
    )
    severity = 0
    recommendations = []
    for name, value, warning, critical, advice in metrics:
        if not measured(value):
            severity = max(severity, 1)
            recommendations.append(f"{name} indisponible : rétablir la collecte avant de conclure sur cette ressource.")
        elif value >= warning:
            severity = max(severity, 2 if value >= critical else 1)
            recommendations.append(f"{name} à {number(value)} % : {advice}")

    if not result.get("success"):
        severity = max(severity, 1)
    color = ("VERT", "ORANGE", "ROUGE")[severity]
    if not recommendations:
        recommendations.append("Conserver une marge de ressources et suivre l'évolution de la charge avant tout nouveau déploiement.")

    if result.get("success"):
        summary = (
            f"CPU {number(data.get('cpu_percent'))} % ; "
            f"RAM {number(memory.get('used_gb'))}/{number(memory.get('total_gb'))} Go "
            f"({number(memory.get('percent'))} %) ; "
            f"Disque {number(disk.get('free_gb'))}/{number(disk.get('total_gb'))} Go libres "
            f"({number(disk_percent)} % utilisés)."
        )
    else:
        summary = "Télémétrie indisponible : " + str(result.get("error") or result.get("message") or "collecte impossible")
    recommendations = recommendations[:3]
    if len(recommendations) < 3:
        recommendations.append("Vérifier les sondes des services et équipements pour compléter le diagnostic de l'infrastructure.")
    return (
        f"[État Opérationnel]\nStatut matériel {color}. {summary}\n"
        "Ce relevé concerne l'hôte ; les services et équipements distants ne sont pas mesurés.\n\n"
        "[Recommandations M.O.C.T.A.R]\n"
        + "\n".join(f"{index}. {advice}" for index, advice in enumerate(recommendations, 1))
    )


def select_tools_for_prompt(prompt: str) -> Tuple[str, ...]:
    """Sélectionne un ensemble pertinent et réduit d'outils selon l'intention du prompt.

    Limite les schémas transmis à Gemini aux outils pertinents parmi les 46 actions.
    """
    p = (prompt or "").lower()
    tools: set[str] = set()

    def has_keyword(keywords):
        return any(re.search(r"\b" + re.escape(word) + r"(?:s|x)?\b", p) for word in keywords)

    # Mots-clés Énergie / Solaire / Batterie
    if has_keyword(("energie", "énergie", "solaire", "panneau", "batterie", "consommation", "kw", "kwh", "onduleur", "autonomie", "audit")):
        tools.update([
            "calculate_solar_sizing",
            "calculate_battery_sizing",
            "calculate_inverter_sizing",
            "calculate_consumption",
            "calculate_energy_cost",
        ])

    # Mots-clés IoT / Domotique / Capteurs / Relais
    if has_keyword(("iot", "capteur", "relais", "salon", "lumiere", "lumière", "temperature", "température", "domotique", "switch")):
        tools.update([
            "get_iot_status",
            "get_iot_temperature",
            "set_relay_state",
        ])

    # Mots-clés Maintenance / Git / Projets / Code
    if has_keyword(("git", "commit", "push", "pull", "branche", "projet", "vscode", "serveur", "code", "repo", "maintenance", "papa", "baba")):
        tools.update([
            "git_status",
            "git_pull",
            "git_log",
            "prepare_commit",
            "prepare_push",
            "open_project",
            "maintenance_checklist",
            "get_project_context",
        ])

    # Mots-clés Interface HUD 3D / Globe / Géolocalisation
    if has_keyword(("hud", "globe", "terre", "orbe", "plasma", "quantum", "quantique", "position", "localise", "monde", "noc")):
        tools.add("switch_hud_view")

    # Mots-clés Tâches / Notes / Rappels / Mémoire
    if has_keyword(("tache", "tâche", "todo", "note", "rappel", "rappelle", "agenda", "memoire", "mémoire", "souviens", "recherche")):
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
    if _is_system_status_prompt(prompt) or has_keyword(("temperature", "température")):
        tools.add("get_system_telemetry")

    # Mots-clés Système / Heure / Date / Volume / Apps / Ouverture
    if has_keyword(("heure", "date", "jour", "volume", "son", "ouvre", "ouve", "lance", "ferme", "capture", "ecran", "écran")):
        tools.update([
            "get_time_response",
            "get_date_response",
            "get_battery_response",
            "open_app",
            "close_app",
            "change_volume",
            "open_site",
        ])

    # Mots-clés Web / Musique / Recherche
    if has_keyword(("musique", "chanson", "youtube", "google", "cherche", "site", "web", "navigue", "internet")):
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

        # Inférence exclusivement Google Gemini Cloud.
        self.brain = AIBrain(model=model, api_type="gemini")
        self.model = self.brain.gemini_model
        self.base_url = self.brain.base_url

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
            telemetry_result = None
            telemetry_deadline = None
            original_timeout = self.brain.timeout

            targeted_tools = select_tools_for_prompt(user_input)
            system_context = None
            if _is_system_status_prompt(user_input):
                try:
                    telemetry_tool = actions.TOOL_DISPATCHER.get("get_system_telemetry")
                    if not callable(telemetry_tool):
                        raise RuntimeError("L'outil get_system_telemetry est indisponible.")
                    telemetry_result = telemetry_tool()
                    if not isinstance(telemetry_result, dict):
                        raise TypeError("Résultat de télémétrie invalide.")
                except Exception as exc:
                    logger.warning("Précollecte de télémétrie indisponible : %s", exc)
                    telemetry_result = {"success": False, "data": None, "message": "Collecte impossible.", "error": str(exc)}
                system_context = {"system_telemetry": telemetry_result, "collected_at": datetime.now().isoformat()}
                tools_used.append({"tool": "get_system_telemetry", "arguments": {}, **{
                    key: telemetry_result.get(key) for key in ("success", "message", "data", "error")
                }})
                targeted_tools = tuple(name for name in targeted_tools if name != "get_system_telemetry")
                if not targeted_tools:
                    # Une seule synthèse ; le rapport déterministe reste disponible si l'API échoue.
                    telemetry_deadline = time.monotonic() + 1.0

            # Interception des pensées intermédiaires du LLM
            orig_call_llm = self.agent._call_llm

            def hooked_call_llm(messages, tools=None, think=None):
                if telemetry_deadline is not None:
                    remaining = telemetry_deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Budget de synthèse de télémétrie dépassé.")
                    self.brain.timeout = min(original_timeout, remaining)
                    think = False
                resp = orig_call_llm(messages, tools=tools, think=think)
                content = resp.get("content") or ""
                if content.strip():
                    thought_chain.append(content.strip())
                return resp

            # Interception des outils appelés et de leurs résultats
            orig_execute_tool = actions.execute_tool

            def hooked_execute_tool(tool_name, arguments=None):
                if tool_name == "get_system_telemetry" and telemetry_result is not None:
                    return telemetry_result
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

            fallback_required = False

            def local_fallback() -> str:
                nonlocal status, fallback_required
                fallback_required = True
                if telemetry_result is not None:
                    return _format_telemetry_report(telemetry_result)

                # Priorité au moteur déterministe de 46 tâches autonomes
                try:
                    import autonomous_library
                    auto_res = autonomous_library.execute_autonomous_task(user_input, session_id=current_session)
                    if auto_res.get("handled"):
                        status = "success"
                        if auto_res.get("tools_used"):
                            tools_used.extend(auto_res["tools_used"])
                        if auto_res.get("action") in ("open_browser", "open_app") and auto_res.get("url"):
                            tools_used.append({
                                "tool": "open_site" if auto_res.get("action") == "open_browser" else "switch_hud_view",
                                "data": {
                                    "action": auto_res.get("action"),
                                    "url": auto_res.get("url"),
                                    "title": auto_res.get("title"),
                                }
                            })
                        return auto_res["reply"]
                except Exception as auto_exc:
                    logger.warning("Erreur execution bibliotheque autonome: %s", auto_exc)

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
                elif intent in {"greetings", "hello"} or ai_brain.normalize_prompt(user_input) in {"bonjour", "bonsoir", "salut", "hello", "coucou"}:
                    return "Bonjour monsieur. Je suis prêt. En quoi puis-je vous aider ?"
                elif intent and intent in actions.TOOL_DISPATCHER:
                    tool_res = actions.execute_tool(intent, detected.get("slots") or {})
                    return tool_res.get("message") if isinstance(tool_res, dict) else str(tool_res)
                status = "error"
                return "Le moteur IA est indisponible pour cette demande. Vérifiez le serveur et le modèle configurés, puis réessayez."

            status = "success"
            try:
                self.agent._call_llm = hooked_call_llm
                agent_result = self.agent.run(
                    prompt=user_input,
                    context=system_context,
                    session_id=current_session,
                    tool_names=targeted_tools,
                    fallback=local_fallback,
                )
                if getattr(agent_result, "used_fallback", False) or getattr(agent_result, "status", None) == "fallback":
                    fallback_required = True

                if hasattr(agent_result, "response"):
                    reply = agent_result.response
                elif isinstance(agent_result, dict):
                    reply = agent_result.get("response") or agent_result.get("message") or str(agent_result)
                else:
                    reply = str(agent_result or "").strip()

                if not reply:
                    reply = local_fallback()
                if telemetry_deadline is not None:
                    measured_report = _format_telemetry_report(telemetry_result)
                    recommendations_heading = "[Recommandations M.O.C.T.A.R]"
                    if telemetry_result.get("success") and all(
                        section in reply for section in ("[État Opérationnel]", recommendations_heading)
                    ):
                        # Le modèle conseille ; les chiffres et le statut proviennent toujours des mesures.
                        recommendations = reply.partition(recommendations_heading)[2].strip()
                        reply = (
                            measured_report.partition(recommendations_heading)[0]
                            + recommendations_heading + "\n" + recommendations
                        ) if recommendations else measured_report
                    else:
                        reply = measured_report
            except Exception as exc:
                logger.exception("Erreur exécution agent headless : %s", exc)
                fallback_required = True
                try:
                    import autonomous_library
                    auto_fallback = autonomous_library.execute_autonomous_task(user_input, session_id=current_session)
                    if auto_fallback.get("handled"):
                        reply = f"⚠️ [Mode Autonome Activé]\n{auto_fallback['reply']}"
                        status = "success"
                        if auto_fallback.get("tools_used"):
                            tools_used.extend(auto_fallback["tools_used"])
                    else:
                        reply = f"Erreur lors de l'exécution de la requête : {exc}"
                        status = "error"
                except Exception:
                    reply = f"Erreur lors de l'exécution de la requête : {exc}"
                    status = "error"
            finally:
                self.agent._call_llm = orig_call_llm
                self.brain.timeout = original_timeout
                actions.execute_tool = orig_execute_tool
                agent_core.execute_tool = orig_agent_execute_tool

            # Une réponse déterministe ou une erreur d'outil ne prouve pas une
            # panne IA. Seul l'échec observé de Gemini signale son indisponibilité ;
            # cette lecture ne fait aucun appel réseau.
            # Propagation structuree des etats de securite, confirmation et verification
            res_status = getattr(agent_result, "status", None) if not isinstance(agent_result, dict) else agent_result.get("status")
            if res_status:
                status = res_status

            sec_status = getattr(agent_result, "security_status", None) if not isinstance(agent_result, dict) else agent_result.get("security_status")
            req_confirm = getattr(agent_result, "requires_confirmation", None) if not isinstance(agent_result, dict) else agent_result.get("requires_confirmation")
            pending_act_id = getattr(agent_result, "pending_action_id", None) if not isinstance(agent_result, dict) else agent_result.get("pending_action_id")
            tool_calls = getattr(agent_result, "tool_calls", None) if not isinstance(agent_result, dict) else agent_result.get("tool_calls")
            verif_status = getattr(agent_result, "verification_status", None) if not isinstance(agent_result, dict) else agent_result.get("verification_status")

            ai_status = self.brain.get_health_status(probe=False)
            model_offline = fallback_required and ai_status.get("all_providers_offline") is True
            resp_dict: Dict[str, Any] = {
                **ai_status,
                "status": status,
                "reply": reply,
                "security_status": sec_status or ("blocked" if status == "blocked" else ("pending_confirmation" if status == "pending_confirmation" else "allowed")),
                "requires_confirmation": bool(req_confirm or (status == "pending_confirmation")),
                "pending_action_id": pending_act_id,
                "tool_calls": tool_calls or tools_used,
                "verification_status": verif_status or ("verified" if status == "success" else status),
                "thought_chain": thought_chain,
                "tools_used": tools_used,
                "timestamp": timestamp,
                "session_id": current_session,
                "source": source,
                "model_offline": model_offline,
                "model_offline_details": (
                    "Gemini Cloud est indisponible (actions autonomes disponibles)"
                    if model_offline else None
                ),
                "autonomous_available": True,
            }

            for tu in tools_used:
                t_data = tu.get("data")
                if isinstance(t_data, dict) and t_data.get("action") in ("open_browser", "open_app") and t_data.get("url"):
                    resp_dict["action"] = t_data.get("action")
                    resp_dict["url"] = t_data.get("url")
                    resp_dict["title"] = t_data.get("title")
                    break
                if tu.get("tool") == "switch_hud_view":
                    req_v = str((tu.get("arguments") or {}).get("view") or "").lower()
                    resp_dict["action"] = "open_app"
                    resp_dict["url"] = "/" if req_v == "noc" else "/hud"
                    resp_dict["title"] = "Centre de Commandement NOC" if req_v == "noc" else "HUD 3D Cybernétique"
                    break

            return resp_dict

    async def execute_prompt_async(
        self,
        user_input: str,
        source: str = "web_text",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Version asynchrone non-bloquante pour boucles asyncio / FastAPI."""
        return await asyncio.to_thread(self.execute_prompt, user_input, source, session_id)

    def get_status(self) -> Dict[str, Any]:
        """Renvoie l'état du moteur IA, du fournisseur actif, des outils et de la mémoire."""
        ai_stat = self.brain.get_health_status()
        online = bool(ai_stat.get("online"))
        mode = ai_stat.get("mode") or ("gemini" if online else "local")
        active_model = ai_stat.get("model") or self.model
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
            "status": "ready" if online else "degraded",
            "online": online,
            "provider": "gemini",
            "mode": mode,
            "active_provider": ai_stat.get("active_provider", mode),
            "model": active_model,
            "base_url": self.base_url,
            "gemini_online": bool(ai_stat.get("gemini_online", mode == "gemini" and online)),
            "gemini_configured": bool(ai_stat.get("gemini_configured", mode == "gemini")),
            "all_providers_offline": bool(ai_stat.get("all_providers_offline", not online)),
            "model_offline": bool(ai_stat.get("all_providers_offline", not online)),
            "autonomous_ready": True,
            "autonomous_tasks_count": len(actions.TOOL_DISPATCHER),
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
        """Historique public ; les conversations WhatsApp restent privées."""
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
                        WHERE session_id = ? AND session_id NOT LIKE 'whatsapp:%'
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
                        WHERE session_id NOT LIKE 'whatsapp:%'
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
