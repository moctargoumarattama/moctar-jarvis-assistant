"""Orchestrateur d'agent autonome avec boucle de raisonnement ReAct pour JARVIS.

Fournit la classe AgentCore qui implémente :
- Une boucle ReAct (Reasoning + Acting) multi-tours avec auto-correction.
- L'utilisation automatique du catalogue d'outils standardisés via actions.
- L'intégration de la couche d'inférence AIBrain (Ollama / OpenAI).
- Le respect strict des politiques de sécurité et des confirmations sensibles.
- La traçabilité complète des étapes via logging ([THOUGHT], [ACTION], [OBSERVATION], [FINAL_RESPONSE]).
"""

from copy import deepcopy
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4

import ai_brain
from ai_brain import AIBrain
from assistant_memory import AssistantMemory
from assistant_security import SecurityPolicy, SecurityPolicyEngine
import config
from actions import execute_tool, get_all_tools_schema

logger = logging.getLogger("jarvis.agent")

AGENT_SYSTEM_PROMPT = (
    ai_brain.MOCTAR_SYSTEM_PROMPT
    + "\nRÈGLE ABSOLUE : Ne fais JAMAIS de promesse d'action ni de phrase d'attente "
    "(interdit : 'Je vais vérifier', 'Je commence par', 'Patientez'). "
    "Si une information demande un outil, APPELLE DIRECTEMENT L'OUTIL au premier tour sans texte d'introduction.\n"
    "EXEMPLE D'APPEL D'OUTIL ATTENDU :\n"
    "Action: open_site\n"
    "Action Input: {\"target\": \"https://www.youtube.com\"}\n\n"
    "Utilise les observations réelles déjà obtenues dans le contexte système sans redemander les mêmes mesures. "
    "N'invente ni mesure, heure, fichier, état IoT ou statut Git ; signale les données indisponibles. "
    "system_telemetry décrit uniquement l'hôte mesuré, pas les services ni les machines distantes sans observation dédiée.\n"
    "Pour plusieurs actions, appelle les outils successivement et analyse chaque résultat. "
    "En cas d'échec, corrige les arguments ou explique la cause ; ne prétends jamais avoir réussi.\n"
    "Fonde les recommandations sur les observations ; une recommandation n'est pas une action exécutée.\n"
    "Respecte les validations de sécurité et demande les confirmations requises pour toute action sensible."
)

_SECRET_KEY_MARKERS = ("secret", "token", "password", "passwd", "api_key", "apikey", "credential")


@dataclass
class AgentState:
    """État interne conservé pour les interactions multi-tours et reprises après confirmation."""
    request_id: str
    user_prompt: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    thinking: bool = False
    model_steps: int = 0
    tool_calls: int = 0
    tool_results: List[str] = field(default_factory=list)


@dataclass
class PendingAgentAction:
    """Action sensible mise en pause en attente de validation utilisateur."""
    state: AgentState
    tool_call: Dict[str, Any]
    remaining_tool_calls: List[Dict[str, Any]]
    tool_name: str
    arguments: Dict[str, Any]


@dataclass
class AgentResult:
    """Résultat synthétique renvoyé par l'orchestrateur d'agent."""
    response: str
    status: str = "final"
    pending_action: Optional[PendingAgentAction] = None
    used_fallback: bool = False


class AgentCore:
    """Orchestrateur d'agent autonome avec boucle ReAct et Function Calling natif."""

    def __init__(
        self,
        registry: Optional[Any] = None,
        security: Optional[Any] = None,
        *,
        ai_brain_instance: Optional[AIBrain] = None,
        chat_client: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_iterations: int = 5,
        max_steps: Optional[int] = None,
        system_prompt: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        memory: Optional[AssistantMemory] = None,
    ) -> None:
        self.brain = ai_brain_instance or AIBrain()
        self.chat_client = chat_client
        self.registry = registry
        self.security = security or SecurityPolicyEngine()
        self.memory = memory or AssistantMemory()
        configured_steps = config.AI_SETTINGS.get("agent_max_steps", 5) if hasattr(config, "AI_SETTINGS") else 5
        self.max_iterations = max(1, min(int(max_steps or max_iterations or configured_steps), 10))
        self.max_steps = self.max_iterations
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        self.thinking_mode = (
            thinking_mode
            or (config.AI_SETTINGS.get("agent_thinking", "auto") if hasattr(config, "AI_SETTINGS") else "auto")
            or "auto"
        ).strip().lower()

        # Chargement des schémas d'outils
        if tools is not None:
            self.tools = list(tools)
        elif self.registry is not None and hasattr(self.registry, "ollama_tools"):
            domain_allowed = getattr(self.security, "is_domain_allowed", None)
            self.tools = self.registry.ollama_tools(domain_allowed=domain_allowed)
        else:
            try:
                self.tools = get_all_tools_schema()
            except Exception as exc:
                logger.warning("Chargement direct des schémas d'actions impossible : %s", exc)
                self.tools = []

        logger.info(
            "AgentCore initialisé avec %d outils disponibles (max_iterations=%d)",
            len(self.tools),
            self.max_iterations,
        )

    # --------------------------------------------------------------------------
    # GESTION INTERNE DES APPELS MODÈLE
    # --------------------------------------------------------------------------

    def _should_think(self, prompt: str) -> bool:
        """Détermine si le mode de pensée approfondie (reasoning) doit être activé."""
        if self.thinking_mode == "always":
            return True
        if self.thinking_mode == "never":
            return False
        # Mode 'auto' : activer pour les requêtes complexes à plusieurs critères
        normalized = prompt.lower()
        complex_cues = (
            "analyse", "compare", "priorise", "pourquoi", "comment",
            "explique", "plan", "synthese", "apres-midi", "etape", "recherche"
        )
        return len(prompt.split()) > 7 or any(cue in normalized for cue in complex_cues)

    @staticmethod
    def _is_action_promise(content: str) -> bool:
        """Repère un arrêt sur une annonce d'action au lieu d'un résultat réel."""
        return bool(re.match(
            r"^\s*(?:je vais (?:v[ée]rifier|consulter|analyser|examiner|r[ée]cup[ée]rer|collecter|ex[ée]cuter|commencer)\b|"
            r"je commence par\b|patientez\b)",
            content,
            flags=re.IGNORECASE,
        ))

    @staticmethod
    def _extract_open_fast_path(prompt: str) -> Optional[Dict[str, str]]:
        """Détecte les ordres directs d'ouverture de sites ou applications."""
        norm = (prompt or "").strip()
        m = re.match(
            r"^(?:ouvre|ouvrez|ouve|lance|lancez|va sur|navigue vers|go to)\s+(?:le site\s+|l'application\s+|l'app\s+|la page\s+)?(.+)$",
            norm,
            re.IGNORECASE,
        )
        if not m:
            return None
        target = m.group(1).strip().rstrip(".!?")
        if not target:
            return None
        return {"target": target}

    def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        think: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Appelle le client LLM (chat_client injecté pour tests ou AIBrain natif)."""
        active_tools = list(tools) if tools is not None else None

        if self.chat_client is not None:
            think_flag = self._should_think(messages[-1].get("content", "")) if think is None else think
            try:
                response = self.chat_client(
                    messages, tools=active_tools, think=think_flag,
                    temperature=ai_brain.OLLAMA_TEMPERATURE,
                )
            except Exception as exc:
                raise exc

            # Normalisation selon la forme de la réponse du chat_client
            if isinstance(response, dict) and "message" in response:
                return self.brain._normalize_ollama_payload(response)
            if isinstance(response, dict) and "choices" in response:
                return self.brain._normalize_openai_payload(response)
            if isinstance(response, dict) and "finish_reason" in response:
                return response
            return {
                "content": str(response) if response else None,
                "tool_calls": [],
                "finish_reason": "stop",
            }

        return self.brain.chat(
            messages, tools=active_tools, temperature=ai_brain.OLLAMA_TEMPERATURE,
        )

    # --------------------------------------------------------------------------
    # BOUCLE PRINCIPALE REACT MULTI-TOURS (PROCESS_QUERY)
    # --------------------------------------------------------------------------

    def process_query(
        self,
        user_query: str,
        session_history: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "default",
    ) -> str:
        """Orchestre la boucle ReAct complète pour traiter une requête utilisateur de manière autonome.

        Args:
            user_query (str): Demande ou instruction formulée par l'utilisateur.
            session_history (Optional[List[Dict[str, Any]]]): Historique préalable de la session.
            session_id (str): Identifiant de la session pour la mémoire court-terme.

        Returns:
            str: Réponse finale synthétisée à renvoyer à l'utilisateur.
        """
        logger.info("[START] Traitement de la requête utilisateur: '%s'", user_query)

        # 1. Enrichissement dynamique du System Prompt avec les faits mémorisés pertinents
        system_content = self.system_prompt
        if self.memory:
            try:
                memory_snippet = self.memory.get_system_context_snippet(user_query)
                if memory_snippet:
                    system_content = f"{system_content}\n\n{memory_snippet}"
            except Exception as exc:
                logger.warning("Erreur récupération snippet mémoire : %s", exc)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]

        # 2. Restauration de l'historique conversationnel (Session history ou Working Memory)
        if session_history is not None:
            for turn in session_history:
                if isinstance(turn, dict) and "role" in turn and "content" in turn:
                    messages.append({"role": turn["role"], "content": turn["content"]})
        elif self.memory:
            try:
                working_context = self.memory.get_context_window(session_id=session_id)
                for turn in working_context:
                    messages.append(turn)
            except Exception as exc:
                logger.warning("Erreur récupération contexte mémoire : %s", exc)

        # 3. Ajout de la nouvelle requête utilisateur
        messages.append({"role": "user", "content": user_query})
        if self.memory:
            try:
                self.memory.add_message(session_id=session_id, role="user", content=user_query)
            except Exception as exc:
                logger.warning("Erreur persistance message user en mémoire : %s", exc)

        # Fast-path pour ouverture directe de sites/apps
        if self.chat_client is None:
            open_info = self._extract_open_fast_path(user_query)
            if open_info:
                domain_allowed = True
                if self.security is not None and hasattr(self.security, "is_domain_allowed"):
                    domain_allowed = self.security.is_domain_allowed("web")
                if domain_allowed:
                    from actions.web_actions import resolve_site_url
                    site_url, title = resolve_site_url(open_info["target"])
                    execute_tool("open_site", {"target": open_info["target"]})
                    final_text = f"🌐 Ouverture de {title} ({site_url}) dans votre navigateur."
                    if self.memory:
                        try:
                            self.memory.add_message(session_id=session_id, role="assistant", content=final_text)
                        except Exception:
                            pass
                    return final_text

        active_tools = list(self.tools)

        for iteration in range(self.max_iterations):
            logger.info(
                "[THOUGHT] Itération ReAct %d/%d - Analyse de la situation par JARVIS...",
                iteration + 1,
                self.max_iterations,
            )

            try:
                llm_response = self._call_llm(messages, tools=active_tools)
            except Exception as exc:
                logger.error("[ERROR] Échec lors de l'appel LLM: %s", exc)
                return f"Désolé, une erreur est survenue lors de l'inférence : {exc}"

            if llm_response.get("finish_reason") == "error":
                err_msg = llm_response.get("content") or "Erreur inconnue du modèle."
                logger.error("[ERROR] Le modèle a renvoyé une erreur: %s", err_msg)
                return f"Je rencontre une difficulté avec le modèle d'intelligence : {err_msg}"

            content = llm_response.get("content") or ""
            tool_calls = llm_response.get("tool_calls") or []

            # ÉTAPE C : Des outils ont été demandés par le modèle
            if tool_calls:
                logger.info(
                    "[THOUGHT] Décision de l'agent : Exécuter %d outil(s) pour agir sur le monde réel.",
                    len(tool_calls),
                )

                # Sauvegarde du message assistant avec ses tool_calls
                assistant_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                                if isinstance(tc["arguments"], dict)
                                else str(tc["arguments"]),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                if llm_response.get("_gemini_parts"):
                    assistant_entry["_gemini_parts"] = llm_response["_gemini_parts"]
                messages.append(assistant_entry)
                if self.memory:
                    try:
                        self.memory.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=content,
                            tool_calls=assistant_entry["tool_calls"],
                        )
                    except Exception as exc:
                        logger.warning("Erreur persistance message assistant tool_calls : %s", exc)

                # Exécution et observation séquentielle de chaque outil
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    tool_id = tc["id"]

                    logger.info("[ACTION] Exécution de '%s' avec les arguments: %s", tool_name, tool_args)

                    # Contrôle de sécurité préventif des arguments
                    if self.security is not None and hasattr(self.security, "validate_arguments"):
                        is_valid, sec_err = self.security.validate_arguments(tool_name, tool_args)
                        if not is_valid:
                            logger.warning("[SECURITY] Blocage de sécurité pour '%s' : %s", tool_name, sec_err)
                            if hasattr(self.security, "audit_log"):
                                risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(tool_name, tool_args)
                                self.security.audit_log(
                                    tool_name=tool_name,
                                    arguments=tool_args,
                                    risk_level=risk,
                                    confirmed=False,
                                    status="blocked",
                                    details=sec_err,
                                )
                            observation_result = {
                                "success": False,
                                "action": tool_name,
                                "message": f"Action bloquée par la politique de sécurité : {sec_err}",
                                "data": None,
                                "error": "SecurityViolation",
                            }
                            tool_message = {
                                "role": "tool",
                                "name": tool_name,
                                "tool_call_id": tool_id,
                                "content": json.dumps(observation_result, ensure_ascii=False),
                            }
                            messages.append(tool_message)
                            if self.memory:
                                try:
                                    self.memory.add_message(
                                        session_id=session_id,
                                        role="tool",
                                        content=tool_message["content"],
                                        tool_call_id=tool_id,
                                    )
                                except Exception:
                                    pass
                            continue

                    # Exécution de l'action via le dispatcher standardisé
                    observation_result = execute_tool(tool_name, tool_args)
                    if hasattr(self.security, "audit_log"):
                        risk = getattr(self.security, "get_risk_level", lambda t, a: 1)(tool_name, tool_args)
                        self.security.audit_log(
                            tool_name=tool_name,
                            arguments=tool_args,
                            risk_level=risk,
                            confirmed=False,
                            status="allowed" if observation_result.get("success") else "failed",
                        )

                    logger.info(
                        "[OBSERVATION] Résultat '%s': success=%s, message='%s'",
                        tool_name,
                        observation_result.get("success"),
                        observation_result.get("message"),
                    )

                    tool_message = {
                        "role": "tool",
                        "name": tool_name,
                        "tool_call_id": tool_id,
                        "content": json.dumps(observation_result, ensure_ascii=False),
                    }
                    messages.append(tool_message)
                    if self.memory:
                        try:
                            self.memory.add_message(
                                session_id=session_id,
                                role="tool",
                                content=tool_message["content"],
                                tool_call_id=tool_id,
                            )
                        except Exception as exc:
                            logger.warning("Erreur persistance observation outil : %s", exc)

                # Poursuite de la boucle ReAct pour que le LLM analyse les observations
                continue

            # ÉTAPE D : Aucun outil requis -> Réponse finale formulée par l'agent
            final_text = content.strip()
            if not final_text:
                final_text = "Action effectuée avec succès."
            if self.memory:
                try:
                    self.memory.add_message(session_id=session_id, role="assistant", content=final_text)
                except Exception as exc:
                    logger.warning("Erreur persistance réponse finale : %s", exc)
            logger.info("[FINAL_RESPONSE] Réponse finale générée: '%s'", final_text)
            return final_text

        # ÉTAPE E : Limite maximale d'itérations atteinte
        logger.warning(
            "[LIMIT] Nombre maximal d'itérations atteint (%d). Forçage d'une synthèse finale.",
            self.max_iterations,
        )
        messages.append({
            "role": "user",
            "content": (
                "Tu as atteint le nombre maximal d'étapes de travail autorisées. "
                "Fais maintenant une synthèse claire et définitive de ce qui a été accompli et des points en suspens."
            ),
        })

        try:
            forced_response = self._call_llm(messages, tools=None)
            final_text = (forced_response.get("content") or "").strip()
        except Exception as exc:
            logger.warning("Erreur lors de la synthese forcee LLM : %s", exc)
            final_text = ""

        if not final_text:
            # Synthèse déterministe des outils exécutés pour garantir zéro silence
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            if tool_msgs:
                lines = ["📋 **Synthèse des actions exécutées par M.O.C.T.A.R :**"]
                for tm in tool_msgs:
                    t_name = tm.get("name") or "Action"
                    raw_c = tm.get("content") or ""
                    try:
                        parsed_c = json.loads(raw_c) if isinstance(raw_c, str) else raw_c
                        msg = parsed_c.get("message") or parsed_c.get("data") or "Exécuté avec succès."
                    except Exception:
                        msg = str(raw_c)[:120]
                    lines.append(f"- **{t_name}** : {msg}")
                final_text = "\n".join(lines)
            else:
                final_text = "Nombre maximal d'étapes de réflexion atteint sans conclusion."

        if self.memory:
            try:
                self.memory.add_message(session_id=session_id, role="assistant", content=final_text)
            except Exception as exc:
                logger.warning("Erreur persistance réponse forçée : %s", exc)

        logger.info("[FINAL_RESPONSE] (Synthèse forcée) : '%s'", final_text)
        return final_text

    # --------------------------------------------------------------------------
    # COMPATIBILITÉ AVEC L'ARCHITECTURE JARVIS (RUN / RESUME / CONFIRMATIONS)
    # --------------------------------------------------------------------------

    def run(
        self,
        prompt: str = "",
        *,
        user_query: Optional[str] = None,
        context: Optional[Any] = None,
        fallback: Optional[Callable[[], str]] = None,
        tool_names: Optional[Tuple[str, ...]] = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Point d'entrée d'exécution compatible avec assistant_core et la suite de tests."""
        effective_prompt = (prompt or user_query or "").strip()
        request_id = uuid4().hex
        logger.info("AgentCore.run(request_id=%s): prompt='%s'", request_id, effective_prompt)
        prompt = effective_prompt

        # Fast-path pour ouverture directe de sites/apps si chat_client n'est pas mocké
        if self.chat_client is None:
            open_fast = self._extract_open_fast_path(prompt)
            if open_fast:
                domain_allowed = True
                if self.security is not None and hasattr(self.security, "is_domain_allowed"):
                    domain_allowed = self.security.is_domain_allowed("web")
                if domain_allowed:
                    from actions.web_actions import resolve_site_url
                    site_url, title = resolve_site_url(open_fast["target"])
                    execute_tool("open_site", {"target": open_fast["target"]})
                    reply_msg = f"🌐 J'ouvre {title} ({site_url}) dans votre navigateur."
                    if self.memory:
                        try:
                            s_id = kwargs.get("session_id", "default")
                            self.memory.add_message(session_id=s_id, role="user", content=prompt)
                            self.memory.add_message(session_id=s_id, role="assistant", content=reply_msg)
                        except Exception:
                            pass
                    return AgentResult(
                        response=reply_msg,
                        status="final",
                    )

        # Filtrage des outils selon tool_names si précisé
        if tool_names is not None:
            selected_names = set(tool_names)
            active_tools = [t for t in self.tools if t.get("function", {}).get("name") in selected_names]
        else:
            active_tools = list(self.tools)

        # Préparation du contexte initial
        system_content = self.system_prompt
        if self.memory:
            try:
                memory_snippet = self.memory.get_system_context_snippet(prompt)
                if memory_snippet:
                    system_content = f"{system_content}\n\n{memory_snippet}"
            except Exception:
                pass
        if context:
            system_content += (
                "\n\nContexte local additionnel (observations réelles déjà obtenues ; "
                "utilise directement les mesures présentes sans nouvelle collecte) :\n"
                f"{json.dumps(context, ensure_ascii=False)}"
            )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

        state = AgentState(
            request_id=request_id,
            user_prompt=prompt,
            messages=messages,
            tools=active_tools,
            thinking=self._should_think(prompt),
        )
        promise_retry_used = False

        for step in range(self.max_iterations):
            state.model_steps += 1
            logger.info("[THOUGHT] Étape %d/%d (request_id=%s)", state.model_steps, self.max_iterations, request_id)

            try:
                llm_response = self._call_llm(state.messages, tools=active_tools, think=state.thinking)
            except Exception as exc:
                logger.warning("Exception lors de l'appel LLM dans run(): %s", exc)
                if fallback is not None:
                    return AgentResult(response=fallback(), status="fallback", used_fallback=True)
                return AgentResult(response=f"Erreur d'inférence : {exc}", status="error")

            if llm_response.get("finish_reason") == "error":
                if fallback is not None:
                    return AgentResult(response=fallback(), status="fallback", used_fallback=True)
                return AgentResult(response=llm_response.get("content") or "Erreur inconnue.", status="error")

            content = llm_response.get("content") or ""
            tool_calls = llm_response.get("tool_calls") or []

            if not tool_calls:
                if self._is_action_promise(content):
                    if not promise_retry_used and state.model_steps < self.max_steps:
                        promise_retry_used = True
                        state.messages.append({
                            "role": "system",
                            "content": (
                                "Une promesse d'action seule ne répond pas à la demande. "
                                "Appelle maintenant l'outil nécessaire sans introduction. "
                                "Si le contexte contient déjà les observations demandées, donne directement "
                                "les valeurs réelles et les recommandations dans les deux sections obligatoires. "
                                "Si l'action est impossible, explique précisément la limitation."
                            ),
                        })
                        continue
                    if fallback is not None:
                        return AgentResult(response=fallback(), status="fallback", used_fallback=True)
                    return AgentResult(
                        response="Le modèle n'a fourni ni résultat ni appel d'outil pour cette demande.",
                        status="error",
                    )
                return AgentResult(response=content.strip(), status="final")

            # Arrêt si le nombre maximal d'étapes de modèle est atteint
            if state.model_steps >= self.max_steps:
                return AgentResult(response="Nombre maximal d'étapes atteint sans conclusion.", status="max_steps")

            # Ajout du message assistant
            assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                            if isinstance(tc["arguments"], dict)
                            else str(tc["arguments"]),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            if llm_response.get("_gemini_parts"):
                assistant_msg["_gemini_parts"] = llm_response["_gemini_parts"]
            state.messages.append(assistant_msg)

            for index, tc in enumerate(tool_calls):
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                call_id = tc["id"]

                # 1. Validation préventive des arguments contre les injections
                if self.security is not None and hasattr(self.security, "validate_arguments"):
                    is_valid, sec_err = self.security.validate_arguments(tool_name, tool_args)
                    if not is_valid:
                        logger.warning("[SECURITY] Blocage de l'outil '%s' : %s", tool_name, sec_err)
                        if hasattr(self.security, "audit_log"):
                            risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(tool_name, tool_args)
                            self.security.audit_log(
                                tool_name=tool_name,
                                arguments=tool_args,
                                risk_level=risk,
                                confirmed=False,
                                status="blocked",
                                details=sec_err,
                            )
                        obs_content = json.dumps({"ok": False, "status": "security_violation", "result": sec_err})
                        state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
                        continue

                # 2. Gestion via registry (si configuré, pour tests avec mock)
                if self.registry is not None and hasattr(self.registry, "get"):
                    route = self.registry.get(tool_name)
                    if route is None:
                        obs_content = json.dumps({"ok": False, "status": "unknown_tool", "result": f"Outil '{tool_name}' inconnu."})
                        state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
                        continue

                    # Validation des arguments requis
                    params_schema = getattr(route, "parameters", {}) or {}
                    required_params = params_schema.get("required") or []
                    missing = [p for p in required_params if p not in tool_args or tool_args[p] is None or tool_args[p] == ""]
                    if missing:
                        obs_content = json.dumps({"ok": False, "status": "invalid_arguments", "result": f"Arguments manquants : {missing}"})
                        state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
                        continue

                    # Contrôle des permissions de domaine
                    if self.security is not None and hasattr(self.security, "is_domain_allowed"):
                        if not self.security.is_domain_allowed(route.domain):
                            obs_content = json.dumps({"ok": False, "status": "domain_denied", "result": f"Permission refusee pour le domaine {route.domain}."})
                            state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
                            continue

                    # Contrôle des actions nécessitant confirmation
                    if self.security is not None and hasattr(self.security, "requires_confirmation"):
                        try:
                            needs_conf = self.security.requires_confirmation(route.name, tool_args)
                        except TypeError:
                            needs_conf = self.security.requires_confirmation(route.name)

                        if needs_conf:
                            logger.info("Confirmation requise pour l'outil sensible : %s", route.name)
                            pending = PendingAgentAction(
                                state=state,
                                tool_call=tc,
                                remaining_tool_calls=tool_calls[index + 1:],
                                tool_name=route.name,
                                arguments=tool_args,
                            )
                            if hasattr(self.security, "audit_log"):
                                risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(route.name, tool_args)
                                self.security.audit_log(
                                    tool_name=route.name,
                                    arguments=tool_args,
                                    risk_level=risk,
                                    confirmed=False,
                                    status="pending_confirmation",
                                    details="En attente de confirmation utilisateur",
                                )
                            try:
                                prompt_msg = self.security.confirmation_prompt(route.name, tool_args)
                            except TypeError:
                                prompt_msg = self.security.confirmation_prompt(route.name)
                            return AgentResult(
                                response=prompt_msg,
                                status="confirmation_required",
                                pending_action=pending,
                            )

                    # Exécution du handler du registry
                    state.tool_calls += 1
                    try:
                        handler_res = route.handler({"intent": tool_name, "target": tool_args.get("target", ""), "slots": tool_args})
                        obs_content = json.dumps({"ok": True, "status": "completed", "result": handler_res})
                        if hasattr(self.security, "audit_log"):
                            risk = getattr(self.security, "get_risk_level", lambda t, a: 2)(route.name, tool_args)
                            self.security.audit_log(
                                tool_name=route.name,
                                arguments=tool_args,
                                risk_level=risk,
                                confirmed=False,
                                status="allowed",
                            )
                    except Exception as e:
                        obs_content = json.dumps({"ok": False, "status": "failed", "result": str(e)})
                    state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
                    continue

                # 3. Exécution générale via execute_tool (route non gérée par registry)
                if self.security is not None and hasattr(self.security, "requires_confirmation"):
                    try:
                        needs_conf = self.security.requires_confirmation(tool_name, tool_args)
                    except TypeError:
                        needs_conf = self.security.requires_confirmation(tool_name)

                    if needs_conf:
                        logger.info("Confirmation requise pour l'outil sensible (direct) : %s", tool_name)
                        pending = PendingAgentAction(
                            state=state,
                            tool_call=tc,
                            remaining_tool_calls=tool_calls[index + 1:],
                            tool_name=tool_name,
                            arguments=tool_args,
                        )
                        if hasattr(self.security, "audit_log"):
                            risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(tool_name, tool_args)
                            self.security.audit_log(
                                tool_name=tool_name,
                                arguments=tool_args,
                                risk_level=risk,
                                confirmed=False,
                                status="pending_confirmation",
                                details="En attente de confirmation utilisateur",
                            )
                        try:
                            prompt_msg = self.security.confirmation_prompt(tool_name, tool_args)
                        except TypeError:
                            prompt_msg = self.security.confirmation_prompt(tool_name)
                        return AgentResult(
                            response=prompt_msg,
                            status="confirmation_required",
                            pending_action=pending,
                        )

                state.tool_calls += 1
                obs = execute_tool(tool_name, tool_args)
                logger.info("[OBSERVATION] %s", obs.get("message"))
                if hasattr(self.security, "audit_log"):
                    risk = getattr(self.security, "get_risk_level", lambda t, a: 1)(tool_name, tool_args)
                    self.security.audit_log(
                        tool_name=tool_name,
                        arguments=tool_args,
                        risk_level=risk,
                        confirmed=False,
                        status="allowed" if obs.get("success") else "failed",
                    )
                state.messages.append({
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": call_id,
                    "content": json.dumps(obs, ensure_ascii=False),
                })

        return AgentResult(response="Nombre maximal d'étapes atteint sans conclusion.", status="max_steps")

    def resume(
        self,
        pending_action: Any,
        *,
        confirmed: bool,
        fallback: Optional[Callable[[], str]] = None,
    ) -> AgentResult:
        """Reprend l'exécution après une demande de confirmation d'action sensible."""
        if not isinstance(pending_action, PendingAgentAction):
            return AgentResult("La confirmation agentique n'est plus valide.", status="invalid_confirmation")

        state = pending_action.state
        tool_name = pending_action.tool_name
        tool_args = pending_action.arguments
        call_id = pending_action.tool_call.get("id") or "call_unknown"

        if not confirmed:
            logger.info("Action '%s' refusée par l'utilisateur.", tool_name)
            if hasattr(self.security, "audit_log"):
                risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(tool_name, tool_args)
                self.security.audit_log(
                    tool_name=tool_name,
                    arguments=tool_args,
                    risk_level=risk,
                    confirmed=False,
                    status="blocked",
                    details="Action refusée par l'utilisateur",
                )
            state.messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": call_id,
                "content": json.dumps({"ok": False, "status": "refused", "result": "Action annulee par l'utilisateur."}),
            })
            return AgentResult(response="Action annulee.", status="refused")

        # Confirmation accordée : exécution de l'outil
        logger.info("Action '%s' confirmée. Exécution en cours...", tool_name)
        if hasattr(self.security, "audit_log"):
            risk = getattr(self.security, "get_risk_level", lambda t, a: 3)(tool_name, tool_args)
            self.security.audit_log(
                tool_name=tool_name,
                arguments=tool_args,
                risk_level=risk,
                confirmed=True,
                status="allowed",
                details="Action validée par confirmation utilisateur",
            )

        if self.registry is not None and hasattr(self.registry, "get"):
            route = self.registry.get(tool_name)
            if route is not None:
                try:
                    handler_res = route.handler({"intent": tool_name, "target": tool_args.get("target", ""), "slots": tool_args})
                    obs_content = json.dumps({"ok": True, "status": "completed", "result": handler_res})
                except Exception as e:
                    obs_content = json.dumps({"ok": False, "status": "failed", "result": str(e)})
                state.messages.append({"role": "tool", "name": tool_name, "tool_call_id": call_id, "content": obs_content})
        else:
            obs = execute_tool(tool_name, tool_args)
            state.messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": call_id,
                "content": json.dumps(obs, ensure_ascii=False),
            })

        # Exécuter les éventuels appels restants
        for remaining in pending_action.remaining_tool_calls:
            r_name = remaining["name"]
            r_args = remaining["arguments"]
            r_id = remaining["id"]
            obs_r = execute_tool(r_name, r_args)
            state.messages.append({
                "role": "tool",
                "name": r_name,
                "tool_call_id": r_id,
                "content": json.dumps(obs_r, ensure_ascii=False),
            })

        # Relancer un tour LLM pour conclure la réponse
        try:
            llm_response = self._call_llm(state.messages, tools=state.tools, think=state.thinking)
            content = llm_response.get("content") or "Action effectuée avec succès."
            return AgentResult(response=content.strip(), status="final")
        except Exception as exc:
            if fallback is not None:
                return AgentResult(response=fallback(), status="fallback", used_fallback=True)
            return AgentResult(response=f"Erreur après confirmation : {exc}", status="error")
