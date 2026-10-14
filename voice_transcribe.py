#!/usr/bin/env python3
"""Diagnostic et transcription hors FastAPI, sans téléchargement implicite.

--check importe réellement faster-whisper dans ce worker jetable, mais ne charge
jamais les poids. Seule la transcription charge le modèle local CPU/int8.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator


APP_DIR = Path("/opt/noc-dashboard")
MODEL_NAMES = ("tiny", "base")


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
    yield APP_DIR / "models" / ".download-cache" / "hub"
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
    yield APP_DIR / "models" / "whisper", requested
    names = (requested, *(name for name in MODEL_NAMES if name != requested))
    for name in names:
        yield APP_DIR / "models" / ("whisper-" + name), name
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
    requested = os.environ.get("NOC_WHISPER_MODEL", "tiny").strip().lower()
    expected = os.environ.get("NOC_WHISPER_MODEL_PATH", "").strip() or str(APP_DIR / "models" / "whisper")
    result = {"status": "disabled", "ready": False,
              "model": requested if requested in MODEL_NAMES else "tiny",
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


def transcribe_audio(audio_path: str | os.PathLike[str]) -> str:
    """Transcrire l'audio fourni avec le modèle local résolu, CPU/int8, deux threads."""
    audio = Path(audio_path)
    if not _readable_asset(audio):
        raise ValueError("Audio absent, vide ou non lisible.")
    readiness = is_voice_ready()
    if not readiness["ready"]:
        raise RuntimeError(readiness["reason"])
    factory, _ = _dependency()
    model = factory(readiness["model_path"], device="cpu", compute_type="int8",
                    cpu_threads=2, num_workers=1, local_files_only=True)
    segments, _ = model.transcribe(str(audio), language="fr", beam_size=1, best_of=1,
                                   vad_filter=True, condition_on_previous_text=False)
    pieces: list[str] = []
    size = 0
    for segment in segments:
        piece = segment.text.strip()
        if not piece:
            continue
        size += len(piece) + (1 if pieces else 0)
        if size > 1000:
            raise ValueError("Transcription trop longue.")
        pieces.append(piece)
    text = " ".join(pieces).strip()
    if not text:
        raise ValueError("Aucune parole reconnue.")
    return text


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
    except Exception:
        # Pas de transcription, de secret ni de traceback dans les journaux.
        print(json.dumps({"error": "Transcription locale impossible; vérifier --check."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
