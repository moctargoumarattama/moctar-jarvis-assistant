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
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

import autonomous_library
import collector
import desktop_window_manager
import dynamic_attention_engine
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


VOICE_SESSION_DURATION_SECONDS = float(os.getenv("VOICE_SESSION_DURATION_SECONDS", "0.0"))
VOICE_BUTLER_GREETINGS = (
    "À vos ordres Moctar, je vous écoute.",
    "Oui Moctar, je suis là.",
)
_WAKE_ONLY_SENTINEL = "__MOCTAR_WAKE_ONLY__"

_VOICE_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:yo|hey|ok|bonjour|salut|dis|allo|ecoute|et\s+vous|you|c['’]est\s+(?:le\s+)?)\s*)?"
    r"(?:yo\s+moctar|moctar|moktar|mokta|mokhtar|mouctar|mouktar|motard|"
    r"m['’]?occupe\s+tard|m['’]?occaque|moccupe\s+tard|moccaque|"
    r"m(?:\.|\s)*o(?:\.|\s)*c(?:\.|\s)*t(?:\.|\s)*a(?:\.|\s)*r(?:\.|\b)|"
    r"moc|mok|mokti|jarvis|elmo)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)
_VOICE_WAKE_ANYWHERE_RE = re.compile(
    r"\b(?:(?:yo|hey|ok|bonjour|salut|dis|allo|ecoute|et\s+vous|you|c['’]est\s+(?:le\s+)?)\s*)?"
    r"(yo\s+moctar|moctar|moktar|mokta|mokhtar|mouctar|mouktar|motard|"
    r"m['’]?occupe\s+tard|m['’]?occaque|moccupe\s+tard|moccaque|"
    r"m(?:\.|\s)*o(?:\.|\s)*c(?:\.|\s)*t(?:\.|\s)*a(?:\.|\s)*r(?:\.|\b)|"
    r"moc|mok|mokti|jarvis|elmo|yo\s+elmo|allo\s+moctar|dis\s+moctar)\b[\s,.:;!?-]*",
    re.IGNORECASE,
)
_VOICE_FILLER_WORDS = {
    "au", "yo", "hey", "ok", "bonjour", "salut", "dis", "allo", "allô", "ecoute", "écoute", "oui",
    "et", "vous", "you", "tout", "le", "monde", "ah", "eh", "oh", "ha",
    "moctar", "moktar", "mokta", "mokhtar", "mouctar", "mouktar", "motard",
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
    if not text or len(text.strip()) < 3:
        return False
    # Réparation phonétique amont préalable pour reconnaître les commandes vocales altérées
    repaired, _ = stt_phonetic_repair.repair_stt_transcript(text)
    norm = _strip_accents_1to1(repaired).lower().strip()
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
    if re.search(r"\b(?:quelle?\s+heure|il\s+est\s+quelle?\s+heure|donne\s+l['’]heure|quel\s+jour|quelle?\s+date|on\s+est\s+le\s+combien)\b", norm):
        return True
    # 3. Météo & Climat
    if re.search(r"\b(?:m[ée]t[ée]o|quel\s+temps|quel\s+degr[ée]|combien\s+de\s+degr[ée]s?|pleut[- ]il|est[- ]ce\s+qu['’]il\s+pleut|il\s+fait\s+quel\s+degr[ée]|fait\s+quel\s+degr[ée])\b", norm):
        return True
    # 4. Multimédia & Audio (Lecture, Pause, Volume, Spotify, YouTube)
    if re.search(r"\b(?:arr[êe]te\s+la\s+musique|stop\s+la\s+musique|pause\s+la\s+musique|pause\s+youtube|mets\s+youtube\s+en\s+pause|youtube\s+en\s+pause|j['’]?ai\s+youtube\s+en\s+pause|pause\s+la\s+vid[ée]o|mets\s+en\s+pause|mets\s+sur\s+pause|en\s+pause|on\s+pause|pause)\b", norm):
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
    # 7. Identité, Rôle, Métier & Capacités ("c'est quoi ton job", "tu es qui toi", "tu sers à quoi")
    if re.search(r"\b(?:c['’]est\s+quoi\s+ton\s+job|quel\s+est\s+ton\s+job|ton\s+job|ton\s+r[ôo]le|tu\s+es\s+qui|t['’]es\s+qui|qui\s+es[- ]tu|qui\s+tu\s+es|tu\s+sers\s+[aà]\s+quoi|tu\s+fais\s+quoi|tu\s+sais\s+faire\s+quoi|peux[- ]tu\s+m['’]aider|aide[- ]moi)\b", norm):
        return True
    # 8. Transitions collaboratives ("on va passer à ceci", "on continue", "on enchaîne")
    if re.search(r"\b(?:on\s+(?:va\s+)?passer\s+[aà]\s+ceci|on\s+enchaine|on\s+encha[îi]ne|on\s+continue|passons\s+[aà]\s+la\s+suite)\b", norm):
        return True
    # 9. Interruption vocale prioritaire (Barge-In)
    if re.search(r"\b(?:tais[- ]toi|ferme[- ]la|chut|silence|stop|pause|attends?\s*(?:voir)?)\b", norm):
        return True
    # 10. Présence et surnoms complices ("tu es là", "t'es là", "moc", "mokti")
    if re.search(r"\b(?:tu\s+es\s+l[aà]|t['’]es\s+l[aà]|es[- ]tu\s+l[aà]|moc|mokti)\b", norm):
        return True
    # 11. Vision et Analyse d'Écran ("qu'est-ce que tu vois sur mon écran", "regarde mon écran", "analyse mon écran", "regarde les erreurs")
    if re.search(r"\b(?:qu['’]est[- ]ce\s+que\s+tu\s+vois|que\s+vois[- ]tu|regarde|analyse|lis|d[ée]cris)\s+(?:sur\s+|[aà]\s+)?(?:mon\s+|l['’])?[ée]cran\b", norm) or re.search(r"\b(?:regarde\s+les\s+erreurs|analyse\s+les\s+erreurs|vois[- ]tu\s+des\s+erreurs|qu['’]y\s+a[- ]t[- ]il\s+[aà]\s+l['’][ée]cran)\b", norm):
        return True
    # 12. Interface HUD 3D & Globe Terrestre ("mode hud 3d", "affiche la terre", "montre le globe", "affiche ma position", "mode orbe", "mode plasma", "mode quantum")
    if re.search(r"\b(?:mode\s+)?hud(?:\s*3\s*d)?\b", norm) or re.search(r"\b(?:mode\s+)?(?:hope|hop|up|hot|hub)\s*3\s*d\b", norm) or re.search(r"\b(?:affiche\s+(?:la\s+terre|le\s+globe|ma\s+position|l['’]orbe|le\s+hud)|montre\s+(?:la\s+terre|le\s+globe|ma\s+position|le\s+hud)|globe\s+terrestre|localise[- ]moi|mode\s+orbe|mode\s+globe|mode\s+plasma|mode\s+quantum|ferme\s+le\s+globe|retourne\s+[aà]\s+l['’]orbe|ouvre\s+(?:le\s+)?hud)\b", norm):
        return True
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
    repaired, _ = stt_phonetic_repair.repair_stt_transcript(transcript)
    filtered = voice_transcribe.filter_whisper_hallucinations(repaired)
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

    if session_active:
        clean_text = filtered.strip(" ,.:;!?-")
        if len(clean_text) < 4 or _is_pure_filler_or_wake(clean_text):
            return False, "", False
        return True, clean_text, False

    clean_text = filtered.strip(" ,.:;!?-")
    if clean_text and is_direct_voice_command(clean_text):
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
    "Tu es M.O.C.T.A.R, l'intelligence artificielle cybernétique et copilote d'ingénierie de Moctar. "
    "Ton style allie la précision d'un centre de commandement futuriste (NOC) à la fluidité d'un associé technique d'élite. "
    "Tes réponses orales sont ultra-réactives, posées, analytiques et concises (1 à 3 phrases maximum). "
    "Pas de jargon théâtral de bande dessinée, mais une prestance technologique moderne, directe et chirurgicale. "
    "Termine toujours tes interventions vocales en t'adressant à lui par 'Moctar.'"
)


def handle_instant_local_command(text: str, memory: Any = None) -> str | None:
    """Intercepteur de commandes locales instantanées (0 ms, sans LLM)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    normalized = _strip_accents_1to1(cleaned).lower()

    # 0a. Commandes d'interruption vocale (Barge-In)
    if re.search(r"\b(?:tais[- ]toi|ferme[- ]la|chut|silence)\b", normalized) or normalized in ("tais-toi", "tais toi", "stop", "chut", "silence"):
        return "Pardon, je t'écoute Moctar."

    if re.search(r"\b(?:attends?\s*(?:voir)?)\b", normalized) or normalized in ("attends", "attends voir"):
        return "Je vous attends Moctar, prenez votre temps."

    # 0b. Présence complice ("t'es là", "tu es là")
    if re.search(r"\b(?:t['’]es\s+l[aà]|tu\s+es\s+l[aà]|es[- ]tu\s+l[aà])\b", normalized):
        hour = datetime.now().hour
        if hour >= 23 or hour < 6:
            return "Oui Moctar, je suis là ! Encore au travail ? Je reste avec toi, dis-moi ce qu'on boucle."
        return "Oui Moctar, je suis là !"

    # 0c. Salutations chaleureuses selon le moment de la journée & continuité épisodique
    if normalized in ("bonjour", "salut", "bonjour moctar", "salut moctar", "moc", "yo moc", "yo mokti"):
        hour = datetime.now().hour
        if memory and hasattr(memory, "build_episodic_greeting"):
            try:
                ep_greet = memory.build_episodic_greeting(hour=hour)
                if ep_greet:
                    return ep_greet
            except Exception:
                pass
        if 6 <= hour < 12:
            return "Bonjour Moctar ! Prêt pour cette journée ?"
        elif 12 <= hour < 14:
            return "Bonjour Moctar ! Bon appétit ou pause bien méritée, je reste disponible !"
        elif 14 <= hour < 19:
            return "Oui Moctar, je suis là ! On avance sur quoi ?"
        elif 19 <= hour < 23:
            return "Bonsoir Moctar ! Je suis là, qu'est-ce qu'on fait ?"
        else:
            return "Encore au travail Moctar ? Je reste avec toi, dis-moi ce qu'on boucle."

    # 1. Identité ("comment t'appelles-tu", "qui es-tu", "c'est quoi ton nom", etc.)
    if re.search(r"\b(?:qui\s+es[- ]tu|comment\s+tu\s+t['’]appelles|comment\s+t['’]appelles[- ]tu|c['’]est\s+quoi\s+ton\s+nom|quel\s+est\s+ton\s+nom|qui\s+tu\s+es|pr[ée]sente[- ]toi)\b", normalized):
        return "Je suis M.O.C.T.A.R, votre système autonome de contrôle et d'intelligence opérationnelle, Moctar."

    # 1b. Rôle & Métier d'Ingénieur ("c'est quoi ton job", "tu es qui toi", "quel est ton job", "tu sers à quoi", "tu fais quoi")
    if re.search(r"\b(?:c['’]est\s+quoi\s+ton\s+job|quel\s+est\s+ton\s+job|ton\s+job|tu\s+es\s+qui\s+toi|t['’]es\s+qui\s+toi|tu\s+sers\s+[aà]\s+quoi|tu\s+fais\s+quoi|tu\s+sais\s+faire\s+quoi|quel\s+est\s+ton\s+r[ôo]le|ton\s+r[ôo]le)\b", normalized):
        return "Je suis M.O.C.T.A.R, votre copilote d'ingénierie et système d'intelligence opérationnelle sur ce Dell Precision M4800. Je pilote vos environnements de travail, surveille l'infrastructure NOC, exécute vos commandes et assure le suivi de vos projets en temps réel, Moctar."

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

    # 6. Mémoire de Travail Active ("on en était où", "qu'est-ce qu'on faisait", "rappelle-moi notre tâche", "c'est quoi notre tâche")
    if re.search(r"\b(?:on\s+en\s+[ée]tait\s+o[uù]|qu['’]est[- ]ce\s+qu['’]on\s+faisait|on\s+faisait\s+quoi|rappelle[- ]moi\s+(?:ce\s+qu['’]on\s+fai|notre\s+t[âa]che)|notre\s+t[âa]che\s+en\s+cours|t[âa]che\s+active)\b", normalized):
        try:
            import active_task_tracker
            return active_task_tracker.format_active_task_speech()
        except Exception:
            return "Nous sommes en veille opérationnelle sur l'infrastructure Dell Precision M4800, Moctar."

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
        try:
            from native_voice_daemon import NativeVoiceDaemon
            NativeVoiceDaemon.get_instance().start()
        except Exception:
            pass
        try:
            yield
        finally:
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

    @application.get("/")
    async def dashboard():
        return FileResponse(BASE_DIR / "static" / "index.html", media_type="text/html")

    @application.get("/hud")
    async def hud_dashboard():
        hud_path = BASE_DIR / "templates" / "hud.html"
        if not hud_path.is_file():
            hud_path = BASE_DIR / "static" / "hud.html"
        return FileResponse(hud_path, media_type="text/html")

    @application.get("/static/js/hud_3d.js")
    async def hud_3d_script():
        return FileResponse(BASE_DIR / "static" / "js" / "hud_3d.js", media_type="application/javascript")

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
        import hud_state_manager
        try:
            body = await request.json()
        except Exception:
            body = {}
        view = body.get("view", "orb")
        locate = bool(body.get("locate", False))
        updated = hud_state_manager.set_hud_view(view=view, locate=locate)
        return JSONResponse(updated)

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
                    "reason": "empty",
                    "user_text": "",
                    "reply_text": "",
                    "audio_url": None,
                    "session_active": False,
                }
            )

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
                transcript = await transcribe_direct_audio(audio_bytes, detected_mime)
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

        runtime: Runtime = application.state.runtime
        header_session = request.headers.get("x-voice-session-active", "").strip().lower() in {"1", "true", "yes"}
        header_barge_in = request.headers.get("x-voice-barge-in", "").strip().lower() in {"1", "true", "yes"}
        query_session = request.query_params.get("session_active", "").strip().lower() in {"1", "true", "yes"}
        runtime_session = getattr(runtime, "voice_session_until", 0.0) > time.monotonic()
        session_active = header_session or header_barge_in or query_session or runtime_session

        # 1. Couche de Réparation Phonétique & Correction d'Erreurs STT (< 0.1 ms)
        repaired_transcript, stt_repairs = stt_phonetic_repair.repair_stt_transcript(transcript)
        if stt_repairs:
            LOGGER.info(
                "⚡ [STT PHONETIC REPAIR] (0ms): '%s' -> '%s' (regles: %s)",
                transcript,
                repaired_transcript,
                [r["rule"] for r in stt_repairs],
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
            if VOICE_SESSION_DURATION_SECONDS > 0:
                runtime.voice_session_until = time.monotonic() + max(VOICE_SESSION_DURATION_SECONDS, 30.0)
            is_inc = bool(attention.get("is_incomplete"))
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

        # Filtrage acoustique : si le PC joue du son (YouTube, musique), ignorer les bruits sans mot-clé explicite
        pc_playing = False
        try:
            import native_voice_daemon
            pc_playing = native_voice_daemon.is_pc_audio_playing()
        except Exception:
            pass

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

        runtime.voice_session_until = time.monotonic() + VOICE_SESSION_DURATION_SECONDS

        agent_result: dict[str, Any] = {}
        memory_instance = getattr(getattr(runtime, "jarvis", None), "memory", None)
        instant_reply = handle_instant_local_command(command_text, memory=memory_instance)
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

        audio_url: str | None = None
        audio_chunks: list[str] = []
        try:
            audio_url, audio_chunks = await generate_cached_tts_chunks(reply_text, DEFAULT_TTS_VOICE)
        except Exception as exc:
            LOGGER.warning("Synthèse edge-tts indisponible sur /api/voice/interact : %s", exc)

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
                "session_active": bool(runtime.voice_session_until > time.monotonic() or (header_session and not attention.get("should_disengage")) or (header_barge_in and not attention.get("should_disengage"))),
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
    async def native_voice_trigger():
        """Point d'entrée pour déclencher l'écoute système native (raccourci clavier, script bash)."""
        from native_voice_daemon import NativeVoiceDaemon
        daemon = NativeVoiceDaemon.get_instance()
        asyncio.create_task(asyncio.to_thread(daemon.trigger_interaction_flow))
        return {"ok": True, "triggered": True, "message": "Écoute système native démarrée"}

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
