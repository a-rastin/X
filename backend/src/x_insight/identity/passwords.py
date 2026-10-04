"""Standard password hashing (S03, plan.md §11).

PBKDF2-HMAC-SHA256 with 600 000 iterations (OWASP-recommended, Django
``pbkdf2_sha256`` compatible): ``pbkdf2_sha256$600000$salt$hash`` where salt
(16 bytes) and derived key (32 bytes) are base64-encoded. Implemented with
stdlib :mod:`hashlib`/:mod:`secrets` only, so no extra dependency needs
pinning in ``pyproject.toml`` — the pinned Python (``==3.12.*``) pins the
implementation. All pinned dependencies already use ``==``.

Passwords are used verbatim (UTF-8, no trimming): ``"  secret  "`` never
becomes ``"secret"``. Empty-string rejection lives at the HTTP layer so
login stays generic (401) and password change is explicit (422).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
DKLEN = 32

# Hash of the default password ``admin`` (verified against this scheme).
# Used only by migration 0002 so a freshly migrated DB permits admin/admin
# without embedding the plaintext. Runtime seeding hashes with a fresh salt.
DEFAULT_ADMIN_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$tjHQEyjmzxcTSSiMHZCVAA==$UASsgt9clH2ILDdwDx3OwXdEfbg0pY1iLMWdRIkPa1E="
)


def hash_password(password: str) -> str:
    """Hash a password with a fresh random salt (never trim input)."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, DKLEN)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"{ALGORITHM}${ITERATIONS}${salt_b64}${hash_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification; malformed hashes never authenticate."""
    try:
        algorithm, iterations_raw, salt_b64, hash_b64 = password_hash.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_raw)
        if iterations <= 0:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except Exception:
        return False
    try:
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, len(expected)
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)
