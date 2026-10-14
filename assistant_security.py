"""Moteur de contrôle d'accès, classification des risques et pare-feu de sécurité pour JARVIS.

Fournit :
1. Une classification rigoureuse des actions en 3 Tiers (Lecture seule, Modéré, Critique/Physique).
2. Un moteur d'évaluation dynamique du risque et de détection préventive d'injections (SecurityPolicyEngine).
3. Un mécanisme de confirmation explicite Human-in-the-Loop pour toute opération sensible (Tier 3).
4. Un journal d'audit de sécurité local inviolable (JSONL) avec masquage automatique des identifiants et secrets.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import config

logger = logging.getLogger("jarvis.security")

# ==============================================================================
# CLASSIFICATION STATIQUE DES ACTIONS PAR NIVEAU DE RISQUE (3 TIERS)
# ==============================================================================

# Tier 1 : Lecture seule / Inoffensif (Exécution automatique sans confirmation)
TIER_1_READ_ONLY: Set[str] = {
    # Système & OS
    "get_time_response",
    "time",
    "get_system_metrics",
    "system_metrics",
    "read_file",
    "list_files",
    "get_process_list",
    "get_disk_usage",
    "get_network_info",
    "read_notes",
    "read_personal_file",
    "capture_screenshot",
    "analyze_screen",
    # IoT & Domotique
    "get_iot_status",
    "iot_status",
    "get_relay_states",
    "get_sensor_data",
    "get_temperature",
    "iot_temperature",
    "iot_sensors",
    # Énergie & Solaire
    "get_energy_consumption",
    "energy_consumption",
    "get_solar_production",
    "solar_status",
    "calculate_consumption",
    "calculate_solar_roi",
    # Web & Recherche
    "fetch_web_page",
    "search_google",
    "http_request",
    "scrape_url",
    # Personnel
    "list_todos",
    "list_todos_for_day",
    "search_personal_data",
    "get_open_todos",
    "get_upcoming_reminders",
    "poll_due_reminders",
    # Projets & Git
    "git_status",
    "git_diff",
    "list_projects",
    "get_project_summary",
    "git_log",
    # Multimédia
    "get_playback_status",
    "list_playlists",
    "get_volume",
    # Mémoire
    "search_memory_facts",
}

# Tier 2 : Actions modérées / Réversibles (Exécution automatique avec journalisation)
TIER_2_MODERATE_ACTIONS: Set[str] = {
    # Personnel
    "create_note",
    "add_todo",
    "schedule_reminder",
    "delete_todo",
    "store_memory_fact",
    # Multimédia
    "play_music",
    "pause_music",
    "stop_music",
    "next_track",
    "previous_track",
    "set_volume",
    # Projets
    "create_branch",
    "git_commit",
    "open_project",
    # Système & Navigation
    "write_file",
    "open_app",
    "open_site",
    "open_folder",
}

# Tier 3 : Actions critiques / Physiques / Destructrices (Interception bloquante obligatoire)
TIER_3_CRITICAL_ACTIONS: Set[str] = {
    # IoT physique & Relais de puissance
    "set_relay_state",
    "toggle_relay",
    "iot_relay_on",
    "iot_relay_off",
    "emergency_shutdown",
    "cut_power",
    "control_relay",
    # Exécution de commandes et altérations OS
    "run_bash",
    "run_shell",
    "execute_command",
    "restart_service",
    "stop_service",
    "kill_process",
    "reboot_system",
    "shutdown_system",
    "delete_file",
    "remove_directory",
    "close_app",
    # Déploiements & modifications distantes Git
    "launch_project_server",
    "git_push",
    "git_pull",
    "git_push_prepare",
    "git_reset",
    "delete_project",
}

# Ensemble historique pour rétrocompatibilité
SENSITIVE_INTENTS: Set[str] = set(TIER_3_CRITICAL_ACTIONS)

# ==============================================================================
# MOTIFS DANGEREUX POUR DÉTECTION D'INJECTIONS & SANITIZATION
# ==============================================================================

# Marqueurs de clés sensibles pour le masquage dans le journal d'audit
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "auth",
    "private_key",
)

# Motifs de commandes destructrices (bloqués immédiatement ou promus en Tier 3)
_DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"\brm\s+-[rRfF]{1,3}\b", re.IGNORECASE),
    re.compile(r"\b(format|mkfs|mkfs\.\w+)\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE),  # Fork bomb
    re.compile(r"\b(shutdown|reboot|poweroff|init\s+[06])\b", re.IGNORECASE),
    re.compile(r"\bkill\s+-9\b", re.IGNORECASE),
    re.compile(r"\b(del\s+/[fFqQsS]|rmdir\s+/[sS])\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(database|table)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+(-R\s+)?777\s+/", re.IGNORECASE),
]

# Fichiers système et secrets hautement sensibles
_SENSITIVE_PATH_PATTERNS = [
    re.compile(r"/etc/(shadow|passwd|sudoers)", re.IGNORECASE),
    re.compile(r"(~|\$HOME|\%USERPROFILE\%)/\.ssh/(id_rsa|id_ed25519|authorized_keys)", re.IGNORECASE),
    re.compile(r"(\%SystemRoot\%|C:\\Windows)\\System32\\config\\SAM", re.IGNORECASE),
]

# Opérateurs d'injection et de chaînage shell dans les arguments non prévus pour cela
_COMMAND_CHAINING_PATTERNS = [
    re.compile(r"[;&|`$]"),
    re.compile(r"\$\(.*\)"),
    re.compile(r"`.*`"),
    re.compile(r">\s*/"),
]


def _sanitize_for_audit(data: Any) -> Any:
    """Masque récursivement les clés et tokens sensibles pour le journal d'audit."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(marker in k_lower for marker in _SECRET_KEY_MARKERS):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = _sanitize_for_audit(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_for_audit(item) for item in data]
    elif isinstance(data, str):
        val = re.sub(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}", r"\1***REDACTED***", data)
        val = re.sub(r"(sk-[a-zA-Z0-9]{20,})", r"***REDACTED***", val)
        val = re.sub(r"(--password[=\s]+)[^\s]+", r"\1***REDACTED***", val, flags=re.IGNORECASE)
        return val
    return data


# ==============================================================================
# MOTEUR PRINCIPAL DE POLITIQUE DE SÉCURITÉ (SECURITY POLICY ENGINE)
# ==============================================================================

class SecurityPolicyEngine:
    """Moteur centralisé de contrôle d'accès, d'assainissement et d'audit de sécurité."""

    def __init__(
        self,
        settings: Optional[Dict[str, Any]] = None,
        log_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.settings = settings if settings is not None else getattr(config, "SECURITY_SETTINGS", {})
        if log_path is not None:
            self.log_path = Path(log_path)
        else:
            data_dir = getattr(config, "DATA_DIR", Path(__file__).resolve().parent / "data")
            self.log_path = Path(data_dir) / "security_audit.log"

    # --------------------------------------------------------------------------
    # CONTRÔLE D'ACCÈS PAR DOMAINE
    # --------------------------------------------------------------------------

    def is_domain_allowed(self, domain: str) -> bool:
        """Vérifie si les actions appartenant à un domaine fonctionnel sont autorisées.

        Args:
            domain: Nom du domaine ('system', 'iot', 'energy', 'web', 'personal', 'project', 'music').

        Returns:
            bool: True si autorisé, False si explicitement désactivé.
        """
        if not domain:
            return True
        domain_clean = str(domain).strip().lower()
        permissions = self.settings.get("domain_permissions", {})
        return bool(permissions.get(domain_clean, True))

    # --------------------------------------------------------------------------
    # ÉVALUATION DU NIVEAU DE RISQUE (1, 2 OU 3)
    # --------------------------------------------------------------------------

    def get_risk_level(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Évalue dynamiquement le niveau de risque d'un outil et de ses arguments.

        Tiers :
        - 1 : Lecture seule / Inoffensif
        - 2 : Action modérée réversible
        - 3 : Action critique / Physique / Destructrice

        Args:
            tool_name: Nom de la fonction ou de l'action.
            arguments: Dictionnaire des paramètres fournis.

        Returns:
            int: 1, 2 ou 3.
        """
        tool_clean = tool_name.strip().lower()

        # 1. Classification de base
        if tool_clean in TIER_3_CRITICAL_ACTIONS or tool_clean in SENSITIVE_INTENTS:
            base_risk = 3
        elif tool_clean in TIER_2_MODERATE_ACTIONS:
            base_risk = 2
        elif tool_clean in TIER_1_READ_ONLY:
            base_risk = 1
        else:
            # Heuristique d'inférence sémantique par préfixe
            if any(tool_clean.startswith(p) for p in ("get_", "read_", "list_", "search_", "calculate_", "check_", "show_", "fetch_")):
                base_risk = 1
            elif any(tool_clean.startswith(p) for p in ("set_relay", "run_", "exec_", "kill_", "delete_", "remove_", "restart_", "stop_", "reboot_", "shutdown_", "cut_")):
                base_risk = 3
            else:
                base_risk = 2

        # 2. Promotion dynamique du risque si les arguments contiennent des motifs dangereux
        if arguments and isinstance(arguments, dict):
            for key, val in arguments.items():
                val_str = str(val or "")
                # Présence de commandes destructrices
                if any(pat.search(val_str) for pat in _DESTRUCTIVE_COMMAND_PATTERNS):
                    return 3
                # Présence de chemins système critiques
                if any(pat.search(val_str) for pat in _SENSITIVE_PATH_PATTERNS):
                    return 3

        return base_risk

    # --------------------------------------------------------------------------
    # ASSAINISSEMENT ET DÉTECTION D'INJECTIONS
    # --------------------------------------------------------------------------

    def validate_arguments(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
    ) -> Tuple[bool, Optional[str]]:
        """Assainit et valide préventivement les arguments pour empêcher les injections.

        Args:
            tool_name: Nom de l'outil ciblé.
            arguments: Paramètres passés à l'outil.

        Returns:
            Tuple[bool, Optional[str]]: (True, None) si valide, (False, "Raison du blocage") sinon.
        """
        if not arguments or not isinstance(arguments, dict):
            return True, None

        tool_clean = tool_name.strip().lower()

        for param_name, param_val in arguments.items():
            if param_val is None:
                continue

            val_str = str(param_val)

            # 1. Détection de null bytes
            if "\x00" in val_str:
                return False, f"Caractère nul (null-byte) interdit détecté dans l'argument '{param_name}'."

            # 2. Détection de Path Traversal
            if param_name in ("file_path", "filename", "path", "source", "destination", "target", "file"):
                if "../" in val_str or "..\\" in val_str or re.search(r"(^|[/\\])\.\.([/\\]|$)", val_str):
                    return False, f"Tentative de traversée de répertoire (path traversal) détectée dans '{param_name}'."

            # 3. Détection de commandes destructrices majeures
            for pat in _DESTRUCTIVE_COMMAND_PATTERNS:
                if pat.search(val_str):
                    return False, f"Commande destructrice interdite détectée dans '{param_name}' : motif '{pat.pattern}'."

            # 4. Détection de ciblage de fichiers système sensibles
            for pat in _SENSITIVE_PATH_PATTERNS:
                if pat.search(val_str):
                    return False, f"Accès non autorisé à un fichier système sensible détecté dans '{param_name}'."

            # 5. Détection d'injection de chaînage shell dans les arguments non destinés au shell
            if tool_clean not in ("run_bash", "run_shell", "execute_command"):
                # Pour les outils ordinaires (open_app, open_site, read_file, kill_process, etc.)
                if param_name in ("target", "app_name", "url", "file_path", "process_name", "service_name", "path", "command"):
                    for chain_pat in _COMMAND_CHAINING_PATTERNS:
                        if chain_pat.search(val_str):
                            return False, f"Opérateur de chaînage ou d'injection shell interdit détecté dans '{param_name}' : '{val_str}'."

        return True, None

    # --------------------------------------------------------------------------
    # HUMAN-IN-THE-LOOP (CONFIRMATION REQUISE & PROMPT EXPLICITE)
    # --------------------------------------------------------------------------

    def requires_confirmation(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Détermine si l'outil nécessite une approbation humaine explicite avant exécution.

        Args:
            tool_name: Nom de l'action ou intent.
            arguments: Arguments associés à l'action.

        Returns:
            bool: True si confirmation obligatoire, False sinon.
        """
        if not self.settings.get("require_confirmation_for_sensitive", True):
            return False

        # Vérification du niveau de risque dynamique
        risk = self.get_risk_level(tool_name, arguments)
        if risk >= 3:
            return True

        tool_clean = tool_name.strip().lower()
        return tool_clean in SENSITIVE_INTENTS or tool_clean in TIER_3_CRITICAL_ACTIONS

    @classmethod
    def confirmation_prompt(
        cls,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Formule une demande de confirmation explicite en français décrivant l'action.

        Args:
            tool_name: Nom de l'action sensible.
            arguments: Paramètres de l'action.

        Returns:
            str: Message de confirmation adressé à l'utilisateur.
        """
        args = arguments or {}
        tool_clean = tool_name.strip().lower()

        # IoT et relais physiques
        if "relay" in tool_clean:
            relay_id = args.get("relay_id") or args.get("target") or "inconnu"
            state = args.get("state") or ("ON" if "on" in tool_clean else "OFF")
            return (
                f"Action critique IoT : Commutation du relais '{relay_id}' vers l'état '{state}'. "
                f"Confirme cette action sensible: {tool_name}. Dis 'oui' pour confirmer ou 'non' pour annuler."
            )

        # Processus et services
        if tool_clean in ("kill_process", "restart_service", "stop_service", "close_app"):
            target = args.get("target") or args.get("pid") or args.get("service_name") or "inconnu"
            return (
                f"Action critique Système : {tool_name} sur '{target}'. "
                f"Confirme cette action sensible: {tool_name}. Dis 'oui' pour confirmer ou 'non' pour annuler."
            )

        # Exécution de commandes shell
        if tool_clean in ("run_bash", "run_shell", "execute_command"):
            cmd = args.get("command") or args.get("cmd") or "inconnue"
            return (
                f"Action critique Shell : Exécution de la commande '{cmd}'. "
                f"Confirme cette action sensible: {tool_name}. Dis 'oui' pour confirmer ou 'non' pour annuler."
            )

        # Git et projets
        if tool_clean in ("launch_project_server", "git_push", "git_pull", "git_reset"):
            target = args.get("target") or args.get("project_name") or args.get("remote") or ""
            target_str = f" pour '{target}'" if target else ""
            return (
                f"Action sensible Projet : {tool_name}{target_str}. "
                f"Confirme cette action sensible: {tool_name}. Dis 'oui' pour confirmer ou 'non' pour annuler."
            )

        # Formulation générique robuste
        if args:
            sanitized_args = _sanitize_for_audit(args)
            return f"Confirme cette action sensible: {tool_name} avec les paramètres {sanitized_args}. Dis 'oui' pour confirmer ou 'non' pour annuler."

        return f"Confirme cette action sensible: {tool_name}. Dis 'oui' pour confirmer ou 'non' pour annuler."

    # --------------------------------------------------------------------------
    # JOURNAL D'AUDIT DE SÉCURITÉ LOCAL (JSONL)
    # --------------------------------------------------------------------------

    def audit_log(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        risk_level: int = 1,
        confirmed: bool = False,
        status: str = "allowed",
        details: Optional[str] = None,
    ) -> None:
        """Enregistre un événement structuré dans le journal d'audit de sécurité local.

        Args:
            tool_name: Nom de l'outil invoqué.
            arguments: Paramètres de l'appel (masqués automatiquement).
            risk_level: Niveau de risque évalué (1, 2 ou 3).
            confirmed: Si l'action a été confirmée par l'utilisateur.
            status: 'allowed', 'blocked', ou 'pending_confirmation'.
            details: Informations contextuelles complémentaires.
        """
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "tool": tool_name,
            "arguments": _sanitize_for_audit(arguments or {}),
            "risk_level": int(risk_level),
            "confirmed": bool(confirmed),
            "status": str(status),
        }
        if details:
            entry["details"] = str(details)

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Erreur lors de l'écriture dans le journal de sécurité %s : %s", self.log_path, exc)

    def get_audit_logs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Lit et retourne les entrées du journal d'audit de sécurité.

        Args:
            limit: Nombre maximal d'entrées récentes à retourner.

        Returns:
            List[Dict[str, Any]]: Liste des événements audités.
        """
        if not self.log_path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_clean = line.strip()
                    if line_clean:
                        entries.append(json.loads(line_clean))
        except Exception as exc:
            logger.error("Erreur lors de la lecture du journal d'audit : %s", exc)
            return []

        if limit is not None and limit > 0:
            return entries[-limit:]
        return entries


# ==============================================================================
# ALIAS POUR RÉTROCOMPATIBILITÉ TOTALE
# ==============================================================================
SecurityPolicy = SecurityPolicyEngine
