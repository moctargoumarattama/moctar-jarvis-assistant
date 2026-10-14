"""API NOC, consultation directe et flux SSE partagé, un seul worker."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

import collector
from config import RESTART_ALLOWLIST, Settings
from cyberdefense import CyberDefenseManager, TRAP_PATHS
from sentinel import SentinelManager
from vps_registry import VPSRegistry
from whatsapp import AUDIO_MIME_TYPES, WhatsAppController, parse_message

LOGGER = logging.getLogger("noc-dashboard")
BASE_DIR = Path(__file__).resolve().parent

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
            healthy = not stale and item["report"]["cpu_percent"] < 95 and item["report"]["memory"]["percent"] < 90 and item["report"]["disk"]["percent"] < 90 and all(service["status"] == "active" for service in item["report"]["services"])
            self.collector.transition("vps:" + hostname, (stale, healthy), f"VPS {hostname} · {'rapport expiré' if stale else ('opérationnel' if healthy else 'incident détecté')}", healthy)
            item["stale"] = stale
            result.append({**item["report"], "last_seen": item["last_seen"], "age_seconds": round(age, 1), "stale": stale, "healthy": healthy, "restored": restored})
        return sorted(result, key=lambda entry: entry["hostname"])

    async def publish(self, snapshot: dict[str, Any], *, collected: bool = True) -> None:
        snapshot["vps"] = self.vps_snapshot()
        if any(not item["healthy"] for item in snapshot["vps"]):
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
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
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

    @application.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @application.get("/api/health")
    async def health():
        runtime: Runtime = application.state.runtime
        latest = runtime.latest
        last_success = latest["collector"].get("last_success") if latest else None
        collecting = bool(last_success and not latest["collector"].get("error") and time.monotonic() - runtime.last_published <= max(10, 3 * settings.interval) and runtime.collect_task and not runtime.collect_task.done())
        return JSONResponse({"status": "ok" if collecting else "starting" if latest is None else "degraded", "collector_ready": collecting, "interval_seconds": settings.interval, "process_id": os.getpid()}, status_code=200 if collecting else 503)

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
        if report.hostname not in runtime.vps and len(runtime.vps) >= settings.max_vps:
            raise HTTPException(429, "Limite de serveurs VPS atteinte")
        runtime.vps[report.hostname] = {"report": report.model_dump(), "seen": time.monotonic(), "last_seen": collector.utc_now(), "stale": False, "restored": False}
        await runtime.vps_registry.persist(runtime.vps)
        await runtime.publish_module_update()
        return {"ok": True, "hostname": report.hostname}

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
        if not settings.admin_phone_number or not settings.whatsapp_webhook_token:
            raise HTTPException(503, "Commandes WhatsApp non configurées : ADMIN_PHONE_NUMBER et NOC_WHATSAPP_WEBHOOK_TOKEN requis")
        if not token_matches(request.headers.get("x-gateway-secret"), settings.whatsapp_webhook_token):
            raise HTTPException(403, "Requête non autorisée")

    @application.get("/api/voice/status")
    async def voice_status():
        return application.state.runtime.whatsapp.snapshot()

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
            return await application.state.runtime.whatsapp.accept(payload)
        except PermissionError:
            raise HTTPException(403, "Émetteur non autorisé") from None
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
            parse_message(payload, settings.admin_phone_number)
            if payload["sessionId"] != settings.whatsapp_session_id:
                raise PermissionError()
        except PermissionError:
            raise HTTPException(403, "Émetteur ou session non autorisé") from None
        except ValueError:
            raise HTTPException(422, "Headers X-WhatsApp-From (ou X-Sender) et X-Message-Id valides requis") from None
        if controller.uploading >= 2 or controller.queue.full():
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
        except PermissionError:
            raise HTTPException(403, "Émetteur non autorisé") from None
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

    @application.get("/api/jarvis/status")
    async def jarvis_status() -> JSONResponse:
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            return JSONResponse(
                {
                    "status": "offline",
                    "available": False,
                    "message": "Module Jarvis headless non disponible sur ce serveur.",
                }
            )
        try:
            status = runtime.jarvis.get_status()
            status["available"] = True
            return JSONResponse(status)
        except Exception as exc:
            return JSONResponse(
                {
                    "status": "error",
                    "available": False,
                    "error": str(exc),
                },
                status_code=500,
            )

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

if __name__ == "__main__":
    import uvicorn
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, loop="asyncio", http="h11", ws="none", access_log=False, proxy_headers=False)
