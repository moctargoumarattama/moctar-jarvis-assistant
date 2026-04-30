import base64
import hashlib
import hmac
import os


ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        DEFAULT_ITERATIONS,
    )
    return (
        f"{ALGORITHM}${DEFAULT_ITERATIONS}"
        f"${_b64encode(salt)}${_b64encode(digest)}"
    )


def is_password_hash(stored_password):
    if not stored_password:
        return False

    parts = stored_password.split("$")
    return len(parts) == 4 and parts[0] == ALGORITHM


def verify_password(password, stored_password):
    if not password or not stored_password:
        return False

    if not is_password_hash(stored_password):
        # Legacy CSV rows stored plaintext. Login supports them so they can be
        # upgraded transparently after a successful authentication.
        return hmac.compare_digest(password, stored_password)

    try:
        algorithm, iterations, salt, expected = stored_password.split("$")
        if algorithm != ALGORITHM:
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(_b64encode(digest), expected)
    except (TypeError, ValueError):
        return False
