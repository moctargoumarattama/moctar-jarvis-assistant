"""API NOC, consultation directe et flux SSE partagé, un seul worker."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Path as APIPath, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

import collector
import voice_transcribe
from config import RESTART_ALLOWLIST, Settings
from cyberdefense import CyberDefenseManager, TRAP_PATHS
from sentinel import SentinelManager
from vps_registry import VPSRegistry
from whatsapp import AUDIO_MIME_TYPES, WhatsAppController

LOGGER = logging.getLogger("noc-dashboard")
BASE_DIR = Path(__file__).resolve().parent

DEFAULT_TTS_VOICE = "fr-FR-HenriNeural"
ALLOWED_TTS_VOICES = {
    "fr-FR-HenriNeural",
    "fr-FR-RemyMultilingualNeural",
    "fr-FR-DeniseNeural",
    "fr-FR-EloiseNeural",
}
_TTS_MEMORY_CACHE: dict[str, bytes] = {}
_TTS_CACHE_MAX_ITEMS = 128
DIRECT_AUDIO_MIME_TYPES = AUDIO_MIME_TYPES | {
    "application/octet-stream",
    "audio/webm",
    "video/webm",
    "audio/mp4",
    "audio/x-m4a",
}


def clean_tts_text(text: str) -> str:
    """Nettoie le Markdown et les symboles techniques pour une diction vocale fluide."""
    cleaned = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"[*_#>[\]()~|]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def get_tts_cache_dir() -> Path:
    """Retourne le dossier de cache local pour les fichiers MP3 générés par edge-tts."""
    configured = os.environ.get("MOCTAR_TTS_CACHE_DIR", "").strip()
    candidates = (
        [Path(configured).expanduser()] if configured else []
    ) + [
        BASE_DIR / "data" / "tts_cache",
        Path(tempfile.gettempdir()) / "moctar_tts_cache",
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


async def synthesize_edge_tts(text: str, voice: str = DEFAULT_TTS_VOICE) -> bytes:
    """Génère un flux audio MP3 via edge-tts pour le texte et la voix demandés."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    audio_buffer = bytearray()
    async for chunk in communicate.stream():
        if isinstance(chunk, dict) and chunk.get("type") == "audio":
            data = chunk.get("data")
            if data:
                audio_buffer.extend(data)
    if not audio_buffer:
        raise RuntimeError("edge-tts n'a retourné aucun flux audio.")
    return bytes(audio_buffer)


async def generate_cached_tts_with_key(text: str, voice: str = DEFAULT_TTS_VOICE) -> tuple[bytes, bool, str]:
    """Retourne (mp3_bytes, cache_hit, cache_key) avec mise en cache mémoire et disque."""
    cleaned = clean_tts_text(text)
    if not cleaned:
        raise ValueError("Le texte à synthétiser est vide.")
    selected_voice = voice if voice in ALLOWED_TTS_VOICES else DEFAULT_TTS_VOICE
    cache_key = hashlib.sha256(f"{selected_voice}:{cleaned}".encode("utf-8")).hexdigest()

    if cache_key in _TTS_MEMORY_CACHE:
        return _TTS_MEMORY_CACHE[cache_key], True, cache_key

    cache_file = get_tts_cache_dir() / f"{cache_key}.mp3"
    try:
        if cache_file.is_file() and cache_file.stat().st_size > 0:
            data = await asyncio.to_thread(cache_file.read_bytes)
            if data:
                if len(_TTS_MEMORY_CACHE) >= _TTS_CACHE_MAX_ITEMS:
                    _TTS_MEMORY_CACHE.pop(next(iter(_TTS_MEMORY_CACHE)), None)
                _TTS_MEMORY_CACHE[cache_key] = data
                return data, True, cache_key
    except OSError:
        pass

    audio_bytes = await synthesize_edge_tts(cleaned, selected_voice)
    if len(_TTS_MEMORY_CACHE) >= _TTS_CACHE_MAX_ITEMS:
        _TTS_MEMORY_CACHE.pop(next(iter(_TTS_MEMORY_CACHE)), None)
    _TTS_MEMORY_CACHE[cache_key] = audio_bytes
    try:
        await asyncio.to_thread(cache_file.write_bytes, audio_bytes)
    except OSError:
        pass
    return audio_bytes, False, cache_key


async def generate_cached_tts(text: str, voice: str = DEFAULT_TTS_VOICE) -> tuple[bytes, bool]:
    """Retourne (mp3_bytes, cache_hit) avec mise en cache mémoire et disque."""
    audio_bytes, cache_hit, _ = await generate_cached_tts_with_key(text, voice)
    return audio_bytes, cache_hit


_VOICE_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:yo|hey|ok|bonjour|salut|dis|écoute|ecoute)\s+)?(?:moctar|moktar|mokhtar|elmo)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)
_VOICE_WAKE_ANYWHERE_RE = re.compile(
    r"\b(?:yo\s+moctar|moctar|moktar|mokhtar|yo\s+elmo|elmo)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)


def extract_voice_interact_command(transcript: str, session_active: bool = False) -> tuple[bool, str, bool]:
    """Vérifie le mot-clé ('Moctar', 'Yo Moctar', 'ELMo') ou une session vocale ouverte.

    Retourne (accepted, command_text, wake_word_detected).
    """
    filtered = voice_transcribe.filter_whisper_hallucinations(transcript)
    if not filtered:
        return False, "", False

    prefix_match = _VOICE_WAKE_PREFIX_RE.match(filtered)
    if prefix_match:
        remainder = filtered[prefix_match.end():].strip(" ,.:;!?-")
        command = remainder if remainder else "Dis-moi que tu es à l'écoute en une phrase courte."
        return True, command, True

    anywhere_match = _VOICE_WAKE_ANYWHERE_RE.search(filtered)
    if anywhere_match:
        remainder = filtered[anywhere_match.end():].strip(" ,.:;!?-")
        command = remainder if remainder else filtered
        return True, command, True

    if session_active:
        return True, filtered, False

    return False, "", False


def _extract_multipart_audio(raw_body: bytes, content_type_header: str) -> tuple[bytes, str]:
    """Extrait le fichier audio d'une requête multipart/form-data sans dépendance externe."""
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type_header, re.IGNORECASE)
    if not match:
        raise ValueError("Boundary multipart manquant.")
    boundary = (match.group(1) or match.group(2)).encode("latin-1")
    delimiter = b"--" + boundary
    for part in raw_body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" in part:
            header_block, payload = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_block, payload = part.split(b"\n\n", 1)
        else:
            continue
        headers_text = header_block.decode("latin-1", errors="ignore")
        if "content-disposition" not in headers_text.lower():
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        mime_match = re.search(r"content-type:\s*([^\r\n;]+)", headers_text, re.IGNORECASE)
        part_mime = mime_match.group(1).strip().lower() if mime_match else "audio/wav"
        if payload:
            return payload, part_mime
    raise ValueError("Aucun fichier audio trouvé dans le corps multipart.")


def _convert_audio_for_whisper(source_path: Path, content_type: str) -> Path | None:
    """Convertit un fichier WebM/OGG/WAV non-PCM en WAV mono 16kHz PCM si possible."""
    if voice_transcribe.read_wav_pcm(source_path) is not None:
        return None
    # Essai 1 : soundfile (rapide pour WAV/FLAC/OGG supportés par libsndfile)
    try:
        import numpy as np
        import soundfile as sf

        data, samplerate = sf.read(str(source_path), dtype="float32", always_2d=True)
        if data.size > 0:
            mono = data.mean(axis=1)
            fd, wav_name = tempfile.mkstemp(prefix="moctar-sf-", suffix=".wav")
            os.close(fd)
            wav_path = Path(wav_name)
            sf.write(str(wav_path), np.clip(mono, -1.0, 1.0), samplerate, subtype="PCM_16", format="WAV")
            return wav_path
    except Exception:
        pass
    # Essai 2 : ffmpeg (universel pour WebM/Opus/MP4 capturés par MediaRecorder)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        import subprocess

        fd, wav_name = tempfile.mkstemp(prefix="moctar-ffmpeg-", suffix=".wav")
        os.close(fd)
        wav_path = Path(wav_name)
        try:
            proc = subprocess.run(
                [
                    ffmpeg_bin,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0 and wav_path.is_file() and wav_path.stat().st_size > 44:
                return wav_path
        except Exception:
            pass
        wav_path.unlink(missing_ok=True)
    return None


async def transcribe_direct_audio(audio_bytes: bytes, content_type: str = "audio/wav") -> str:
    """Transcrit directement un blob audio (WAV/WebM/OGG) via faster-whisper local."""
    if not audio_bytes:
        raise ValueError("Fichier audio vide.")
    ext = ".wav"
    if "webm" in content_type:
        ext = ".webm"
    elif "ogg" in content_type or "opus" in content_type:
        ext = ".ogg"
    elif "mp3" in content_type or "mpeg" in content_type:
        ext = ".mp3"
    elif "mp4" in content_type or "m4a" in content_type:
        ext = ".m4a"

    fd, tmp_name = tempfile.mkstemp(prefix="moctar-voice-direct-", suffix=ext)
    tmp_path = Path(tmp_name)
    converted_path: Path | None = None
    try:
        with os.fdopen(fd, "wb") as handle:
            await asyncio.to_thread(handle.write, audio_bytes)
        converted_path = await asyncio.to_thread(_convert_audio_for_whisper, tmp_path, content_type)
        target_path = converted_path if converted_path is not None else tmp_path
        try:
            raw_text = await asyncio.to_thread(voice_transcribe.transcribe_audio, target_path)
        except ValueError as exc:
            if "aucune parole" in str(exc).lower():
                return ""
            raise
        return voice_transcribe.filter_whisper_hallucinations(raw_text)
    finally:
        tmp_path.unlink(missing_ok=True)
        if converted_path is not None:
            converted_path.unlink(missing_ok=True)


try:
    from headless_bridge import JarvisHeadlessBridge
    JARVIS_AVAILABLE = True
except Exception as _jarvis_err:
    JarvisHeadlessBridge = None
    JARVIS_AVAILABLE = False
    LOGGER.warning("JarvisHeadlessBridge indisponible : %s", _jarvis_err)


class Metrics(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    used: Annotated[int, Field(ge=0, le=2**60, strict=True)]
    total: Annotated[int, Field(gt=0, le=2**60, strict=True)]
    percent: Annotated[float, Field(ge=0, le=100, strict=True)]

    @field_validator("used", "total", mode="before")
    @classmethod
    def coerce_to_int(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("Les booléens ne sont pas acceptés")
        if isinstance(v, (int, float)):
            return int(round(v))
        return v

    @model_validator(mode="after")
    def consistent(self) -> Metrics:
        if self.used > self.total:
            raise ValueError("used ne peut pas dépasser total")
        if abs(self.percent - self.used / self.total * 100) > 2:
            raise ValueError("Pourcentage incohérent avec used/total")
        return self


class RemoteService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")]
    status: Annotated[str, Field(pattern=r"^(active|inactive|failed|unknown|activating|deactivating|reloading|maintenance)$")]


class VPSReport(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    hostname: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
    cpu_percent: Annotated[float, Field(ge=0, le=100, strict=True)]
    memory: Metrics
    disk: Metrics
    services: Annotated[list[RemoteService], Field(max_length=16)] = []

    @model_validator(mode="after")
    def unique_services(self) -> VPSReport:
        if len({item.name for item in self.services}) != len(self.services):
            raise ValueError("Services dupliqués")
        return self


async def limited_json(request: Request, limit: int) -> Any:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Content-Type application/json requis")
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > limit or int(length) < 0:
                raise HTTPException(413, "Corps de requête trop volumineux")
        except ValueError:
            raise HTTPException(400, "Content-Length invalide") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Corps de requête trop volumineux")
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "JSON invalide") from None


def validate_payload(model: type[BaseModel], data: Any) -> Any:
    try:
        return model.model_validate(data)
    except (ValidationError, ValueError):
        # Les données reçues ne sont pas recopiées dans les erreurs.
        raise HTTPException(422, "Données invalides : types, limites ou champs non conformes") from None


def token_matches(received: str | None, expected: str) -> bool:
    return bool(expected and received and hmac.compare_digest(received.encode(), expected.encode()))


def same_origin(request: Request) -> None:
    # Les actions web sont directes ; une page tierce ne peut pas les déclencher.
    site = request.headers.get("sec-fetch-site")
    if site and site.lower() not in {"same-origin", "none"}:
        raise HTTPException(403, "Origine non autorisée")
    origin = request.headers.get("origin")
    if origin:
        try:
            parts = urlsplit(origin)
            target = urlsplit(str(request.url))
            valid = (parts.scheme in {"http", "https"} and parts.scheme == target.scheme
                     and parts.hostname == target.hostname
                     and (parts.port or (443 if parts.scheme == "https" else 80))
                     == (target.port or (443 if target.scheme == "https" else 80))
                     and not parts.username and not parts.password and parts.path in {"", "/"}
                     and not parts.query and not parts.fragment)
        except ValueError:
            raise HTTPException(403, "Origine non autorisée") from None
        if not valid:
            raise HTTPException(403, "Origine non autorisée")


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.collector = collector.Collector(settings)
        self.latest: dict[str, Any] | None = None
        self.local_status = "operational"
        self.serialized = "{}"
        self.revision = 0
        self.last_published = 0.0
        self.collect_task: asyncio.Task | None = None
        self.changed = asyncio.Condition()
        self.vps: dict[str, dict[str, Any]] = {}
        self.vps_registry = VPSRegistry(settings, VPSReport)
        self.active_streams = 0
        self.restart_locks = {name: asyncio.Lock() for name in RESTART_ALLOWLIST}
        self.restart_times: dict[str, float] = {}
        self.sentinel = SentinelManager(settings, self)
        self.cyber = CyberDefenseManager(settings, self)
        self.whatsapp = WhatsAppController(settings, self, self.restart_service)
        self.jarvis: Any = None
        if JARVIS_AVAILABLE and JarvisHeadlessBridge:
            try:
                self.jarvis = JarvisHeadlessBridge(base_dir=BASE_DIR)
                LOGGER.info("JarvisHeadlessBridge connecté au Runtime NOC.")
            except Exception as exc:
                LOGGER.error("Erreur lors de l'initialisation de JarvisHeadlessBridge: %s", exc)

    def vps_snapshot(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        result = []
        for hostname, item in list(self.vps.items()):
            age = max(0, now - item["seen"])
            if age > self.settings.vps_retention_seconds:
                del self.vps[hostname]
                self.collector._states.pop("vps:" + hostname, None)
                continue
            restored = item.get("restored", False)
            stale = restored or age > self.settings.vps_stale_seconds
            archived = stale and age > 86400
            healthy = not stale and item["report"]["cpu_percent"] < 95 and item["report"]["memory"]["percent"] < 90 and item["report"]["disk"]["percent"] < 90 and all(service["status"] == "active" for service in item["report"]["services"])
            self.collector.transition("vps:" + hostname, (stale, healthy, archived), f"VPS {hostname} · {'déconnecté · archivé' if archived else ('rapport expiré' if stale else ('opérationnel' if healthy else 'incident détecté'))}", healthy or archived)
            item["stale"] = stale
            result.append({**item["report"], "last_seen": item["last_seen"], "age_seconds": round(age, 1), "stale": stale, "healthy": healthy, "restored": restored, "archived": archived, "status": "disconnected" if archived else ("stale" if stale else "online")})
        return sorted(result, key=lambda entry: entry["hostname"])

    async def publish(self, snapshot: dict[str, Any], *, collected: bool = True) -> None:
        if collected:
            self.local_status = snapshot["global_status"]
        else:
            snapshot["global_status"] = self.local_status
        snapshot["vps"] = self.vps_snapshot()
        if any(not item["healthy"] and not item["archived"] for item in snapshot["vps"]):
            snapshot["global_status"] = "incident"
        snapshot["events"] = list(self.collector.events)
        snapshot["sentinel"] = self.sentinel.snapshot()
        snapshot["cyber"] = self.cyber.snapshot()
        snapshot["voice"] = self.whatsapp.snapshot()
        serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        self.latest = snapshot
        self.serialized = serialized
        if collected:
            self.last_published = time.monotonic()
        async with self.changed:
            self.revision += 1
            self.changed.notify_all()

    async def publish_module_update(self) -> None:
        if self.latest is not None:
            await self.publish(dict(self.latest), collected=False)

    async def restart_service(self, name: str) -> dict[str, Any]:
        """Chemin commun UI/WhatsApp après l'authentification propre au canal."""
        if name not in RESTART_ALLOWLIST:
            raise HTTPException(404, "Service non autorisé")
        sudo = shutil.which("sudo")
        systemctl = self.collector.systemctl
        if not self.settings.enable_restart or not systemctl or not sudo:
            raise HTTPException(503, "Redémarrages indisponibles sur cet hôte")
        lock = self.restart_locks[name]
        if lock.locked() or time.monotonic() - self.restart_times.get(name, -math.inf) < 30:
            raise HTTPException(429, "Redémarrage déjà demandé : patienter 30 secondes")
        async with lock:
            self.restart_times[name] = time.monotonic()
            self.collector.add_event("warning", f"Redémarrage demandé · {name}")
            try:
                code, _, _ = await collector.run_command([sudo, "-n", systemctl, "--no-block", "restart", name + ".service"], 5)
            except (OSError, asyncio.TimeoutError, RuntimeError):
                self.collector.add_event("error", f"Redémarrage non confirmé · {name}")
                raise HTTPException(504, "Commande expirée ou indisponible; vérifier le journal") from None
            if code != 0:
                self.collector.add_event("error", f"Redémarrage refusé · {name}")
                raise HTTPException(502, "systemd a refusé la commande; vérifier sudoers et le journal")
            self.collector.add_event("info", f"Redémarrage accepté par systemd · {name}")
            await self.publish_module_update()
            return {"ok": True, "service": name, "message": "Redémarrage accepté. Le collecteur vérifiera le retour du service."}

    async def collect_loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                snapshot = await self.collector.collect()
                if self.collector._states.get("collector") == "failed":
                    self.collector.transition("collector", "ready", "Collecteur rétabli", True)
                await self.publish(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("Collecte indisponible : %s", type(exc).__name__)
                self.collector.transition("collector", "failed", "Collecteur indisponible · nouvel essai automatique", False)
                if self.latest is not None:
                    snapshot = dict(self.latest)
                    snapshot["timestamp"] = collector.utc_now()
                    snapshot["global_status"] = "incident"
                    snapshot["collector"] = {"last_success": self.latest["collector"]["last_success"], "error": "Collecte indisponible"}
                    await self.publish(snapshot)
            await asyncio.sleep(max(0.05, self.settings.interval - (time.monotonic() - started)))


class CountedEventSourceResponse(EventSourceResponse):
    """Libère la place même si le client part avant le premier événement."""

    def __init__(self, *args, runtime: Runtime, **kwargs):
        self.runtime = runtime
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.runtime.active_streams = max(0, self.runtime.active_streams - 1)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = Runtime(settings)
        application.state.runtime = runtime
        runtime.vps = await runtime.vps_registry.load()
        await runtime.collector.start()
        await runtime.sentinel.start()
        await runtime.cyber.start()
        await runtime.whatsapp.start()
        task = asyncio.create_task(runtime.collect_loop(), name="noc-collector")
        runtime.collect_task = task
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await runtime.collector.close()
            await runtime.whatsapp.close()
            await runtime.sentinel.close()
            await runtime.cyber.close()

    application = FastAPI(title="NOC · Tour de contrôle", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        runtime = getattr(application.state, "runtime", None)
        peer = request.client.host if request.client else ""
        # Les headers X-Forwarded-For/Forwarded reçus ne sont jamais une identité
        # pour les bannissements. Le lancement Uvicorn désactive leur réécriture.
        if (runtime and runtime.cyber.is_banned(peer) and
                request.url.path.rstrip("/") not in TRAP_PATHS):
            response = JSONResponse({"detail": "Accès refusé"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob: data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @application.get("/")
    async def dashboard():
        return FileResponse(BASE_DIR / "static" / "index.html", media_type="text/html")

    @application.get("/static/live_metrics.js")
    async def live_metrics_script():
        return FileResponse(BASE_DIR / "static" / "live_metrics.js", media_type="application/javascript")

    @application.get("/static/live_metrics.css")
    async def live_metrics_styles():
        return FileResponse(BASE_DIR / "static" / "live_metrics.css", media_type="text/css")

    @application.get("/static/cosmos.js")
    async def cosmos_script():
        return FileResponse(BASE_DIR / "static" / "cosmos.js", media_type="application/javascript")

    @application.get("/static/voice.js", include_in_schema=False)
    async def browser_voice_script():
        return FileResponse(BASE_DIR / "static" / "voice.js", media_type="application/javascript")

    @application.get("/static/cosmos.css")
    async def cosmos_styles():
        return FileResponse(BASE_DIR / "static" / "cosmos.css", media_type="text/css")

    @application.get("/static/mobile.js", include_in_schema=False)
    async def mobile_script():
        return FileResponse(BASE_DIR / "static" / "mobile.js", media_type="application/javascript")

    @application.get("/static/mobile.css", include_in_schema=False)
    async def mobile_styles():
        return FileResponse(BASE_DIR / "static" / "mobile.css", media_type="text/css")

    @application.get("/manifest.webmanifest", include_in_schema=False)
    async def pwa_manifest():
        return FileResponse(BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")

    @application.get("/service-worker.js", include_in_schema=False)
    async def pwa_worker():
        return FileResponse(BASE_DIR / "static" / "service-worker.js", media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/"})

    @application.get("/static/pwa.js", include_in_schema=False)
    async def pwa_script():
        return FileResponse(BASE_DIR / "static" / "pwa.js", media_type="application/javascript")

    @application.get("/static/pwa.css", include_in_schema=False)
    async def pwa_styles():
        return FileResponse(BASE_DIR / "static" / "pwa.css", media_type="text/css")

    @application.get("/static/icons/{name}", include_in_schema=False)
    async def pwa_icon(name: str):
        if name not in {
            "moctar-192.png", "moctar-512.png",
            "moctar-logo-v2-180.png", "moctar-logo-v2-192.png", "moctar-logo-v2-512.png",
        }:
            raise HTTPException(status_code=404)
        return FileResponse(BASE_DIR / "static" / "icons" / name, media_type="image/png")

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    async def ai_status_payload() -> dict[str, Any]:
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            return {
                "status": "offline",
                "available": False,
                "online": False,
                "model_offline": True,
                "all_providers_offline": True,
                "message": "Module Jarvis headless non disponible sur ce serveur.",
            }
        try:
            # Provider probes use synchronous HTTP; never block the ASGI loop.
            status = await asyncio.to_thread(runtime.jarvis.get_status)
            return {**status, "available": True}
        except Exception as exc:
            LOGGER.warning("Lecture du statut IA indisponible : %s", exc)
            # A failed status probe does not prove that both providers are down.
            return {"status": "error", "available": True, "error": "Statut IA temporairement indisponible."}

    @application.get("/api/health")
    async def health():
        runtime: Runtime = application.state.runtime
        latest = runtime.latest
        last_success = latest["collector"].get("last_success") if latest else None
        collecting = bool(last_success and not latest["collector"].get("error") and time.monotonic() - runtime.last_published <= max(10, 3 * settings.interval) and runtime.collect_task and not runtime.collect_task.done())
        ai_status = await ai_status_payload()
        return JSONResponse({"status": "ok" if collecting else "starting" if latest is None else "degraded", "collector_ready": collecting, "interval_seconds": settings.interval, "process_id": os.getpid(), "ai": ai_status, "ai_online": ai_status.get("online")}, status_code=200 if collecting else 503)

    @application.get("/api/overview")
    @application.get("/api/snapshot")
    async def snapshot():
        runtime: Runtime = application.state.runtime
        if runtime.latest is None:
            raise HTTPException(503, "Première collecte en cours", headers={"Retry-After": "2"})
        return Response(content=runtime.serialized, media_type="application/json")

    @application.get("/events")
    @application.get("/api/stream")
    async def stream(request: Request):
        runtime: Runtime = application.state.runtime
        peer = request.client.host if request.client else ""
        if runtime.active_streams >= settings.max_streams:
            raise HTTPException(429, "Limite de flux simultanés atteinte", headers={"Retry-After": "5"})
        runtime.active_streams += 1

        async def events():
            revision = -1
            while True:
                if await request.is_disconnected() or runtime.cyber.is_banned(peer):
                    break
                async with runtime.changed:
                    if runtime.revision == revision or runtime.latest is None:
                        try:
                            await asyncio.wait_for(runtime.changed.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            continue
                    revision = runtime.revision
                    data = runtime.serialized
                # Un ban doit aussi fermer les flux déjà ouverts : leur réveil
                # est déclenché par la publication de l'incident dans le SSE.
                if runtime.cyber.is_banned(peer):
                    break
                yield {"event": "snapshot", "id": str(revision), "retry": 2000, "data": data}

        return CountedEventSourceResponse(events(), runtime=runtime, ping=15, send_timeout=10, headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-store"})

    @application.post("/api/vps/report")
    async def vps_report(request: Request):
        if not settings.vps_token:
            raise HTTPException(503, "Rapports VPS désactivés : jeton non configuré")
        if not token_matches(request.headers.get("x-vps-token"), settings.vps_token):
            raise HTTPException(401, "Jeton VPS incorrect")
        report: VPSReport = validate_payload(VPSReport, await limited_json(request, 32768))
        runtime: Runtime = application.state.runtime
        runtime.vps_snapshot()
        entry = {"report": report.model_dump(), "seen": time.monotonic(), "last_seen": collector.utc_now(), "stale": False, "restored": False}
        if not await runtime.vps_registry.upsert(runtime.vps, report.hostname, entry):
            raise HTTPException(429, "Limite de serveurs VPS atteinte")
        await runtime.publish_module_update()
        return {"ok": True, "hostname": report.hostname}

    @application.delete("/api/vps/{hostname}")
    async def delete_vps(hostname: Annotated[str, APIPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")], request: Request):
        same_origin(request)
        if not settings.vps_token and not settings.gateway_secret:
            raise HTTPException(503, "Gestion VPS désactivée : jeton non configuré")
        if not (token_matches(request.headers.get("x-vps-token"), settings.vps_token)
                or token_matches(request.headers.get("x-gateway-secret"), settings.gateway_secret)):
            raise HTTPException(401, "Jeton VPS ou passerelle incorrect")
        runtime: Runtime = application.state.runtime
        try:
            removed = await runtime.vps_registry.remove(runtime.vps, hostname)
        except OSError:
            LOGGER.exception("Suppression VPS non persistée · %s", hostname)
            raise HTTPException(503, "Registre VPS indisponible ; nœud conservé") from None
        if not removed:
            raise HTTPException(404, "Serveur VPS inconnu")
        runtime.collector._states.pop("vps:" + hostname, None)
        runtime.collector.add_event("info", f"VPS {hostname} · supprimé du registre")
        await runtime.publish_module_update()
        return {"ok": True, "hostname": hostname, "removed": True}

    @application.post("/api/service/{name}/restart")
    async def restart(name: str, request: Request):
        same_origin(request)
        if name not in RESTART_ALLOWLIST:
            raise HTTPException(404, "Service non autorisé")
        runtime: Runtime = application.state.runtime
        return await runtime.restart_service(name)

    async def honeypot(request: Request):
        """Ne lit ni ne renvoie de vrais secrets ; enregistre seulement IP/route."""
        runtime: Runtime = application.state.runtime
        result = await runtime.cyber.trap(request.client.host if request.client else "",
                                         request.url.path.rstrip("/"))
        return JSONResponse({"detail": "Accès refusé" if result["blocked"] else "Not Found"},
                            status_code=403 if result["blocked"] else 404)

    for trap_path in sorted(TRAP_PATHS):
        for path in (trap_path, trap_path + "/"):
            application.add_api_route(path, honeypot, methods=["GET", "POST"], include_in_schema=False)

    @application.get("/api/cyber/banned-ips")
    async def cyber_banned_ips():
        return application.state.runtime.cyber.snapshot()

    @application.post("/api/cyber/simulate")
    async def cyber_simulate(request: Request):
        same_origin(request)
        payload = await limited_json(request, 1024)
        if not isinstance(payload, dict) or set(payload) != {"path"} or not isinstance(payload["path"], str):
            raise HTTPException(422, "Objet JSON attendu : {path: route piège}")
        if payload["path"] not in TRAP_PATHS:
            raise HTTPException(422, "Route de simulation non autorisée")
        return await application.state.runtime.cyber.simulate(payload["path"])

    @application.delete("/api/cyber/unban/{ip}")
    async def cyber_unban(ip: str, request: Request):
        same_origin(request)
        try:
            return await application.state.runtime.cyber.unban(ip)
        except ValueError:
            raise HTTPException(422, "Adresse IP invalide") from None

    @application.post("/api/cyber/audit")
    async def cyber_audit(request: Request):
        same_origin(request)
        return await application.state.runtime.cyber.audit_refresh()

    @application.get("/api/cyber/export")
    async def cyber_export():
        # Export local sans secrets de passerelle ni mots de passe Sentinel.
        return JSONResponse(application.state.runtime.cyber.snapshot(), headers={
            "Content-Disposition": 'attachment; filename="noc-cyberdefense-logs.json"'})

    def require_gateway(request: Request) -> None:
        same_origin(request)
        if not application.state.runtime.whatsapp.webhook_configured:
            raise HTTPException(503, "Commandes WhatsApp non configurées : WHATSAPP_ADMIN_NUMBERS et NOC_WHATSAPP_WEBHOOK_TOKEN requis")
        if not token_matches(request.headers.get("x-gateway-secret"), settings.whatsapp_webhook_token):
            raise HTTPException(403, "Requête non autorisée")

    @application.get("/api/voice/status")
    async def voice_status():
        return application.state.runtime.whatsapp.snapshot()

    class VoiceTTSPayload(BaseModel):
        model_config = ConfigDict(extra="ignore")
        text: str = Field(..., min_length=1, max_length=4000)
        voice: str = Field(default=DEFAULT_TTS_VOICE, max_length=80)

        @model_validator(mode="after")
        def validate_text_and_voice(self) -> VoiceTTSPayload:
            if not self.text.strip():
                raise ValueError("Le texte ne peut pas être vide")
            if self.voice not in ALLOWED_TTS_VOICES:
                self.voice = DEFAULT_TTS_VOICE
            return self

    @application.post("/api/voice/tts")
    async def voice_tts(request: Request):
        data = await limited_json(request, 16384)
        payload = validate_payload(VoiceTTSPayload, data)
        try:
            audio_bytes, cache_hit = await generate_cached_tts(payload.text, payload.voice)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception as exc:
            LOGGER.warning("Synthèse vocale edge-tts indisponible : %s", exc)
            raise HTTPException(503, f"Synthèse vocale indisponible : {exc}") from None
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "X-TTS-Cache": "HIT" if cache_hit else "MISS",
                "X-TTS-Voice": payload.voice,
            },
        )

    @application.post("/api/voice/transcribe")
    async def voice_transcribe_endpoint(request: Request):
        raw_content_type = request.headers.get("content-type", "")
        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        max_bytes = 12 * 1024 * 1024  # 12 Mio max pour un enregistrement vocal direct
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > max_bytes or int(length) < 0:
                    raise HTTPException(413, "Audio trop volumineux (12 Mio maximum)")
            except ValueError:
                raise HTTPException(400, "Content-Length invalide") from None

        body = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(body) + len(chunk) > max_bytes:
                    raise HTTPException(413, "Audio trop volumineux (12 Mio maximum)")
                body.extend(chunk)

        if not body:
            raise HTTPException(422, "Fichier audio vide")

        audio_bytes = bytes(body)
        detected_mime = content_type or "audio/wav"
        if content_type == "multipart/form-data":
            try:
                audio_bytes, detected_mime = _extract_multipart_audio(audio_bytes, raw_content_type)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        elif content_type and content_type not in DIRECT_AUDIO_MIME_TYPES:
            raise HTTPException(415, "Format audio non supporté (attendu: WAV, WebM, OGG, MP3 ou multipart/form-data)")

        try:
            transcript = await transcribe_direct_audio(audio_bytes, detected_mime)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception as exc:
            LOGGER.warning("Erreur de transcription faster-whisper : %s", exc)
            raise HTTPException(500, f"Échec de transcription : {exc}") from None

        return JSONResponse(
            {
                "ok": True,
                "text": transcript,
                "language": "fr",
                "engine": "faster-whisper",
            }
        )

    @application.get("/api/voice/tts/cache/{filename}")
    async def voice_tts_cache_file(
        filename: Annotated[str, APIPath(pattern=r"^[a-fA-F0-9]{16,64}\.mp3$")],
    ):
        cache_key = filename[:-4].lower()
        if cache_key in _TTS_MEMORY_CACHE:
            return Response(
                content=_TTS_MEMORY_CACHE[cache_key],
                media_type="audio/mpeg",
                headers={"X-TTS-Cache": "HIT", "X-TTS-Voice": DEFAULT_TTS_VOICE},
            )
        cache_file = get_tts_cache_dir() / f"{cache_key}.mp3"
        if cache_file.is_file():
            try:
                data = await asyncio.to_thread(cache_file.read_bytes)
                if data:
                    return Response(
                        content=data,
                        media_type="audio/mpeg",
                        headers={"X-TTS-Cache": "HIT", "X-TTS-Voice": DEFAULT_TTS_VOICE},
                    )
            except OSError:
                pass
        raise HTTPException(404, "Fichier audio TTS introuvable en cache")

    @application.post("/api/voice/interact")
    async def voice_interact_endpoint(request: Request):
        raw_content_type = request.headers.get("content-type", "")
        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        max_bytes = 12 * 1024 * 1024
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > max_bytes or int(length) < 0:
                    raise HTTPException(413, "Audio trop volumineux (12 Mio maximum)")
            except ValueError:
                raise HTTPException(400, "Content-Length invalide") from None

        body = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(body) + len(chunk) > max_bytes:
                    raise HTTPException(413, "Audio trop volumineux (12 Mio maximum)")
                body.extend(chunk)

        if not body:
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "empty_audio",
                    "user_text": "",
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        audio_bytes = bytes(body)
        detected_mime = content_type or "audio/webm"
        if content_type == "multipart/form-data":
            try:
                audio_bytes, detected_mime = _extract_multipart_audio(audio_bytes, raw_content_type)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        elif content_type and content_type not in DIRECT_AUDIO_MIME_TYPES:
            raise HTTPException(415, "Format audio non supporté (attendu: WAV, WebM, OGG, MP3 ou multipart/form-data)")

        try:
            transcript = await transcribe_direct_audio(audio_bytes, detected_mime)
        except ValueError:
            transcript = ""
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception as exc:
            LOGGER.warning("Erreur de transcription faster-whisper sur /api/voice/interact : %s", exc)
            raise HTTPException(500, f"Échec de transcription : {exc}") from None

        if not transcript:
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "silence_or_hallucination",
                    "user_text": "",
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        runtime: Runtime = application.state.runtime
        header_session = request.headers.get("x-voice-session-active", "").strip().lower() in {"1", "true", "yes"}
        query_session = request.query_params.get("session_active", "").strip().lower() in {"1", "true", "yes"}
        runtime_session = getattr(runtime, "voice_session_until", 0.0) > time.monotonic()
        session_active = header_session or query_session or runtime_session

        accepted, command_text, wake_detected = extract_voice_interact_command(
            transcript,
            session_active=session_active,
        )
        if not accepted or not command_text:
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "wake_word_missing",
                    "user_text": transcript,
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        runtime.voice_session_until = time.monotonic() + 60.0

        agent_result: dict[str, Any] = {}
        if runtime.jarvis:
            try:
                agent_result = await runtime.jarvis.execute_prompt_async(
                    user_input=command_text,
                    source="web_voice",
                )
            except Exception as exc:
                LOGGER.warning("Erreur AgentCore lors de /api/voice/interact : %s", exc)
                agent_result = {
                    "status": "error",
                    "reply": "Je suis à l'écoute Moctar, mais une erreur interne est survenue.",
                }
        else:
            agent_result = {
                "status": "offline",
                "reply": "Je suis en ligne Moctar, mais le module AgentCore n'est pas initialisé.",
            }

        reply_text = str(
            agent_result.get("reply")
            or agent_result.get("response")
            or "Bien reçu, Moctar."
        ).strip()

        audio_url: str | None = None
        try:
            _, _, cache_key = await generate_cached_tts_with_key(reply_text, DEFAULT_TTS_VOICE)
            audio_url = f"/api/voice/tts/cache/{cache_key}.mp3"
        except Exception as exc:
            LOGGER.warning("Synthèse edge-tts indisponible sur /api/voice/interact : %s", exc)

        return JSONResponse(
            {
                "ok": True,
                "ignored": False,
                "user_text": command_text,
                "raw_transcript": transcript,
                "reply_text": reply_text,
                "audio_url": audio_url,
                "session_active": True,
                "wake_word_detected": wake_detected,
                "action": agent_result.get("action"),
                "url": agent_result.get("url"),
                "title": agent_result.get("title"),
                "tools_used": agent_result.get("tools_used", []),
                "thought_chain": agent_result.get("thought_chain", []),
                "fallback_mode": bool(agent_result.get("fallback_mode")),
                "model_offline": bool(agent_result.get("model_offline")),
            }
        )

    @application.post("/api/voice/simulate", status_code=202)
    async def voice_simulate(request: Request):
        same_origin(request)
        payload = await limited_json(request, 4096)
        if not isinstance(payload, dict) or set(payload) != {"text"}:
            raise HTTPException(422, "Objet JSON attendu : {text: commande}")
        try:
            return await application.state.runtime.whatsapp.simulate(payload["text"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except asyncio.QueueFull:
            raise HTTPException(429, "File de commandes pleine", headers={"Retry-After": "5"}) from None

    @application.post("/api/voice/webhook")
    @application.post("/api/whatsapp/webhook")
    async def whatsapp_webhook(request: Request):
        require_gateway(request)
        payload = await limited_json(request, 32768)
        if not isinstance(payload, dict):
            raise HTTPException(422, "Message WhatsApp invalide")
        # L'ID Baileys peut venir du header, mais doit rester stable lors des reprises.
        message_header = request.headers.get("x-message-id")
        data = payload.get("data", payload)
        if message_header and isinstance(data, dict):
            key = data.get("key") if isinstance(data.get("key"), dict) else {}
            supplied = data.get("id", data.get("messageId", key.get("id")))
            if supplied is not None and supplied != message_header:
                raise HTTPException(422, "Identifiants de message contradictoires")
            if supplied is None:
                payload = dict(payload)
                enriched = {**data, "id": message_header}
                if "data" in payload:
                    payload["data"] = enriched
                else:
                    payload = enriched
        try:
            result = await application.state.runtime.whatsapp.accept(payload)
            LOGGER.info("Webhook WhatsApp accepté=%s doublon=%s",
                        result.get("accepted"), result.get("duplicate", False))
            return result
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError:
            raise HTTPException(422, "Message invalide : fournir l'ID Baileys stable (id, messageId ou X-Message-Id) et un texte valide") from None
        except asyncio.QueueFull:
            raise HTTPException(429, "File de commandes pleine", headers={"Retry-After": "5"}) from None

    @application.post("/api/voice/process", status_code=202)
    async def voice_process(request: Request):
        """Audio déjà déchiffré envoyé par Baileys; jamais un chemin client à lire."""
        require_gateway(request)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            return await whatsapp_webhook(request)
        if content_type not in AUDIO_MIME_TYPES | {"application/octet-stream"}:
            raise HTTPException(415, "Envoyez le fichier audio brut (OGG, Opus, MP3 ou WAV), ou un message JSON")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(415, "Audio HTTP compressé interdit")
        controller = application.state.runtime.whatsapp
        payload = {"sessionId": request.headers.get("x-session-id", settings.whatsapp_session_id),
                   "from": request.headers.get("x-whatsapp-from", request.headers.get("x-sender")),
                   "id": request.headers.get("x-message-id"),
                   "audio": {"path": "uploaded-audio", "mimetype": "audio/ogg" if content_type == "application/octet-stream" else content_type}}
        if request.headers.get("x-sender"):
            payload["sender"] = request.headers["x-sender"]
        if request.headers.get("x-sender-pn"):
            payload["senderPn"] = request.headers["x-sender-pn"]
        try:
            controller.authorize(payload)
        except PermissionError as exc:
            with contextlib.suppress(Exception):
                await controller._publish()
            raise HTTPException(403, str(exc)) from None
        except ValueError:
            raise HTTPException(422, "Headers X-WhatsApp-From (ou X-Sender) et X-Message-Id valides requis") from None
        if controller.uploading >= 2 or controller.conversation_queue.full():
            raise HTTPException(429, "File de commandes pleine", headers={"Retry-After": "5"})
        limit = settings.whatsapp_max_audio_bytes
        length = request.headers.get("content-length")
        if length:
            try:
                if not 0 < int(length) <= limit:
                    raise HTTPException(413, "Audio vide ou trop volumineux (8 Mio maximum)")
            except ValueError:
                raise HTTPException(400, "Content-Length invalide") from None
        controller.uploading += 1
        owned_by_worker = False
        path = None
        try:
            descriptor, filename = tempfile.mkstemp(prefix="noc-voice-upload-", suffix=".audio",
                                                    dir="/tmp" if os.name == "posix" else None)
            path = Path(filename)
            with os.fdopen(descriptor, "wb") as outgoing:
                count = 0
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > limit:
                            raise HTTPException(413, "Audio trop volumineux (8 Mio maximum)")
                        await asyncio.to_thread(outgoing.write, chunk)
                if count == 0:
                    raise HTTPException(422, "Fichier audio vide")
            result = await controller.accept(payload, uploaded_audio=path)
            owned_by_worker = result.get("accepted") is True and not result.get("duplicate")
            return result
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError:
            raise HTTPException(422, "Message audio invalide") from None
        except TimeoutError:
            raise HTTPException(408, "Envoi audio expiré") from None
        except asyncio.QueueFull:
            raise HTTPException(429, "File de commandes pleine", headers={"Retry-After": "5"}) from None
        finally:
            controller.uploading -= 1
            if path is not None and not owned_by_worker:
                path.unlink(missing_ok=True)

    @application.post("/api/sentinel/incident")
    async def sentinel_incident(request: Request):
        if not settings.vps_token:
            raise HTTPException(503, "Sentinel désactivé : jeton VPS non configuré")
        if not token_matches(request.headers.get("x-vps-token"), settings.vps_token):
            raise HTTPException(401, "Jeton VPS incorrect")
        return await application.state.runtime.sentinel.accept(await limited_json(request, 16384))

    # --------------------------------------------------------------------------
    # API AGENT AUTONOME JARVIS
    # --------------------------------------------------------------------------

    class JarvisQueryPayload(BaseModel):
        model_config = ConfigDict(extra="ignore")
        prompt: str = Field(..., min_length=1, max_length=4000)
        source: str = Field(default="web_text")

        @model_validator(mode="after")
        def not_blank(self) -> JarvisQueryPayload:
            if not self.prompt.strip():
                raise ValueError("Le prompt ne peut pas être vide")
            return self

    @application.post("/api/jarvis/query")
    async def jarvis_query(request: Request) -> JSONResponse:
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            raise HTTPException(503, "Agent Jarvis non initialisé ou indisponible")
        data = await limited_json(request, 32768)
        payload = validate_payload(JarvisQueryPayload, data)
        try:
            result = await runtime.jarvis.execute_prompt_async(
                user_input=payload.prompt.strip(),
                source=payload.source,
            )
            return JSONResponse(result)
        except Exception as exc:
            LOGGER.exception("Erreur lors de l'exécution de Jarvis")
            return JSONResponse(
                {
                    "status": "error",
                    "reply": f"Erreur interne lors de l'exécution de l'agent : {exc}",
                    "thought_chain": [],
                    "tools_used": [],
                    "timestamp": collector.utc_now(),
                },
                status_code=500,
            )

    @application.get("/api/status")
    @application.get("/api/system/ai-status")
    @application.get("/api/jarvis/status")
    async def jarvis_status() -> JSONResponse:
        status = await ai_status_payload()
        return JSONResponse(status, status_code=500 if status["status"] == "error" else 200)

    @application.get("/api/jarvis/history")
    async def jarvis_history(limit: int = 20) -> JSONResponse:
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            return JSONResponse({"history": [], "messages": []})
        try:
            history = runtime.jarvis.get_history(limit=min(max(1, limit), 100))
            return JSONResponse({"history": history, "messages": history, "total": len(history)})
        except Exception as exc:
            return JSONResponse({"error": str(exc), "history": [], "messages": []}, status_code=500)

    @application.delete("/api/jarvis/history/clear")
    async def jarvis_clear_history() -> JSONResponse:
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            raise HTTPException(503, "Agent Jarvis non initialisé")
        try:
            res = runtime.jarvis.clear_history()
            res["ok"] = True
            return JSONResponse(res)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc), "ok": False}, status_code=500)

    # --------------------------------------------------------------------------
    # API PLANIFICATEUR D'AUTOMATISATIONS & TÂCHES
    # --------------------------------------------------------------------------
    @application.get("/api/scheduler/tasks")
    async def scheduler_list_tasks(status: str | None = None) -> JSONResponse:
        try:
            from scheduler import scheduler_store
            tasks = scheduler_store.list_tasks(status=status)
            return JSONResponse({"tasks": tasks, "total": len(tasks), "ok": True})
        except Exception as exc:
            return JSONResponse({"error": str(exc), "tasks": [], "total": 0, "ok": False}, status_code=500)

    @application.post("/api/scheduler/tasks")
    async def scheduler_create_task(request: Request) -> JSONResponse:
        try:
            from scheduler import scheduler_store
            data = await limited_json(request, 65536)
            title = str(data.get("title") or "").strip()
            message = str(data.get("message") or "").strip()
            if not title or not message:
                raise HTTPException(400, "Le titre et le message sont obligatoires.")
            platform = str(data.get("platform") or "system").strip().lower()
            target = str(data.get("target") or "").strip()
            send_time = str(data.get("send_time") or "09:00").strip()
            frequency = str(data.get("frequency") or "daily").strip().lower()
            weekday = data.get("weekday")
            ai_enhanced = bool(data.get("ai_enhanced", False))
            media_paths = data.get("media_paths") or []

            task = scheduler_store.create_task(
                title=title,
                platform=platform,
                target=target,
                message=message,
                media_paths=media_paths,
                frequency=frequency,
                weekday=int(weekday) if weekday is not None else None,
                send_time=send_time,
                ai_enhanced=ai_enhanced,
            )
            return JSONResponse({"task": task, "ok": True}, status_code=201)
        except HTTPException:
            raise
        except Exception as exc:
            return JSONResponse({"error": str(exc), "ok": False}, status_code=500)

    @application.post("/api/scheduler/tasks/{task_id}/toggle")
    async def scheduler_toggle_task(task_id: int) -> JSONResponse:
        try:
            from scheduler import scheduler_store
            task = scheduler_store.get_task(task_id)
            if not task:
                raise HTTPException(404, "Tâche introuvable")
            new_status = "paused" if task.get("status") == "active" else "active"
            updated = scheduler_store.update_task(task_id, status=new_status)
            return JSONResponse({"task": updated, "status": new_status, "ok": True})
        except HTTPException:
            raise
        except Exception as exc:
            return JSONResponse({"error": str(exc), "ok": False}, status_code=500)

    @application.post("/api/scheduler/tasks/{task_id}/confirm")
    async def scheduler_confirm_task(task_id: int) -> JSONResponse:
        try:
            from scheduler import scheduler_core, scheduler_store
            task = scheduler_store.get_task(task_id)
            if not task:
                raise HTTPException(404, "Tâche introuvable")
            success, detail = scheduler_core.confirm_and_send(task_id)
            return JSONResponse({"ok": success, "detail": detail, "task": scheduler_store.get_task(task_id)})
        except HTTPException:
            raise
        except Exception as exc:
            return JSONResponse({"error": str(exc), "ok": False}, status_code=500)

    @application.delete("/api/scheduler/tasks/{task_id}")
    async def scheduler_delete_task(task_id: int) -> JSONResponse:
        try:
            from scheduler import scheduler_store
            deleted = scheduler_store.delete_task(task_id)
            return JSONResponse({"deleted": deleted, "ok": True})
        except Exception as exc:
            return JSONResponse({"error": str(exc), "ok": False}, status_code=500)

    return application


app = create_app()


def run_server(settings: Settings | None = None) -> None:
    """Launch HTTPS when both certificate files are present, otherwise HTTP."""
    import uvicorn

    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ssl_options: dict[str, str] = {}
    try:
        if Path(settings.ssl_keyfile).is_file() and Path(settings.ssl_certfile).is_file():
            ssl_options = {"ssl_keyfile": settings.ssl_keyfile, "ssl_certfile": settings.ssl_certfile}
    except OSError:
        LOGGER.warning("Certificats SSL inaccessibles ; demarrage HTTP.")
    LOGGER.info("Demarrage %s sur %s:%s", "HTTPS" if ssl_options else "HTTP", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, loop="asyncio", http="h11", ws="none", access_log=False, proxy_headers=False, **ssl_options)


if __name__ == "__main__":
    run_server()
