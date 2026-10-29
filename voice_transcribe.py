#!/usr/bin/env python3
"""Diagnostic et transcription hors FastAPI, sans téléchargement implicite.

--check importe réellement faster-whisper dans ce worker jetable, mais ne charge
jamais les poids. Seule la transcription charge le modèle local CPU/int8.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import time
import wave
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import numpy as np


logger = logging.getLogger(__name__)
APP_DIR = Path(os.getenv("JARVIS_APP_ROOT", Path(__file__).resolve().parent)).resolve()
APP_DIRS = (APP_DIR, Path("/opt/noc-dashboard"))
MODEL_NAMES = ("base", "tiny")


def _dependency() -> tuple[Any, bool]:
    # Limiter les pools natifs AVANT l'import de CTranslate2/Numpy dans le worker.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "2"
    package = importlib.import_module("faster_whisper")
    factory = getattr(package, "WhisperModel", None)
    if not callable(factory):
        raise ImportError("WhisperModel indisponible")
    try:
        utility = importlib.import_module("faster_whisper.utils")
        can_download = callable(getattr(utility, "download_model", None))
    except (ImportError, OSError, RuntimeError):
        can_download = False
    return factory, can_download


def _cache_roots() -> Iterator[Path]:
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(name):
            yield Path(os.environ[name]).expanduser()
    if os.environ.get("HF_HOME"):
        yield Path(os.environ["HF_HOME"]).expanduser() / "hub"
    if os.environ.get("XDG_CACHE_HOME"):
        yield Path(os.environ["XDG_CACHE_HOME"]).expanduser() / "huggingface" / "hub"
    for d in (APP_DIR, Path('/opt/noc-dashboard')):
        yield d / "models" / ".download-cache" / "hub"
        yield d / "models"
    yield Path.home() / ".cache" / "huggingface" / "hub"
    yield Path("/home/moctar/.cache/huggingface/hub")


def _snapshots(repository: Path) -> Iterator[Path]:
    """Un nombre borné de révisions présentes; aucun appel Hugging Face."""
    try:
        with (repository / "refs" / "main").open("r", encoding="ascii") as reference:
            revision = reference.read(129).strip()
        if re.fullmatch(r"[a-fA-F0-9]{7,64}", revision):
            yield repository / "snapshots" / revision
    except (OSError, UnicodeError):
        pass
    try:
        with os.scandir(repository / "snapshots") as entries:
            for index, entry in enumerate(entries):
                if index >= 32:
                    break
                if re.fullmatch(r"[a-fA-F0-9]{7,64}", entry.name) and entry.is_dir():
                    yield Path(entry.path)
    except OSError:
        pass


def _candidates(requested: str) -> Iterator[tuple[Path, str]]:
    configured = os.environ.get("NOC_WHISPER_MODEL_PATH", "").strip()
    if configured:
        yield Path(configured).expanduser(), requested
    names = (requested, *(name for name in MODEL_NAMES if name != requested))
    for d in (APP_DIR, Path('/opt/noc-dashboard')):
        yield d / "models" / "whisper", requested
        for name in names:
            yield d / "models" / ("whisper-" + name), name
    yield Path.cwd() / "models" / "whisper", requested
    for name in names:
        yield Path.cwd() / "models" / ("whisper-" + name), name
        yield Path.home() / ".cache" / "whisper" / name, name
    for root in _cache_roots():
        for name in names:
            for publisher in ("Systran", "guillaumekln"):
                yield from ((snapshot, name) for snapshot in
                            _snapshots(root / f"models--{publisher}--faster-whisper-{name}"))


def _readable_asset(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        # os.access seul n'est pas une preuve que le service peut ouvrir le fichier.
        with path.open("rb") as asset:
            return bool(asset.read(1))
    except OSError:
        return False


def _local_model(path: Path, hint: str) -> tuple[Path, str] | None:
    try:
        resolved = path.resolve()
        if not resolved.is_dir():
            return None
        if not all(_readable_asset(resolved / name) for name in ("model.bin", "config.json", "tokenizer.json")):
            return None
        if not any(_readable_asset(resolved / name) for name in ("vocabulary.txt", "vocabulary.json")):
            return None
        if (resolved / "config.json").stat().st_size > 65536:
            return None
        config = json.loads((resolved / "config.json").read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return None
        actual = hint
        marker = resolved / ".noc-whisper-model.json"
        if marker.is_file():
            if marker.stat().st_size > 1024:
                return None
            declaration = json.loads(marker.read_text(encoding="utf-8"))
            actual = declaration.get("model") if isinstance(declaration, dict) else None
            if actual not in MODEL_NAMES:
                return None
        else:
            match = re.search(r"(?:^|-)whisper-(tiny|base)$", resolved.name)
            if match:
                actual = match[1]
            heads = config.get("alignment_heads")
            if isinstance(heads, list) and heads:
                layers = [head[0] for head in heads if isinstance(head, list) and len(head) == 2
                          and type(head[0]) is int and head[0] >= 0]
                if layers:
                    maximum = max(layers)
                    if maximum >= 6:
                        return None
                    actual = "tiny" if maximum <= 3 else "base"
        return resolved, actual
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def is_voice_ready() -> dict[str, Any]:
    """Préflight local: dépendance importable et modèle complet/lisible, sans inférence.

    download_available indique une capacité logicielle, pas un modèle installé
    ni un accès réseau garanti. À appeler via --check dans le Python vocal.
    """
    requested = os.environ.get("NOC_WHISPER_MODEL", "base").strip().lower()
    expected = os.environ.get("NOC_WHISPER_MODEL_PATH", "").strip() or str(APP_DIR / "models" / "whisper")
    result = {"status": "disabled", "ready": False,
              "model": requested if requested in MODEL_NAMES else "base",
              "model_path": str(Path(expected).expanduser().absolute()),
              "reason": "", "download_available": False}
    enabled = os.environ.get("NOC_VOICE_ENABLED", "true").strip().lower()
    if enabled in {"false", "0", "no", "off"}:
        result["reason"] = "Commande vocale désactivée par NOC_VOICE_ENABLED."
        return result
    if enabled not in {"true", "1", "yes", "on"}:
        result["reason"] = "NOC_VOICE_ENABLED doit être true ou false."
        return result
    if requested not in MODEL_NAMES:
        result["reason"] = "NOC_WHISPER_MODEL doit être tiny ou base, multilingue."
        return result
    try:
        _, result["download_available"] = _dependency()
    except Exception:
        result["reason"] = "faster-whisper absent ou inutilisable dans le Python vocal; lancer install-voice.sh."
        return result
    seen: set[str] = set()
    for path, hint in _candidates(requested):
        key = str(path.absolute())
        if key in seen:
            continue
        seen.add(key)
        found = _local_model(path, hint)
        if found:
            resolved, actual = found
            result.update(status="ready", ready=True, model=actual, model_path=str(resolved),
                          reason="Modèle local lisible et faster-whisper disponible.")
            return result
    result.update(status="missing_model",
                  reason="Modèle local absent, incomplet ou non lisible par l'utilisateur du service; lancer install-voice.sh.")
    return result


def read_wav_pcm(path: str | os.PathLike[str]) -> np.ndarray | None:
    """Read mono 16-bit PCM WAV directly, avoiding PyAV's decoder path."""
    import numpy as np

    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                return None
            frames = wf.readframes(wf.getnframes())
            return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None


HALLUCINATION_SUBSTRINGS = (
    "sous-titres réalisés par",
    "sous-titres par",
    "sous-titrage réalisé",
    "sous-titrage st",
    "sous-titrage :",
    "sous-titres :",
    "radio-canada",
    "société radio-canada",
    "societe radio-canada",
    "amara.org",
    "merci d'avoir regardé",
    "merci d avoir regarde",
    "merci de regarder cette vidéo",
    "regardé cette vidéo",
    "regarde cette video",
    "merci d'avoir écouté",
    "merci d'avoir suivi",
    "merci d avoir suivi",
    "merci d'avoir visionné",
    "merci d avoir visionne",
    "merci de votre attention",
    "merci pour votre attention",
    "bon visionnage",
    "à bientôt pour une nouvelle vidéo",
    "a bientot pour une nouvelle video",
    "au revoir youtube",
    "abonnez-vous",
    "abonnez vous",
    "abonnez-vous à la chaîne",
    "abonnez vous a la chaine",
    "laissez un pouce bleu",
    "activez la cloche",
    "likez et partagez",
    "n'oubliez pas de vous abonner",
    "n'hésitez pas à vous abonner",
    "n hesitez pas a vous abonner",
    "traduit par",
    "transcription par",
    "subtitles by",
    "thank you for watching",
    "thanks for watching",
    "is that you",
    "thank you",
    "thanks for",
    "are you there",
    "[musique]",
    "(musique)",
    "[applaudissements]",
    "[rires]",
    "[silence]",
    "réalisation, dégénération",
    "realisation, degeneration",
    "dégénération, pause",
    "degeneration, pause",
)

SHORT_CONFIRMATION_PHRASES = {
    "oui",
    "oui.",
    "oui !",
    "non",
    "non.",
    "ok",
    "ok.",
    "d'accord",
    "d accord",
    "valide",
    "confirme",
    "vas-y",
    "vas y",
    "merci",
    "merci.",
    "merci !",
    "au revoir",
    "au revoir.",
}

SINGLE_TOKEN_NOISE_PHRASES = {
    "m",
    "b",
    "c",
    "d",
    "f",
    "g",
    "h",
    "j",
    "k",
    "l",
    "n",
    "p",
    "q",
    "r",
    "s",
    "t",
    "v",
    "w",
    "x",
    "z",
    "hm",
    "mhm",
    "hmm",
    "uh",
    "um",
}

HALLUCINATION_EXACT_PHRASES = {
    "youtube",
    "merci",
    "merci.",
    "merci !",
    "au revoir",
    "au revoir.",
    "bye",
    "oui",
    "oui.",
    "oui !",
    "non",
    "non.",
    "is that you",
    "is that you?",
    "is that you.",
    "assistant vocal",
    "assistant vocal.",
    "thank you",
    "thank you.",
    "...",
    ".",
    "musique",
    "musique.",
    "[musique]",
    "(musique)",
    "applaudissements",
    "[applaudissements]",
    "bruit",
    "silence",
}


def has_repetitive_hallucination(text: str) -> bool:
    """Détecte les répétitions dégénérées de motifs (ex: 'c'est une vidéo' répété plus de 3 fois)."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    # Répétition d'un motif textuel de 3 à 50 caractères plus de 3 fois (au moins 4 occurrences consécutives)
    pattern = r"(\b.{3,50}?\b)(?:[\s,.:;!?…—–-]+(?:\1)){3,}"
    if re.search(pattern, cleaned, re.IGNORECASE):
        return True
    # Répétition de n-grams de mots plus de 3 fois (>= 4 occurrences)
    words = [w.lower() for w in re.split(r"[\s,.:;!?…—–-]+", cleaned) if w]
    if len(words) >= 8:
        for n in (1, 2, 3, 4, 5):
            ngrams = [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
            counts = Counter(ngrams)
            if any(cnt >= 4 for cnt in counts.values()):
                return True
    return False


def deduplicate_repetitive_text(text: str) -> str:
    """Réduit les répétitions consécutives de phrases ou segments identiques générées par Whisper."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    # Déduplication de segments répétés (ex: 'Salut, tu me reçois ? Salut, tu me reçois ? Tu me reçois ?' -> 'Salut, tu me reçois ?')
    segments = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if len(segments) > 1:
        seen = set()
        deduped_segments = []
        for seg in segments:
            seg_norm = seg.lower().strip(" ,.:;!?-")
            if seg_norm not in seen:
                seen.add(seg_norm)
                deduped_segments.append(seg)
        if deduped_segments:
            cleaned = " ".join(deduped_segments)

    # Déduplication de motifs consécutifs (ex: 'Tu me reçois ? Tu me reçois ?')
    pattern = r"(\b.{3,80}?\b)(?:[\s,.:;!?…—–-]+(?:\1))+"
    cleaned = re.sub(pattern, r"\1", cleaned, flags=re.IGNORECASE)
    # Déduplication de mots individuels consécutifs (ex: 'oui oui oui oui' -> 'oui')
    cleaned = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


# Prompt d'amorce d'élite et de biasing acoustique contextuel pour Moctar
MOCTAR_STT_INITIAL_PROMPT = (
    "Moctar, Jarvis, Klasora, Babamarket, Mercamarket, Heliantha, "
    "Noor Express, SupMTI, NeuroVision, Sentinel, Rabat, Salé, Niamey, "
    "Real Madrid, VS Code, FastAPI, Flutter."
)

PROMPT_PROPER_NOUNS = {
    "moctar", "jarvis", "klasora", "babamarket", "mercamarket",
    "heliantha", "noor", "express", "supmti", "neurovision",
    "sentinel", "rabat", "sale", "salé", "niamey", "real", "madrid",
    "vscode", "fastapi", "flutter",
}

ACTION_AND_CONVERSATIONAL_VERBS = re.compile(
    r"\b(?:ouvre|ouvrir|active|activer|lance|lancer|arr[eê]te|arr[eê]ter|ferme|fermer|mets|mettre|montre|montrer|"
    r"affiche|afficher|fais|faire|dis|dire|donne|donner|cherche|chercher|supprime|supprimer|relance|relancer|"
    r"aide|regarde|regarder|analyse|analyser|optimise|optimiser|peux|comment|pourquoi|combien|quand|est|sont|suis|es|va|vas)\b",
    re.IGNORECASE,
)


def filter_whisper_hallucinations(text: str, *, allow_short_confirmations: bool = False) -> str:
    """Élimine les hallucinations récurrentes de Whisper sur du silence ou du bruit de fond sans détruire la parole réelle."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""

    # En cas de boucle répétitive Whisper, dédupliquer pour conserver la phrase utile au lieu de l'anéantir
    if has_repetitive_hallucination(cleaned):
        cleaned = deduplicate_repetitive_text(cleaned)

    lowered = cleaned.lower().strip(" .,!?:;\"'()-[]{}…")
    if not lowered or lowered in SINGLE_TOKEN_NOISE_PHRASES:
        return ""

    tokens = [tok for tok in re.split(r"\W+", lowered) if tok]
    if not tokens:
        return ""

    # Protection Anti-Écho / Hallucination du prompt :
    # Si Whisper renvoie en silence une sous-suite de >= 3 noms propres du prompt sans verbe d'action, rejeter.
    matched_prompt_nouns = [tok for tok in tokens if tok in PROMPT_PROPER_NOUNS]
    if len(matched_prompt_nouns) >= 3 and not ACTION_AND_CONVERSATIONAL_VERBS.search(cleaned):
        logger.info(f"🚫 [WHISPER ANTI-ECHO]: Écho suspect du prompt détecté ({matched_prompt_nouns}) sans verbe d'action. Rejet.")
        return ""

    # Préservation impérative des demandes réelles contenant le nom de l'assistant ou un verbe d'action
    USER_INTENT_EXCEPTION_PATTERN = re.compile(
        r"\b(?:jarvis|moctar|salut|bonjour|coucou|hello|re[çc]ois|recois|entends|m['’]entends|vois|m['’]vois|"
        r"mets|mettre|ajoute|ajouter|active|activer|d[ée]sactive|desactiver|coupe|couper|ferme|fermer|arr[êe]te|arreter|quitte|quitter|"
        r"g[ée]n[ée]re|genere|fais|faire|traduis|traduire|peux[- ]tu|aide|affiche|montre|scanner|cam[ée]ra|camera|webcam|neurovision|fatigue)\b",
        re.IGNORECASE,
    )
    has_user_intent = bool(USER_INTENT_EXCEPTION_PATTERN.search(cleaned))

    if not has_user_intent:
        if lowered in HALLUCINATION_EXACT_PHRASES:
            if allow_short_confirmations and lowered in SHORT_CONFIRMATION_PHRASES:
                return cleaned
            return ""
        for pattern in HALLUCINATION_SUBSTRINGS:
            if pattern in lowered:
                return ""
        if re.search(r"\b(?:is\s+that\s+you|thank\s+you|thanks|how\s+are\s+you|what\s+is|you\s+there|are\s+you|can\s+you|hello\s+world|goodbye)\b", lowered):
            return ""

    # Rejeter uniquement les répétitions dégénérées d'un seul mot bruité ou symbole
    tokens = [tok for tok in re.split(r"\W+", lowered) if tok]
    if not tokens:
        return ""
    if len(tokens) >= 5 and len(set(tokens)) == 1:
        return ""
    return cleaned


def is_silent_pcm(samples: Any, rms_threshold: float = 0.002) -> bool:
    """Détecte un tableau PCM mono silencieux avant d'appeler Whisper."""
    try:
        import numpy as np

        if not isinstance(samples, np.ndarray):
            return False
        if samples.size == 0:
            return True
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))
        peak = float(np.max(np.abs(samples)))
        return rms < rms_threshold and peak < 0.01
    except Exception:
        return False


def load_audio(path: str | os.PathLike[str]) -> np.ndarray | str:
    """Prefer direct PCM decoding; leave other supported formats to faster-whisper."""
    samples = read_wav_pcm(path)
    return samples if samples is not None else str(path)


def build_dynamic_stt_prompt(
    active_window: dict[str, Any] | None = None,
    extra_terms: list[str] | None = None,
) -> str:
    """Génère un prompt de biasing acoustique dynamique (Zero-Shot Acoustic Biasing)
    pour amorcer le décodeur Whisper Large-v3 avec le vocabulaire opérationnel de Moctar,
    de l'environnement Dell Precision M4800 et des fenêtres/outils actifs.
    """
    base_anchors = [
        MOCTAR_STT_INITIAL_PROMPT,
        "Bonjour Moctar, ici M.O.C.T.A.R sur Dell Precision M4800. JARVIS.",
        "Tu me reçois ? Fort et clair, 5 sur 5, test micro, tu m'entends, tu es là, comment tu vas, la forme,",
        "qu'est-ce que tu peux faire, de quoi es-tu capable, c'est quoi le programme, aide-moi, dis-moi tout,",
        "Ouvre Visual Studio Code, VS Code, code, terminal, bash, Python, pytest, Git status, commit, push, pull,",
        "Baba Market, Noor Express, EduManage, mode HUD 3D, mode NOC, globe terrestre, localise-moi,",
        "Docker, Uvicorn, FastAPI, systemctl, NOC, supervision, météo, degrés, Niamey, Maroc,",
        "mode bureau, mode travail, ferme ça, relance-le, mets-le à droite, mets-le à gauche, plein écran, snap,",
        "WhatsApp, volume, musique, Coran.",
    ]

    context_biasing: list[str] = []

    # 1. Fenêtre active au premier plan (Dell M4800)
    win = active_window
    if win is None:
        try:
            import desktop_window_manager
            win = desktop_window_manager.get_active_window_info()
        except Exception:
            win = None

    if win and win.get("available"):
        app = str(win.get("app_name") or "").lower()
        title = str(win.get("window_title") or "")
        if "vscode" in app or "code" in app:
            context_biasing.append("Éditeur VS Code : fichier, fonction, terminal intégré, débogage, sauvegarder, extension.")
        elif "terminal" in app or "bash" in app:
            context_biasing.append("Terminal bash : script, sudo, journalctl, htop, curl, grep, apt, processus.")
        elif any(b in app for b in ("chrome", "firefox", "browser")):
            context_biasing.append("Navigateur Web : onglet, recherche, page, YouTube, lien, actualiser.")

        clean_title_tokens = [
            t for t in re.findall(r"[\w.-]+", title)
            if len(t) > 3 and not t.isdigit() and t.lower() not in ("precision", "dell")
        ]
        if clean_title_tokens:
            context_biasing.append(" ".join(clean_title_tokens[:6]) + ".")

    if extra_terms:
        context_biasing.append(" ".join(extra_terms) + ".")

    full_prompt = " ".join(base_anchors + context_biasing)
    if len(full_prompt) > 850:
        full_prompt = full_prompt[:850].rsplit(" ", 1)[0] + "."
    return full_prompt


def transcribe_groq_api(
    audio_path: str | os.PathLike[str],
    api_key: str | None = None,
    prompt: str | None = None,
) -> str | None:
    """Transcrit l'audio via l'API Groq Whisper Large-v3 (< 150ms) si GROQ_API_KEY est configurée."""
    key = (api_key or os.environ.get("GROQ_API_KEY", "") or os.environ.get("NOC_GROQ_API_KEY", "")).strip()
    if not key:
        return None

    audio = Path(audio_path)
    if not audio.is_file() or audio.stat().st_size == 0:
        return None

    start_time = time.perf_counter()
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    model = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3")

    prompt_fr = prompt if prompt is not None else build_dynamic_stt_prompt()

    # Essai 1 : SDK groq si présent
    try:
        import importlib
        groq_mod = importlib.import_module("groq")
        Groq = getattr(groq_mod, "Groq")

        client = Groq(api_key=key, timeout=8.0, max_retries=0)
        with audio.open("rb") as f:
            resp = client.audio.transcriptions.create(
                file=(audio.name, f.read()),
                model=model,
                language="fr",
                temperature=0.0,
                prompt=prompt_fr,
                response_format="json",
            )
        text = str(getattr(resp, "text", "") or "").strip()
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.info(f"⚡ [GROQ WHISPER LARGE-V3] ({duration_ms}ms): '{text}'")
        return text
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Groq SDK transcription a échoué (%s), tentative via HTTP direct", exc)

    # Essai 2 : requêtes HTTP directes via requests ou httpx
    suffix = audio.suffix.lower()
    mime = "audio/wav" if suffix == ".wav" else "audio/webm" if suffix == ".webm" else "audio/mpeg" if suffix == ".mp3" else "application/octet-stream"
    headers = {"Authorization": f"Bearer {key}"}
    data = {
        "model": model,
        "language": "fr",
        "temperature": "0.0",
        "prompt": prompt_fr,
        "response_format": "json",
    }

    try:
        import requests

        with audio.open("rb") as f:
            files = {"file": (audio.name, f, mime)}
            res = requests.post(url, headers=headers, files=files, data=data, timeout=12.0)
        if res.status_code == 200:
            text = str(res.json().get("text") or "").strip()
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info(f"⚡ [GROQ WHISPER LARGE-V3] ({duration_ms}ms): '{text}'")
            return text
        else:
            logger.warning("Groq API HTTP %s: %s", res.status_code, res.text[:200])
            return None
    except Exception as exc:
        logger.warning("Groq API transcription indisponible (%s), repli sur faster-whisper local", exc)
        return None


def transcribe_gemini_api(audio_path: str | os.PathLike[str]) -> str | None:
    """Fallback neural de transcription via Gemini Multimodal si faster-whisper local est indisponible."""
    start_time = time.perf_counter()
    try:
        import config
        from google import genai
        from google.genai import types as genai_types

        key = getattr(config, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        if not key or not genai:
            return None
        p = Path(audio_path)
        if not p.is_file() or p.stat().st_size == 0:
            return None
        client = genai.Client(api_key=key)
        part = genai_types.Part.from_bytes(data=p.read_bytes(), mime_type="audio/wav")
        model = getattr(config, "gemini_model", "gemini-2.0-flash") or "gemini-2.0-flash"
        resp = client.models.generate_content(
            model=model,
            contents=[
                part,
                "Transcris fidèlement et mot pour mot en français ce qui est prononcé dans cet audio. "
                "Ne réponds que par la transcription exacte, sans guillemets ni commentaire.",
            ],
        )
        text = (resp.text or "").strip()
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.info(f"⚡ [GEMINI AUDIO STT FALLBACK] ({duration_ms}ms): '{text}'")
        return text
    except Exception as exc:
        logger.debug("Gemini audio fallback indisponible : %s", exc)
        return None


def transcribe_audio(
    audio_path: str | os.PathLike[str],
    *,
    allow_empty: bool = False,
    allow_short_confirmations: bool = False,
    groq_api_key: str | None = None,
    groq_prompt: str | None = None,
) -> str:
    """Transcrire l'audio fourni via Groq Whisper Large-v3 (< 150ms) ou repli sur faster-whisper local."""
    audio = Path(audio_path)
    if not _readable_asset(audio):
        raise ValueError("Audio absent, vide ou non lisible.")
    loaded_audio = load_audio(audio)
    if is_silent_pcm(loaded_audio):
        return ""

    # 1. Tentative ultra-rapide via Groq API si GROQ_API_KEY est configurée
    if groq_prompt is not None:
        groq_text = transcribe_groq_api(audio, api_key=groq_api_key, prompt=groq_prompt)
    else:
        groq_text = transcribe_groq_api(audio, api_key=groq_api_key)
    if groq_text is not None:
        filtered = filter_whisper_hallucinations(groq_text, allow_short_confirmations=allow_short_confirmations)
        if groq_text.strip() and not filtered:
            if allow_empty:
                return ""
            raise ValueError("Aucune parole reconnue.")
        return filtered

    # 2. Repli automatique sur faster-whisper local avec garde-fous stricts
    readiness = is_voice_ready()
    if not readiness["ready"]:
        gemini_text = transcribe_gemini_api(audio)
        if gemini_text:
            filtered = filter_whisper_hallucinations(gemini_text, allow_short_confirmations=allow_short_confirmations)
            if filtered:
                return filtered
        raise RuntimeError(readiness["reason"])

    try:
        factory, _ = _dependency()
        model = factory(readiness["model_path"], device="cpu", compute_type="int8",
                        cpu_threads=2, num_workers=1, local_files_only=True)
        segments, _ = model.transcribe(
            loaded_audio,
            language="fr",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            vad_filter=True,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.2,
            no_speech_threshold=0.6,
            initial_prompt=build_dynamic_stt_prompt(),
        )
        pieces: list[str] = []
        size = 0
        filtered_noise_or_hallucination = False
        for segment in segments:
            no_speech_prob = getattr(segment, "no_speech_prob", None)
            if isinstance(no_speech_prob, (int, float)) and float(no_speech_prob) >= 0.6:
                filtered_noise_or_hallucination = True
                continue
            piece = segment.text.strip()
            if not piece:
                continue
            size += len(piece) + (1 if pieces else 0)
            if size > 1000:
                raise ValueError("Transcription trop longue.")
            pieces.append(piece)
        raw_text = " ".join(pieces).strip()
        text = raw_text
        logger.info(f"🎙️ [WHISPER BRUT]: '{text}'")
        text = filter_whisper_hallucinations(raw_text, allow_short_confirmations=allow_short_confirmations)
        if raw_text and not text:
            filtered_noise_or_hallucination = True
        if not text:
            if allow_empty or filtered_noise_or_hallucination:
                return ""
            raise ValueError("Aucune parole reconnue.")
        return text
    except Exception as exc:
        gemini_text = transcribe_gemini_api(audio)
        if gemini_text:
            filtered = filter_whisper_hallucinations(gemini_text, allow_short_confirmations=allow_short_confirmations)
            if filtered:
                return filtered
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic/transcription NOC hors processus")
    parser.add_argument("--check", action="store_true", help="Diagnostic JSON sans poids ni téléchargement")
    parser.add_argument("--model", help="Chemin local préféré, avec recherche de secours")
    parser.add_argument("--model-name", choices=MODEL_NAMES, help="tiny/base; sinon NOC_WHISPER_MODEL")
    parser.add_argument("--audio", help="WAV mono 16 kHz préparé par le NOC")
    args = parser.parse_args()
    if args.model:
        os.environ["NOC_WHISPER_MODEL_PATH"] = args.model
    if args.model_name:
        os.environ["NOC_WHISPER_MODEL"] = args.model_name
    if args.check:
        print(json.dumps(is_voice_ready(), ensure_ascii=True))
        return 0
    if not args.audio:
        parser.error("--audio est obligatoire hors --check")
    try:
        print(json.dumps({"text": transcribe_audio(args.audio)}, ensure_ascii=True))
        return 0
    except Exception as exc:
        # Le worker est jetable: rendre le vrai motif exploitable dans son JSON.
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
