import logging
import re
import threading
import time
import unicodedata

try:
    import speech_recognition as sr
except Exception:
    class _SRFallback:
        class WaitTimeoutError(Exception):
            pass

        class UnknownValueError(Exception):
            pass

        class RequestError(Exception):
            pass

        class Recognizer:
            def adjust_for_ambient_noise(self, source, duration=0.2):
                return None

            def listen(self, source, timeout=2, phrase_time_limit=2):
                raise _SRFallback.WaitTimeoutError()

            def recognize_google(self, audio, language="fr-FR"):
                raise _SRFallback.UnknownValueError()

        class Microphone:
            def __init__(self, device_index=None):
                self.device_index = device_index

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

    sr = _SRFallback()

import config


logger = logging.getLogger("jarvis")
_recognizer = sr.Recognizer()
_activation_event = threading.Event()
_stop_event = threading.Event()
_pause_event = threading.Event()
_listener_started = False
_listener_thread = None
_listener_guard = threading.Lock()
_microphone_lock = threading.Lock()
_activation_payload_lock = threading.Lock()
_pending_wake_lock = threading.Lock()
_last_activation_at = 0.0
_last_error_logs = {}
_ambient_adjusted_at = 0.0
_activation_payload = None
_pending_wake = None
_WAKE_WORD_ALIASES = {
    "hey moctar": ("hey moctar",),
    "yo moctar": ("yo moctar", "you moctar"),
    "yo": ("yo", "you"),
}
_WEAK_WAKE_ALIASES = {
    "hey moctar": ("mokhtar", "moktar", "moctar"),
}
_COMMAND_HINTS = {
    "active",
    "ouvre",
    "open",
    "mets",
    "met",
    "lance",
    "cherche",
    "ajoute",
    "cree",
    "resume",
    "rappelle",
    "donne",
    "allume",
    "eteins",
    "coupe",
    "baisse",
    "augmente",
    "quelle",
    "quel",
    "combien",
    "joue",
    "play",
}
_MAX_INLINE_COMMAND_WORDS = 6
_PENDING_WAKE_SECONDS = 4.0


def normalize_wake_text(text):
    if not text:
        return ""

    normalized = unicodedata.normalize("NFD", text.lower().strip())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _ranked_wake_words(wake_words=None):
    ranked = []
    seen = set()

    for index, item in enumerate(wake_words or config.SOFT_WAKE_WORDS):
        candidate = normalize_wake_text(item)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ranked.append((index, candidate))

    ranked.sort(key=lambda item: (-len(item[1].split()), item[0]))
    return [candidate for _, candidate in ranked]


def _wake_aliases(candidate):
    return _WAKE_WORD_ALIASES.get(candidate, (candidate,))


def _weak_wake_aliases():
    for wake_word in _ranked_wake_words():
        for alias in _WEAK_WAKE_ALIASES.get(wake_word, ()):
            yield wake_word, alias


def _extract_inline_command(normalized_text, alias):
    if not normalized_text or not alias:
        return None

    if normalized_text == alias:
        return ""

    prefix = f"{alias} "
    if normalized_text.startswith(prefix):
        return normalized_text[len(prefix) :].strip()

    return None


def _looks_like_inline_command(command):
    if not command:
        return False

    tokens = command.split()
    if not tokens or len(tokens) > _MAX_INLINE_COMMAND_WORDS:
        return False

    return tokens[0] in _COMMAND_HINTS


def _score_wake_word(normalized_text, candidate, alias, command):
    if not normalized_text or not candidate or command is None:
        return 0.0

    if candidate == "yo":
        if not command:
            return 0.91 if alias == "yo" else 0.89
        if not _looks_like_inline_command(command):
            return 0.0
        base_score = 0.90 if alias == "yo" else 0.88
        penalty = min(max(len(command.split()) - 3, 0) * 0.01, 0.03)
        return max(config.SOFT_WAKE_MIN_CONFIDENCE, base_score - penalty)

    if not command:
        return 1.0 if alias == candidate else 0.95
    if not _looks_like_inline_command(command):
        return 0.0

    penalty = min(len(command.split()) * 0.02, 0.08)
    base_score = 0.98 if alias == candidate else 0.94
    return max(config.SOFT_WAKE_MIN_CONFIDENCE, base_score - penalty)


def _score_pending_wake_command(command):
    if not _looks_like_inline_command(command):
        return 0.0

    penalty = min(max(len(command.split()) - 2, 0) * 0.01, 0.04)
    return max(config.SOFT_WAKE_MIN_CONFIDENCE, 0.90 - penalty)


def _score_command_only_activation(normalized_text):
    if not _looks_like_inline_command(normalized_text):
        return 0.0

    # Music playing in the room often produces transcripts beginning with
    # "mets" or "joue". Require an explicit wake word for those commands.
    if normalized_text.split()[0] in {"mets", "met", "joue", "play"}:
        return 0.0

    penalty = min(max(len(normalized_text.split()) - 2, 0) * 0.01, 0.04)
    return max(config.SOFT_WAKE_MIN_CONFIDENCE, 0.89 - penalty)


def parse_wake_activation(text, wake_words=None):
    normalized = normalize_wake_text(text)
    if not normalized:
        return None

    for candidate in _ranked_wake_words(wake_words):
        for alias in _wake_aliases(candidate):
            command = _extract_inline_command(normalized, alias)
            confidence = _score_wake_word(normalized, candidate, alias, command)
            if confidence < config.SOFT_WAKE_MIN_CONFIDENCE:
                continue
            return {
                "wake_word": candidate,
                "matched_alias": alias,
                "command": command or "",
                "confidence": confidence,
                "normalized": normalized,
            }

    return None


def _clear_pending_wake():
    global _pending_wake

    with _pending_wake_lock:
        _pending_wake = None


def _store_pending_wake(wake_word, alias, raw_text, normalized_text, now_value):
    global _pending_wake

    with _pending_wake_lock:
        _pending_wake = {
            "wake_word": wake_word,
            "matched_alias": alias,
            "raw": raw_text,
            "normalized": normalized_text,
            "expires_at": now_value + _PENDING_WAKE_SECONDS,
        }


def _consume_pending_wake_command(normalized_text, now_value):
    global _pending_wake

    with _pending_wake_lock:
        pending = _pending_wake
        if not pending:
            return None
        if now_value > pending["expires_at"]:
            _pending_wake = None
            return None
        wake_word = pending["wake_word"]

    confidence = _score_pending_wake_command(normalized_text)
    if confidence < config.SOFT_WAKE_MIN_CONFIDENCE:
        return None

    _clear_pending_wake()
    return {
        "wake_word": wake_word,
        "matched_alias": "pending",
        "command": normalized_text,
        "confidence": confidence,
        "normalized": normalized_text,
    }


def evaluate_transcript_for_activation(text, now_value=None):
    normalized = normalize_wake_text(text)
    if not normalized:
        return None

    activation = parse_wake_activation(normalized)
    if activation:
        _clear_pending_wake()
        return activation

    now_value = time.monotonic() if now_value is None else now_value

    for wake_word, alias in _weak_wake_aliases():
        command = _extract_inline_command(normalized, alias)
        if command is None:
            continue
        if command:
            confidence = _score_pending_wake_command(command)
            if confidence < config.SOFT_WAKE_MIN_CONFIDENCE:
                return None
            return {
                "wake_word": wake_word,
                "matched_alias": alias,
                "command": command,
                "confidence": confidence,
                "normalized": normalized,
            }
        _store_pending_wake(wake_word, alias, text, normalized, now_value)
        logger.info("Soft wake pending armed: raw=%r normalized=%r canonical=%r", text, normalized, wake_word)
        return None

    pending_activation = _consume_pending_wake_command(normalized, now_value)
    if pending_activation:
        return pending_activation

    confidence = _score_command_only_activation(normalized)
    if confidence < config.SOFT_WAKE_MIN_CONFIDENCE:
        return None

    _clear_pending_wake()
    return {
        "wake_word": "implicit command",
        "matched_alias": "command_only",
        "command": normalized,
        "confidence": confidence,
        "normalized": normalized,
        "command_only": True,
    }


def detect_wake_phrase(text, wake_words=None):
    activation = parse_wake_activation(text, wake_words=wake_words)
    if not activation:
        return None, 0.0

    return activation["wake_word"], activation["confidence"]


def contains_wake_word(text, wake_words=None):
    return parse_wake_activation(text, wake_words=wake_words) is not None


def _log_throttled(level, key, message, cooldown=30.0):
    now_value = time.monotonic()
    last_logged = _last_error_logs.get(key, 0.0)
    if now_value - last_logged < cooldown:
        return

    _last_error_logs[key] = now_value
    getattr(logger, level)(message)


def _adjust_for_ambient_noise(source):
    global _ambient_adjusted_at

    now_value = time.monotonic()
    if now_value - _ambient_adjusted_at < 30:
        return

    try:
        _recognizer.adjust_for_ambient_noise(source, duration=0.2)
        _ambient_adjusted_at = now_value
    except Exception:
        return


def _listen_for_short_phrase():
    with sr.Microphone(device_index=config.MIC_INDEX) as source:
        _adjust_for_ambient_noise(source)
        return _recognizer.listen(
            source,
            timeout=config.SOFT_WAKE_TIMEOUT,
            phrase_time_limit=config.SOFT_WAKE_PHRASE_LIMIT,
        )


def _listen_loop():
    global _activation_payload, _last_activation_at

    while not _stop_event.is_set():
        if not config.ENABLE_SOFT_WAKE_WORD:
            time.sleep(0.5)
            continue

        if _pause_event.is_set():
            time.sleep(0.1)
            continue

        acquired = _microphone_lock.acquire(timeout=0.1)
        if not acquired:
            time.sleep(0.05)
            continue

        audio = None
        try:
            try:
                audio = _listen_for_short_phrase()
            except sr.WaitTimeoutError:
                audio = None
            except Exception as exc:
                _log_throttled("warning", "soft_wake_micro", f"Soft wake word micro indisponible : {exc}", cooldown=15.0)
                time.sleep(0.5)
        finally:
            _microphone_lock.release()

        if audio is None:
            continue

        try:
            text = _recognizer.recognize_google(audio, language=config.SOFT_WAKE_LANGUAGE)
        except sr.UnknownValueError:
            continue
        except sr.RequestError as exc:
            _log_throttled("warning", "soft_wake_request", f"Soft wake word indisponible : {exc}", cooldown=20.0)
            time.sleep(0.5)
            continue
        except Exception as exc:
            _log_throttled("warning", "soft_wake_generic", f"Soft wake word en erreur : {exc}", cooldown=20.0)
            time.sleep(0.3)
            continue

        activation = evaluate_transcript_for_activation(text)
        normalized = normalize_wake_text(text)
        logger.info(
            "Soft wake transcript: raw=%r normalized=%r match=%r command=%r confidence=%.2f",
            text,
            normalized,
            activation["wake_word"] if activation else None,
            activation["command"] if activation else "",
            activation["confidence"] if activation else 0.0,
        )
        if not activation:
            logger.debug("Soft wake ignore: %s", normalized)
            continue

        now_value = time.monotonic()
        if now_value - _last_activation_at < config.SOFT_WAKE_COOLDOWN_SECONDS:
            continue

        _last_activation_at = now_value
        with _activation_payload_lock:
            _activation_payload = {
                "wake_word": activation["wake_word"],
                "command": activation["command"],
                "confidence": activation["confidence"],
                "command_only": activation.get("command_only", False),
                "raw": text,
                "normalized": normalized,
            }
        _activation_event.set()
        logger.info(
            "Soft wake word detected: %s (confidence=%.2f, command=%r)",
            activation["wake_word"],
            activation["confidence"],
            activation["command"],
        )


def start_soft_wake_listener():
    global _listener_started, _listener_thread

    if not config.ENABLE_SOFT_WAKE_WORD:
        return "Soft wake word desactive dans config."

    with _listener_guard:
        if _listener_started and _listener_thread and _listener_thread.is_alive():
            return "Soft wake word deja actif."

        _stop_event.clear()
        _pause_event.clear()
        _listener_thread = threading.Thread(
            target=_listen_loop,
            name="soft-wake-listener",
            daemon=True,
        )
        _listener_thread.start()
        _listener_started = True
        return f"Soft wake word actif : {' / '.join(config.SOFT_WAKE_WORDS)}."


def should_activate_by_voice():
    if config.ENABLE_SOFT_WAKE_WORD and not _listener_started:
        start_soft_wake_listener()

    return _activation_event.is_set()


def consume_voice_activation():
    global _activation_payload

    if config.ENABLE_SOFT_WAKE_WORD and not _listener_started:
        start_soft_wake_listener()

    if not _activation_event.is_set():
        return None

    _activation_event.clear()
    with _activation_payload_lock:
        payload = _activation_payload
        _activation_payload = None
    return payload


def pause_soft_wake_listener():
    _pause_event.set()


def resume_soft_wake_listener():
    _pause_event.clear()


def acquire_command_microphone(app=None):
    pause_soft_wake_listener()
    while not _microphone_lock.acquire(timeout=0.1):
        if app is not None:
            app.processEvents()


def release_command_microphone():
    if _microphone_lock.locked():
        _microphone_lock.release()
    resume_soft_wake_listener()


def stop_soft_wake_listener():
    global _activation_payload, _listener_started

    _stop_event.set()
    if _listener_thread and _listener_thread.is_alive():
        _listener_thread.join(timeout=2)
    _listener_started = False
    _activation_event.clear()
    _pause_event.clear()
    with _activation_payload_lock:
        _activation_payload = None
    _clear_pending_wake()


def reset_soft_wake_state():
    global _activation_payload, _last_activation_at, _listener_started

    _listener_started = False
    _activation_event.clear()
    _stop_event.clear()
    _pause_event.clear()
    _last_activation_at = 0.0
    with _activation_payload_lock:
        _activation_payload = None
    _clear_pending_wake()
