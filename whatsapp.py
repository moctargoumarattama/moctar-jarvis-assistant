"""Commandes WhatsApp bornées, liste blanche et transcription hors du processus NOC.

Le routeur vérifie le secret du webhook AVANT d'appeler ce module. Le numéro seul
ne prouve pas l'origine d'un webhook. Aucun texte reçu n'est passé à un shell.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, unquote, urlsplit

import httpx

from collector import utc_now
from config import Settings
import system_manager

LOGGER = logging.getLogger(__name__)

AUDIO_MIME_TYPES = frozenset({"audio/ogg", "audio/opus", "application/ogg",
                              "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav"})


def normalize_phone(value: Any) -> str:
    """Accepter un numéro personnel international; jamais un groupe ou un LID."""
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Numéro WhatsApp invalide")
    phone = value.strip()
    if "@" in phone:
        phone, domain = phone.rsplit("@", 1)
        if domain not in {"c.us", "s.whatsapp.net"}:
            raise ValueError("Seuls les messages personnels sont autorisés")
        # Baileys peut désigner un appareil PN par numéro:appareil@serveur.
        # Cette règle ne s'applique jamais aux identifiants indépendants @lid.
        match = re.fullmatch(r"([0-9]{8,15})(?::[0-9]{1,10})?", phone)
        if not match:
            raise ValueError("Identifiant personnel WhatsApp invalide")
        return match[1]
    if not re.fullmatch(r"\+?[0-9 ()-]+", phone):
        raise ValueError("Numéro WhatsApp invalide")
    phone = re.sub(r"[^0-9]", "", phone)
    if not 8 <= len(phone) <= 15:
        raise ValueError("Utiliser un numéro international de 8 à 15 chiffres")
    return phone


def validate_media_url(value: Any) -> str:
    """Aucun téléchargement vers un hôte tiers ni redirection (SSRF)."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("URL audio invalide")
    try:
        url = urlsplit(value)
        allowed = (url.scheme == "http" and url.hostname == "127.0.0.1"
                   and url.port == 3001 and not url.username and not url.password
                   and not url.fragment)
    except ValueError:
        allowed = False
    if not allowed:
        raise ValueError("Audio autorisé uniquement depuis la passerelle locale")
    decoded = unquote(url.path)
    if (not decoded.startswith(("/media/", "/uploads/"))
            or any(part in {"..", "."} for part in decoded.split("/"))
            or "\\" in decoded or "%" in decoded or any(ord(char) < 32 for char in decoded)):
        raise ValueError("Chemin de média interdit")
    return value


def admin_numbers(settings: Settings) -> tuple[str, ...]:
    """Une liste explicite vide refuse tout accès; None conserve la configuration historique."""
    configured = getattr(settings, "whatsapp_admin_numbers", None)
    values = (settings.admin_phone_number,) if configured is None else configured
    return tuple(dict.fromkeys(normalize_phone(value) for value in values if value))


def parse_message(payload: dict[str, Any], admin_phone_number: str | tuple[str, ...]) -> dict[str, Any] | None:
    """Normaliser le format documenté et les enveloppes usuelles des passerelles.

    Format stable: {id, from, text} ou {id, from, audio:{path|url,mimetype}}.
    Alias: {messageId, sender, message:texte}. L'identifiant reste obligatoire.
    Enveloppe acceptée: {event:'message',data:{id,from,body,type,media}}.
    Une passerelle Baileys doit fournir un média déjà déchiffré via path/url.
    """
    if not isinstance(payload, dict):
        raise ValueError("Objet JSON attendu")
    if "event" in payload and payload["event"] not in {"message", "message.received", "messages.upsert"}:
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Message WhatsApp invalide")
    key = data.get("key", {})
    if not isinstance(key, dict):
        key = {}
    if any(name in item and not isinstance(item[name], bool)
           for item in (data, key) for name in ("fromMe",)):
        raise ValueError("fromMe doit être un booléen")
    if data.get("fromMe") is True or key.get("fromMe") is True:
        return None  # Éviter les boucles avec nos propres réponses.
    try:
        configured = (admin_phone_number,) if isinstance(admin_phone_number, str) else admin_phone_number
        admins = {normalize_phone(number) for number in configured}
        # Vérifier TOUS les champs explicites : un alias autorisé ne doit pas
        # dissimuler un autre numéro, un groupe ou un identifiant invalide.
        senders = [item[name] for item, name in ((data, "from"), (data, "sender"), (key, "remoteJid"))
                   if name in item]
        if not senders:
            raise ValueError("Émetteur obligatoire")
        phones = []
        has_lid = False
        for sender in senders:
            if isinstance(sender, str) and re.fullmatch(r"[0-9]{1,30}(?::[0-9]{1,10})?@lid", sender.strip()):
                has_lid = True
            else:
                phones.append(normalize_phone(sender))
        associated = [item[name] for item, name in ((data, "senderPn"), (key, "remoteJidAlt"))
                      if name in item]
        if has_lid and not associated:
            raise ValueError("LID sans numéro personnel associé")
        phones.extend(normalize_phone(sender) for sender in associated)
        if not phones or phones[0] not in admins or any(phone != phones[0] for phone in phones):
            raise ValueError("Émetteur non autorisé ou contradictoire")
    except ValueError as exc:
        raise PermissionError("Émetteur non autorisé") from exc
    message_id = data.get("id", data.get("messageId", key.get("id")))
    if isinstance(message_id, dict):
        message_id = message_id.get("id", message_id.get("_serialized"))
    if not isinstance(message_id, str) or not re.fullmatch(r"[\w.:@+-]{1,128}", message_id):
        raise ValueError("Identifiant de message obligatoire (128 caractères maximum)")
    raw_message = data.get("message", {})
    message_text = raw_message if isinstance(raw_message, str) else ""
    message = raw_message
    if not isinstance(message, dict):
        message = {}
    extended = message.get("extendedTextMessage", {})
    if not isinstance(extended, dict):
        extended = {}
    text = data.get("text", data.get("body", message.get("conversation", extended.get("text", message_text))))
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError("Texte limité à 1000 caractères")
    audio = data.get("audio")
    if audio is None and data.get("messageType") == "audioMessage":
        audio = {"url": data.get("audioUrl"), "path": data.get("audioPath"),
                 "mimetype": data.get("mimetype", "audio/ogg")}
    if audio is None and (data.get("type") in {"ptt", "audio"} or "audioMessage" in message):
        audio = data.get("media", message.get("audioMessage"))
    if audio is not None:
        if not isinstance(audio, dict):
            raise ValueError("Média audio invalide")
        mimetype = audio.get("mimetype", data.get("mimetype", "audio/ogg"))
        if not isinstance(mimetype, str) or mimetype.split(";", 1)[0].strip().lower() not in AUDIO_MIME_TYPES:
            raise ValueError("Formats audio autorisés : OGG, Opus, MP3 et WAV")
        path, url = audio.get("path"), audio.get("url")
        if bool(path) == bool(url):
            raise ValueError("Fournir un seul chemin ou une seule URL audio déchiffrée")
        if path and (not isinstance(path, str) or len(path) > 2048):
            raise ValueError("Chemin audio invalide")
        if url:
            validate_media_url(url)
        audio = {"path": path, "url": url, "mimetype": mimetype}
    elif not text.strip():
        raise ValueError("Texte ou note vocale obligatoire")
    return {"id": message_id, "sender": phones[0], "text": text.strip(), "audio": audio,
            "source": "voice" if audio else "text"}


def parse_intent(text: str) -> tuple[str, str | None]:
    """Grammaire volontairement exacte: les négations et phrases ambiguës refusent."""
    normalized = "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                         if not unicodedata.combining(char))
    normalized = " ".join(normalized.strip().rstrip(".!?").split())
    # Homophone observé avec tiny en français : lecture seule, jamais une action.
    if normalized in {"status", "statut", "etat", "statut des services", "etat des services",
                      "statue", "statue des services", "statuts", "statuts des services"}:
        return "status", None
    if normalized in {"aide", "help"}:
        return "help", None
    match = re.fullmatch(r"(?:redemarre|redemarrage|restart) ([a-z0-9-]+)", normalized)
    if match and (match[1] in system_manager.SERVICES_ALLOWLIST or match[1] in {"apps", "tout", "jarvis"}):
        return "restart", match[1]
    match = re.fullmatch(r"(?:arrete|arret|stop) ([a-z0-9-]+)", normalized)
    if match and (match[1] in system_manager.SERVICES_ALLOWLIST or match[1] == "jarvis"):
        return "stop", match[1]
    match = re.fullmatch(r"(?:demarre|demarrage|start) ([a-z0-9-]+)", normalized)
    if match and match[1] in system_manager.SERVICES_ALLOWLIST:
        return "start", match[1]
    if normalized == "reboot":
        return "reboot", None
    if normalized == "confirmer reboot":
        return "confirm_reboot", None
    if normalized == "sauvegarde babamarket":
        return "backup", "babamarket"
    return "unknown", None


def is_control_message(text: str) -> bool:
    """Les commandes ambiguës ou niées ne doivent jamais devenir des actions ReAct."""
    normalized = "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                         if not unicodedata.combining(char))
    return bool(re.search(r"\b(?:restart|redemarr\w*|stop|arret\w*|reboot|systemctl|sudo|sauvegarde|shutdown|poweroff)\b", normalized)
                or re.match(r"\s*(?:help|aide|confirmer)\b", normalized)
                or (re.match(r"\s*(?:status|statut|etat)\b", normalized)
                    and re.search(r"[;&|`$<>]", normalized)))


async def run_process(arguments: list[str], timeout: float, capture: bool = False) -> tuple[int, bytes]:
    """Sortie limitée, sans shell; tuer aussi les descendants lors d'un timeout."""
    process = await asyncio.create_subprocess_exec(
        *arguments, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name == "posix",
    )
    try:
        async with asyncio.timeout(timeout):
            output = bytearray()
            if capture:
                assert process.stdout is not None
                while chunk := await process.stdout.read(1024):
                    if len(output) + len(chunk) > 8192:
                        raise ValueError("Sortie du transcripteur trop volumineuse")
                    output.extend(chunk)
            code = await process.wait()
            return code, bytes(output)
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 2)
        raise


class WhatsAppController:
    def __init__(self, settings: Settings, runtime: Any,
                 restart: Callable[[str], Awaitable[dict[str, Any]]]):
        self.settings, self.runtime, self.restart = settings, runtime, restart
        self.system_manager = system_manager.SystemManager(runtime, settings)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        # Deux workers fixes : une inférence lente ne retarde pas les commandes opérateur.
        self.conversation_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        self.client: httpx.AsyncClient | None = None
        self.remote_client: httpx.AsyncClient | None = None
        self.worker: asyncio.Task[None] | None = None
        self.conversation_worker: asyncio.Task[None] | None = None
        self.readiness_task: asyncio.Task[None] | None = None
        self.last_command: dict[str, Any] | None = None
        self.busy = False
        self.uploading = 0
        self._seen: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._pending_reboot: dict[tuple[str, str], float] = {}
        self._active_commands = 0
        self._check_lock = asyncio.Lock()
        self._voice = {"status": "checking" if settings.voice_enabled else "disabled",
                       "model": settings.whisper_model, "model_path": None,
                       "reason": "Vérification du moteur vocal en cours" if settings.voice_enabled
                       else "Module vocal désactivé par NOC_VOICE_ENABLED", "download_available": False}
        self._voice_python = sys.executable

    @property
    def enabled(self) -> bool:
        """État réel de transcription, indépendant du secret du webhook."""
        return self.settings.voice_enabled and self._voice["status"] == "ready"

    @property
    def webhook_configured(self) -> bool:
        return bool(admin_numbers(self.settings) and self.settings.whatsapp_webhook_token)

    async def start(self) -> None:
        if self.webhook_configured:
            admin_numbers(self.settings)
            send_url = self.settings.whatsapp_send_url.replace(
                "{session_id}", quote(self.settings.whatsapp_session_id, safe=""))
            send = urlsplit(send_url)
            if (send.scheme != "http" or send.hostname not in {"127.0.0.1", "::1"}
                    or send.port != 3001 or send.username or send.password or send.fragment):
                raise ValueError("API WhatsApp d'envoi limitée à la passerelle locale:3001")
            self.client = httpx.AsyncClient(
                timeout=3, trust_env=False, verify=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            )
        if self.settings.voice_remote_url:
            remote = urlsplit(self.settings.voice_remote_url)
            try:
                tailscale = ipaddress.ip_address(remote.hostname or "") in ipaddress.ip_network("100.64.0.0/10")
            except ValueError:
                tailscale = False
            if (remote.scheme not in {"https", "http"} or (remote.scheme == "http" and not tailscale)
                    or remote.username or remote.password or remote.fragment
                    or not self.settings.voice_remote_token):
                raise ValueError("Transcription distante: HTTPS ou IP Tailscale, jeton obligatoire")
            self.remote_client = httpx.AsyncClient(
                timeout=self.settings.whisper_timeout, trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        # La simulation et le moteur vocal fonctionnent aussi avant de relier WhatsApp.
        self.worker = asyncio.create_task(self._worker(), name="noc-whatsapp")
        self.conversation_worker = asyncio.create_task(
            self._worker(self.conversation_queue), name="noc-whatsapp-conversation")
        self.readiness_task = asyncio.create_task(self._readiness_loop(), name="noc-voice-readiness")
        LOGGER.info("Workers WhatsApp prêts : traitement texte indépendant de Whisper (voice_enabled=%s)",
                    self.settings.voice_enabled)

    async def _readiness_loop(self) -> None:
        while True:
            await self.refresh_voice_readiness()
            await asyncio.sleep(300 if self.enabled else 30)

    async def refresh_voice_readiness(self) -> dict[str, Any]:
        """Importer les dépendances uniquement dans un enfant borné, sans modèle chargé."""
        async with self._check_lock:
            info = {"status": "disabled", "model": self.settings.whisper_model,
                    "model_path": None, "reason": "Module vocal désactivé par NOC_VOICE_ENABLED",
                    "download_available": False}
            if self.settings.voice_enabled:
                if not shutil.which("ffmpeg"):
                    info["reason"] = "ffmpeg absent : installez ffmpeg sur le Dell"
                elif self.remote_client is not None:
                    info.update(status="ready", model="remote", reason="Transcription distante configurée")
                else:
                    info["reason"] = "faster-whisper absent ou inutilisable dans les environnements Python"
                    candidates = dict.fromkeys((self.settings.whisper_python, sys.executable,
                                               str(Path(__file__).parent / "voice-venv" / "bin" / "python")))
                    for candidate in candidates:
                        if not candidate or not Path(candidate).is_file():
                            continue
                        try:
                            code, output = await run_process([
                                candidate, str(Path(__file__).with_name("voice_transcribe.py")),
                                "--check", "--model", self.settings.whisper_model_path,
                                "--model-name", self.settings.whisper_model,
                            ], 20, capture=True)
                            result = json.loads(output) if code == 0 else None
                            if not isinstance(result, dict) or result.get("status") not in {"ready", "missing_model", "disabled"}:
                                continue
                            # Le JSON du worker ne contient ni secrets ni erreurs natives brutes.
                            checked = {"status": result["status"],
                                       "model": str(result.get("model", self.settings.whisper_model))[:24],
                                       "model_path": result.get("model_path"),
                                       "reason": str(result.get("reason", "Moteur vocal indisponible"))[:240],
                                       "download_available": result.get("download_available") is True}
                            if checked["status"] == "ready":
                                info = checked
                                self._voice_python = candidate
                                break
                            if checked["status"] == "missing_model" or info["status"] != "missing_model":
                                info = checked
                        except (OSError, TimeoutError, ValueError):
                            continue
            changed = info != self._voice
            self._voice = info
            if changed:
                await self._publish()
            return self.snapshot()

    async def close(self) -> None:
        if self.readiness_task:
            self.readiness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.readiness_task
            self.readiness_task = None
        for worker in (self.worker, self.conversation_worker):
            if not worker:
                continue
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self.worker = self.conversation_worker = None
        self._pending_reboot.clear()
        for client in (self.client, self.remote_client):
            if client:
                await client.aclose()
        self.client = self.remote_client = None
        for queue in (self.queue, self.conversation_queue):
            while not queue.empty():
                message = queue.get_nowait()
                self._remove_upload(message)
                queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        command = dict(self.last_command) if self.last_command else None
        if command is not None:
            private = command.pop("_private", command.get("simulated") is not True
                                  and command.get("source") in {"text", "voice"}
                                  and command.get("action") in {"pending", "unknown"})
            if private:
                # Le tableau de bord public ne révèle ni conversation ni ID Baileys.
                command.update(id="private-whatsapp", transcription="Conversation WhatsApp privée",
                               response="Conversation WhatsApp privée", service=None)
        return {**self._voice, "enabled": self.enabled,
                "status": "active" if self.enabled and self.busy else self._voice["status"],
                "webhook_configured": self.webhook_configured,
                "webhook_status": "configured" if self.webhook_configured else "missing_configuration",
                "text_enabled": self.webhook_configured and self.worker is not None and not self.worker.done(),
                "voice_enabled": self.enabled,
                "busy": self.busy, "queued": self.queue.qsize() + self.conversation_queue.qsize(),
                "last_command": command}

    async def accept(self, payload: dict[str, Any], *, uploaded_audio: Path | None = None) -> dict[str, Any]:
        if not self.webhook_configured:
            raise RuntimeError("Commandes WhatsApp non configurées")
        try:
            message = self.authorize(payload)
        except PermissionError:
            with contextlib.suppress(Exception):
                await self._publish()
            raise
        if message is None:
            return {"accepted": False, "ignored": True}
        LOGGER.info("WhatsApp reçu: expéditeur=%s texte=%r", message["sender"],
                    message["text"] if message["text"] else "[audio]")
        message["session"] = self.settings.whatsapp_session_id
        now = time.monotonic()
        while self._seen and now - next(iter(self._seen.values())) > 86400:
            self._seen.popitem(last=False)
        identity = (message["sender"], message["session"], message["id"])
        if identity in self._seen:
            return {"accepted": True, "duplicate": True}
        if uploaded_audio is not None:
            # Seul le routeur peut fournir ce chemin, jamais le JSON reçu.
            if message["audio"] is None:
                raise ValueError("Audio obligatoire")
            message["audio"] = {"upload_path": str(uploaded_audio)}
        # QueueFull laisse l'identifiant réessayable; aucun effet si non accepté.
        action, _ = parse_intent(message["text"])
        queue = (self.conversation_queue if message["audio"] or (
            action == "unknown" and not is_control_message(message["text"])) else self.queue)
        queue.put_nowait(message)
        self._seen[identity] = now
        if len(self._seen) > 256:
            self._seen.popitem(last=False)
        LOGGER.info("WhatsApp en file: id=%s voie=%s commande=%s", message["id"],
                    "vocale" if queue is self.conversation_queue else "opérateur", action)
        return {"accepted": True, "duplicate": False}

    def authorize(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Vérifier les identités avant toute mise en file, transcription ou exécution."""
        try:
            data = payload.get("data", payload)
            sessions = [item["sessionId"] for item in (payload, data)
                        if isinstance(item, dict) and "sessionId" in item]
            if any(str(session) != self.settings.whatsapp_session_id for session in sessions):
                raise PermissionError("Session WhatsApp non autorisée")
            return parse_message(payload, admin_numbers(self.settings))
        except PermissionError:
            self.runtime.collector.add_event(
                "warning", "ALERTE WhatsApp : ordre rejeté, émetteur ou session non autorisé.")
            raise PermissionError("ALERTE : commande refusée, numéro administrateur ou session non autorisé.") from None

    async def simulate(self, text: str) -> dict[str, Any]:
        """Tester le parser et le SSE sans commande systemd, sauvegarde ou envoi WhatsApp."""
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise ValueError("Commande texte requise, limitée à 1000 caractères")
        self.queue.put_nowait({"id": "simulation-" + str(time.time_ns()), "text": text.strip(),
                              "audio": None, "source": "simulation", "simulated": True})
        return {"accepted": True, "simulated": True, "message": "Simulation en file : aucun service ne sera modifié."}

    @staticmethod
    def _remove_upload(message: dict[str, Any]) -> None:
        path = (message.get("audio") or {}).get("upload_path")
        if path:
            with contextlib.suppress(OSError):
                Path(path).unlink(missing_ok=True)

    async def _publish(self) -> None:
        await self.runtime.publish_module_update()

    async def _worker(self, queue: asyncio.Queue[dict[str, Any]] | None = None) -> None:
        queue = self.queue if queue is None else queue
        while True:
            message = await queue.get()
            self._active_commands += 1
            self.busy = True
            command = {
                "id": message["id"], "timestamp": utc_now(), "source": message["source"],
                "transcription": message["text"][:1000], "action": "pending", "service": None,
                "status": "processing", "response": "", "reply_sent": False,
                "simulated": message.get("simulated") is True,
                "_private": message.get("simulated") is not True and (
                    bool(message["audio"]) or parse_intent(message["text"])[0] == "unknown"),
            }
            self.last_command = command
            try:
                try:
                    await self._publish()
                    text = await self._transcribe(message["audio"]) if message["audio"] else message["text"]
                    action, service = parse_intent(text)
                    LOGGER.info("WhatsApp commande détectée: expéditeur=%s commande=%s cible=%s",
                                message.get("sender", "simulation"), action, service or "-")
                    command.update(transcription=text[:1000], action=action, service=service)
                    command["_private"] = message.get("simulated") is not True and action == "unknown"
                    await self._publish()
                    if message.get("simulated"):
                        if action == "status":
                            response, success = await self._execute(action, service)
                            response = "SIMULATION · " + response
                        elif action in {"restart", "start", "stop", "backup", "reboot", "confirm_reboot"}:
                            response, success = f"SIMULATION · {action} {service or ''} reconnu. Aucune action exécutée.", True
                        else:
                            response, success = "SIMULATION · Commande non reconnue. Aucune inférence exécutée.", False
                    else:
                        response, success = await self._execute(action, service, {**message, "text": text})
                    LOGGER.info("WhatsApp action terminée: commande=%s cible=%s succès=%s",
                                action, service or "-", success)
                    if len(response) > 4096:
                        response = response[:4050] + "\n… Réponse abrégée (limite WhatsApp)."
                    command.update(response=response, status="completed" if success else "failed", timestamp=utc_now())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.exception("Échec du traitement WhatsApp: id=%s", message.get("id"))
                    # Ne publier ni chemins, ni secrets, ni erreurs natives.
                    response = "Commande impossible : " + (
                        str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    )[:180]
                    command.update(response=response, status="failed", timestamp=utc_now())
                self.last_command = command
                await self._publish()
                if message.get("simulated"):
                    self.runtime.collector.add_event("info", f"Simulation vocale · {command['action']} · {command['status']}")
                    continue
                command["reply_sent"] = await self._send_reply(command["response"], message["sender"])
                level = "info" if command["status"] == "completed" and command["reply_sent"] else "warning"
                delivery = "" if command["reply_sent"] else " · réponse non livrée"
                self.runtime.collector.add_event(level, f"WhatsApp · {command['action']} · {command['status']}{delivery}")
                self.last_command = command
                await self._publish()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Erreur non gérée dans le worker WhatsApp: id=%s", message.get("id"))
            finally:
                self._active_commands -= 1
                self.busy = self._active_commands > 0
                self._remove_upload(message)
                queue.task_done()
                try:
                    await self._publish()
                except Exception:
                    LOGGER.exception("Échec de publication après la commande WhatsApp: id=%s",
                                     message.get("id"))

    async def _execute(self, action: str, service: str | None,
                       message: dict[str, Any] | None = None) -> tuple[str, bool]:
        if action == "stop" and service == "jarvis":
            return "Action refusée : M.O.C.T.A.R est le superviseur permanent et doit rester actif.", False
        if action in {"restart", "start"} and service == "jarvis":
            return "Action refusée : M.O.C.T.A.R est le superviseur permanent et ne peut pas être redémarré par WhatsApp.", False
        if action in {"restart", "start", "stop", "reboot", "confirm_reboot"} and not self.settings.enable_restart:
            self._pending_reboot.clear()
            return "Commandes système désactivées par NOC_ENABLE_RESTART.", False
        if action in {"reboot", "confirm_reboot"}:
            self._pending_reboot.clear()
            return "Action refusée : M.O.C.T.A.R est le superviseur permanent; le redémarrage de l’hôte est interdit par WhatsApp.", False
        if action == "help":
            return ("Commandes : statut ; restart/redémarre <service|apps|tout> ; start/démarre <service> ; stop/arrête <service> ; "
                    "sauvegarde babamarket ; aide. "
                    "Services : klasora, ollama, tunnel. jarvis.service reste toujours actif. "
                    "Les autres questions sont transmises à M.O.C.T.A.R.", True)
        if action == "status":
            return await asyncio.to_thread(self.system_manager.get_full_infrastructure_status), True
        if action in {"restart", "start", "stop"}:
            targets = (system_manager.config.MANAGED_SERVICE_GROUPS["apps"]
                       if service in {"apps", "tout"} and action == "restart" else (service,))
            operation = {"restart": system_manager.restart_system_service,
                         "start": system_manager.start_system_service,
                         "stop": system_manager.stop_system_service}[action]
            results = [await asyncio.to_thread(operation, target) for target in targets]
            success = all(result.get("ok") is True for result in results)
            if success:
                verb = {"restart": "redémarré", "start": "démarré", "stop": "arrêté"}[action]
                return "; ".join(f"{target} {verb} avec succès" for target in targets), True
            failures = [str(result.get("message", "opération échouée"))[:180]
                        for result in results if result.get("ok") is not True]
            return "Échec : " + "; ".join(failures), False
        if action == "backup":
            return await self._backup()
        if action == "unknown" and message and not is_control_message(message["text"]):
            jarvis = getattr(self.runtime, "jarvis", None)
            if jarvis is None:
                return "M.O.C.T.A.R indisponible. Les commandes opérateur restent disponibles : statut, restart, stop, aide.", False
            result = await jarvis.execute_prompt_async(
                user_input=message["text"],
                source="whatsapp_voice" if message["source"] == "voice" else "whatsapp_text",
                session_id=f"whatsapp:{message['session']}:{message['sender']}",
            )
            if not isinstance(result, dict) or not isinstance(result.get("reply"), str):
                return "M.O.C.T.A.R n'a pas fourni de réponse exploitable.", False
            return result["reply"], result.get("status") != "error"
        return ("Commande système refusée ou non reconnue. Envoyez aide pour la syntaxe exacte et les services autorisés.", False)

    async def _backup(self) -> tuple[str, bool]:
        configured = self.settings.backup_script
        if not configured:
            return "Sauvegarde non configurée : renseignez NOC_BACKUP_SCRIPT.", False
        script = Path(configured)
        if not script.is_absolute() or not script.is_file() or script.is_symlink():
            return "Script de sauvegarde absent ou interdit.", False
        resolved = script.resolve()
        if resolved != script:
            return "Liens symboliques interdits pour le script de sauvegarde.", False
        attributes = script.stat()
        if os.name != "posix" or attributes.st_uid != 0 or attributes.st_mode & 0o022 or not os.access(script, os.X_OK):
            return "Le script doit être exécutable, appartenir à root et ne pas être modifiable par le groupe ou les autres utilisateurs.", False
        for parent in script.parents:
            parent_attributes = parent.stat()
            if parent_attributes.st_uid != 0 or parent_attributes.st_mode & 0o022:
                return "Les répertoires du script doivent appartenir à root et ne pas être modifiables par le groupe ou les autres utilisateurs.", False
        arguments = [str(resolved)]
        # Une seule commande root exacte autorisée par l'installateur; les chemins
        # personnalisés s'exécutent uniquement avec les droits de noc-dashboard.
        if configured == "/usr/local/bin/backup-babamarket.sh":
            sudo = shutil.which("sudo")
            if not sudo:
                return "Sauvegarde indisponible : sudo absent sur cet hôte.", False
            arguments = [sudo, "-n", configured]
        code, _ = await run_process(arguments, self.settings.backup_timeout)
        return ("✅ Sauvegarde BabaMarket terminée." if code == 0 else "❌ Échec du script de sauvegarde BabaMarket."), code == 0

    async def _send_reply(self, response: str, recipient: str | None = None) -> bool:
        if self.client is None:
            LOGGER.error("Réponse WhatsApp impossible: client HTTP non initialisé")
            return False
        number = normalize_phone(recipient or self.settings.admin_phone_number)
        recipient_value = number + "@s.whatsapp.net" if self.settings.whatsapp_send_to_field == "jid" else number
        payload = {
            self.settings.whatsapp_send_to_field: recipient_value,
            self.settings.whatsapp_send_text_field: response[:4096],
        }
        url = self.settings.whatsapp_send_url.replace(
            "{session_id}", quote(self.settings.whatsapp_session_id, safe=""))
        LOGGER.info("WhatsApp envoi passerelle: POST %s destinataire=%s", url, recipient_value)
        try:
            async with asyncio.timeout(self.settings.whatsapp_send_timeout):
                async with self.client.stream("POST", url, json=payload,
                                              headers={"x-gateway-secret": self.settings.gateway_secret},
                                              timeout=self.settings.whatsapp_send_timeout) as reply:
                    LOGGER.info("WhatsApp passerelle réponse: HTTP %s", reply.status_code)
                    if not reply.is_success:
                        LOGGER.error("Échec passerelle WhatsApp: HTTP %s", reply.status_code)
                    return reply.is_success
        except (httpx.HTTPError, TimeoutError):
            LOGGER.exception("Échec HTTP vers la passerelle WhatsApp: POST %s", url)
            return False

    def _copy_local_audio(self, source: str, destination: Path) -> None:
        root = Path(self.settings.whatsapp_media_dir).resolve()
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Audio hors du répertoire de médias autorisé") from exc
        if candidate.resolve() != candidate or not candidate.resolve().is_relative_to(root):
            raise ValueError("Liens symboliques ou traversée de répertoire interdits")
        for parent in (candidate, *candidate.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("Lien symbolique audio interdit")
        if candidate.suffix.lower() not in {".ogg", ".opus", ".mp3", ".wav"}:
            raise ValueError("Extension audio OGG, Opus, MP3 ou WAV requise")
        try:
            descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise ValueError("Note vocale locale inaccessible") from exc
        with os.fdopen(descriptor, "rb") as incoming:
            attributes = os.fstat(incoming.fileno())
            if not stat.S_ISREG(attributes.st_mode) or not 0 < attributes.st_size <= self.settings.whatsapp_max_audio_bytes:
                raise ValueError("Note vocale vide ou trop volumineuse")
            with destination.open("wb") as outgoing:
                count = 0
                while chunk := incoming.read(16384):
                    count += len(chunk)
                    if count > self.settings.whatsapp_max_audio_bytes:
                        raise ValueError("Note vocale trop volumineuse")
                    outgoing.write(chunk)

    async def _download_audio(self, url: str, destination: Path) -> None:
        validate_media_url(url)
        if self.client is None:
            raise RuntimeError("Passerelle audio indisponible")
        async with asyncio.timeout(10):
            async with self.client.stream("GET", url, headers={"x-gateway-secret": self.settings.gateway_secret,
                                                                 "accept-encoding": "identity"}) as response:
                if not response.is_success:
                    raise ValueError("Note vocale locale inaccessible")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Compression HTTP audio interdite")
                count = 0
                with destination.open("wb") as outgoing:
                    async for chunk in response.aiter_raw(16384):
                        count += len(chunk)
                        if count > self.settings.whatsapp_max_audio_bytes:
                            raise ValueError("Note vocale trop volumineuse")
                        outgoing.write(chunk)
                if count == 0:
                    raise ValueError("Note vocale vide")

    async def _transcribe(self, audio: dict[str, Any]) -> str:
        if not self.settings.voice_enabled:
            raise ValueError("Module vocal désactivé par NOC_VOICE_ENABLED")
        if self._voice["status"] != "ready":
            await self.refresh_voice_readiness()
            if not self.enabled:
                raise ValueError(self._voice["reason"])
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("ffmpeg doit être installé pour les commandes vocales")
        temporary_root = "/tmp" if os.name == "posix" else None
        with tempfile.TemporaryDirectory(prefix="noc-voice-", dir=temporary_root) as temporary:
            directory = Path(temporary)
            incoming, wav = directory / "message.ogg", directory / "message.wav"
            if audio.get("upload_path"):
                await asyncio.to_thread(shutil.copyfile, audio["upload_path"], incoming)
            elif audio.get("path"):
                await asyncio.to_thread(self._copy_local_audio, audio["path"], incoming)
            else:
                await self._download_audio(audio["url"], incoming)
            incoming.chmod(0o600)
            code, _ = await run_process([
                ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-threads", "1", "-i", str(incoming), "-vn", "-sn", "-dn", "-t", str(self.settings.whatsapp_audio_seconds),
                "-ac", "1", "-ar", "16000", "-f", "wav", str(wav),
            ], 20)
            if code or not wav.is_file():
                raise ValueError("Conversion audio impossible")
            wav.chmod(0o600)
            if self.remote_client is not None:
                with wav.open("rb") as file:
                    async with asyncio.timeout(self.settings.whisper_timeout):
                        async with self.remote_client.stream(
                            "POST", self.settings.voice_remote_url,
                            headers={"Authorization": "Bearer " + self.settings.voice_remote_token,
                                     "accept-encoding": "identity"},
                            files={"file": ("message.wav", file, "audio/wav")},
                        ) as response:
                            if not response.is_success:
                                raise ValueError("Transcription distante indisponible")
                            if response.headers.get("content-encoding", "identity").lower() != "identity":
                                raise ValueError("Compression HTTP transcription interdite")
                            output = bytearray()
                            async for chunk in response.aiter_raw(1024):
                                if len(output) + len(chunk) > 8192:
                                    raise ValueError("Transcription trop volumineuse")
                                output.extend(chunk)
            else:
                model = Path(self._voice.get("model_path") or self.settings.whisper_model_path)
                if not model.is_dir() or not (model / "model.bin").is_file():
                    raise ValueError("Modèle Whisper local absent ou illisible : lancez install-voice.sh")
                code, output = await run_process([
                    self._voice_python,
                    str(Path(__file__).with_name("voice_transcribe.py")),
                    "--model", str(model), "--model-name", self.settings.whisper_model, "--audio", str(wav),
                ], self.settings.whisper_timeout, capture=True)
                if code:
                    raise ValueError("Transcription locale impossible (voir installation faster-whisper)")
            decoded = json.loads(output)
            text = decoded.get("text") if isinstance(decoded, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text) > 1000:
                raise ValueError("Transcription vide ou trop longue")
            return text.strip()
