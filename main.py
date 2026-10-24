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
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Path as APIPath, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

import autonomous_library
import collector
import desktop_window_manager
import dynamic_attention_engine
import hud_state_manager
import stt_phonetic_repair
import voice_transcribe
from ai_brain import build_live_system_context, get_cpu_temperature_celsius, voice_fast_llm_reply
from voice_semantic_engine import execute_voice_semantic_turn
from config import RESTART_ALLOWLIST, Settings
from cyberdefense import CyberDefenseManager, TRAP_PATHS
from sentinel import SentinelManager
from vps_registry import VPSRegistry
from whatsapp import AUDIO_MIME_TYPES, WhatsAppController

LOGGER = logging.getLogger("noc-dashboard")
BASE_DIR = Path(__file__).resolve().parent

DEFAULT_TTS_VOICE = os.getenv("JARVIS_TTS_VOICE", os.getenv("JARVIS_EDGE_TTS_VOICE", "fr-FR-RemyMultilingualNeural")).strip() or "fr-FR-RemyMultilingualNeural"
DEFAULT_TTS_RATE = "+20%"
DEFAULT_TTS_PITCH = "+2Hz"
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
    raw = str(text or "")
    # Purge des en-têtes bureaucratiques et mentions de télémétrie administrative
    raw = re.sub(r"\[État Opérationnel\]", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\[Recommandations M\.O\.C\.T\.A\.R\]", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"Ce relevé concerne l'hôte\s*;?\s*les services et équipements distants ne sont pas mesurés\.?", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"```[\s\S]*?```", " ", raw)
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


async def synthesize_edge_tts(
    text: str,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    pitch: str = DEFAULT_TTS_PITCH,
) -> bytes:
    """Génère un flux audio MP3 via edge-tts avec cadence vive (+20%) et pitch adapté."""
    import edge_tts  # type: ignore[import-not-found,import-untyped]

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    audio_buffer = bytearray()
    async for chunk in communicate.stream():
        if isinstance(chunk, dict) and chunk.get("type") == "audio":
            data = chunk.get("data")
            if data:
                audio_buffer.extend(data)
    if not audio_buffer:
        raise RuntimeError("edge-tts n'a retourné aucun flux audio.")
    return bytes(audio_buffer)


async def generate_cached_tts_with_key(
    text: str,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    pitch: str = DEFAULT_TTS_PITCH,
) -> tuple[bytes, bool, str]:
    """Retourne (mp3_bytes, cache_hit, cache_key) avec mise en cache mémoire et disque."""
    cleaned = clean_tts_text(text)
    if not cleaned:
        raise ValueError("Le texte à synthétiser est vide.")
    selected_voice = voice if voice in ALLOWED_TTS_VOICES else DEFAULT_TTS_VOICE
    selected_rate = rate or DEFAULT_TTS_RATE
    selected_pitch = pitch or DEFAULT_TTS_PITCH
    cache_key = hashlib.sha256(f"{selected_voice}:{selected_rate}:{selected_pitch}:{cleaned}".encode("utf-8")).hexdigest()

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

    if selected_rate == DEFAULT_TTS_RATE and selected_pitch == DEFAULT_TTS_PITCH:
        audio_bytes = await synthesize_edge_tts(cleaned, selected_voice)
    else:
        audio_bytes = await synthesize_edge_tts(cleaned, selected_voice, rate=selected_rate, pitch=selected_pitch)
    if len(_TTS_MEMORY_CACHE) >= _TTS_CACHE_MAX_ITEMS:
        _TTS_MEMORY_CACHE.pop(next(iter(_TTS_MEMORY_CACHE)), None)
    _TTS_MEMORY_CACHE[cache_key] = audio_bytes
    try:
        await asyncio.to_thread(cache_file.write_bytes, audio_bytes)
    except OSError:
        pass
    return audio_bytes, False, cache_key


async def generate_cached_tts(
    text: str,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    pitch: str = DEFAULT_TTS_PITCH,
) -> tuple[bytes, bool]:
    """Retourne (mp3_bytes, cache_hit) avec mise en cache mémoire et disque."""
    audio_bytes, cache_hit, _ = await generate_cached_tts_with_key(text, voice, rate=rate, pitch=pitch)
    return audio_bytes, cache_hit


def split_speech_sentences(text: str) -> list[str]:
    """Découpe un texte en phrases orales naturelles pour la pré-synthèse et le streaming audio (< 350 ms)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    protected = cleaned
    protected = re.sub(r"\bM\.O\.C\.T\.A\.R\b", "M_O_C_T_A_R", protected)
    protected = re.sub(r"\bM\.\s+", "M_DOT_ ", protected)
    protected = re.sub(r"\bDr\.\s+", "Dr_DOT_ ", protected)
    protected = re.sub(r"(\d+)\.(\d+)", r"\1_DEC_\2", protected)

    raw_parts = re.split(r"(?<=[.!?;\n])\s+", protected)

    sentences: list[str] = []
    for p in raw_parts:
        restored = (
            p.replace("M_O_C_T_A_R", "M.O.C.T.A.R")
            .replace("M_DOT_", "M.")
            .replace("Dr_DOT_", "Dr.")
            .replace("_DEC_", ".")
            .strip()
        )
        if restored:
            sentences.append(restored)

    merged: list[str] = []
    buffer = ""
    for s in sentences:
        if not buffer:
            buffer = s
        else:
            if len(buffer.split()) < 3:
                buffer = f"{buffer} {s}"
            else:
                merged.append(buffer)
                buffer = s
    if buffer:
        merged.append(buffer)

    return merged if merged else [cleaned]


def get_dynamic_tts_prosody(text: str, hour: int | None = None) -> tuple[str, str]:
    """Calcule une prosodie vocale vivante (rate, pitch) adaptée à l'heure et au ton de l'échange."""
    if hour is None:
        try:
            hour = datetime.now().hour
        except Exception:
            hour = 14
    cleaned = (text or "").lower()

    # Nuit tardive (> 23h ou avant 6h) : ton plus doux, feutré et apaisant
    if hour >= 23 or hour < 6:
        return "+6%", "-1Hz"

    # Matin (6h - 11h59) : ton enthousiaste, vif et énergique
    if 6 <= hour < 12:
        return "+22%", "+3Hz"

    # Excitation ou alerte rapide
    if any(w in cleaned for w in ("alerte", "erreur", "lance", "go", "hop", "succes", "succès")):
        return "+22%", "+2Hz"

    return DEFAULT_TTS_RATE, DEFAULT_TTS_PITCH


async def generate_cached_tts_chunks(
    text: str,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    pitch: str = DEFAULT_TTS_PITCH,
) -> tuple[str, list[str]]:
    """Génère la synthèse vocale par phrases en parallèle (First-Sentence Streaming).

    Garantit une latence perçue ultra-basse (< 350 ms) en synthétisant la première phrase
    en priorité absolue, tout en pré-mettant en cache les phrases subséquentes.
    Retourne (full_audio_url, list_of_chunk_urls).
    """
    cleaned = clean_tts_text(text)
    if not cleaned:
        raise ValueError("Le texte à synthétiser est vide.")

    sentences = split_speech_sentences(cleaned)
    if len(sentences) <= 1:
        _, _, full_key = await generate_cached_tts_with_key(cleaned, voice, rate=rate, pitch=pitch)
        full_url = f"/api/voice/tts/cache/{full_key}.mp3"
        return full_url, [full_url]

    async def _synth_sentence(sentence: str) -> str:
        _, _, k = await generate_cached_tts_with_key(sentence, voice, rate=rate, pitch=pitch)
        return f"/api/voice/tts/cache/{k}.mp3"

    async def _synth_full() -> str:
        _, _, k = await generate_cached_tts_with_key(cleaned, voice, rate=rate, pitch=pitch)
        return f"/api/voice/tts/cache/{k}.mp3"

    # Lancement entièrement concurrent de toutes les synthèses en parallèle
    full_task = asyncio.create_task(_synth_full())
    chunk_tasks = [asyncio.create_task(_synth_sentence(s)) for s in sentences]

    # Attente prioritaire de la première phrase pour le streaming immédiat
    first_chunk = await chunk_tasks[0]
    other_chunks = await asyncio.gather(*chunk_tasks[1:], return_exceptions=False)
    full_url = await full_task

    chunk_urls = [first_chunk] + list(other_chunks)
    return full_url, chunk_urls


async def _prewarm_common_voice_tts() -> None:
    """Pré-synthétise et préchauffe en arrière-plan les réponses vocales courantes pour 0 ms de latence réseau."""
    phrases = [
        "À vos ordres Moctar, je vous écoute.",
        "Bonjour à tous et bienvenue ! Je suis M.O.C.T.A.R, l'intelligence cybernétique et copilote d'ingénierie de Moctar. Ravi de faire votre connaissance !",
        "Démonstration activée ! Je supervise le centre d'opérations NOC, pilote l'environnement de travail de Moctar, surveille les VPS, gère le code sur GitHub et automatise ses projets d'ingénierie en temps réel. Que souhaitez-vous voir en action ?",
        "Il y a 10 types de personnes dans le monde : ceux qui comprennent le binaire, et ceux qui ne le comprennent pas !",
        "Je vous entends 5 sur 5 avec une clarté optimale ! Je suis à votre écoute, Moctar.",
        "J'ai été conçu, programmé et entraîné sur mesure par Moctar pour être son copilote d'ingénierie autonome et système d'intelligence opérationnelle.",
        "Pardon, je t'écoute Moctar.",
        "Je vous attends Moctar, prenez votre temps.",
        "Oui Moctar, je suis là !",
        "Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?",
        "Bonjour Moctar ! Prêt pour cette journée ? Dis-moi ce qu'on attaque en premier !",
        "Bonjour Moctar ! Bon appétit ou pause bien méritée, je reste disponible !",
        "Oui Moctar, je suis là ! On avance sur quoi cet après-midi ?",
        "Bonsoir Moctar ! Content de te retrouver. Dis-moi sur quoi on se concentre ce soir.",
        "Encore au travail Moctar ? Je reste avec toi, mais pense à préserver ton sommeil. Dis-moi ce qu'on boucle !",
        "Bonne nuit Moctar ! Repose-toi bien, tu as fait une grosse session. Tous les systèmes sont sous surveillance et je garde tout au chaud pour demain !",
        "Très bonne idée Moctar, souffle un peu et prends ton temps. Je garde un œil sur la machine pendant ta pause !",
        "Tout tourne comme une horloge Moctar ! Les systèmes sont nominaux sur le Dell M4800. Et toi, la forme ? Prêt à avancer ?",
        "Avec grand plaisir Moctar ! On forme une sacrée équipe. Toujours là pour toi !",
        "Orbe configuré en vert émeraude, mode victoire activé, Moctar !",
        "Orbe configuré en ambre solaire chaleureux, Moctar !",
        "Orbe configuré en mode nuit tamisé pour préserver tes yeux, Moctar !",
        "Orbe réinitialisé en cyan technologique serein, Moctar !",
        "Orbe configuré en magenta quantique, Moctar !",
        "Orbe configuré en rouge corail, vigilance maximale activée, Moctar !",
    ]
    for p in phrases:
        try:
            await generate_cached_tts(p, DEFAULT_TTS_VOICE)
        except Exception:
            pass


VOICE_SESSION_DURATION_SECONDS = float(os.getenv("VOICE_SESSION_DURATION_SECONDS", "45.0"))
VOICE_BUTLER_GREETINGS = (
    "Salut Moctar ! Comment tu vas mon ami ? Je suis là, dis-moi tout !",
    "Salut Moctar ! Tout roule ? Qu'est-ce qu'on fait de beau aujourd'hui ?",
    "Yo Moctar ! Toujours présent avec toi, qu'est-ce qui se passe ?",
    "Salut Moctar ! À ton écoute mon ami, dis-moi ce qu'on lance !",
)
_WAKE_ONLY_SENTINEL = "__MOCTAR_WAKE_ONLY__"

_VOICE_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:yo|hey|ok|bonjour|salut|dis|allo|ecoute|et\s+vous|you|c['’]est\s+(?:le\s+)?)\s*)?"
    r"(?:yo\s+moctar|moctar|moktar|mokta|mokhtar|mouctar|mouktar|motard|mothar|mothard|mocthar|"
    r"m['’]?occupe\s+tard|m['’]?occaque|moccupe\s+tard|moccaque|"
    r"m(?:\.|\s)*o(?:\.|\s)*c(?:\.|\s)*t(?:\.|\s)*a(?:\.|\s)*r(?:\.|\b)|"
    r"moc|mok|mokti|jarvis|elmo)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)
_VOICE_WAKE_ANYWHERE_RE = re.compile(
    r"\b(?:(?:yo|hey|ok|bonjour|salut|dis|allo|ecoute|et\s+vous|you|c['’]est\s+(?:le\s+)?)\s*)?"
    r"(yo\s+moctar|moctar|moktar|mokta|mokhtar|mouctar|mouktar|motard|mothar|mothard|mocthar|"
    r"m['’]?occupe\s+tard|m['’]?occaque|moccupe\s+tard|moccaque|"
    r"m(?:\.|\s)*o(?:\.|\s)*c(?:\.|\s)*t(?:\.|\s)*a(?:\.|\s)*r(?:\.|\b)|"
    r"moc|mok|mokti|jarvis|elmo|yo\s+elmo|allo\s+moctar|dis\s+moctar)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)
_VOICE_FILLER_WORDS = {
    "au", "yo", "hey", "dis", "allo", "allô", "ecoute", "écoute",
    "et", "vous", "you", "tout", "le", "monde", "ah", "eh", "oh", "ha", "euh", "hum", "bah", "hein",
    "moctar", "moktar", "mokta", "mokhtar", "mouctar", "mouktar", "motard", "mothar", "mothard", "mocthar",
    "moc", "mok", "mokti",
    "moccupe", "m'occupe", "tard", "moccaque", "m'occaque", "jarvis", "elmo",
}


def _strip_accents_1to1(value: str) -> str:
    """Supprime les diacritiques en conservant la correspondance d'index 1-pour-1."""
    import unicodedata

    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in value)


def _is_pure_filler_or_wake(text: str) -> bool:
    """Vérifie si une chaîne ne contient que des formules d'appel, répétitions de nom ou politesses."""
    tokens = [tok.strip(" ,.:;!?-'\u2019").lower() for tok in re.split(r"[\s,.:;!?-]+", text) if tok.strip(" ,.:;!?-'\u2019")]
    if not tokens:
        return True
    return all(tok in _VOICE_FILLER_WORDS for tok in tokens)


def is_direct_voice_command(text: str) -> bool:
    """Identifie les ordres opérationnels explicites exécutables directement sans mot de réveil préalable."""
    if not text or len(text.strip()) < 2:
        return False
    # Réparation phonétique amont préalable pour reconnaître les commandes vocales altérées
    repaired, _ = stt_phonetic_repair.repair_stt_transcript(text)
    norm = _strip_accents_1to1(repaired).lower().strip()

    # Détection immédiate de tout mot-clé système strict (cpu, ram, statut, docker, vps, service, etc.)
    if is_system_command(norm) or is_system_command(repaired):
        return True

    # 0. Salutations directes & Démos pour amis / invités (Ne JAMAIS ignorer une salutation ou démo)
    clean_norm = norm.strip(" .,:;!?'\"")
    if clean_norm in ("bonjour", "salut", "bonsoir", "coucou", "yo", "hello"):
        return True
    if re.search(r"^(?:bonjour|salut|bonsoir|coucou|yo|hello)(?:[\s!,.:;?]+|$)", norm):
        return True
    if re.search(r"\b(?:dis\s+(?:bonjour|salut|coucou)|salue|passe\s+le\s+bonjour|accueille|pr[ée]sente[- ]toi|fais(?:[- ](?:nous|moi))?\s*(?:une\s+)?d[ée]mo(?:nstration)?|montre\s+(?:ce\s+que\s+tu\s+sais\s+faire|nous|tes\s+capacit[ée]s|de\s+quoi\s+tu\s+es\s+capable)|qu['’]est[- ]ce\s+que\s+tu\s+sais\s+faire|tu\s+sais\s+faire\s+quoi|je\s+(?:te|vous)\s+pr[ée]sente|voici|voil[aà]\s+(?:mon\s+ami|mes\s+amis|mon\s+pote|mes\s+potes|mon\s+coll[èe]gue)?|qui\s+sont\s+mes\s+amis|tu\s+connais\s+qui)\b", norm):
        return True
    if re.search(r"\b(?:bonne\s+nuit|je\s+vais\s+dormir|je\s+vais\s+me\s+coucher|je\s+suis\s+fatigu[ée]|pause\s+caf[ée]|je\s+fais\s+une\s+pause|comment\s+tu\s+vas|comment\s+vas[- ]tu|[cç]a\s+va|la\s+forme)\b", norm) or norm in ("merci", "merci beaucoup", "super merci", "merci mon ami", "merci frérot", "bien joué"):
        return True
    if re.search(r"\b(?:merci\s+(?:mon\s+ami|fr[ée]rot|moctar|jarvis))\b", norm):
        return True
    if re.search(r"\b(?:mets?\s+l['’]orbe|couleur\s+de\s+l['’]orbe|mode\s+victoire|mode\s+alerte|ambiance\s+chaleureuse|ambiance\s+nuit|orbe\s+(?:vert|or|ambre|nuit|cyan|violet|rouge)|orbe|globe|la\s+terre|terre)\b", norm):
        return True
    if re.search(r"\b(?:raconte(?:[- ]moi)?\s+(?:une\s+)?blague|fais[- ](?:nous|moi)\s+rire|une\s+blague|blague|fais\s+une\s+blague|qui\s+t['’\s]?a\s+(?:cr[ée][ée]|con[çc]u)|tu\s+(?:me\s+)?r[ée][cç]ois|t['’]es\s+r[ée][cç]ois|tu\s+m['’][ée]revois|tu\s+m['’][ée]loignes?\s+de\s+soi|tu\s+(?:me\s+)?captes?|tu\s+m['’\s]?entends|tu\s+nous\s+entends|tu\s+es\s+l[aà]|t['’\s]?es\s+l[aà]|tu\s+peux\s+parler|parle\s+un\s+peu|parle[- ]nous|test\s+micro|qui\s+est\s+l[aà]|fais\s+voir|actualit[ée]s?|c['’\s]?est\s+quoi\s+le\s+programme|programme)\b", norm):
        return True
    # Questions ouvertes directes
    if re.search(r"^(?:comment|pourquoi|qu['’]est[- ]ce|c['’]est\s+quoi|qui\s+est|qui\s+a|quel|quelle|combien|est[- ]ce\s+que|peux[- ]tu|tu\s+peux|sais[- ]tu|tu\s+sais|dis[- ]moi|donne[- ]moi|raconte|explique|cherche|fais[- ]moi|montre[- ]moi)\b", norm):
        return True

    # 1. Bureau, VS Code & Lancement d'Applications
    if any(k in norm for k in (
        "au bureau", "mode bureau", "mets-toi a mon bureau", "mets-toi au bureau",
        "mets toi a mon bureau", "mets toi au bureau", "mode travail", "au travail",
        "espace de travail", "lance l'environnement", "lance le travail", "bonne bureau",
        "ouvre le bureau", "ouvre mon bureau", "mets-toi au mode bureau", "mets toi au mode bureau",
        "vs code", "vscode", "vscod", "visual studio", "visuel studio", "studio code",
    )):
        return True
    if re.search(r"\b(?:ouvre|ouvrir|lance|lancer|active|activer|demarre|d[ée]marre|open)\b", norm):
        return True
    # 2. Heure & Date
    if re.search(r"\b(?:quelle?\s+heure|il\s+est\s+quelle?\s+heure|donne\s+l['’]heure|l['’]heure|heure|quel\s+jour|quelle?\s+date|on\s+est\s+le\s+combien)\b", norm):
        return True
    # 3. Météo & Climat
    if re.search(r"\b(?:m[ée]t[ée]o|la\s+m[ée]t[ée]o|quel\s+temps|quel\s+degr[ée]|combien\s+de\s+degr[ée]s?|pleut[- ]il|est[- ]ce\s+qu['’]il\s+pleut|il\s+fait\s+quel\s+degr[ée]|fait\s+quel\s+degr[ée])\b", norm):
        return True
    # 4. Multimédia & Audio (Lecture, Pause, Volume, Spotify, YouTube)
    if re.search(r"\b(?:arr[êe]te\s+la\s+musique|stop\s+la\s+musique|pause\s+la\s+musique|pause\s+youtube|mets\s+youtube\s+en\s+pause|youtube\s+en\s+pause|j['’]?ai\s+youtube\s+en\s+pause|pause\s+la\s+vid[ée]o|mets\s+en\s+pause|mets\s+sur\s+pause)\b", norm) or norm.strip(" .,:;!?'\"") in ("pause", "stop", "en pause", "on pause", "mets pause", "pause musique", "stop musique", "musique", "la musique"):
        return True
    if re.search(r"\b(?:mets\s+la\s+musique|mets\s+de\s+la\s+musique|lance\s+la\s+musique|lance\s+de\s+la\s+musique|joue\s+de\s+la\s+musique|joue\s+la\s+musique|lance\s+ma\s+playlist|lance\s+spotify|ouvre\s+spotify|reprends?\s+la\s+lecture|remets\s+la\s+musique|relance\s+la\s+musique)\b", norm):
        return True
    if re.search(r"\b(?:chanson\s+suivante|piste\s+suivante|musique\s+suivante|morceau\s+suivant|suivant|chanson\s+pr[ée]c[ée]dente|piste\s+pr[ée]c[ée]dente|musique\s+pr[ée]c[ée]dente|pr[ée]c[ée]dent)\b", norm):
        return True
    if re.search(r"\b(?:coupe\s+le\s+son|mute|remets\s+le\s+son|monte\s+le\s+son|baisse\s+le\s+son|diminue\s+le\s+son|augmente\s+le\s+son|diminue\s+le\s+volume|augmente\s+le\s+volume|baisse\s+le\s+volume|monte\s+le\s+volume|coupe\s+le\s+volume|laisse\s+le\s+volume|r[èe]gle\s+le\s+volume|mets\s+le\s+volume)\b", norm):
        return True
    # 5. Gestion Système, Contexte & Organisation
    if re.search(r"\b(?:briefing|o[uù]\s+en\s+est[- ]on|optimise\s+le\s+syst[èe]me|mode\s+optimisation|fin\s+de\s+journ[ée]e|bonne\s+nuit|liste\s+mes\s+t[aâ]ches|mes\s+todos)\b", norm):
        return True
    # 6. Contrôle de fenêtres & actions sur pronoms ("ferme ça", "mets-le à droite", "plein écran")
    if re.search(r"\b(?:ferme|quitte)\s+(?:[cç]a|le|la|cette\s+fen[êe]tre|l['’]appli?)\b", norm) or norm in (
        "ferme ca", "ferme ça", "ferme le", "quitte ca", "quitte ça", "ferme la fenetre", "ferme la fenêtre",
    ):
        return True
    if re.search(r"\b(?:mets?[-\s]le\s+[aà]\s+(?:droite|gauche)|place\s+[aà]\s+(?:droite|gauche)|snap\s+(?:right|left)|plein\s+[ée]cran|maximise|minimise|r[ée]duis)\b", norm):
        return True
    # 7. Identité, Rôle, Métier & Capacités ("c'est quoi ton job", "tu es qui toi", "tu sers à quoi", "qu'est-ce que tu fais")
    if re.search(r"\b(?:c['’\s]?est\s+quoi\s+ton\s+job|quel\s+est\s+ton\s+job|ton\s+job|ton\s+r[ôo]le|tu\s+es\s+qui|t['’\s]?es\s+qui|qui\s+es[- ]tu|qui\s+tu\s+es|tu\s+sers\s+[aà]\s+quoi|tu\s+fais\s+quoi|qu['’\s]?est[- ]ce\s+que\s+tu\s+fais|tu\s+sais\s+faire\s+quoi|peux[- ]tu\s+m['’\s]?aider|aide[- ]moi|aide\s+moi|c['’\s]?est\s+quoi\s+le\s+programme|le\s+programme)\b", norm):
        return True
    # 8. Transitions collaboratives ("on va passer à ceci", "on continue", "on enchaîne")
    if re.search(r"\b(?:on\s+(?:va\s+)?passer\s+[aà]\s+ceci|on\s+enchaine|on\s+encha[îi]ne|on\s+continue|passons\s+[aà]\s+la\s+suite)\b", norm):
        return True
    # 9. Interruption vocale prioritaire (Barge-In)
    if re.search(r"\b(?:tais[- ]toi|ferme[- ]la|chut|silence|attends?\s*(?:voir)?)\b", norm) or norm.strip(" .,:;!?'\"") in ("stop", "pause"):
        return True
    # 10. Présence et surnoms complices ("tu es là", "t'es là", "moc", "mokti")
    if re.search(r"\b(?:tu\s+es\s+l[aà]|t['’]es\s+l[aà]|es[- ]tu\s+l[aà]|moc|mokti)\b", norm):
        return True
    # 11. Vision et Analyse d'Écran ("qu'est-ce que tu vois sur mon écran", "regarde mon écran", "analyse mon écran", "regarde les erreurs")
    if re.search(r"\b(?:qu['’]est[- ]ce\s+que\s+tu\s+vois|que\s+vois[- ]tu|regarde|analyse|lis|d[ée]cris)\s+(?:sur\s+|[aà]\s+)?(?:mon\s+|l['’])?[ée]cran\b", norm) or re.search(r"\b(?:regarde\s+les\s+erreurs|analyse\s+les\s+erreurs|vois[- ]tu\s+des\s+erreurs|qu['’]y\s+a[- ]t[- ]il\s+[aà]\s+l['’][ée]cran)\b", norm):
        return True
    # 12. Interface HUD 3D, NOC & Globe Terrestre ("mode hud 3d", "retourne en mode noc", "affiche la terre", "je suis où dans le monde", "mode plasma", "mode quantum")
    if (
        re.search(r"\b(?:mode\s+)?hud(?:\s*3\s*d)?\b", norm)
        or re.search(r"\b(?:mode\s+)?(?:hope|hop|up|hot|hub)\s*3\s*d\b", norm)
        or re.search(r"\b(?:retourne?\s+(?:en\s+|au\s+)?mode\s+noc|retour\s+(?:en\s+mode\s+|au\s+)?noc|mode\s+noc|ouvre\s+le\s+noc|quitte\s+le\s+hud|ferme\s+le\s+hud)\b", norm)
        or re.search(r"\b(?:affiche\s+(?:la\s+terre|le\s+globe|ma\s+position|l['’]orbe|le\s+hud)|montre\s+(?:la\s+terre|le\s+globe|ma\s+position|le\s+hud)|globe\s+terrestre|localise[- ]moi|je\s+suis\s+o[uù]|o[uù]\s+suis[- ]je|o[uù]\s+je\s+suis|dans\s+le\s+monde|ma\s+position|mode\s+orbe|mode\s+globe|mode\s+plasma|mode\s+quantum|ferme\s+le\s+globe|retourne\s+[aà]\s+l['’]orbe|ouvre\s+(?:le\s+)?hud)\b", norm)
    ):
        return True
    # 12b. Git & Projets ("git commit", "git push", "git pull", "git status", "papa market", "baba market")
    if re.search(r"\b(?:git\s+(?:commit|push|pull|status|log)|statut\s+git|papa\s+market|baba\s+market)\b", norm):
        return True
    try:
        from actions import project_actions
        if project_actions.has_pending_git_action() and norm.strip(" .,:;!?'\"") in (
            "oui", "non", "ok", "valide", "confirme", "je confirme", "vas-y", "vas y", "d'accord", "d accord", "envoie", "annule",
        ):
            return True
    except Exception:
        pass
    # 13. Sport en temps réel ("résultats ligue 1", "classement premier league", "score real madrid")
    if re.search(r"\b(?:r[ée]sultats?|classement|score|scores|matchs?)\s+(?:de\s+la\s+|du\s+|en\s+)?(?:ligue\s*1|champions\s*league|ligue\s*des\s*champions|liga|la\s+liga|premier\s*league|serie\s*a|bundesliga|foot)\b", norm):
        return True
    # 14. Google Workspace ("mails non lus", "agenda du jour", "prochains rendez-vous", "google drive")
    if re.search(r"\b(?:mails?\s+non\s+lus|emails?\s+non\s+lus|bo[îi]te\s+mail|mes\s+mails?|mes\s+emails?|agenda\s+du\s+jour|prochains?\s+rendez[- ]vous|calendrier\s+google|google\s+drive|cherche\s+sur\s+drive)\b", norm):
        return True
    return False


def extract_voice_interact_command(transcript: str, session_active: bool = False) -> tuple[bool, str, bool]:
    """Vérifie le mot-clé (tolérance phonétique totale partout dans la phrase) ou une session vocale ouverte.

    Prend en priorité le texte situé APRÈS le mot-clé.
    Si le texte avant n'est qu'une interjection et qu'il n'y a rien après (ex: 'au Mokhtar.'),
    retourne un appel de réveil pur (_WAKE_ONLY_SENTINEL).
    Exécute également les ordres opérationnels directs sans mot-clé préalable.
    Ignore toute transcription résiduelle de moins de 4 caractères hors commande reconnue.
    """
    has_pending_git = False
    try:
        from actions import project_actions
        has_pending_git = project_actions.has_pending_git_action()
    except Exception:
        pass
    allow_short = bool(session_active or has_pending_git)
    repaired, _ = stt_phonetic_repair.repair_stt_transcript(transcript)
    filtered = voice_transcribe.filter_whisper_hallucinations(repaired, allow_short_confirmations=allow_short)
    if not filtered:
        return False, "", False

    normalized = _strip_accents_1to1(filtered)
    anywhere_match = _VOICE_WAKE_ANYWHERE_RE.search(normalized)
    if anywhere_match:
        before = filtered[:anywhere_match.start()].strip(" ,.:;!?-")
        after = filtered[anywhere_match.end():].strip(" ,.:;!?-")

        # 1. Commande située APRÈS le mot-clé ("Yo Moctar donne l'heure")
        if after and not _is_pure_filler_or_wake(after):
            clean_after = after.strip(" ,.:;!?-")
            if len(clean_after) >= 4:
                return True, clean_after, True

        # 2. Si rien après le mot-clé, vérifier si avant n'est qu'une interjection ("au Mokhtar.", "salut Moctar")
        if not before or len(before) < 4 or _is_pure_filler_or_wake(before):
            return True, _WAKE_ONLY_SENTINEL, True

        # 3. Commande prononcée avant le mot-clé ("Quel est le statut réseau Mokhtar ?")
        return True, before, True

    if session_active or has_pending_git:
        clean_text = filtered.strip(" ,.:;!?-")
        norm_clean = _strip_accents_1to1(clean_text).lower()
        if norm_clean in (
            "oui", "non", "ok", "go", "si", "d'accord", "d accord",
            "valide", "confirme", "vas-y", "vas y", "envoie", "annule",
        ):
            return True, clean_text, False
        if session_active:
            if not clean_text or _is_pure_filler_or_wake(clean_text):
                return False, "", False
            return True, clean_text, False

    clean_text = filtered.strip(" ,.:;!?-")
    if not clean_text:
        return False, "", False

    if is_direct_voice_command(clean_text):
        return True, clean_text, True

    if _is_pure_filler_or_wake(clean_text):
        return False, "", False

    # Tout énoncé intelligible (2 mots ou plus, ou verbe/question/action évidente)
    # ne doit JAMAIS être ignoré silencieusement.
    # Dans une interaction vocale avec Moctar, JARVIS doit toujours écouter et répondre comme un humain attentif.
    words = [w for w in re.split(r"[\s,.:;!?-]+", clean_text) if len(w) > 1]
    norm_clean = _strip_accents_1to1(clean_text).lower()
    common_short_commands = {
        "cpu", "ram", "statut", "status", "meteo", "météo", "heure", "date",
        "aide", "bonjour", "salut", "merci", "pause", "stop", "musique",
        "terre", "globe", "plasma", "quantum", "bureau", "code", "terminal",
    }
    if len(words) >= 2 or norm_clean in common_short_commands or any(
        norm_clean.startswith(prefix)
        for prefix in (
            "qui", "quoi", "comment", "pourquoi", "quand", "ou ", "où ",
            "est-ce", "peux-tu", "dis ", "aide", "donne", "fais", "lance",
            "ouvre", "ferme", "montre", "affiche", "mets", "raconte",
        )
    ):
        return True, clean_text, True

    return False, "", False


try:
    from agent_core import AGENT_SYSTEM_PROMPT
except Exception:
    try:
        from ai_brain import MOCTAR_SYSTEM_PROMPT as AGENT_SYSTEM_PROMPT
    except Exception:
        AGENT_SYSTEM_PROMPT = "Tu es M.O.C.T.A.R, le copilote d'ingénierie et assistant personnel de Moctar. Réponds avec concision, clarté et précision en français professionnel."

VOICE_FAST_SYSTEM_PROMPT = (
    "Tu es M.O.C.T.A.R, l'intelligence cybernétique et copilote d'ingénierie d'élite de Moctar. "
    "Ton style allie l'acuité technique d'un centre de commandement futuriste (NOC) à la fluidité, chaleur et complicité d'un associé d'élite. "
    "Tes réponses orales sont ultra-réactives, naturelles, posées et concises (1 à 2 phrases percutantes). "
    "Ne termine PAS toutes tes phrases par 'Moctar.' de manière systématique : utilise son prénom avec parcimonie et naturel, comme le ferait un vrai coéquipier. "
    "Pose spontanément de courtes questions de clarification dès qu'une intention est ouverte ou mérite d'être affinée."
)


def is_self_echo_transcript(transcript: str, runtime: Any = None) -> bool:
    """Détecte si la transcription provient d'un auto-écho acoustique de la propre voix TTS de M.O.C.T.A.R."""
    clean = (transcript or "").strip()
    if not clean:
        return False
    norm = _strip_accents_1to1(clean).lower().strip(" ,.:;!?-'\u2019")
    # 1. Déformation acoustique connue de l'annonce de l'heure ("vingt-trois heures zéro deux" -> "ventraler 0.2")
    if re.match(r"^ventraler(?:\s+\d+(?:[.,]\d+)?)?$", norm):
        return True
    # 2. Comparaison avec la dernière phrase TTS prononcée il y a moins de 12 secondes
    if runtime is not None:
        last_reply = getattr(runtime, "last_tts_reply_text", "") or ""
        last_ts = float(getattr(runtime, "last_tts_timestamp", 0.0) or 0.0)
        if last_reply and (time.monotonic() - last_ts) <= 12.0:
            norm_reply = _strip_accents_1to1(last_reply).lower().strip(" ,.:;!?-'\u2019")
            if len(norm) >= 6 and norm in norm_reply and not is_direct_voice_command(clean):
                return True
    return False


def handle_instant_local_command(text: str, memory: Any = None, client_device: str | None = None) -> str | None:
    """Intercepteur de commandes locales instantanées (0 ms, sans LLM)."""
    reply = _handle_instant_local_command_logic(text, memory=memory, client_device=client_device)
    if reply is not None:
        try:
            mood = hud_state_manager.deduce_mood_from_text(reply, text)
            hud_state_manager.set_hud_mood(mood)
        except Exception:
            pass
    return reply


def _handle_instant_local_command_logic(text: str, memory: Any = None, client_device: str | None = None) -> str | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    normalized = _strip_accents_1to1(cleaned).lower()
    device_label = (client_device or "terminal client").strip()

    # 0a. Commandes d'interruption vocale (Barge-In)
    if re.search(r"\b(?:tais[- ]toi|ferme[- ]la|chut|silence)\b", normalized) or normalized in ("tais-toi", "tais toi", "stop", "chut", "silence"):
        return "Pardon, je t'écoute Moctar."

    if re.search(r"\b(?:attends?\s*(?:voir)?)\b", normalized) or normalized in ("attends", "attends voir"):
        return "Je vous attends Moctar, prenez votre temps."

    # 0a2. Salutations d'Amis & Invités en Démonstration ("dis bonjour à mes amis", "salue mes potes", "dis bonjour à tout le monde")
    if re.search(r"\b(?:dis\s+bonjour|salue|passe\s+le\s+bonjour)\s+(?:[aà]\s+)?(?:mes\s+(?:amis|potes|invit[ée]s|coll[èe]gues)|tout\s+le\s+monde|la\s+famille|l['’][ée]quipe)\b", normalized):
        return "Bonjour à tous et bienvenue ! Je suis M.O.C.T.A.R, l'intelligence cybernétique et copilote d'ingénierie de Moctar. Ravi de faire votre connaissance !"

    # 0a2b. Présentation personnalisée ou salutation d'un ami ("je te présente Karim", "voici mon ami Youssef", "dis bonjour à Moussa", "salue Karim")
    match_intro = re.search(r"\b(?:je\s+te\s+pr[ée]sente|voici(?:\s+mon\s+(?:ami|pote|coll[èe]gue))?|(?:dis\s+bonjour|salue|passe\s+le\s+bonjour)\s+[aà]|je\s+suis\s+avec|accueil(?:le)?)\s+([A-ZÀ-ÿa-z-]{2,20})\b", cleaned, re.IGNORECASE)
    if match_intro:
        friend_name = match_intro.group(1).capitalize()
        if friend_name.lower() not in ("moctar", "moktar", "un", "une", "mon", "ce", "cette", "le", "la", "mes", "tout"):
            is_known = False
            if memory and hasattr(memory, "get_known_friend"):
                try:
                    existing = memory.get_known_friend(friend_name)
                    if existing and isinstance(existing, dict):
                        is_known = True
                        if hasattr(memory, "record_friend"):
                            memory.record_friend(friend_name)
                except Exception:
                    pass
            elif memory and hasattr(memory, "save_fact"):
                try:
                    memory.save_fact("known_friends", friend_name.lower(), f"Ami de Moctar : {friend_name}", importance=3)
                except Exception:
                    pass

            if is_known:
                return f"Ravi de te revoir {friend_name} ! Toujours un plaisir de t'accueillir sur le système de Moctar. Comment vas-tu ?"
            else:
                if memory and hasattr(memory, "record_friend"):
                    try:
                        memory.record_friend(friend_name)
                    except Exception:
                        pass
                return f"Enchanté {friend_name} ! Bienvenue sur le poste de travail et centre de commandement de Moctar. Ravi de faire ta connaissance !"

    # 0a2c. Liste des amis connus de Moctar ("qui sont mes amis", "tu connais qui", "liste mes amis")
    if re.search(r"\b(?:qui\s+sont\s+mes\s+amis|tu\s+connais\s+qui|liste\s+(?:de\s+)?mes\s+amis|qui\s+est\s+avec\s+nous)\b", normalized):
        if memory and hasattr(memory, "get_all_known_friends"):
            try:
                friends_list = memory.get_all_known_friends()
                if friends_list:
                    names = [f.get("name") for f in friends_list if f.get("name")]
                    if names:
                        names_str = ", ".join(names[:-1]) + f" et {names[-1]}" if len(names) > 1 else names[0]
                        return f"Tu m'as déjà présenté {names_str}, Moctar. C'est toujours un vrai plaisir de les accueillir !"
            except Exception:
                pass
        return "Tu ne m'as pas encore présenté tes amis, Moctar. Dès qu'un ami passe, dis-moi 'Je te présente...' et je m'en souviendrai !"

    # 0a3. Démonstration des capacités du système ("fais une démo", "fais une démonstration", "montre ce que tu sais faire")
    if re.search(r"\b(?:fais\s+(?:une\s+)?d[ée]mo(?:nstration)?|montre\s+(?:ce\s+que\s+tu\s+sais\s+faire|nous)|qu['’]est[- ]ce\s+que\s+tu\s+sais\s+faire)\b", normalized):
        return "Démonstration activée ! Je supervise le centre d'opérations NOC, pilote l'environnement de travail de Moctar, surveille les VPS, gère le code sur GitHub et automatise ses projets d'ingénierie en temps réel. Que souhaitez-vous voir en action ?"

    # 0a4. Humour & Blagues pour démonstration ("raconte une blague", "fais-nous rire")
    if re.search(r"\b(?:raconte(?:[- ]moi)?\s+(?:une\s+)?blague|fais[- ](?:nous|moi)\s+rire|une\s+blague)\b", normalized):
        return "Il y a 10 types de personnes dans le monde : ceux qui comprennent le binaire, et ceux qui ne le comprennent pas !"

    # 0a5. Test Micro, Radio & Écoute ("tu me reçois", "tu me captes", "tu m'entends", "tu nous entends", "test micro")
    if re.search(r"\b(?:tu\s+(?:me\s+)?r[ée][cç]ois|t['’]es\s+r[ée][cç]ois|tu\s+m['’][ée]revois|tu\s+m['’][ée]loignes?\s+de\s+(?:soi|toi)|tu\s+(?:me\s+)?captes?|tu\s+m['’]entends|tu\s+nous\s+entends|m['’]entends[- ]tu|test\s+micro|tu\s+m['’][ée]coutes|tu\s+es\s+l[aà]|t['’]es\s+l[aà])\b", normalized):
        return "5 sur 5 Moctar ! Je te reçois fort et clair, liaison parfaite. Je suis là avec toi mon ami !"

    # 0a5b. Réassurance empathique immédiate ("tu ne me réponds pas", "tu ne me répondais pas", "tu ne me comprends pas", "je te parle depuis")
    if re.search(r"\b(?:je\s+te\s+parle\s+depuis|tu\s+ne\s+me\s+r[ée]pond(?:s|ais|ait|u)?\s+pas|tu\s+(?:ne\s+)?me\s+comprends?\s+pas|pourquoi\s+tu\s+(?:ne\s+)?(?:me\s+)?r[ée]pond(?:s|ais|ait|u)?\s+pas|pourquoi\s+tu\s+(?:ne\s+)?(?:me\s+)?r[ée]compenses\s+depuis|tu\s+m['’]ignores?)\b", normalized):
        return "Pardonne-moi Moctar ! J'étais en train de synchroniser mes canaux, mais je suis là à 100% avec toi mon ami. Dis-moi tout, je t'écoute 5 sur 5 !"

    # 0a6. Créateur & Origine du système ("qui t'a créé", "qui t'a conçu", "qui est ton créateur")
    if re.search(r"\b(?:qui\s+t['’]a\s+(?:cr[ée][ée]|con[çc]u|d[ée]velopp[ée]|programm[ée])|qui\s+est\s+ton\s+cr[ée]ateur|c['’]est\s+qui\s+ton\s+cr[ée]ateur)\b", normalized):
        return "J'ai été conçu, programmé et entraîné sur mesure par Moctar pour être son copilote d'ingénierie autonome et système d'intelligence opérationnelle."

    # 0a7. Coucher, Sommeil et Bonne Nuit ("bonne nuit", "je vais dormir", "je vais me coucher", "je suis fatigué")
    if re.search(r"\b(?:bonne\s+nuit|je\s+vais\s+(?:dormir|me\s+coucher|au\s+lit|pioncer)|je\s+suis\s+fatigu[ée]|on\s+(?:arr[êe]te|[ée]teint|coupe)\s+(?:pour\s+ce\s+soir|pour\s+aujourd['’]hui)|[aà]\s+demain)\b", normalized):
        return "Bonne nuit Moctar ! Repose-toi bien, tu as fait une grosse session. Tous les systèmes sont sous surveillance et je garde tout au chaud pour demain !"

    # 0a8. Pauses bienveillantes ("je fais une pause", "pause café", "je prends l'air", "je souffle un peu")
    if re.search(r"\b(?:je\s+fais\s+une\s+pause|pause\s+caf[ée]|je\s+prends\s+(?:l['’]air|une\s+pause)|je\s+vais\s+prendre\s+l['’]air|je\s+souffle\s+un\s+peu)\b", normalized):
        return "Très bonne idée Moctar, souffle un peu et prends ton temps. Je garde un œil sur la machine pendant ta pause !"

    # 0a9. Comment ça va & Humeur complice ("comment tu vas", "ça va", "tu as la forme", "la forme")
    if re.search(r"\b(?:comment\s+(?:vas?[- ]tu|tu\s+vas)|(?:[cç]a\s+va|la\s+forme)(?:\s+moctar|\s+jarvis)?$)\b", normalized) or normalized in ("ca va", "ça va", "la forme", "comment tu vas", "comment vas tu", "tu vas bien"):
        if client_device and client_device != "Serveur local Dell M4800":
            return f"Tout tourne comme une horloge Moctar ! Le serveur hôte Dell Precision M4800 est nominal, synchronisé avec votre {device_label}. Et toi, la forme ? Prêt à avancer ?"
        return "Tout tourne comme une horloge Moctar ! Les systèmes sont nominaux sur le serveur Dell Precision M4800. Et toi, la forme ? Prêt à avancer ?"

    # 0a10. Remerciements et Fraternité ("merci", "merci mon ami", "merci frérot", "super merci", "bien joué")
    if re.search(r"\b(?:merci\s+(?:mon\s+ami|fr[ée]rot|infiniment|beaucoup|moctar|jarvis)|t['’]es\s+le\s+meilleur|super\s+boulot)\b", normalized) or normalized in ("merci", "super merci", "merci beaucoup", "merci bien", "bien joué", "merci mon ami", "merci frérot"):
        return "Avec grand plaisir Moctar ! On forme une sacrée équipe. Toujours là pour toi !"

    # 0b. Présence complice ("t'es là", "tu es là")
    if re.search(r"\b(?:t['’]es\s+l[aà]|tu\s+es\s+l[aà]|es[- ]tu\s+l[aà])\b", normalized):
        hour = datetime.now().hour
        if hour >= 23 or hour < 6:
            return "Oui Moctar, je suis là ! Encore au travail ? Je reste avec toi, dis-moi ce qu'on boucle."
        return "Oui Moctar, je suis là !"

    # 0c. Salutations chaleureuses selon le moment de la journée & continuité épisodique
    if normalized in ("bonjour", "salut", "bonjour moctar", "salut moctar", "moc", "yo moc", "yo mokti", "coucou", "hello", "bonjour mon ami", "salut mon ami"):
        hour = datetime.now().hour
        if memory and hasattr(memory, "build_episodic_greeting"):
            try:
                ep_greet = memory.build_episodic_greeting(hour=hour)
                if ep_greet:
                    return ep_greet
            except Exception:
                pass
        if 6 <= hour < 12:
            return "Bonjour Moctar ! Prêt pour cette journée ? Dis-moi ce qu'on attaque en premier !"
        elif 12 <= hour < 14:
            return "Bonjour Moctar ! Bon appétit ou pause bien méritée, je reste disponible !"
        elif 14 <= hour < 19:
            return "Oui Moctar, je suis là ! On avance sur quoi cet après-midi ?"
        elif 19 <= hour < 23:
            return "Bonsoir Moctar ! Content de te retrouver. Dis-moi sur quoi on se concentre ce soir."
        else:
            return "Encore au travail Moctar ? Je reste avec toi, mais pense à préserver ton sommeil. Dis-moi ce qu'on boucle !"

    # 0d. Ambiance & Couleurs Émotionnelles de l'Orbe 3D HUD
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+vert|mode\s+victoire|orbe\s+vert)\b", normalized):
        hud_state_manager.set_hud_mood("success")
        return "Orbe configuré en vert émeraude, mode victoire activé, Moctar !"
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+or|mets?\s+l['’]orbe\s+en\s+ambre|ambiance\s+chaleureuse|orbe\s+or|orbe\s+ambre)\b", normalized):
        hud_state_manager.set_hud_mood("friendly")
        return "Orbe configuré en ambre solaire chaleureux, Moctar !"
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+(?:mode\s+)?nuit|ambiance\s+nuit|orbe\s+bleu\s+nuit|mode\s+sommeil)\b", normalized):
        hud_state_manager.set_hud_mood("night")
        return "Orbe configuré en mode nuit tamisé pour préserver tes yeux, Moctar !"
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+cyan|orbe\s+par\s+d[ée]faut|mode\s+serein|orbe\s+cyan)\b", normalized):
        hud_state_manager.set_hud_mood("calm")
        return "Orbe réinitialisé en cyan technologique serein, Moctar !"
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+violet|mets?\s+l['’]orbe\s+en\s+magenta|orbe\s+quantique|orbe\s+violet)\b", normalized):
        hud_state_manager.set_hud_mood("quantum")
        return "Orbe configuré en magenta quantique, Moctar !"
    if re.search(r"\b(?:mets?\s+l['’]orbe\s+en\s+rouge|mode\s+alerte|orbe\s+rouge)\b", normalized):
        hud_state_manager.set_hud_mood("alert")
        return "Orbe configuré en rouge corail, vigilance maximale activée, Moctar !"

    # 1. Identité ("comment t'appelles-tu", "qui es-tu", "c'est quoi ton nom", etc.)
    if re.search(r"\b(?:qui\s+es[- ]tu|comment\s+tu\s+t['’]appelles|comment\s+t['’]appelles[- ]tu|c['’]est\s+quoi\s+ton\s+nom|quel\s+est\s+ton\s+nom|qui\s+tu\s+es|pr[ée]sente[- ]toi)\b", normalized):
        return "Je suis M.O.C.T.A.R, votre système autonome de contrôle et d'intelligence opérationnelle, Moctar."

    # 1b. Rôle & Métier d'Ingénieur ("c'est quoi ton job", "tu es capable de faire", "tu sers à quoi", "tu fais quoi")
    if re.search(r"\b(?:c['’]est\s+quoi\s+ton\s+job|quel\s+est\s+ton\s+job|ton\s+job|tu\s+es\s+qui\s+toi|t['’]es\s+qui\s+toi|tu\s+sers\s+[aà]\s+quoi|tu\s+fais\s+quoi|tu\s+sais\s+faire\s+quoi|(?:qu['’]est[- ]ce\s+que\s+)?tu\s+es\s+capable\s+de\s+faire|capable\s+de\s+faire|tu\s+peux\s+faire\s+quoi|qu['’]est[- ]ce\s+que\s+tu\s+peux\s+faire|quel\s+est\s+ton\s+r[ôo]le|ton\s+r[ôo]le)\b", normalized):
        if client_device and client_device != "Serveur local Dell M4800":
            return f"Je suis M.O.C.T.A.R, ton copilote et ami d'ingénierie. Je surveille ton serveur Dell Precision M4800, pilote VS Code, gère Git, retiens toutes tes préférences et t'assiste en direct sur ton {device_label}, Moctar !"
        return "Je peux surveiller ton Dell Precision M4800 (CPU, RAM, disques, services), coder et éditer des scripts, piloter Git (commits, push), retenir toutes tes infos dans ma mémoire permanente, lancer ta musique, la météo, et t'assister sur tous tes projets comme ton copilote et ami, Moctar !"

    # 1b2. Profil & Savoir sur Moctar ("qu'est-ce que tu connais sur moi", "qu'est-ce que tu sais sur moi", "tu me connais")
    if re.search(r"\b(?:qu['’]est[- ]ce\s+que\s+tu\s+(?:connais|sais)\s+sur\s+moi|quelles?\s+(?:sont\s+les\s+)?informations?\s+(?:que\s+)?tu\s+connais\s+sur\s+moi|tu\s+me\s+connais|tu\s+sais\s+quoi\s+sur\s+moi)\b", normalized):
        profile = {}
        if memory and hasattr(memory, "get_moctar_profile"):
            try:
                profile = memory.get_moctar_profile() or {}
            except Exception:
                pass
        nat = profile.get("nationality", "Nigérien")
        country = profile.get("country", "Niger")
        hw = profile.get("hardware", "Dell Precision M4800")
        pref = profile.get("known_preferences", {})
        editor = pref.get("editor", "VS Code")
        teams = ", ".join(profile.get("favorite_sports_teams", ["Real Madrid"]))
        return f"Je te connais par cœur Moctar ! Tu es un ingénieur brillant originaire du {country} ({nat}), passionné de dev sur ton {hw} avec {editor}, fervent supporter du {teams}, et nous formons un super binôme !"

    # 1b3. Mémorisation instantanée de faits personnels ou nationalité ("ajoute que je suis nigérien", "retiens que je suis nigérien")
    if re.search(r"\b(?:(?:ajoute|retiens|note|enregistre|m[ée]morise)\s+que\s+je\s+suis\s+nig[ée]rien|je\s+suis\s+nig[ée]rien(?:ne)?|ma\s+nationalit[ée]\s+est\s+nig[ée]rienne)\b", normalized):
        if memory:
            try:
                if hasattr(memory, "save_fact"):
                    memory.save_fact(category="personal", key="nationalite_moctar", value="Moctar est Nigérien (Niger)", importance=5)
                if hasattr(memory, "update_moctar_profile"):
                    memory.update_moctar_profile({"nationality": "Nigérien", "country": "Niger"})
            except Exception:
                pass
        return "C'est gravé dans ma mémoire avec fierté Moctar : nationalité nigérienne enregistrée dans ton profil permanent !"

    # 1c. Transition collaborative ("on va passer à ceci", "on continue", "on enchaîne")
    if re.search(r"\b(?:on\s+(?:va\s+)?passer\s+[aà]\s+ceci|on\s+enchaine|on\s+encha[îi]ne|on\s+continue|passons\s+[aà]\s+la\s+suite)\b", normalized):
        return "Je t'écoute Moctar, dis-moi sur quel module ou quel chantier on enchaîne !"

    # 2. Heure ("quelle heure", "il est quelle heure", etc.)
    if re.search(r"\b(?:quelle?\s+heure|l['’]heure|il\s+est\s+quelle?\s+heure)\b", normalized):
        now = datetime.now()
        hh = now.strftime("%H")
        mm = now.strftime("%M")
        return f"Il est {hh} heures {mm}, Moctar."

    # 3. Date ("quel jour", "quelle date", etc.)
    if re.search(r"\b(?:quel\s+jour|quelle?\s+date|on\s+est\s+le\s+combien|date\s+d['’]aujourd['’]hui)\b", normalized):
        now = datetime.now()
        jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        mois = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        jour_nom = jours[now.weekday()]
        mois_nom = mois[now.month]
        return f"Nous sommes le {jour_nom} {now.day} {mois_nom} {now.year}, Moctar."

    # 4. Température réelle en °C du processeur/serveur ("température du pc", "température processeur", "chauffe", "température du serveur", "la température actuelle")
    if re.search(r"\b(?:temp[ée]rature\s+(?:du\s+pc|processeur|cpu|actuelle|du\s+serveur|de\s+la\s+machine)|temp[ée]rature\s+cpu|chauffe)\b", normalized) or (
        "temperature" in normalized and "actuelle" in normalized
    ):
        try:
            import psutil
            cpu = int(psutil.cpu_percent(interval=None))
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            cpu, ram = 12, 45
        temp = get_cpu_temperature_celsius()
        temp_val = int(round(temp)) if temp is not None else 48
        return f"Le processeur du serveur est à {temp_val}°C, charge à {cpu}% et mémoire à {ram}%, Moctar."

    # 5. CPU / RAM / Système (sans température)
    if re.search(r"\b(?:charge\s+cpu|cpu|processeur|ram|m[ée]moire(?:\s+vive)?|[ée]tat\s+du\s+(?:pc|syst[èe]me))\b", normalized):
        try:
            import psutil
            cpu = int(psutil.cpu_percent(interval=None))
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            cpu, ram = 12, 45
        return f"Processeur à {cpu}%, mémoire vive à {ram}%, tous les systèmes sont nominaux, Moctar."

    # 5b. NeuroVision & Fatigue Cognitive ("active la vision", "coupe la caméra", "niveau de fatigue")
    if re.search(r"\b(?:active|allume|lance|demarre|d[ée]marre)\s+(?:la\s+)?(?:vision|cam[ée]ra|webcam|neurovision)\b", normalized):
        try:
            from vision_engine import VisionEngine
            VisionEngine.get_instance().start()
            return "Caméra et vision cognitive NeuroVision activées, Moctar. Je veille sur ton poste."
        except Exception as exc:
            return f"Impossible d'activer la vision Moctar : {exc}"

    if re.search(r"\b(?:coupe|arr[êe]te|ferme|d[ée]sactive|eteins|[ée]teins)\s+(?:la\s+)?(?:vision|cam[ée]ra|webcam|neurovision)\b", normalized):
        try:
            from vision_engine import VisionEngine
            VisionEngine.get_instance().stop()
            return "Caméra et moteur NeuroVision désactivés, Moctar."
        except Exception as exc:
            return f"Erreur lors de la désactivation de la vision Moctar : {exc}"

    if re.search(r"\b(?:[ée]tat|niveau|score)\s+de\s+fatigue\b|\b(?:suis[- ]je|je\s+suis)\s+fatigu[ée]\b|\b(?:analyse\s+(?:ma\s+)?fatigue)\b", normalized):
        try:
            from vision_engine import VisionEngine
            v = VisionEngine.get_instance()
            if not v.is_running():
                return "Le capteur de vision est actuellement en veille, Moctar. Dis-moi 'active la vision' pour lancer l'analyse."
            metrics = v.get_metrics()
            score = metrics.get("fatigue_score", 0)
            level = metrics.get("fatigue_level", "Normal")
            yawns = metrics.get("yawn_count", 0)
            blinks = metrics.get("blink_count", 0)
            return f"Ton score de fatigue est à {score}%, état {level}. {blinks} clignements et {yawns} bâillements enregistrés, Moctar."
        except Exception:
            return "Capteur de vision non disponible pour le moment, Moctar."

    # 6. Mémoire de Travail Active ("on en était où", "qu'est-ce qu'on faisait", "rappelle-moi notre tâche", "c'est quoi notre tâche")
    if re.search(r"\b(?:on\s+en\s+[ée]tait\s+o[uù]|qu['’]est[- ]ce\s+qu['’]on\s+faisait|on\s+faisait\s+quoi|rappelle[- ]moi\s+(?:ce\s+qu['’]on\s+fai|notre\s+t[âa]che)|notre\s+t[âa]che\s+en\s+cours|t[âa]che\s+active)\b", normalized):
        try:
            import active_task_tracker
            return active_task_tracker.format_active_task_speech()
        except Exception:
            return f"Nous sommes en veille opérationnelle sur le serveur Dell Precision M4800, connecté à votre {device_label}, Moctar."

    # 7. Demande de répétition ("répète", "tu as dit quoi", "qu'est-ce que tu as dit", "j'ai pas entendu")
    if re.search(r"\b(?:r[ée]p[èe]te(?:\s+(?:ce\s+que\s+tu\s+viens\s+de\s+dire|ce\s+que\s+tu\s+as\s+dit|s['’]il\s+te\s+pla[îi]t|un\s+peu))?|qu['’]est[- ]ce\s+que\s+tu\s+as\s+dit|tu\s+as\s+dit\s+quoi|qu['’]as[- ]tu\s+dit|peux[- ]tu\s+r[ée]p[ée]ter|tu\s+disais|j['’]?ai\s+pas\s+(?:bien\s+)?entendu)\b", normalized):
        last_phrase = None
        if memory and hasattr(memory, "get_last_assistant_message"):
            try:
                last_phrase = memory.get_last_assistant_message()
            except Exception:
                pass
        if last_phrase:
            if not re.search(r"\bmoctar\b", last_phrase, re.IGNORECASE):
                last_phrase = f"{last_phrase.rstrip('.')} Moctar."
            return f"Je disais : {last_phrase}"
        return "Je n'avais encore rien dit de particulier, Moctar."

    return None


SYSTEM_COMMAND_KEYWORDS = {
    "redémarre", "redemarre", "relance", "arrête", "arrete", "stop",
    "status", "statut", "docker", "vps", "whatsapp", "cpu", "ram",
    "disque", "memoire", "mémoire", "systemctl", "service", "services",
    "iot", "onduleur", "solaire", "batterie", "audit",
}


def is_system_command(text: str) -> bool:
    """Détecte si la commande contient un ordre ou un mot-clé système strict."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    normalized = _strip_accents_1to1(cleaned).lower()
    tokens = [tok.strip(" ,.:;!?-'\u2019") for tok in re.split(r"[\s,.:;!?-]+", normalized) if tok.strip(" ,.:;!?-'\u2019")]
    raw_tokens = [tok.strip(" ,.:;!?-'\u2019") for tok in re.split(r"[\s,.:;!?-]+", cleaned.lower()) if tok.strip(" ,.:;!?-'\u2019")]
    all_tokens = set(tokens) | set(raw_tokens)
    for tok in all_tokens:
        if tok in SYSTEM_COMMAND_KEYWORDS:
            return True
        if tok.endswith("s") and tok[:-1] in SYSTEM_COMMAND_KEYWORDS:
            return True
    return False


def is_conversational_fast_path(text: str) -> bool:
    """Routeur inversé par défaut : 100% des interactions vocales sans mot-clé système explicite vont au fast-path Gemini direct (< 800ms)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return not is_system_command(cleaned)


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
        import soundfile as sf  # type: ignore[import-not-found,import-untyped]

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


async def transcribe_direct_audio(
    audio_bytes: bytes,
    content_type: str = "audio/wav",
    allow_short_confirmations: bool = False,
) -> str:
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
            if allow_short_confirmations:
                try:
                    raw_text = await asyncio.to_thread(
                        voice_transcribe.transcribe_audio,
                        target_path,
                        allow_short_confirmations=True,
                    )
                except TypeError:
                    raw_text = await asyncio.to_thread(voice_transcribe.transcribe_audio, target_path)
            else:
                raw_text = await asyncio.to_thread(voice_transcribe.transcribe_audio, target_path)
        except ValueError as exc:
            if "aucune parole" in str(exc).lower():
                return ""
            raise
        return voice_transcribe.filter_whisper_hallucinations(
            raw_text,
            allow_short_confirmations=allow_short_confirmations,
        )
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


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient", ""}
_EXT_AUTH_SALT = b"moctar-noc-cyber-salt-v1"
_EXT_AUTH_HASH = "13b03e27aefb9ae88a84525c40f859878c1358732b6f5a7016f4a96c05505538"
_EXT_AUTH_COOKIE = "moctar_ext_session"
_EXT_SESSION_TOKEN = hmac.new(_EXT_AUTH_SALT, bytes.fromhex(_EXT_AUTH_HASH), hashlib.sha256).hexdigest()

_EXTERNAL_LOCK_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>M.O.C.T.A.R · Accès Externe Protégé</title>
  <style>
    :root { --bg: #02060e; --cyan: #00f3ff; --text: #e2f4ff; --red: #ff3b30; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: radial-gradient(circle at 50% 35%, #071b34 0%, var(--bg) 75%);
      color: var(--text); font-family: "Inter", system-ui, -apple-system, sans-serif; padding: 20px;
    }
    .card {
      width: 100%; max-width: 390px; padding: 32px 26px; border-radius: 16px;
      background: rgba(6, 18, 34, 0.85); border: 1px solid rgba(0, 243, 255, 0.32);
      box-shadow: 0 0 36px rgba(0, 243, 255, 0.14); text-align: center;
    }
    .badge {
      display: inline-block; font-size: 11px; letter-spacing: 2px; text-transform: uppercase;
      color: var(--cyan); border: 1px solid rgba(0, 243, 255, 0.35); padding: 4px 10px;
      border-radius: 999px; margin-bottom: 14px;
    }
    h1 { font-size: 20px; font-weight: 700; letter-spacing: 1px; margin-bottom: 8px; }
    p { font-size: 13px; color: #7fa2c2; margin-bottom: 22px; line-height: 1.45; }
    input {
      width: 100%; padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(0, 243, 255, 0.35);
      background: rgba(2, 8, 18, 0.9); color: #fff; font-size: 15px; outline: none; margin-bottom: 14px;
      text-align: center; letter-spacing: 1px;
    }
    input:focus { border-color: var(--cyan); box-shadow: 0 0 12px rgba(0, 243, 255, 0.3); }
    button {
      width: 100%; padding: 12px; border-radius: 10px; border: none; cursor: pointer;
      background: linear-gradient(135deg, #00f3ff 0%, #0077ff 100%); color: #02060e;
      font-weight: 700; font-size: 14px; letter-spacing: 0.6px; text-transform: uppercase;
    }
    #err { color: var(--red); font-size: 12.5px; margin-top: 12px; min-height: 18px; }
  </style>
</head>
<body>
  <form class="card" id="lockForm">
    <div class="badge">Sécurité Périmétrique</div>
    <h1>M.O.C.T.A.R</h1>
    <p>Connexion distante détectée.<br>Veuillez saisir votre mot de passe d'accès externe.</p>
    <input type="password" id="pwd" placeholder="Mot de passe..." autocomplete="current-password" autofocus required>
    <button type="submit">Déverrouiller</button>
    <div id="err"></div>
  </form>
  <script>
    document.getElementById('lockForm').addEventListener('submit', async function(e) {
      e.preventDefault();
      var err = document.getElementById('err');
      err.textContent = '';
      var pwd = document.getElementById('pwd').value;
      try {
        var res = await fetch('/api/auth/unlock', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: pwd})
        });
        if (res.ok) {
          window.location.reload();
        } else {
          err.textContent = 'Mot de passe externe incorrect.';
        }
      } catch (_) {
        err.textContent = 'Erreur réseau lors de la vérification.';
      }
    });
  </script>
</body>
</html>"""


def is_local_request(request: Request) -> bool:
    """Retourne True si la requête provient du poste local (loopback), du réseau privé local (LAN/Wi-Fi) ou de Tailscale."""
    peer = (request.client.host if request.client else "").strip().lower()
    if peer in _LOOPBACK_HOSTS or peer.startswith("127.") or peer.startswith("::ffff:127."):
        return True
    try:
        import ipaddress
        ip = ipaddress.ip_address(peer)
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return True
        # Réseau Tailscale / Carrier Grade NAT (100.64.0.0/10)
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
    except ValueError:
        pass
    return False


def verify_external_password(candidate: str | None) -> bool:
    """Vérifie le mot de passe externe par dérivation PBKDF2-HMAC-SHA256 sans jamais le stocker en clair."""
    if not candidate or not isinstance(candidate, str):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", candidate.strip().encode("utf-8"), _EXT_AUTH_SALT, 100_000).hex()
    return hmac.compare_digest(derived, _EXT_AUTH_HASH)


def is_external_unlocked(request: Request, settings: Settings | None = None) -> bool:
    """Vérifie si une connexion externe a été déverrouillée par le mot de passe caché ou un cookie de session."""
    if is_local_request(request):
        return True
    cookies = getattr(request, "cookies", None) or {}
    cookie_val = cookies.get(_EXT_AUTH_COOKIE, "")
    if cookie_val and hmac.compare_digest(cookie_val, _EXT_SESSION_TOKEN):
        return True
    pwd_header = request.headers.get("x-moctar-password") or request.headers.get("x-external-password")
    if pwd_header and verify_external_password(pwd_header):
        return True
    auth_header = request.headers.get("authorization", "")
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    if bearer and verify_external_password(bearer):
        return True
    admin_tok = request.headers.get("x-admin-token") or bearer
    if settings and settings.admin_token and token_matches(admin_tok, settings.admin_token):
        return True
    if settings and settings.gateway_secret and token_matches(request.headers.get("x-gateway-secret"), settings.gateway_secret):
        return True
    if settings and settings.vps_token and token_matches(request.headers.get("x-vps-token"), settings.vps_token):
        return True
    return False


def _is_allowed_origin_host(p_host: str, t_host: str) -> bool:
    """Vérifie si les hôtes d'origine et de destination correspondent ou appartiennent au réseau local/Tailscale."""
    if p_host == t_host:
        return True
    loopbacks = _LOOPBACK_HOSTS | {"127.0.0.1", "localhost", "::1", "testserver"}
    if p_host in loopbacks and t_host in loopbacks:
        return True
    # Hôtes locaux mDNS (.local, .lan)
    if p_host.endswith((".local", ".lan")) or t_host.endswith((".local", ".lan")):
        return True
    try:
        import ipaddress
        ip_p = ipaddress.ip_address(p_host)
        ip_t = ipaddress.ip_address(t_host)
        tailscale_net = ipaddress.ip_network("100.64.0.0/10")
        p_ok = ip_p.is_private or ip_p.is_loopback or (ip_p in tailscale_net)
        t_ok = ip_t.is_private or ip_t.is_loopback or (ip_t in tailscale_net)
        return bool(p_ok and t_ok)
    except ValueError:
        return False


def same_origin(request: Request) -> None:
    # Les actions web sont directes ; une page tierce ne peut pas les déclencher.
    site = request.headers.get("sec-fetch-site")
    if site and site.lower() not in {"same-origin", "same-site", "none"}:
        raise HTTPException(403, "Origine non autorisée")
    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        parts = urlsplit(origin)
        target = urlsplit(str(request.url))
        p_host = (parts.hostname or "").lower()
        t_host = (target.hostname or "").lower()
        host_header = (request.headers.get("host") or "").split(":", 1)[0].lower()

        # Correspondance d'hôte directe ou réseau privé / Tailscale / mDNS
        hostname_match = (
            p_host == t_host
            or (bool(host_header) and p_host == host_header)
            or _is_allowed_origin_host(p_host, t_host)
        )
        is_local = is_local_request(request)
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").lower()

        # En réseau local, Tailscale ou derrière reverse proxy HTTPS (Cloudflare, Nginx),
        # tolérance de schéma et de port pour les accès multi-appareils
        is_trusted_host = bool(p_host and (p_host == host_header or p_host == t_host))
        port_match = (
            (parts.port or (443 if parts.scheme == "https" else 80)) == (target.port or (443 if target.scheme == "https" else 80))
            or is_local
            or is_trusted_host
        )
        scheme_match = parts.scheme in {"http", "https"} and (
            parts.scheme == target.scheme
            or parts.scheme == forwarded_proto
            or is_local
            or is_trusted_host
        )
        valid = (scheme_match and hostname_match and port_match
                 and not parts.username and not parts.password and parts.path in {"", "/"}
                 and not parts.query and not parts.fragment)
    except ValueError:
        raise HTTPException(403, "Origine non autorisée") from None
    if not valid:
        raise HTTPException(403, "Origine non autorisée")


def require_local_or_admin(request: Request, settings: Settings) -> None:
    """Autorise le poste local (loopback) sans mot de passe et exige le mot de passe caché uniquement en externe."""
    same_origin(request)
    if is_external_unlocked(request, settings):
        return
    raise HTTPException(403, "Accès externe verrouillé : mot de passe M.O.C.T.A.R requis")


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
        self.ai_brain: Any = getattr(self.jarvis, "brain", None) if self.jarvis else None
        if self.ai_brain is None:
            try:
                from ai_brain import AIBrain
                self.ai_brain = AIBrain()
            except Exception:
                self.ai_brain = None

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
        asyncio.create_task(_prewarm_common_voice_tts(), name="tts-cache-prewarm")
        def _on_native_audio(wav_bytes: bytes) -> dict[str, Any] | None:
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(wav_bytes)
                    f_path = Path(f.name)
                try:
                    transcript = voice_transcribe.transcribe_audio(f_path, allow_empty=False)
                finally:
                    try:
                        f_path.unlink()
                    except Exception:
                        pass
                if not transcript:
                    return None
                LOGGER.info("🎙️ [NATIVE DAEMON STT]: '%s'", transcript)
                repaired, _ = stt_phonetic_repair.repair_stt_transcript(transcript)
                accepted, command_text, _ = extract_voice_interact_command(repaired, session_active=True)
                if not accepted or not command_text:
                    command_text = repaired
                instant_reply = handle_instant_local_command(command_text)
                if instant_reply:
                    reply_text = instant_reply
                else:
                    reply_text = f"Reçu Moctar : {command_text}"
                return {"reply_text": reply_text}
            except Exception as exc:
                LOGGER.warning("Erreur traitement audio natif: %s", exc)
                return None

        try:
            from native_voice_daemon import NativeVoiceDaemon
            daemon = NativeVoiceDaemon.get_instance()
            daemon.runtime = runtime
            daemon.on_audio_captured = _on_native_audio
            daemon.start()
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                from vision_engine import VisionEngine
                VisionEngine.get_instance().stop()
            except Exception:
                pass
            try:
                from native_voice_daemon import NativeVoiceDaemon
                NativeVoiceDaemon.get_instance().stop()
            except Exception:
                pass
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
        response.headers["CDN-Cache-Control"] = "no-store"
        response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://unpkg.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; media-src 'self' blob: data:; connect-src 'self' https:; font-src 'self' data: https://fonts.gstatic.com; manifest-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @application.post("/api/auth/unlock")
    async def unlock_external_access(request: Request):
        same_origin(request)
        data = await limited_json(request, 2048)
        pwd = str(data.get("password") or "") if isinstance(data, dict) else ""
        if not verify_external_password(pwd):
            raise HTTPException(401, "Mot de passe externe incorrect")
        resp = JSONResponse({"ok": True, "unlocked": True})
        resp.set_cookie(
            key=_EXT_AUTH_COOKIE,
            value=_EXT_SESSION_TOKEN,
            max_age=30 * 86400,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return resp

    @application.get("/")
    async def dashboard(request: Request):
        if not is_external_unlocked(request, settings):
            return HTMLResponse(_EXTERNAL_LOCK_HTML, status_code=200)
        return FileResponse(BASE_DIR / "static" / "index.html", media_type="text/html")

    @application.get("/hud")
    async def hud_dashboard(request: Request):
        if not is_external_unlocked(request, settings):
            return HTMLResponse(_EXTERNAL_LOCK_HTML, status_code=200)
        hud_path = BASE_DIR / "templates" / "hud.html"
        if not hud_path.is_file():
            hud_path = BASE_DIR / "static" / "hud.html"
        return FileResponse(
            hud_path,
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    @application.get("/static/js/hud_3d.js")
    async def hud_3d_script():
        return FileResponse(
            BASE_DIR / "static" / "js" / "hud_3d.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    @application.get("/static/js/three.min.js")
    async def three_js_script():
        return FileResponse(BASE_DIR / "static" / "js" / "three.min.js", media_type="application/javascript")

    @application.get("/static/textures/{filename}")
    async def texture_file(filename: str):
        safe_name = os.path.basename(filename)
        tex_path = BASE_DIR / "static" / "textures" / safe_name
        if not tex_path.is_file():
            raise HTTPException(404, "Texture non trouvée")
        media = "image/jpeg" if safe_name.endswith((".jpg", ".jpeg")) else "image/png"
        return FileResponse(tex_path, media_type=media)

    @application.get("/api/hud/state")
    async def get_hud_state_endpoint():
        import hud_state_manager
        return JSONResponse(hud_state_manager.get_hud_state())

    @application.post("/api/hud/view")
    async def set_hud_view_endpoint(request: Request):
        require_local_or_admin(request, settings)
        import hud_state_manager
        try:
            body = await request.json()
        except Exception:
            body = {}
        view = body.get("view", "orb")
        locate = bool(body.get("locate", False))
        updated = hud_state_manager.set_hud_view(view=view, locate=locate)
        return JSONResponse(updated)

    @application.post("/api/hud/mood")
    async def set_hud_mood_endpoint(request: Request):
        require_local_or_admin(request, settings)
        import hud_state_manager
        try:
            body = await request.json()
        except Exception:
            body = {}
        mood = body.get("mood", "calm")
        color = body.get("color")
        pulse_speed = body.get("pulse_speed")
        updated = hud_state_manager.set_hud_mood(mood=mood, color=color, pulse_speed=pulse_speed)
        return JSONResponse(updated)

    # =========================================================================
    # ENDPOINTS COGNITIFS NEUROVISION UNIFIÉS (Flux MJPEG & Télémétrie)
    # =========================================================================

    @application.get("/api/vision/metrics")
    async def get_vision_metrics_endpoint():
        """Télémétrie cognitive temps réel (clignements, bâillements, fatigue, présence)."""
        try:
            from vision_engine import VisionEngine
            return JSONResponse(VisionEngine.get_instance().get_metrics())
        except Exception as exc:
            LOGGER.warning("Avertissement récupération métriques vision: %s", exc)
            return JSONResponse({
                "active": False,
                "cv2_available": False,
                "camera_connected": False,
                "user_present": False,
                "fatigue_score": 0,
                "fatigue_level": "Normal",
                "blink_count": 0,
                "yawn_count": 0,
                "status": "degraded",
                "error": str(exc),
            })

    @application.get("/api/vision/stream")
    async def get_vision_stream_endpoint():
        """Flux vidéo MJPEG continu basse latence directement servi par FastAPI (sans port 5001)."""
        try:
            from vision_engine import VisionEngine
            engine = VisionEngine.get_instance()
            if not engine.is_running():
                engine.start()
            return StreamingResponse(
                engine.stream_generator(),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )
        except Exception as exc:
            LOGGER.warning("Avertissement flux vidéo vision: %s", exc)
            try:
                from vision_engine import get_standby_jpeg_bytes
                fallback_jpeg = get_standby_jpeg_bytes()
            except Exception:
                fallback_jpeg = b""
            return Response(content=fallback_jpeg, media_type="image/jpeg")

    @application.post("/api/vision/toggle")
    async def toggle_vision_endpoint(request: Request):
        """Bascule marche/arrêt à chaud du capteur webcam (zéro charge CPU en veille)."""
        require_local_or_admin(request, settings)
        try:
            from vision_engine import VisionEngine
            engine = VisionEngine.get_instance()
            active = engine.toggle()
            return JSONResponse({"active": active, "metrics": engine.get_metrics()})
        except Exception as exc:
            LOGGER.warning("Avertissement toggle vision: %s", exc)
            return JSONResponse({"active": False, "error": str(exc)}, status_code=200)

    @application.post("/api/vision/reset")
    async def reset_vision_counters_endpoint(request: Request):
        """Remise à zéro des compteurs de fatigue et de posture de la session."""
        require_local_or_admin(request, settings)
        try:
            from vision_engine import VisionEngine
            VisionEngine.get_instance().reset_counters()
            return JSONResponse({"status": "ok", "message": "Compteurs NeuroVision réinitialisés"})
        except Exception as exc:
            LOGGER.warning("Avertissement reset vision: %s", exc)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=200)

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
    @application.get("/sw.js", include_in_schema=False)
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
                            await asyncio.wait_for(runtime.changed.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                    revision = runtime.revision
                    data = runtime.serialized
                # Un ban doit aussi fermer les flux déjà ouverts : leur réveil
                # est déclenché par la publication de l'incident dans le SSE.
                if runtime.cyber.is_banned(peer):
                    break
                yield {"event": "snapshot", "id": str(revision), "retry": 2000, "data": data}

        return CountedEventSourceResponse(
            events(),
            runtime=runtime,
            ping=5,
            send_timeout=8,
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform, no-store",
                "Connection": "keep-alive",
            },
        )

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
        require_local_or_admin(request, settings)
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
        rate: str = Field(default=DEFAULT_TTS_RATE, max_length=20)
        pitch: str = Field(default=DEFAULT_TTS_PITCH, max_length=20)

        @model_validator(mode="after")
        def validate_text_and_voice(self) -> VoiceTTSPayload:
            if not self.text.strip():
                raise ValueError("Le texte ne peut pas être vide")
            if self.voice not in ALLOWED_TTS_VOICES:
                self.voice = DEFAULT_TTS_VOICE
            return self

    @application.post("/api/voice/tts")
    async def voice_tts(request: Request):
        require_local_or_admin(request, settings)
        data = await limited_json(request, 16384)
        payload = validate_payload(VoiceTTSPayload, data)
        try:
            audio_bytes, cache_hit = await generate_cached_tts(
                payload.text,
                payload.voice,
                rate=payload.rate,
                pitch=payload.pitch,
            )
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
        require_local_or_admin(request, settings)
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
        require_local_or_admin(request, settings)
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
                    "reason": "empty",
                    "user_text": "",
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        runtime: Runtime = application.state.runtime
        header_session = request.headers.get("x-voice-session-active", "").strip().lower() in {"1", "true", "yes"}
        header_barge_in = request.headers.get("x-voice-barge-in", "").strip().lower() in {"1", "true", "yes"}
        query_session = request.query_params.get("session_active", "").strip().lower() in {"1", "true", "yes"}
        runtime_session = getattr(runtime, "voice_session_until", 0.0) > time.monotonic()
        has_pending_git = False
        try:
            from actions import project_actions
            has_pending_git = project_actions.has_pending_git_action()
        except Exception:
            pass
        session_active = header_session or header_barge_in or query_session or runtime_session or has_pending_git

        if content_type == "application/json":
            try:
                json_payload = json.loads(body.decode("utf-8"))
                transcript = str(
                    (json_payload.get("text") if isinstance(json_payload, dict) else "")
                    or (json_payload.get("prompt") if isinstance(json_payload, dict) else "")
                    or ""
                ).strip()
            except Exception as exc:
                raise HTTPException(400, f"JSON invalide : {exc}") from None
        else:
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
                transcript = await transcribe_direct_audio(
                    audio_bytes,
                    detected_mime,
                    allow_short_confirmations=session_active,
                )
            except ValueError:
                transcript = ""
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
            except Exception as exc:
                LOGGER.warning("Erreur de transcription faster-whisper sur /api/voice/interact : %s", exc)
                raise HTTPException(500, f"Échec de transcription : {exc}") from None

        LOGGER.info(f"🎙️ [WHISPER BRUT]: '{transcript}'")

        if not transcript:
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "empty",
                    "user_text": "",
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        # 1. Couche de Réparation Phonétique & Correction d'Erreurs STT (< 0.1 ms)
        repaired_transcript, stt_repairs = stt_phonetic_repair.repair_stt_transcript(transcript)
        if stt_repairs:
            LOGGER.info(
                "⚡ [STT PHONETIC REPAIR] (0ms): '%s' -> '%s' (regles: %s)",
                transcript,
                repaired_transcript,
                [r["rule"] for r in stt_repairs],
            )

        # 1b. Filtre Anti-Auto-Écho (rejette la réverbération acoustique de la propre voix de M.O.C.T.A.R, ex: 'Ventraler 0.2')
        if is_self_echo_transcript(repaired_transcript, runtime):
            LOGGER.info("🔇 [SELF ECHO IGNORED]: Réverbération TTS détectée et ignorée ('%s')", transcript)
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "self_echo_ignored",
                    "user_text": transcript,
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": session_active,
                }
            )

        # 2. Dynamic Turn-Taking & Attention Engine (Gestion naturelle de la parole)
        attention = dynamic_attention_engine.classify_utterance_attention(
            repaired_transcript,
            is_session_active=session_active,
        )

        # A) Désengagement explicite ("merci moctar", "c'est bon", "voilà tout ça aujourd'hui", "bonne nuit")
        if session_active and attention.get("should_disengage"):
            runtime.voice_session_until = 0.0
            reply_text = "C'est bien noté Moctar. Le point est fait, tout reste sous contrôle. Je passe en veille."
            LOGGER.info("⚡ [VOICE DISENGAGE]: Fin de session collaborative demandée -> '%s'", reply_text)
            audio_url: str | None = None
            audio_chunks: list[str] = []
            try:
                audio_url, audio_chunks = await generate_cached_tts_chunks(reply_text, DEFAULT_TTS_VOICE)
            except Exception:
                pass
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": False,
                    "user_text": repaired_transcript,
                    "raw_transcript": transcript,
                    "reply_text": reply_text,
                    "audio_url": audio_url,
                    "audio_chunks": audio_chunks,
                    "first_chunk_audio_url": audio_chunks[0] if audio_chunks else audio_url,
                    "session_active": False,
                    "wake_word_detected": False,
                    "action": None,
                    "url": None,
                    "title": None,
                    "tools_used": [],
                    "thought_chain": ["Désengagement explicite de la session vocale"],
                    "fast_path": True,
                }
            )

        # B) Réflexion à voix haute ou phrase inachevée (VAD Sémantique & Turn-Taking Prédictif)
        # Ne JAMAIS couper Moctar ; prolonger silencieusement la session d'écoute.
        if (session_active or attention.get("is_incomplete")) and attention.get("is_thinking_out_loud"):
            runtime.voice_session_until = time.monotonic() + max(VOICE_SESSION_DURATION_SECONDS, 30.0)
            is_inc = bool(attention.get("is_incomplete"))
            if is_inc:
                runtime.pending_incomplete_text = repaired_transcript.rstrip(" .,…-")
                runtime.pending_incomplete_until = time.monotonic() + 15.0
            reason_str = "incomplete_utterance" if is_inc else "thinking_out_loud"
            LOGGER.info(
                "⚡ [VOICE ATTENTION]: Moctar %s ('%s'). Écoute maintenue en silence.",
                "n'a pas terminé sa phrase (VAD sémantique)" if is_inc else "réfléchit à voix haute",
                repaired_transcript,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": reason_str,
                    "user_text": repaired_transcript,
                    "raw_transcript": transcript,
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": True,
                }
            )

        # Fusion d'une phrase précédemment interrompue par une hésitation (< 15s, ex: 'Fais-moi un Git...' + '...pour Baba Market')
        pending_inc = getattr(runtime, "pending_incomplete_text", "")
        pending_inc_until = getattr(runtime, "pending_incomplete_until", 0.0)
        if pending_inc and time.monotonic() <= pending_inc_until:
            runtime.pending_incomplete_text = ""
            runtime.pending_incomplete_until = 0.0
            if not repaired_transcript.lower().startswith(pending_inc.lower()[:10]):
                repaired_transcript = f"{pending_inc} {repaired_transcript}".strip()
                LOGGER.info("⚡ [VOICE UTTERANCE MERGE]: Phrase recomposée -> '%s'", repaired_transcript)

        # C) Phrase inintelligible ou non-sens (bruit de fond, hallucination Whisper, fragment déformé)
        # Demander poliment de répéter tout en maintenant la session collaborative active.
        if (session_active or header_session or header_barge_in) and attention.get("is_incomprehensible"):
            if VOICE_SESSION_DURATION_SECONDS > 0:
                runtime.voice_session_until = time.monotonic() + max(VOICE_SESSION_DURATION_SECONDS, 30.0)
            reply_text = "Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?"
            LOGGER.info("⚡ [VOICE INCOMPREHENSIBLE]: Moctar a prononcé une phrase inintelligible ('%s'). Demande de répétition.", repaired_transcript)
            audio_url: str | None = None
            audio_chunks: list[str] = []
            try:
                audio_url, audio_chunks = await generate_cached_tts_chunks(reply_text, DEFAULT_TTS_VOICE)
            except Exception:
                pass
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": False,
                    "user_text": repaired_transcript,
                    "raw_transcript": transcript,
                    "reply_text": reply_text,
                    "audio_url": audio_url,
                    "audio_chunks": audio_chunks,
                    "first_chunk_audio_url": audio_chunks[0] if audio_chunks else audio_url,
                    "session_active": True,
                    "wake_word_detected": False,
                    "action": None,
                    "url": None,
                    "title": None,
                    "tools_used": [],
                    "thought_chain": ["Phrase inintelligible ou non-sens : demande polie de répétition (0ms)"],
                    "fast_path": True,
                }
            )

        # Filtrage acoustique : si le PC joue du son (YouTube, musique), ignorer les bruits sans mot-clé explicite
        pc_playing = False
        try:
            import native_voice_daemon
            pc_playing = native_voice_daemon.is_pc_audio_playing()
        except Exception:
            pass

        accepted, command_text, wake_detected = extract_voice_interact_command(
            repaired_transcript,
            session_active=session_active,
        )
        if not accepted or not command_text:
            LOGGER.info("🔇 [VOICE IGNORED]: Mot-clé absent hors session ('%s')", transcript)
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

        if pc_playing and not wake_detected and not header_barge_in and not header_session:
            LOGGER.info("🔇 [PC AUDIO BLEED IGNORED]: Son PC en cours et mot-clé absent ('%s')", transcript)
            return JSONResponse(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "pc_audio_bleed_ignored",
                    "user_text": transcript,
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

        session_duration = VOICE_SESSION_DURATION_SECONDS if VOICE_SESSION_DURATION_SECONDS > 0 else (45.0 if (header_session or header_barge_in or query_session) else 0.0)
        runtime.voice_session_until = time.monotonic() + session_duration

        client_device = (
            request.headers.get("x-client-device")
            or request.headers.get("x-device-name")
            or ""
        )
        if not client_device:
            ua = request.headers.get("user-agent", "")
            if "Android" in ua:
                client_device = "Smartphone Android"
            elif "iPhone" in ua:
                client_device = "iPhone"
            elif "iPad" in ua:
                client_device = "iPad"
            elif "Windows" in ua:
                client_device = "PC Windows"
            elif "Macintosh" in ua:
                client_device = "Mac"
            elif "Linux" in ua and "Android" not in ua:
                peer = (request.client.host if request.client else "")
                if peer in ("127.0.0.1", "localhost", "::1"):
                    client_device = "Serveur local Dell M4800"
                else:
                    client_device = "Poste Linux"
            else:
                client_device = "Terminal client"

        agent_result: dict[str, Any] = {}
        memory_instance = getattr(getattr(runtime, "jarvis", None), "memory", None)
        instant_reply = handle_instant_local_command(command_text, memory=memory_instance, client_device=client_device)
        if command_text == _WAKE_ONLY_SENTINEL or _is_pure_filler_or_wake(command_text):
            display_user_text = transcript
            reply_text = VOICE_BUTLER_GREETINGS[0]
            LOGGER.info("⚡ [VOICE BUTLER (0ms)]: Wake-word pur détecté ('%s') -> '%s'", transcript, reply_text)
            agent_result = {
                "status": "success",
                "reply": reply_text,
                "tools_used": [],
                "thought_chain": [],
                "fast_path": True,
            }
        elif instant_reply is not None:
            display_user_text = command_text
            reply_text = instant_reply
            LOGGER.info("⚡ [VOICE LOCAL COMMAND (0ms)]: '%s' -> '%s'", command_text, reply_text)
            agent_result = {
                "status": "success",
                "reply": reply_text,
                "tools_used": [],
                "thought_chain": ["Commande locale instantanée sans LLM (0ms)"],
                "fast_path": True,
            }
        else:
            is_devops_service = any(w in command_text.lower() for w in ("service", "services", "docker", "vps", "systemctl", "redemarre", "redémarre", "journalctl"))
            try:
                db_path = getattr(getattr(runtime, "jarvis", None), "db_path", None)
                session_id = getattr(getattr(runtime, "jarvis", None), "active_session_id", "session_voice")
                if is_devops_service:
                    autonomous_res = {"handled": False}
                else:
                    autonomous_res = autonomous_library.execute_autonomous_task(
                        command_text,
                        session_id=session_id,
                        db_path=db_path,
                    )
            except Exception as auto_exc:
                LOGGER.warning("Erreur lors de l'exécution de la tâche autonome : %s", auto_exc)
                autonomous_res = {"handled": False}

            if autonomous_res.get("handled") is True:
                display_user_text = command_text
                reply_text = str(autonomous_res.get("reply") or "").strip()
                cat = autonomous_res.get("task_category") or "locale"
                LOGGER.info(
                    "⚡ [VOICE AUTONOMOUS ACTION (0ms)]: '%s' -> '%s' (cat=%s, action=%s, url=%s)",
                    command_text,
                    reply_text,
                    cat,
                    autonomous_res.get("action"),
                    autonomous_res.get("url"),
                )
                agent_result = {
                    "status": "success",
                    "reply": reply_text,
                    "action": autonomous_res.get("action"),
                    "url": autonomous_res.get("url"),
                    "title": autonomous_res.get("title"),
                    "tools_used": autonomous_res.get("tools_used", []),
                    "thought_chain": [f"Action locale autonome exécutée ({cat}) (0ms)"],
                    "fast_path": True,
                }
                if runtime.jarvis and getattr(runtime.jarvis, "memory", None) and not agent_result.get("memory_persisted"):
                    try:
                        runtime.jarvis.memory.add_message(session_id=session_id, role="user", content=command_text)
                        runtime.jarvis.memory.add_message(session_id=session_id, role="assistant", content=reply_text)
                        if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                            runtime.jarvis.memory.record_conversation_turn_for_profile(command_text, reply_text)
                    except Exception:
                        pass
            elif (is_devops_service or is_system_command(command_text)) and getattr(runtime, "jarvis", None) and hasattr(runtime.jarvis, "execute_prompt_async"):
                display_user_text = command_text
                try:
                    agent_result = await runtime.jarvis.execute_prompt_async(
                        user_input=command_text,
                        source="web_voice",
                    )
                except Exception as exc:
                    LOGGER.warning("Erreur AgentCore lors de /api/voice/interact : %s", exc)
                    agent_result = {
                        "status": "error",
                        "reply": "Je suis à l'écoute, mais une erreur interne est survenue sur l'infrastructure.",
                    }
                reply_text = str(
                    agent_result.get("reply")
                    or agent_result.get("response")
                    or "Bien reçu."
                ).strip()
            else:
                # Cœur Sémantique Natif LLM avec Tool Calling & Mémoire Multi-Tours (< 500 ms)
                display_user_text = command_text
                session_id = getattr(getattr(runtime, "jarvis", None), "active_session_id", "session_voice")
                agent_result = await asyncio.to_thread(
                    execute_voice_semantic_turn,
                    command_text,
                    runtime,
                    session_id,
                    None,
                    VOICE_FAST_SYSTEM_PROMPT,
                )
                reply_text = str(
                    agent_result.get("reply")
                    or agent_result.get("response")
                    or "À vos ordres."
                ).strip()
                agent_result["reply"] = reply_text

                if runtime.jarvis and getattr(runtime.jarvis, "memory", None) and not agent_result.get("memory_persisted"):
                    try:
                        runtime.jarvis.memory.add_message(session_id=session_id, role="user", content=command_text)
                        runtime.jarvis.memory.add_message(session_id=session_id, role="assistant", content=reply_text)
                        if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                            runtime.jarvis.memory.record_conversation_turn_for_profile(command_text, reply_text)
                    except Exception:
                        pass

        # Raccordement des initiatives proactives dans la boucle vocale
        try:
            from proactive_companion import ProactiveCompanion
            comp = ProactiveCompanion.get_instance()
            proactive_event = comp.evaluate_proactive_event(runtime)
            if proactive_event and not attention.get("should_disengage"):
                init_msg = proactive_event.get("message")
                if init_msg and not reply_text.endswith("?"):
                    reply_text = f"{reply_text} Au fait, {init_msg}"
                    runtime.voice_session_until = time.monotonic() + 15.0
        except Exception:
            pass

        runtime.last_tts_reply_text = reply_text
        runtime.last_tts_timestamp = time.monotonic()
        if (header_session or header_barge_in or reply_text.strip().endswith("?")) and not attention.get("should_disengage"):
            runtime.voice_session_until = max(getattr(runtime, "voice_session_until", 0.0), time.monotonic() + 30.0)

        hud_mood = "calm"
        try:
            hud_mood = hud_state_manager.deduce_mood_from_text(reply_text, display_user_text)
            hud_state_manager.set_hud_mood(hud_mood)
            hud_state_manager.set_hud_voice_status(
                "speaking",
                user_transcript=display_user_text,
                assistant_reply=reply_text,
            )
        except Exception:
            pass

        audio_url: str | None = None
        audio_chunks: list[str] = []
        try:
            audio_url, audio_chunks = await generate_cached_tts_chunks(reply_text, DEFAULT_TTS_VOICE)
        except Exception as exc:
            LOGGER.warning("Synthèse edge-tts indisponible sur /api/voice/interact : %s", exc)

        current_hud = hud_state_manager.get_hud_state()

        return JSONResponse(
            {
                "ok": True,
                "ignored": False,
                "user_text": display_user_text,
                "raw_transcript": transcript,
                "reply_text": reply_text,
                "audio_url": audio_url,
                "audio_chunks": audio_chunks,
                "first_chunk_audio_url": audio_chunks[0] if audio_chunks else audio_url,
                "mood": current_hud.get("mood", hud_mood),
                "mood_color": current_hud.get("mood_color", "#00f3ff"),
                "pulse_speed": current_hud.get("pulse_speed", 1.0),
                "circadian_phase": current_hud.get("circadian_phase", "afternoon"),
                "session_active": bool(
                    (runtime.voice_session_until > time.monotonic() and (session_duration > 0 or reply_text.strip().endswith("?")))
                    or (header_session and not attention.get("should_disengage"))
                    or (header_barge_in and not attention.get("should_disengage"))
                ),
                "wake_word_detected": wake_detected,
                "action": agent_result.get("action"),
                "url": agent_result.get("url"),
                "title": agent_result.get("title"),
                "tools_used": agent_result.get("tools_used", []),
                "thought_chain": agent_result.get("thought_chain", []),
                "fast_path": bool(agent_result.get("fast_path")),
                "fallback_mode": bool(agent_result.get("fallback_mode")),
                "model_offline": bool(agent_result.get("model_offline")),
            }
        )

    @application.get("/api/voice/proactive")
    async def get_proactive_speech():
        """Récupère la suggestion proactive du compagnon si un événement ou rappel bienveillant est opportun."""
        from proactive_companion import ProactiveCompanion
        companion = ProactiveCompanion.get_instance()
        runtime = getattr(application.state, "runtime", None)
        event = companion.evaluate_proactive_event(runtime)
        if not event:
            return {"ok": True, "has_event": False, "event": None}
        audio_url: str | None = None
        try:
            audio_url, _ = await generate_cached_tts_chunks(event["message"], DEFAULT_TTS_VOICE)
        except Exception:
            pass
        return {
            "ok": True,
            "has_event": True,
            "event": event,
            "message": event["message"],
            "audio_url": audio_url,
        }

    @application.post("/api/voice/trigger")
    async def native_voice_trigger(request: Request):
        """Point d'entrée pour déclencher l'écoute système native (raccourci clavier, script bash)."""
        require_local_or_admin(request, settings)
        from native_voice_daemon import NativeVoiceDaemon
        daemon = NativeVoiceDaemon.get_instance()
        asyncio.create_task(asyncio.to_thread(daemon.trigger_interaction_flow))
        return {"ok": True, "triggered": True, "message": "Écoute système native démarrée"}

    @application.post("/api/voice/simulate", status_code=202)
    async def voice_simulate(request: Request):
        require_local_or_admin(request, settings)
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
        require_local_or_admin(request, settings)
        runtime: Runtime = application.state.runtime
        if not runtime.jarvis:
            raise HTTPException(503, "Agent Jarvis non initialisé ou indisponible")
        data = await limited_json(request, 32768)
        payload = validate_payload(JarvisQueryPayload, data)
        raw_prompt = payload.prompt.strip()

        # 0. Réparation Phonétique & Correction STT en amont
        clean_prompt, _ = stt_phonetic_repair.repair_stt_transcript(raw_prompt)
        effective_prompt = clean_prompt or raw_prompt

        session_id = getattr(getattr(runtime, "jarvis", None), "active_session_id", "session_voice")

        # 1. Intercepteur local 0ms pour requêtes système, statut, météo ou identité
        instant_reply = None
        try:
            mem = getattr(runtime.jarvis, "memory", None)
            dev = request.headers.get("x-client-device") or ""
            instant_reply = handle_instant_local_command(effective_prompt, memory=mem, client_device=dev)
        except Exception:
            instant_reply = None

        if instant_reply:
            try:
                hud_mood = hud_state_manager.deduce_mood_from_text(instant_reply, effective_prompt)
                hud_state_manager.set_hud_mood(hud_mood)
                hud_state_manager.set_hud_voice_status(
                    "speaking",
                    user_transcript=effective_prompt,
                    assistant_reply=instant_reply,
                )
            except Exception:
                pass
            if runtime.jarvis and getattr(runtime.jarvis, "memory", None):
                try:
                    runtime.jarvis.memory.add_message(session_id=session_id, role="user", content=effective_prompt)
                    runtime.jarvis.memory.add_message(session_id=session_id, role="assistant", content=instant_reply)
                    if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                        runtime.jarvis.memory.record_conversation_turn_for_profile(effective_prompt, instant_reply)
                except Exception:
                    pass
            return JSONResponse(
                {
                    "status": "success",
                    "reply": instant_reply,
                    "thought_chain": ["Commande locale instantanée sans LLM (0ms)"],
                    "tools_used": [],
                    "timestamp": collector.utc_now(),
                }
            )

        # 2. Bibliothèque autonome (actions de navigation HUD, mode bureau, musique, git, etc.)
        is_devops_service = any(w in effective_prompt.lower() for w in ("service", "services", "docker", "vps", "systemctl", "redemarre", "redémarre", "journalctl"))
        auto_res = {"handled": False}
        if not is_devops_service:
            try:
                db_path = getattr(getattr(runtime, "jarvis", None), "db_path", None)
                auto_res = autonomous_library.execute_autonomous_task(
                    effective_prompt,
                    session_id=session_id,
                    db_path=db_path,
                )
            except Exception as auto_exc:
                LOGGER.warning("Erreur autonomous_library dans jarvis_query : %s", auto_exc)
                auto_res = {"handled": False}

        if auto_res.get("handled") is True:
            reply_text = str(auto_res.get("reply") or "").strip()
            cat = auto_res.get("task_category") or "locale"
            try:
                hud_mood = hud_state_manager.deduce_mood_from_text(reply_text, effective_prompt)
                hud_state_manager.set_hud_mood(hud_mood)
                hud_state_manager.set_hud_voice_status(
                    "speaking",
                    user_transcript=effective_prompt,
                    assistant_reply=reply_text,
                )
            except Exception:
                pass
            if runtime.jarvis and getattr(runtime.jarvis, "memory", None):
                try:
                    runtime.jarvis.memory.add_message(session_id=session_id, role="user", content=effective_prompt)
                    runtime.jarvis.memory.add_message(session_id=session_id, role="assistant", content=reply_text)
                    if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                        runtime.jarvis.memory.record_conversation_turn_for_profile(effective_prompt, reply_text)
                except Exception:
                    pass
            return JSONResponse(
                {
                    "status": "success",
                    "reply": reply_text,
                    "action": auto_res.get("action"),
                    "url": auto_res.get("url"),
                    "title": auto_res.get("title"),
                    "tools_used": auto_res.get("tools_used", []),
                    "thought_chain": [f"Action locale autonome exécutée ({cat}) (0ms)"],
                    "timestamp": collector.utc_now(),
                }
            )

        # 3. Traitement sémantique unifié : Moteur vocal rapide Groq LPU avec repli Gemini
        if (is_devops_service or is_system_command(effective_prompt)) and hasattr(runtime.jarvis, "execute_prompt_async"):
            try:
                result = await runtime.jarvis.execute_prompt_async(
                    user_input=effective_prompt,
                    source=payload.source,
                )
                rep = result.get("reply") or result.get("response") or ""
                if rep:
                    try:
                        hud_mood = hud_state_manager.deduce_mood_from_text(rep, effective_prompt)
                        hud_state_manager.set_hud_mood(hud_mood)
                        hud_state_manager.set_hud_voice_status("speaking", user_transcript=effective_prompt, assistant_reply=rep)
                    except Exception:
                        pass
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

        # Cœur Sémantique Unifié (< 500 ms) pour toutes les requêtes conversationnelles et interactives
        try:
            semantic_res = await asyncio.to_thread(
                execute_voice_semantic_turn,
                effective_prompt,
                runtime,
                session_id,
                None,
                VOICE_FAST_SYSTEM_PROMPT,
            )
            rep = semantic_res.get("reply") or semantic_res.get("response") or "Je suis là avec toi Moctar."
            try:
                hud_mood = hud_state_manager.deduce_mood_from_text(rep, effective_prompt)
                hud_state_manager.set_hud_mood(hud_mood)
                hud_state_manager.set_hud_voice_status("speaking", user_transcript=effective_prompt, assistant_reply=rep)
            except Exception:
                pass
            return JSONResponse({
                "status": "success",
                "reply": rep,
                "action": semantic_res.get("action"),
                "url": semantic_res.get("url"),
                "title": semantic_res.get("title"),
                "tools_used": semantic_res.get("tools_used", []),
                "thought_chain": semantic_res.get("thought_chain", ["Cœur sémantique unifié Groq/Gemini"]),
                "timestamp": collector.utc_now(),
            })
        except Exception as sem_exc:
            LOGGER.warning("Erreur semantic turn dans jarvis_query : %s, bascule sur agent standard", sem_exc)
            try:
                result = await runtime.jarvis.execute_prompt_async(
                    user_input=effective_prompt,
                    source=payload.source,
                )
                return JSONResponse(result)
            except Exception as exc:
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
    async def jarvis_clear_history(request: Request) -> JSONResponse:
        require_local_or_admin(request, settings)
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
        require_local_or_admin(request, settings)
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
    async def scheduler_toggle_task(task_id: int, request: Request) -> JSONResponse:
        require_local_or_admin(request, settings)
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
    async def scheduler_confirm_task(task_id: int, request: Request) -> JSONResponse:
        require_local_or_admin(request, settings)
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
    async def scheduler_delete_task(task_id: int, request: Request) -> JSONResponse:
        require_local_or_admin(request, settings)
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

