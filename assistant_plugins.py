from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PluginRoute:
    intent: str
    domain: str
    handler: Callable[[dict], str]


class PluginRegistry:
    def __init__(self):
        self._routes = {}

    def register(self, intent, domain, handler):
        self._routes[intent] = PluginRoute(
            intent=intent,
            domain=domain,
            handler=handler,
        )

    def get(self, intent):
        return self._routes.get(intent)

    def dispatch(self, intent_data):
        route = self.get(intent_data["intent"])
        if route is None:
            return None
        return route.handler(intent_data)


def build_default_registry(assistant):
    registry = PluginRegistry()

    registry.register("open_app", "system", assistant._handle_open_app)
    registry.register("close_app", "system", assistant._handle_close_app)
    registry.register("open_folder", "system", assistant._handle_open_folder)
    registry.register("time", "system", assistant._handle_time)
    registry.register("battery", "system", assistant._handle_battery)
    registry.register("screenshot", "system", assistant._handle_screenshot)
    registry.register("volume_up", "system", assistant._handle_volume_up)
    registry.register("volume_down", "system", assistant._handle_volume_down)
    registry.register("mute", "system", assistant._handle_mute)

    registry.register("open_site", "web", assistant._handle_open_site)
    registry.register("search_google", "web", assistant._handle_search_google)
    registry.register("search_youtube", "web", assistant._handle_search_youtube)
    registry.register("playlist", "music", assistant._handle_playlist)
    registry.register("play_music", "music", assistant._handle_play_music)

    registry.register("open_project", "project", assistant._handle_open_project)
    registry.register("launch_project_server", "project", assistant._handle_launch_project_server)
    registry.register("git_status", "project", assistant._handle_git_status)
    registry.register("git_pull", "project", assistant._handle_git_pull)
    registry.register("git_log", "project", assistant._handle_git_log)
    registry.register("git_commit_prepare", "project", assistant._handle_git_commit_prepare)
    registry.register("git_push_prepare", "project", assistant._handle_git_push_prepare)

    registry.register("create_note", "personal", assistant._handle_create_note)
    registry.register("add_todo", "personal", assistant._handle_add_todo)
    registry.register("list_todos", "personal", assistant._handle_list_todos)
    registry.register("remind_me", "personal", assistant._handle_remind_me)
    registry.register("summarize_file", "personal", assistant._handle_summarize_file)
    registry.register("search_personal", "personal", assistant._handle_search_personal)

    registry.register("energy_consumption", "energy", assistant._handle_energy_plugin)
    registry.register("energy_cost", "energy", assistant._handle_energy_plugin)
    registry.register("solar_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("battery_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("inverter_sizing", "energy", assistant._handle_energy_plugin)
    registry.register("energy_audit_template", "energy", assistant._handle_energy_plugin)

    registry.register("iot_status", "iot", assistant._handle_iot_status)
    registry.register("iot_temperature", "iot", assistant._handle_iot_temperature)
    registry.register("iot_relay_on", "iot", assistant._handle_iot_relay_on)
    registry.register("iot_relay_off", "iot", assistant._handle_iot_relay_off)

    registry.register("chat_fallback", "local_ai", assistant._handle_local_chat_fallback)
    return registry
