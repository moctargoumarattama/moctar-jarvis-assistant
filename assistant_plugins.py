from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

import config


def _object_parameters(properties=None, required=None):
    schema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _target_parameters(description, *, choices=None, required=True):
    target = {
        "type": "string",
        "description": description,
        "minLength": 1,
    }
    if choices:
        target["enum"] = list(choices)
    return _object_parameters(
        {"target": target},
        required=["target"] if required else None,
    )


@dataclass(frozen=True)
class PluginRoute:
    name: str
    description: str
    parameters: dict
    domain: str
    handler: Callable[[dict], str]
    ai_enabled: bool = False

    @property
    def intent(self):
        """Backward-compatible alias used by the historical intent engine."""
        return self.name

    def as_ollama_tool(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.parameters),
            },
        }


class PluginRegistry:
    def __init__(self):
        self._routes = {}

    def register(
        self,
        name,
        domain,
        handler,
        *,
        description="",
        parameters=None,
        ai_enabled=False,
    ):
        self._routes[name] = PluginRoute(
            name=name,
            description=(description or name.replace("_", " ")).strip(),
            parameters=deepcopy(parameters or _object_parameters()),
            domain=domain,
            handler=handler,
            ai_enabled=bool(ai_enabled),
        )

    def get(self, intent):
        return self._routes.get(intent)

    def dispatch(self, intent_data):
        route = self.get(intent_data["intent"])
        if route is None:
            return None
        return route.handler(intent_data)

    def ai_routes(self):
        return tuple(route for route in self._routes.values() if route.ai_enabled)

    def ollama_tools(self, domain_allowed=None):
        routes = self.ai_routes()
        if domain_allowed is not None:
            routes = tuple(route for route in routes if domain_allowed(route.domain))
        return [route.as_ollama_tool() for route in routes]


def build_default_registry(assistant):
    registry = PluginRegistry()
    safe_apps = tuple(name for name in config.APP_COMMANDS if name != "cmd")
    safe_folders = tuple(dict.fromkeys((*config.FOLDER_PATHS, *config.PROJECTS)))

    registry.register(
        "open_app",
        "system",
        assistant._handle_open_app,
        description="Ouvre une application installee et autorisee sur Windows.",
        parameters=_target_parameters("Nom canonique de l'application.", choices=safe_apps),
        ai_enabled=True,
    )
    registry.register("close_app", "system", assistant._handle_close_app)
    registry.register(
        "open_folder",
        "system",
        assistant._handle_open_folder,
        description="Ouvre un dossier local ou un projet preconfigure dans l'Explorateur Windows.",
        parameters=_target_parameters("Nom canonique du dossier ou du projet.", choices=safe_folders),
        ai_enabled=True,
    )
    registry.register(
        "time",
        "system",
        assistant._handle_time,
        description="Retourne l'heure locale actuelle de l'ordinateur.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register(
        "date",
        "system",
        assistant._handle_date,
        description="Retourne la date locale actuelle de l'ordinateur.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register(
        "battery",
        "system",
        assistant._handle_battery,
        description="Lit le niveau actuel de la batterie de l'ordinateur.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register(
        "get_system_telemetry",
        "system",
        assistant._handle_system_telemetry,
        description="Récupère les métriques matérielles en temps réel du serveur hôte : pourcentage CPU, température CPU, RAM utilisée/totale, et espace disque restant.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register("screenshot", "system", assistant._handle_screenshot)
    registry.register("analyze_screen", "system", assistant._handle_analyze_screen)
    registry.register("volume_up", "system", assistant._handle_volume_up)
    registry.register("volume_down", "system", assistant._handle_volume_down)
    registry.register("mute", "system", assistant._handle_mute)

    registry.register(
        "open_site",
        "web",
        assistant._handle_open_site,
        description="Ouvre un site Web connu dans le navigateur autorise.",
        parameters=_target_parameters("Nom canonique du site.", choices=tuple(config.IMPORTANT_URLS)),
        ai_enabled=True,
    )
    registry.register(
        "search_google",
        "web",
        assistant._handle_search_google,
        description="Lance une recherche Google dans le navigateur.",
        parameters=_target_parameters("Requete exacte a rechercher sur Google."),
        ai_enabled=True,
    )
    registry.register(
        "search_youtube",
        "web",
        assistant._handle_search_youtube,
        description="Lance une recherche YouTube dans le navigateur.",
        parameters=_target_parameters("Requete exacte a rechercher sur YouTube."),
        ai_enabled=True,
    )
    registry.register("playlist", "music", assistant._handle_playlist)
    registry.register("play_music", "music", assistant._handle_play_music)
    registry.register("music_pause", "music", assistant._handle_music_control)
    registry.register("music_resume", "music", assistant._handle_music_control)
    registry.register("music_next", "music", assistant._handle_music_control)
    registry.register("music_previous", "music", assistant._handle_music_control)

    registry.register("open_project", "project", assistant._handle_open_project)
    registry.register("launch_project_server", "project", assistant._handle_launch_project_server)
    registry.register(
        "git_status",
        "project",
        assistant._handle_git_status,
        description="Lit l'etat Git du projet actif ou d'un projet configure, sans modifier le depot.",
        parameters=_target_parameters(
            "Cle optionnelle du projet configure.",
            choices=tuple(config.PROJECTS),
            required=False,
        ),
        ai_enabled=True,
    )
    registry.register("git_pull", "project", assistant._handle_git_pull)
    registry.register(
        "git_log",
        "project",
        assistant._handle_git_log,
        description="Lit les cinq derniers commits du projet actif ou d'un projet configure.",
        parameters=_target_parameters(
            "Cle optionnelle du projet configure.",
            choices=tuple(config.PROJECTS),
            required=False,
        ),
        ai_enabled=True,
    )
    registry.register("git_commit_prepare", "project", assistant._handle_git_commit_prepare)
    registry.register("git_push_prepare", "project", assistant._handle_git_push_prepare)

    registry.register("create_note", "personal", assistant._handle_create_note)
    registry.register("add_todo", "personal", assistant._handle_add_todo)
    registry.register(
        "list_todos",
        "personal",
        assistant._handle_list_todos,
        description="Liste les todos locaux actuels de l'utilisateur.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register("remind_me", "personal", assistant._handle_remind_me)
    registry.register(
        "summarize_file",
        "personal",
        assistant._handle_summarize_file,
        description="Lit et resume un fichier local non sensible indique par l'utilisateur.",
        parameters=_target_parameters("Chemin relatif ou absolu du fichier a resumer."),
        ai_enabled=True,
    )
    registry.register(
        "search_personal",
        "personal",
        assistant._handle_search_personal,
        description="Recherche une information dans les notes et todos locaux.",
        parameters=_target_parameters("Mots a rechercher dans les donnees personnelles locales."),
        ai_enabled=True,
    )
    registry.register("daily_brief", "local_ai", assistant._handle_daily_brief)
    registry.register("next_action", "local_ai", assistant._handle_next_action)
    registry.register("prioritize_tasks", "local_ai", assistant._handle_prioritize_tasks)
    registry.register("focus_mode", "local_ai", assistant._handle_focus_mode)
    registry.register("routine_morning", "local_ai", assistant._handle_routine_morning)
    registry.register("routine_evening", "local_ai", assistant._handle_routine_evening)
    registry.register("assistant_capabilities", "local_ai", assistant._handle_capabilities)

    registry.register("energy_consumption", "energy", assistant._handle_energy_plugin)
    registry.register("energy_cost", "energy", assistant._handle_energy_plugin)
    registry.register("solar_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("battery_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("inverter_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("energy_audit_template", "energy", assistant._handle_energy_plugin)

    registry.register("open_scheduler", "personal", assistant._handle_open_scheduler)
    registry.register("open_history", "personal", assistant._handle_open_history)

    registry.register(
        "iot_status",
        "iot",
        assistant._handle_iot_status,
        description="Lit l'etat reel actuel du systeme IoT configure.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register(
        "iot_temperature",
        "iot",
        assistant._handle_iot_temperature,
        description="Lit la temperature reelle actuelle du systeme IoT configure.",
        parameters=_object_parameters(),
        ai_enabled=True,
    )
    registry.register("iot_relay_on", "iot", assistant._handle_iot_relay_on)
    registry.register("iot_relay_off", "iot", assistant._handle_iot_relay_off)

    registry.register("chat_fallback", "local_ai", assistant._handle_local_chat_fallback)
    return registry
