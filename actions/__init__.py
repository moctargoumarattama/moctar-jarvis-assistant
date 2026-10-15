"""Hub central d'actions et catalogue Function Calling pour JARVIS.

Fournit l'enregistrement centralisé, le dispatching sécurisé et la génération
automatique des schémas JSON d'outils (compatibles Google GenAI) pour l'ensemble
des 7 modules opérationnels du système :
1. energy_actions (consommation, solaire, audit, dimensionnement)
2. iot_actions (relais, sondes, télémétrie locale)
3. system_actions (commandes OS, volume, applications, captures)
4. web_actions (recherche web, navigation, youtube)
5. personal_actions (notes, agenda, rappels, to-do list)
6. project_actions (gestion git, statuts de dev, serveurs)
7. music_actions (contrôle multimédia, streaming)
"""

import inspect
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, get_args, get_origin

# Base model for standardized output
from .base import ActionResult, format_action_result

# 1. Energy Actions
from .energy_actions import (
    calculate_battery_sizing,
    calculate_consumption,
    calculate_energy_cost,
    calculate_inverter_sizing,
    calculate_solar_sizing,
    describe_formulae,
    generate_audit_template,
)

# 2. IoT Actions
from .iot_actions import (
    get_iot_status,
    get_iot_temperature,
    set_relay_state,
)

# 3. System Actions
from .system_actions import (
    analyze_screen,
    capture_screenshot,
    change_volume,
    close_app,
    get_battery_response,
    get_date_response,
    get_system_telemetry,
    get_time_response,
    open_app,
    open_folder,
)

# 4. Web Actions
from .web_actions import (
    open_site,
    open_url_smart,
    search_google,
    search_youtube,
)

# 5. Music Actions
from .music_actions import (
    control_music,
    open_playlist,
    play_music,
)

# 6. Personal Actions
from .personal_actions import (
    add_todo,
    create_note,
    get_upcoming_reminders,
    list_todos,
    list_todos_for_day,
    schedule_reminder,
    search_memory_facts,
    search_personal_data,
    store_memory_fact,
    summarize_text_file,
)

# 7. Project Actions
from .project_actions import (
    git_log,
    git_pull,
    git_status,
    launch_project_server,
    maintenance_checklist,
    open_project,
    open_project_in_vscode,
    prepare_commit,
    prepare_push,
    summarize_tabular_file,
)

logger = logging.getLogger("jarvis.actions")

# Paramètres techniques internes utilisés pour l'injection de dépendances (à masquer du schéma LLM)
_INTERNAL_INJECTION_PARAMS = {
    "battery_provider",
    "browser_module",
    "browser_opener",
    "launcher",
    "now",
    "now_provider",
    "process_provider",
    "pyautogui_module",
    "recommender",
}


# ==============================================================================
# TOOL DISPATCHER CENTRAL
# ==============================================================================

TOOL_DISPATCHER: Dict[str, Callable[..., Any]] = {
    # IoT Actions
    "get_iot_status": get_iot_status,
    "get_iot_temperature": get_iot_temperature,
    "set_relay_state": set_relay_state,

    # System Actions
    "get_time_response": get_time_response,
    "get_date_response": get_date_response,
    "get_battery_response": get_battery_response,
    "get_system_telemetry": get_system_telemetry,
    "capture_screenshot": capture_screenshot,
    "analyze_screen": analyze_screen,
    "change_volume": change_volume,
    "open_app": open_app,
    "close_app": close_app,
    "open_folder": open_folder,

    # Web Actions
    "open_site": open_site,
    "open_url_smart": open_url_smart,
    "search_google": search_google,
    "search_youtube": search_youtube,

    # Music Actions
    "play_music": play_music,
    "control_music": control_music,
    "open_playlist": open_playlist,

    # Energy Actions
    "calculate_consumption": calculate_consumption,
    "calculate_energy_cost": calculate_energy_cost,
    "calculate_solar_sizing": calculate_solar_sizing,
    "calculate_battery_sizing": calculate_battery_sizing,
    "calculate_inverter_sizing": calculate_inverter_sizing,
    "generate_audit_template": generate_audit_template,
    "describe_formulae": describe_formulae,

    # Personal Actions
    "create_note": create_note,
    "add_todo": add_todo,
    "list_todos": list_todos,
    "list_todos_for_day": list_todos_for_day,
    "search_personal_data": search_personal_data,
    "schedule_reminder": schedule_reminder,
    "get_upcoming_reminders": get_upcoming_reminders,
    "summarize_text_file": summarize_text_file,
    "store_memory_fact": store_memory_fact,
    "search_memory_facts": search_memory_facts,

    # Project Actions
    "open_project": open_project,
    "open_project_in_vscode": open_project_in_vscode,
    "launch_project_server": launch_project_server,
    "git_status": git_status,
    "git_pull": git_pull,
    "git_log": git_log,
    "prepare_commit": prepare_commit,
    "prepare_push": prepare_push,
    "maintenance_checklist": maintenance_checklist,
    "summarize_tabular_file": summarize_tabular_file,
}


# ==============================================================================
# GÉNÉRATION AUTOMATIQUE DES SCHÉMAS FUNCTION CALLING (GOOGLE GENAI)
# ==============================================================================

def _parse_docstring_sections(doc: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Parse une docstring Google pour extraire la description globale et la doc des arguments."""
    if not doc:
        return "", {}

    # Extraction de la description (tout ce qui précède les sections de métadonnées)
    sections = re.split(
        r"\n\s*(?:Args|Arguments|Parameters|Returns|Raises|Yields|Examples?):\s*\n",
        doc,
        maxsplit=1,
    )
    description = sections[0].strip()

    # Extraction des arguments de la section Args:
    arg_descriptions: Dict[str, str] = {}
    args_match = re.search(
        r"\n\s*(?:Args|Arguments|Parameters):\s*\n(.*?)(?=\n\s*(?:Returns|Raises|Yields|Examples?):\s*\n|\Z)",
        doc,
        re.DOTALL,
    )
    if args_match:
        arg_block = args_match.group(1)
        current_arg: Optional[str] = None
        current_desc: List[str] = []

        for line in arg_block.splitlines():
            arg_line_match = re.match(
                r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\([^)]*\))?\s*:\s*(.*)$", line
            )
            if arg_line_match:
                if current_arg:
                    arg_descriptions[current_arg] = " ".join(current_desc).strip()
                current_arg = arg_line_match.group(1)
                current_desc = [arg_line_match.group(2).strip()]
            elif current_arg and line.strip():
                current_desc.append(line.strip())

        if current_arg:
            arg_descriptions[current_arg] = " ".join(current_desc).strip()

    return description, arg_descriptions


def _python_type_to_json_schema(type_hint: Any) -> Dict[str, Any]:
    """Convertit une annotation de type Python en définition JSON Schema."""
    if type_hint is Any or type_hint is inspect.Parameter.empty:
        return {"type": "string"}

    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Gestion de Union / Optional[T]
    if origin is Union:
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1:
            return _python_type_to_json_schema(non_none_args[0])
        return {"type": "string"}

    # Types scalaires primitifs
    if type_hint is int:
        return {"type": "integer"}
    if type_hint is float:
        return {"type": "number"}
    if type_hint is bool:
        return {"type": "boolean"}
    if type_hint is str:
        return {"type": "string"}

    # Types composites
    if type_hint in (list, List) or origin in (list, list):
        item_type = args[0] if args else Any
        return {"type": "array", "items": _python_type_to_json_schema(item_type)}

    if type_hint in (dict, Dict) or origin in (dict, dict):
        return {"type": "object"}

    return {"type": "string"}


def function_to_json_schema(
    func: Callable[..., Any], name: Optional[str] = None
) -> Dict[str, Any]:
    """Convertit une fonction Python en schéma Function Calling standard JSON Schema / Google GenAI.

    Args:
        func (Callable[..., Any]): La fonction Python inspectée.
        name (Optional[str]): Nom de l'outil (par défaut func.__name__).

    Returns:
        Dict[str, Any]: Dictionnaire JSON Schema conforme au standard :
            {
                "type": "function",
                "function": {
                    "name": ...,
                    "description": ...,
                    "parameters": {
                        "type": "object",
                        "properties": { ... },
                        "required": [ ... ]
                    }
                }
            }
    """
    tool_name = name or func.__name__
    raw_doc = inspect.getdoc(func) or ""
    description, arg_descriptions = _parse_docstring_sections(raw_doc)

    sig = inspect.signature(func)
    try:
        type_hints = inspect.get_annotations(func) if hasattr(inspect, "get_annotations") else getattr(func, "__annotations__", {})
    except Exception:
        type_hints = {}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        # Ignorer les arguments techniques ou variadiques
        if param_name.startswith("_") or param_name in _INTERNAL_INJECTION_PARAMS:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        param_hint = type_hints.get(param_name, inspect.Parameter.empty)
        param_schema = _python_type_to_json_schema(param_hint)

        # Enrichissement avec la description issue de la docstring Google
        if param_name in arg_descriptions:
            param_schema["description"] = arg_descriptions[param_name]
        else:
            param_schema["description"] = f"Paramètre {param_name}"

        # Détermination du caractère obligatoire (required)
        is_optional = False
        if get_origin(param_hint) is Union:
            if type(None) in get_args(param_hint):
                is_optional = True

        if param.default is inspect.Parameter.empty and not is_optional:
            required.append(param_name)
        elif param.default is not inspect.Parameter.empty and param.default is not None:
            try:
                json.dumps(param.default)
                param_schema["default"] = param.default
            except (TypeError, ValueError):
                pass

        properties[param_name] = param_schema

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or f"Exécute l'action Jarvis '{tool_name}'.",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def get_all_tools_schema() -> List[Dict[str, Any]]:
    """Génère et exporte automatiquement la liste des définitions JSON Schema au standard JSON Schema / Google GenAI.

    Returns:
        List[Dict[str, Any]]: Liste des schémas d'outils disponibles pour le modèle.
    """
    schemas: List[Dict[str, Any]] = []
    for tool_name, func in TOOL_DISPATCHER.items():
        schemas.append(function_to_json_schema(func, name=tool_name))
    return schemas


# ==============================================================================
# ROUTEUR ET EXÉCUTEUR SÉCURISÉ D'OUTILS
# ==============================================================================

def execute_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Valide les arguments, route l'exécution vers la fonction cible et capture toute anomalie.

    Garantit qu'aucun crash ou exception non capturée ne remonte au LLM et retourne
    toujours la structure standardisée :
    {
        "success": bool,
        "action": str,
        "message": str,
        "data": dict | list | str | int | float | None,
        "error": str | None
    }

    Args:
        tool_name (str): Nom exact de l'outil à exécuter dans TOOL_DISPATCHER.
        arguments (Optional[Dict[str, Any]]): Dictionnaire des paramètres fournis par le LLM.

    Returns:
        Dict[str, Any]: Résultat standardisé et JSON-sérialisable de l'outil.
    """
    if tool_name not in TOOL_DISPATCHER:
        error_msg = f"Outil '{tool_name}' non reconnu. Outils valides : {list(TOOL_DISPATCHER.keys())[:10]}..."
        logger.warning("execute_tool: tool not found: %s", tool_name)
        return format_action_result(
            action=tool_name,
            success=False,
            message=error_msg,
            data=None,
            error=f"ToolNotFound: '{tool_name}'",
        )

    target_func = TOOL_DISPATCHER[tool_name]

    # Normalisation des arguments
    raw_args = arguments
    if raw_args is None:
        raw_args = {}
    elif isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except Exception as exc:
            return format_action_result(
                action=tool_name,
                success=False,
                message=f"Arguments JSON invalides reçus pour '{tool_name}' : {exc}",
                data=None,
                error=f"InvalidJSONArguments: {exc}",
            )

    if not isinstance(raw_args, dict):
        return format_action_result(
            action=tool_name,
            success=False,
            message=f"Format d'arguments invalide : un objet dict est attendu, reçu {type(raw_args).__name__}.",
            data=None,
            error="TypeError: arguments must be a dictionary",
        )

    # Normalisation et aliasing intelligent des arguments pour petits modèles
    if "target" not in raw_args:
        for alias in ("url", "site", "query", "name", "app", "target_url"):
            if alias in raw_args and raw_args[alias]:
                raw_args["target"] = raw_args[alias]
                break
    if "query" not in raw_args:
        for alias in ("target", "name", "terme", "recherche"):
            if alias in raw_args and raw_args[alias]:
                raw_args["query"] = raw_args[alias]
                break

    # Inspection de la signature pour validation
    sig = inspect.signature(target_func)
    filtered_kwargs: Dict[str, Any] = {}
    missing_required: List[str] = []

    for param_name, param in sig.parameters.items():
        if param_name.startswith("_") or param_name in _INTERNAL_INJECTION_PARAMS:
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        if param_name in raw_args:
            val = raw_args[param_name]
            # Coercion de type basique si nécessaire (chaîne/float -> int/float/bool)
            if param.annotation is int:
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    val = int(round(val))
                elif isinstance(val, str):
                    try:
                        val = int(round(float(val.strip())))
                    except (ValueError, TypeError):
                        pass
            elif param.annotation is float:
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    val = float(val)
                elif isinstance(val, str):
                    try:
                        val = float(val.strip())
                    except (ValueError, TypeError):
                        pass
            elif param.annotation is bool:
                if isinstance(val, str):
                    val = val.strip().lower() in ("true", "1", "yes", "oui")
                elif isinstance(val, (int, float)):
                    val = bool(val)

            filtered_kwargs[param_name] = val
        elif param.default is inspect.Parameter.empty:
            missing_required.append(param_name)

    if missing_required:
        return format_action_result(
            action=tool_name,
            success=False,
            message=f"Arguments obligatoires manquants pour '{tool_name}' : {', '.join(missing_required)}.",
            data={"missing_arguments": missing_required},
            error=f"MissingRequiredArguments: {missing_required}",
        )

    # Exécution sécurisée
    try:
        raw_result = target_func(**filtered_kwargs)

        # Si la fonction retourne déjà un ActionResult ou un dict standardisé
        if isinstance(raw_result, dict) and "success" in raw_result and "action" in raw_result:
            return raw_result

        # Cas spécifique open_site retournant une chaîne historique
        if tool_name == "open_site":
            from actions.web_actions import resolve_site_url
            target_str = filtered_kwargs.get("target") or filtered_kwargs.get("url") or filtered_kwargs.get("site") or filtered_kwargs.get("query") or ""
            target_url, title = resolve_site_url(target_str)
            return format_action_result(
                action=tool_name,
                success=True,
                message=f"🌐 Ouverture de {title} ({target_url}) dans le navigateur.",
                data={"action": "open_browser", "url": target_url, "title": title, "result": str(raw_result)},
                error=None,
            )

        # Encapsulation automatique pour les fonctions historiques
        return format_action_result(
            action=tool_name,
            success=True,
            message=str(raw_result),
            data=raw_result,
            error=None,
        )

    except Exception as exc:
        logger.exception("Erreur lors de l'exécution de l'outil %s", tool_name)
        return format_action_result(
            action=tool_name,
            success=False,
            message=f"Erreur d'exécution inattendue dans l'outil '{tool_name}' : {str(exc)}",
            data=None,
            error=str(exc),
        )


__all__ = [
    # Hub et outils
    "TOOL_DISPATCHER",
    "get_all_tools_schema",
    "execute_tool",
    "function_to_json_schema",
    "ActionResult",
    "format_action_result",
    # Modules
    "energy_actions",
    "iot_actions",
    "music_actions",
    "personal_actions",
    "project_actions",
    "system_actions",
    "web_actions",
]
