"""Deployment-held provider key, deliberately outside database backup storage."""

from __future__ import annotations

import base64
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.engine import Engine

DATA_KEY_ENV = "X_INSIGHT_PROVIDER_DATA_KEY"
DATA_KEY_FILE_ENV = "X_INSIGHT_PROVIDER_DATA_KEY_FILE"


class ProviderKeyError(Exception):
    """Safe deployment-key diagnostic without paths, secrets or upstream errors."""


def key_path() -> Path:
    configured = os.environ.get(DATA_KEY_FILE_ENV, "")
    return (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[3] / ".secrets" / "provider-data.key"
    )


def load_key() -> bytes:
    """Read the operator override or existing managed key; never generate on use."""
    raw = os.environ.get(DATA_KEY_ENV, "")
    try:
        if raw:
            key = base64.b64decode(raw, validate=True)
        else:
            descriptor = os.open(key_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or not info.st_mode & stat.S_IRUSR
                    or info.st_uid != os.getuid()
                ):
                    raise ValueError("unprotected key file")
                key = handle.read(33)
        if len(key) != 32:
            raise ValueError("invalid key length")
        return key
    except (OSError, ValueError):
        raise ProviderKeyError(
            "Provider data key is unavailable. Recover the original deployment key."
        ) from None


def verify_ciphertexts(rows: Iterable[Mapping[Any, Any]], *, key: bytes | None = None) -> None:
    """Require the deployment key to open every retained encrypted revision."""
    for row in rows:
        nonce, ciphertext = row.get("key_nonce"), row.get("key_ciphertext")
        if nonce is None and ciphertext is None:
            continue
        if key is None:
            key = load_key()
        try:
            if nonce is None or ciphertext is None:
                raise ValueError("incomplete ciphertext")
            AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), None)
        except Exception:
            raise ProviderKeyError(
                "Provider credentials require their original deployment key. "
                "Recover it before continuing."
            ) from None


def initialize_key(engine: Engine) -> None:
    """Provision once, atomically publishing a complete protected key file.

    Query failure never permits provisioning. Historical ciphertext also counts:
    queued jobs can still be pinned to an older provider settings revision.
    """
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT key_nonce, key_ciphertext FROM provider_settings "
                    "WHERE key_ciphertext IS NOT NULL OR key_nonce IS NOT NULL"
                )
            )
            .mappings()
            .all()
        )
    if os.environ.get(DATA_KEY_ENV, "") or os.path.lexists(key_path()) or rows:
        verify_ciphertexts(rows, key=load_key())
        return
    path = key_path()
    temporary = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if stat.S_IMODE(parent.st_mode) & 0o077 or parent.st_uid != os.getuid():
            raise ValueError("unprotected key directory")
        descriptor, temporary = tempfile.mkstemp(prefix=".provider-key-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(os.urandom(32))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard link publishes the complete inode, but cannot replace a winner.
            os.link(temporary, path)
        except FileExistsError:
            pass
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        load_key()
    except (OSError, ValueError):
        raise ProviderKeyError("Provider data key could not be provisioned safely.") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main() -> int:
    """One-time initializer command; safe messages only, never key material."""
    from x_insight.db import get_engine

    try:
        initialize_key(get_engine())
    except Exception:
        print(
            "Provider data key initialization failed; verify database and recover the original key."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
