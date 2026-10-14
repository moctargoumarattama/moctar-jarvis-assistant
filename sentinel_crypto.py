"""Enveloppes AES-256-GCM partagées par le NOC et son agent sentinelle."""

from __future__ import annotations

import base64
import hashlib
import json
import os

AAD = b"noc-sentinel-v1"
MAX_PLAINTEXT_BYTES = 8192


def _key(token: str) -> bytes:
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError("Enveloppe sentinelle invalide")
    return hashlib.sha256(AAD + b":" + token.encode("utf-8")).digest()


def encrypt(payload: dict, token: str) -> dict:
    """Chiffre un objet JSON borné ; aucune donnée sensible dans les erreurs."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not isinstance(payload, dict):
            raise ValueError
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_PLAINTEXT_BYTES:
            raise ValueError
        nonce = os.urandom(12)
        ciphertext = AESGCM(_key(token)).encrypt(nonce, raw, AAD)
        return {"version": 1, "nonce": base64.b64encode(nonce).decode("ascii"), "ciphertext": base64.b64encode(ciphertext).decode("ascii")}
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ValueError("Enveloppe sentinelle invalide") from None


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def decrypt(envelope: dict, token: str) -> dict:
    """Authentifie puis déchiffre ; rejette modification, doublons et base64 invalide."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not isinstance(envelope, dict) or set(envelope) != {"version", "nonce", "ciphertext"}:
            raise ValueError
        if type(envelope["version"]) is not int or envelope["version"] != 1:
            raise ValueError
        nonce_text, ciphertext_text = envelope["nonce"], envelope["ciphertext"]
        if not isinstance(nonce_text, str) or not isinstance(ciphertext_text, str):
            raise ValueError
        if len(nonce_text) != 16 or len(ciphertext_text) > 4 * ((MAX_PLAINTEXT_BYTES + 18) // 3):
            raise ValueError
        nonce = base64.b64decode(nonce_text, validate=True)
        ciphertext = base64.b64decode(ciphertext_text, validate=True)
        if len(nonce) != 12 or not 16 <= len(ciphertext) <= MAX_PLAINTEXT_BYTES + 16:
            raise ValueError
        try:
            raw = AESGCM(_key(token)).decrypt(nonce, ciphertext, AAD)
        except InvalidTag:
            raise ValueError from None
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ValueError("Enveloppe sentinelle invalide") from None
