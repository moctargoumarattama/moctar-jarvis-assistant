import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    label: str
    path: Path
    framework: str | None = None
    server_cwd: Path | None = None
    server_command: tuple[str, ...] | None = None


APP_ROOT = Path(__file__).resolve().parent
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

APP_COMMANDS = {
    "chrome": "start chrome",
    "brave": "start brave",
    "edge": "start msedge",
    "calculator": "start calc",
    "notepad": "start notepad",
    "cmd": "start cmd",
    "explorer": "start explorer",
    "vscode": "start code",
}

APP_PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "brave": "brave.exe",
    "edge": "msedge.exe",
    "calculator": "CalculatorApp.exe",
    "notepad": "notepad.exe",
    "cmd": "cmd.exe",
    "vscode": "Code.exe",
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
    "cmd": ["cmd", "terminal", "invite de commande"],
    "explorer": ["explorer", "explorateur"],
    "vscode": ["vscode", "visual studio code", "code"],
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
    "noor_express": ["noor express", "noor"],
    "baba_market": ["baba market", "baba marketplace", "baba"],
    "edumanage": ["edumanage", "edu manage"],
    "audit_energetique": [
        "audit energetique",
        "audit energie",
        "monitoring energy",
    ],
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


NOOR_PATH = _project_path(DESKTOP_DIR / "NOOR EXPRESS")
BABA_PATH = _project_path(DESKTOP_DIR / "baba_marketplace")
EDUMANAGE_PATH = _project_path(DESKTOP_DIR / "EDUMANAGE")
AUDIT_PATH = _project_path(DESKTOP_DIR / "monitoring energy")


def _optional_int_env(name, default=None):
    raw_value = os.getenv(name, "")
    if raw_value is None:
        return default

    raw_value = raw_value.strip()
    if not raw_value:
        return default

    return int(raw_value)

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
    "timeout": int(os.getenv("JARVIS_MIC_TIMEOUT", "5")),
    "phrase_limit": int(os.getenv("JARVIS_MIC_PHRASE_LIMIT", "6")),
    "ambient_duration": float(os.getenv("JARVIS_MIC_AMBIENT_DURATION", "1")),
    "device_index": _optional_int_env("JARVIS_MIC_INDEX", 2),
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
SOFT_WAKE_MIN_CONFIDENCE = float(os.getenv("SOFT_WAKE_MIN_CONFIDENCE", "0.88"))

AI_SETTINGS = {
    "default_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
    "chat_fallback": "Je ne peux pas utiliser l'IA pour le moment.",
}

WEB_SETTINGS = {
    "default_browser": os.getenv("JARVIS_BROWSER", "default").strip().lower() or "default",
    "duplicate_cooldown_seconds": float(os.getenv("JARVIS_BROWSER_DUPLICATE_COOLDOWN", "4")),
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


def ensure_runtime_directories():
    for directory in [LOGS_DIR, NOTES_DIR, SCREENSHOTS_DIR, DATA_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
