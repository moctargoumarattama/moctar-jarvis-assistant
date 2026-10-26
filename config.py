from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return False


load_dotenv(Path(__file__).resolve().parent / ".env")


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    label: str
    path: Path
    framework: str | None = None
    server_cwd: Path | None = None
    server_command: tuple[str, ...] | None = None


APP_ROOT = Path(os.getenv("JARVIS_APP_ROOT", Path(__file__).resolve().parent)).resolve()
HOME_DIR = Path(os.environ.get("USERPROFILE", str(Path.home())))
DESKTOP_DIR = HOME_DIR / "Desktop"
DOWNLOADS_DIR = HOME_DIR / "Downloads"
DOCUMENTS_DIR = HOME_DIR / "Documents"

LOGS_DIR = APP_ROOT / "logs"
NOTES_DIR = APP_ROOT / "notes"
SCREENSHOTS_DIR = APP_ROOT / "screenshots"
DATA_DIR = APP_ROOT / "data"

TODOS_FILE = DATA_DIR / "todos.json"
REMINDERS_FILE = DATA_DIR / "reminders.json"
USER_MEMORY_FILE = DATA_DIR / "user_memory.json"

IMPORTANT_URLS = {
    "youtube": "https://www.youtube.com",
    "youtube_music": "https://music.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "github": "https://github.com",
    "pythonanywhere": "https://www.pythonanywhere.com",
}

BROWSER_PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "brave": "brave.exe",
    "edge": "msedge.exe",
}

BROWSER_EXECUTABLES = {
    "chrome": "chrome",
    "brave": "brave",
    "edge": "msedge",
}

BROWSER_LABELS = {
    "chrome": "Chrome",
    "brave": "Brave",
    "edge": "Edge",
    "default": "navigateur par defaut",
}

import platform as _platform

_is_win = _platform.system() == "Windows"

APP_COMMANDS = {
    "chrome": "start chrome" if _is_win else "google-chrome",
    "brave": "start brave" if _is_win else "brave-browser",
    "edge": "start msedge" if _is_win else "microsoft-edge",
    "calculator": "start calc" if _is_win else "gnome-calculator",
    "notepad": "start notepad" if _is_win else "gedit",
    "cmd": "start cmd" if _is_win else "gnome-terminal",
    "explorer": "start explorer" if _is_win else f"xdg-open {APP_ROOT.as_posix()}",
    "vscode": "start code" if _is_win else f"code {APP_ROOT.as_posix()}",
    "code": "start code" if _is_win else f"code {APP_ROOT.as_posix()}",
}

APP_PROCESS_NAMES = {
    "chrome": "chrome.exe" if _is_win else "chrome",
    "brave": "brave.exe" if _is_win else "brave",
    "edge": "msedge.exe" if _is_win else "msedge",
    "calculator": "CalculatorApp.exe" if _is_win else "gnome-calculator",
    "notepad": "notepad.exe" if _is_win else "gedit",
    "cmd": "cmd.exe" if _is_win else "gnome-terminal",
    "vscode": "Code.exe" if _is_win else "code",
    "code": "Code.exe" if _is_win else "code",
}

FOLDER_PATHS = {
    "desktop": DESKTOP_DIR,
    "downloads": DOWNLOADS_DIR,
    "documents": DOCUMENTS_DIR,
    "projects": DESKTOP_DIR,
}

APP_ALIASES = {
    "chrome": ["chrome", "google chrome"],
    "brave": ["brave", "brave browser"],
    "edge": ["edge", "microsoft edge"],
    "calculator": ["calculatrice", "calculator", "calculette"],
    "notepad": ["bloc-notes", "bloc note", "notepad"],
    "cmd": ["cmd", "terminal", "invite de commande", "invite de commandes", "console"],
    "explorer": ["explorer", "explorateur", "gestionnaire de fichiers"],
    "vscode": [
        "vscode", "visual studio code", "visuel studio code", "visuel studio",
        "visual studio", "studio code", "vscod", "code", "ou vscod", "ouvre vscode",
    ],
    "code": [
        "vscode", "visual studio code", "visuel studio code", "visuel studio",
        "visual studio", "studio code", "vscod", "code", "ou vscod", "ouvre vscode",
    ],
}

SITE_ALIASES = {
    "youtube": ["youtube", "yt"],
    "youtube_music": ["youtube music", "musique youtube", "yt music"],
    "google": ["google"],
    "gmail": ["gmail", "mail"],
    "whatsapp": ["whatsapp", "whatsapp web"],
    "github": ["github", "git hub"],
    "pythonanywhere": ["pythonanywhere", "python anywhere"],
}

FOLDER_ALIASES = {
    "desktop": ["bureau", "desktop"],
    "downloads": ["telechargements", "telechargement", "downloads", "download"],
    "documents": ["documents", "document"],
    "projects": ["projets", "projet", "projects"],
}

PROJECT_ALIASES = {
    "noor_express": ["noor express", "noor", "noorexpress"],
    "baba_market": ["baba market", "baba marketplace", "baba", "papa market", "babamarket", "papamarket"],
    "merca_market": ["merca market", "mercamarket", "mercantfruit", "mercant fruit", "mercat fruit", "merca-market", "merca"],
    "edumanage": ["edumanage", "edu manage"],
    "audit_energetique": [
        "audit energetique",
        "audit energie",
        "monitoring energy",
    ],
    "jarvis": ["jarvis", "moctar", "moctar jarvis", "projet jarvis"],
}


def _pick_python_executable(project_path):
    candidates = [
        project_path / ".venv" / "Scripts" / "python.exe",
        project_path / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "python"


def _project_path(*candidates):
    for raw_path in candidates:
        path = Path(raw_path)
        if path.exists():
            return path
    return Path(candidates[0])


NOOR_PATH = _project_path(
    DESKTOP_DIR / "NOOR EXPRESS",
    HOME_DIR / "NOOR EXPRESS",
    Path("/opt/noor-express"),
    Path("/var/www/noor-express"),
)
BABA_PATH = _project_path(
    DESKTOP_DIR / "baba_marketplace",
    HOME_DIR / "baba_marketplace",
    HOME_DIR / "babamarket",
    Path("/opt/babamarket"),
    Path("/opt/baba_marketplace"),
    Path("/var/www/babamarket"),
    Path("/home/moctar/baba_marketplace"),
    Path("/home/moctar/babamarket"),
)
EDUMANAGE_PATH = _project_path(
    DESKTOP_DIR / "EDUMANAGE",
    HOME_DIR / "EDUMANAGE",
    Path("/opt/edumanage"),
)
AUDIT_PATH = _project_path(
    DESKTOP_DIR / "monitoring energy",
    HOME_DIR / "monitoring energy",
)


def _optional_int_env(name, default=None):
    raw_value = os.getenv(name, "")
    if raw_value is None:
        return default

    raw_value = raw_value.strip()
    if not raw_value:
        return default

    return int(raw_value)


def _optional_bool_env(name, default):
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}

PROJECTS = {
    "noor_express": ProjectConfig(
        key="noor_express",
        label="Noor Express",
        path=NOOR_PATH,
        framework="fastapi",
        server_cwd=NOOR_PATH / "backend",
        server_command=(
            _pick_python_executable(NOOR_PATH),
            "-m",
            "uvicorn",
            "app.main:app",
            "--reload",
        ),
    ),
    "baba_market": ProjectConfig(
        key="baba_market",
        label="Baba Market",
        path=BABA_PATH,
        framework="flask",
        server_cwd=BABA_PATH,
        server_command=(
            _pick_python_executable(BABA_PATH),
            "-m",
            "flask",
            "--app",
            "wsgi:app",
            "run",
            "--debug",
        ),
    ),
    "edumanage": ProjectConfig(
        key="edumanage",
        label="EDUMANAGE",
        path=EDUMANAGE_PATH,
        framework="flask",
        server_cwd=EDUMANAGE_PATH,
        server_command=(
            _pick_python_executable(EDUMANAGE_PATH),
            "run.py",
        ),
    ),
    "audit_energetique": ProjectConfig(
        key="audit_energetique",
        label="Audit energetique",
        path=AUDIT_PATH,
        framework="flask",
        server_cwd=AUDIT_PATH,
        server_command=(
            _pick_python_executable(AUDIT_PATH),
            "app.py",
        ),
    ),
}

MIC_SETTINGS = {
    "default_language": "fr-FR",
    "timeout": int(os.getenv("JARVIS_MIC_TIMEOUT", "4")),
    "phrase_limit": int(os.getenv("JARVIS_MIC_PHRASE_LIMIT", "7")),
    "ambient_duration": float(os.getenv("JARVIS_MIC_AMBIENT_DURATION", "0.25")),
    "device_index": _optional_int_env("JARVIS_MIC_INDEX", 2),
    "attempts": int(os.getenv("JARVIS_MIC_ATTEMPTS", "1")),
    "calibration_interval": float(os.getenv("JARVIS_MIC_CALIBRATION_INTERVAL", "45")),
    "energy_threshold_ratio": float(os.getenv("JARVIS_MIC_ENERGY_RATIO", "1.15")),
    "pause_threshold": float(os.getenv("JARVIS_MIC_PAUSE_THRESHOLD", "0.8")),
    "phrase_threshold": float(os.getenv("JARVIS_MIC_PHRASE_THRESHOLD", "0.15")),
    "non_speaking_duration": float(os.getenv("JARVIS_MIC_NON_SPEAKING_DURATION", "0.3")),
}

TTS_SETTINGS = {
    "engine": os.getenv("JARVIS_TTS_ENGINE", "edge-tts").strip().lower() or "edge-tts",
    "voice": os.getenv("JARVIS_TTS_VOICE", os.getenv("JARVIS_EDGE_TTS_VOICE", "fr-FR-RemyMultilingualNeural")).strip() or "fr-FR-RemyMultilingualNeural",
    "fallback_voice": "fr-FR-HenriNeural",
    "rate": os.getenv("JARVIS_TTS_RATE", "+0%").strip() or "+0%",
    "pitch": os.getenv("JARVIS_TTS_PITCH", "+0Hz").strip() or "+0Hz",
    "fallback_to_pyttsx3": True,
}

MIC_INDEX = MIC_SETTINGS["device_index"]
ENABLE_SOFT_WAKE_WORD = os.getenv("ENABLE_SOFT_WAKE_WORD", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
SOFT_WAKE_WORDS = ["hey moctar", "yo moctar", "yo"]
SOFT_WAKE_LANGUAGE = os.getenv("SOFT_WAKE_LANGUAGE", "fr-FR").strip() or "fr-FR"
SOFT_WAKE_TIMEOUT = float(os.getenv("SOFT_WAKE_TIMEOUT", "2"))
SOFT_WAKE_PHRASE_LIMIT = float(os.getenv("SOFT_WAKE_PHRASE_LIMIT", "2"))
SOFT_WAKE_COOLDOWN_SECONDS = float(os.getenv("SOFT_WAKE_COOLDOWN_SECONDS", "1.5"))
SOFT_WAKE_DUPLICATE_SECONDS = float(os.getenv("SOFT_WAKE_DUPLICATE_SECONDS", "8"))
SOFT_WAKE_MIN_CONFIDENCE = float(os.getenv("SOFT_WAKE_MIN_CONFIDENCE", "0.88"))
VOICE_COMMAND_DEBOUNCE_SECONDS = float(os.getenv("VOICE_COMMAND_DEBOUNCE_SECONDS", "5"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ai_provider = "gemini"
gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
GEMINI_TIMEOUT_SECONDS = max(10.0, float(os.getenv("GEMINI_TIMEOUT_SECONDS", "15.0")))

AI_SETTINGS = {
    "ai_provider": ai_provider,
    "gemini_model": gemini_model,
    "default_model": gemini_model,
    "base_url": GEMINI_BASE_URL,
    "enable_general_ai": _optional_bool_env("MOCTAR_ENABLE_GENERAL_AI", True),
    "agent_enabled": _optional_bool_env("MOCTAR_AGENT_ENABLED", True),
    "agent_max_steps": max(1, min(_optional_int_env("MOCTAR_AGENT_MAX_STEPS", 5), 10)),
    "agent_thinking": os.getenv("MOCTAR_AGENT_THINKING", "auto").strip().lower() or "auto",
    "chat_fallback": "Je ne peux pas utiliser l'IA pour le moment.",
}

SECURITY_SETTINGS = {
    "require_confirmation_for_sensitive": _optional_bool_env(
        "MOCTAR_REQUIRE_CONFIRMATION",
        True,
    ),
    "domain_permissions": {
        "system": _optional_bool_env("MOCTAR_ALLOW_SYSTEM_ACTIONS", True),
        "web": _optional_bool_env("MOCTAR_ALLOW_WEB_ACTIONS", True),
        "music": _optional_bool_env("MOCTAR_ALLOW_MUSIC_ACTIONS", True),
        "project": _optional_bool_env("MOCTAR_ALLOW_PROJECT_ACTIONS", True),
        "personal": _optional_bool_env("MOCTAR_ALLOW_PERSONAL_ACTIONS", True),
        "energy": _optional_bool_env("MOCTAR_ALLOW_ENERGY_ACTIONS", True),
        "iot": _optional_bool_env("MOCTAR_ALLOW_IOT_ACTIONS", True),
        "local_ai": _optional_bool_env("MOCTAR_ALLOW_LOCAL_AI", True),
    },
}

WEB_SETTINGS = {
    "default_browser": os.getenv("JARVIS_BROWSER", "default").strip().lower() or "default",
    "duplicate_cooldown_seconds": float(os.getenv("JARVIS_BROWSER_DUPLICATE_COOLDOWN", "4")),
}

KNOWLEDGE_SETTINGS = {
    "enable_wikipedia": _optional_bool_env("MOCTAR_ENABLE_WIKIPEDIA", True),
    "timeout_seconds": float(os.getenv("MOCTAR_KNOWLEDGE_TIMEOUT", "4")),
}

IOT_SETTINGS = {
    "base_url": os.getenv("MOCTAR_IOT_BASE_URL", "").strip(),
    "status_endpoint": os.getenv("MOCTAR_IOT_STATUS_ENDPOINT", "/api/status").strip() or "/api/status",
    "temperature_endpoint": os.getenv("MOCTAR_IOT_TEMPERATURE_ENDPOINT", "/api/temperature").strip() or "/api/temperature",
    "relay_endpoint": os.getenv("MOCTAR_IOT_RELAY_ENDPOINT", "/api/relay").strip() or "/api/relay",
    "request_timeout": float(os.getenv("MOCTAR_IOT_TIMEOUT", "3")),
    "temperature_threshold": float(os.getenv("MOCTAR_IOT_TEMP_THRESHOLD", "30")),
}

WAKE_WORD_SETTINGS = {
    "builtin_keyword": os.getenv("JARVIS_WAKE_WORD", "jarvis").strip().lower() or "jarvis",
    "custom_keyword_path": os.getenv("JARVIS_WAKE_WORD_PATH", "").strip(),
    "cooldown_seconds": float(os.getenv("JARVIS_WAKE_WORD_COOLDOWN", "1.5")),
}

GIT_TIMEOUT_SECONDS = 20
DEFAULT_SITE_SEARCH_QUERY = "playlist musique populaire"

# --------------------------------------------------------------------------- #
#  Scheduler settings                                                          #
# --------------------------------------------------------------------------- #

SCHEDULER_SETTINGS = {
    "poll_seconds": max(int(os.getenv("JARVIS_SCHEDULER_POLL_SECONDS", "60")), 5),
}


def ensure_runtime_directories():
    for directory in [LOGS_DIR, NOTES_DIR, SCREENSHOTS_DIR, DATA_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
#  NOC Mission Control & Supervision Settings                                 #
# --------------------------------------------------------------------------- #

import re
import math


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    label: str
    port: int


SERVICES = (
    Service("merca-market", "Merca-Market", 5006),
    Service("klasora", "Klasora", 5005),
    Service("babamarket", "BabaMarket", 5002),
    Service("moc-mal", "Moc-Mal", 5003),
)
RESTART_ALLOWLIST = frozenset(service.name for service in SERVICES)

# Services contrôlés par WhatsApp ; jarvis.service reste le superviseur permanent.
MANAGED_SERVICES = {
    "klasora": "klasora.service",
    "ollama": "ollama.service",
    "tunnel": "cloudflared.service",
}
MANAGED_SERVICE_GROUPS = {"apps": tuple(MANAGED_SERVICES)}


def _noc_number(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} doit être compris entre {minimum} et {maximum}")
    return value


def _noc_boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).lower()
    if raw not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} doit être true ou false")
    return raw in {"true", "1", "yes"}


def parse_whatsapp_admin_numbers(raw: str) -> tuple[str, ...]:
    """Numéros internationaux explicites ; une liste vide désactive les commandes."""
    if not isinstance(raw, str):
        raise ValueError("WHATSAPP_ADMIN_NUMBERS doit être une liste de numéros séparés par des virgules")
    if not raw.strip():
        return ()
    numbers = []
    for item in raw.split(","):
        item = item.strip()
        if not re.fullmatch(r"\+?[0-9 .()-]+", item):
            raise ValueError("WHATSAPP_ADMIN_NUMBERS contient un numéro invalide")
        number = re.sub(r"\D", "", item)
        if not 8 <= len(number) <= 15:
            raise ValueError("WHATSAPP_ADMIN_NUMBERS exige des numéros internationaux de 8 à 15 chiffres")
        if number not in numbers:
            numbers.append(number)
    if len(numbers) > 32:
        raise ValueError("WHATSAPP_ADMIN_NUMBERS accepte au maximum 32 administrateurs")
    return tuple(numbers)


WHATSAPP_ADMIN_NUMBERS = parse_whatsapp_admin_numbers(os.environ.get(
    "WHATSAPP_ADMIN_NUMBERS", os.environ.get("WHATSAPP_ADMIN_PHONE",
    os.environ.get("ADMIN_PHONE_NUMBER", "212770010264"))
))


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 5050
    interval: float = 2.0
    http_timeout: float = 1.5
    systemctl_timeout: float = 1.5
    backup_path: str = "/mnt/babamarket-backups"
    gateway_secret: str = "secret-gateway-local-klasora-2024"
    vps_token: str = ""
    admin_token: str = ""
    enable_restart: bool = True
    vps_stale_seconds: float = 20.0
    vps_retention_seconds: float = 86400.0
    vps_state_path: str = ""
    max_vps: int = 128
    max_streams: int = 32
    event_limit: int = 200
    admin_phone_number: str = "212770010264"
    whatsapp_admin_numbers: tuple[str, ...] | None = None
    whatsapp_webhook_token: str = ""
    whatsapp_session_id: str = "1"
    whatsapp_bridge_url: str = "http://127.0.0.1:3001"
    whatsapp_bridge_restart_cmd: str = ""
    whatsapp_send_url: str = "http://127.0.0.1:3001/session/{session_id}/send"
    whatsapp_send_to_field: str = "jid"
    whatsapp_send_text_field: str = "text"
    whatsapp_send_timeout: float = 10.0
    whatsapp_media_dir: str = "/var/lib/klasora-whatsapp/media"
    whatsapp_max_audio_bytes: int = 8 * 1024 * 1024
    whatsapp_audio_seconds: int = 60
    voice_enabled: bool = True
    whisper_model: str = "base"
    whisper_model_path: str = (APP_ROOT / "models" / "whisper").as_posix()
    whisper_python: str = (APP_ROOT / "venv" / "bin" / "python").as_posix()
    whisper_timeout: float = 120.0
    groq_api_key: str = ""
    backup_script: str = "/usr/local/bin/backup-babamarket.sh"
    backup_timeout: float = 300.0
    voice_remote_url: str = ""
    voice_remote_token: str = ""
    sentinel_stale_seconds: float = 35.0
    sentinel_geoip_enabled: bool = True
    sentinel_state_path: str = str(DATA_DIR / "sentinel-counters.json")
    cyber_state_path: str = str(DATA_DIR / "blocked_ips.json")
    cyber_protected_ips: tuple[str, ...] = ("187.77.171.225",)
    ssl_keyfile: str = (APP_ROOT / "certs" / "server.key").as_posix()
    ssl_certfile: str = (APP_ROOT / "certs" / "server.crt").as_posix()

    @classmethod
    def from_env(cls) -> Settings:
        default_whisper_model_path = (APP_ROOT / "models" / "whisper").as_posix()
        default_whisper_python = (APP_ROOT / "venv" / "bin" / "python").as_posix()
        default_ssl_keyfile = (APP_ROOT / "certs" / "server.key").as_posix()
        default_ssl_certfile = (APP_ROOT / "certs" / "server.crt").as_posix()
        return cls(
            host=os.environ.get("NOC_HOST", "127.0.0.1"),
            port=int(_noc_number("NOC_PORT", 5050, 1, 65535)),
            interval=_noc_number("NOC_INTERVAL_SECONDS", 2, 0.5, 60),
            backup_path=os.environ.get("NOC_BACKUP_PATH", "/mnt/babamarket-backups"),
            gateway_secret=os.environ.get("NOC_GATEWAY_SECRET", cls.__dataclass_fields__["gateway_secret"].default),
            vps_token=os.environ.get("NOC_VPS_TOKEN") or os.environ.get("VPS_TOKEN") or os.environ.get("X_VPS_TOKEN", ""),
            admin_token=os.environ.get("NOC_ADMIN_TOKEN", ""),
            enable_restart=_noc_boolean("NOC_ENABLE_RESTART", True),
            vps_stale_seconds=_noc_number("NOC_VPS_STALE_SECONDS", 20, 5, 3600),
            vps_retention_seconds=_noc_number("NOC_VPS_RETENTION_SECONDS", 86400, 60, 604800),
            vps_state_path=os.environ.get("NOC_VPS_STATE_PATH", str(Path(os.environ.get("STATE_DIRECTORY") or DATA_DIR) / "vps-registry.json")),
            max_vps=int(_noc_number("NOC_MAX_VPS", 128, 1, 1024)),
            max_streams=int(_noc_number("NOC_MAX_STREAMS", 32, 1, 128)),
            admin_phone_number=re.sub(r"\D", "", os.environ.get(
                "WHATSAPP_ADMIN_PHONE", os.environ.get("ADMIN_PHONE_NUMBER", "212770010264"))),
            whatsapp_admin_numbers=parse_whatsapp_admin_numbers(os.environ.get(
                "WHATSAPP_ADMIN_NUMBERS", os.environ.get("WHATSAPP_ADMIN_PHONE",
                os.environ.get("ADMIN_PHONE_NUMBER", "212770010264"))
            )),
            whatsapp_webhook_token=os.environ.get("NOC_WHATSAPP_WEBHOOK_TOKEN", ""),
            whatsapp_session_id=os.environ.get("NOC_WHATSAPP_SESSION_ID", "1"),
            whatsapp_bridge_url=os.environ.get("WHATSAPP_BRIDGE_URL", os.environ.get("NOC_WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3001")).strip() or "http://127.0.0.1:3001",
            whatsapp_bridge_restart_cmd=os.environ.get("WHATSAPP_BRIDGE_RESTART_CMD", "").strip(),
            whatsapp_send_url=os.environ.get("NOC_WHATSAPP_SEND_URL", os.environ.get(
                "WHATSAPP_SEND_URL", "http://127.0.0.1:3001/session/{session_id}/send")),
            whatsapp_send_to_field=os.environ.get("NOC_WHATSAPP_SEND_TO_FIELD", os.environ.get(
                "WHATSAPP_SEND_TO_FIELD", "jid")),
            whatsapp_send_text_field=os.environ.get("NOC_WHATSAPP_SEND_TEXT_FIELD", os.environ.get(
                "WHATSAPP_SEND_TEXT_FIELD", "text")),
            whatsapp_send_timeout=_noc_number("NOC_WHATSAPP_SEND_TIMEOUT", 10, 3, 30),
            whatsapp_media_dir=os.environ.get("NOC_WHATSAPP_MEDIA_DIR", "/var/lib/klasora-whatsapp/media"),
            whatsapp_max_audio_bytes=int(_noc_number("NOC_WHATSAPP_MAX_AUDIO_BYTES", 8388608, 1024, 8388608)),
            whatsapp_audio_seconds=int(_noc_number("NOC_WHATSAPP_AUDIO_SECONDS", 60, 1, 60)),
            voice_enabled=_noc_boolean("NOC_VOICE_ENABLED", True),
            whisper_model=os.environ.get("NOC_WHISPER_MODEL", "base") or "base",
            whisper_model_path=os.environ.get("NOC_WHISPER_MODEL_PATH", default_whisper_model_path) or default_whisper_model_path,
            whisper_python=os.environ.get("NOC_WHISPER_PYTHON", default_whisper_python),
            whisper_timeout=_noc_number("NOC_WHISPER_TIMEOUT", 120, 5, 300),
            groq_api_key=os.environ.get("GROQ_API_KEY", "") or os.environ.get("NOC_GROQ_API_KEY", ""),
            backup_script=os.environ.get("NOC_BACKUP_SCRIPT", os.environ.get("NOC_BABAMARKET_BACKUP_SCRIPT", "/usr/local/bin/backup-babamarket.sh")) or "/usr/local/bin/backup-babamarket.sh",
            backup_timeout=_noc_number("NOC_BACKUP_TIMEOUT", 300, 5, 1800),
            voice_remote_url=os.environ.get("NOC_VOICE_REMOTE_URL", ""),
            voice_remote_token=os.environ.get("NOC_VOICE_REMOTE_TOKEN", ""),
            sentinel_stale_seconds=_noc_number("NOC_SENTINEL_STALE_SECONDS", 35, 15, 300),
            sentinel_geoip_enabled=_noc_boolean("NOC_SENTINEL_GEOIP", True),
            sentinel_state_path=os.environ.get("NOC_SENTINEL_STATE_PATH", str(DATA_DIR / "sentinel-counters.json")),
            cyber_state_path=os.environ.get("NOC_CYBER_STATE_PATH", str(DATA_DIR / "blocked_ips.json")),
            cyber_protected_ips=tuple(value.strip() for value in
                                      os.environ.get("NOC_CYBER_PROTECTED_IPS", "187.77.171.225").split(",")
                                      if value.strip()),
            ssl_keyfile=os.environ.get("NOC_SSL_KEYFILE", default_ssl_keyfile),
            ssl_certfile=os.environ.get("NOC_SSL_CERTFILE", default_ssl_certfile),
        )
