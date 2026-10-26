"""Deployment-key lifecycle at its public initializer and loader boundary."""

import os
import stat
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from x_insight import db
from x_insight.reasoning import provider_key


@pytest.fixture(scope="module")
def key_database():
    """Run real migrations in a uniquely named database; never touch shared rows."""
    from alembic import command
    from alembic.config import Config

    database_name = "provider_key_test_" + uuid.uuid4().hex
    base = make_url(db.get_test_database_url())
    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"client_encoding": "utf8"},
    )
    engine = None
    with admin.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE \"{database_name}\" TEMPLATE template0 ENCODING 'UTF8'")
        )
    try:
        url = base.set(database=database_name).render_as_string(hide_password=False)
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        try:
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(config, "head")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
        engine = create_engine(url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def deployment(key_database, tmp_path, monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_DATA_KEY", raising=False)
    path = tmp_path / "secrets" / "provider-data.key"
    monkeypatch.setenv("X_INSIGHT_PROVIDER_DATA_KEY_FILE", str(path))
    with key_database.begin() as connection:
        connection.execute(text("TRUNCATE provider_settings"))
    yield key_database, path
    with key_database.begin() as connection:
        connection.execute(text("TRUNCATE provider_settings"))


def test_first_startup_provisions_protected_key_and_restart_reuses_it(deployment):
    engine, path = deployment
    provider_key.initialize_key(engine)
    first = provider_key.load_key()
    assert len(first) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    provider_key.initialize_key(engine)
    assert provider_key.load_key() == first


def test_concurrent_initializers_and_separate_api_worker_share_key(deployment, tmp_path):
    import concurrent.futures
    import os
    import subprocess
    import sys

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    engine, path = deployment
    child_env = {
        **os.environ,
        "PROVIDER_KEY_TEST_DATABASE_URL": engine.url.render_as_string(hide_password=False),
    }
    command = [
        sys.executable,
        "-c",
        (
            "import os; from sqlalchemy import create_engine; "
            "from x_insight.reasoning.provider_key import initialize_key; "
            "initialize_key(create_engine(os.environ['PROVIDER_KEY_TEST_DATABASE_URL']))"
        ),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: subprocess.run(command, capture_output=True, env=child_env), range(8)
            )
        )
    assert all(result.returncode == 0 for result in results), results
    key = provider_key.load_key()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, b"synthetic-provider-key", None)
    reader = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; "
                "from x_insight.reasoning.provider_key import load_key; "
                f"assert AESGCM(load_key()).decrypt({nonce!r}, {ciphertext!r}, None) "
                "== b'synthetic-provider-key'"
            ),
        ],
        capture_output=True,
    )
    assert reader.returncode == 0, reader.stderr
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("damage", ["missing", "unreadable", "malformed", "wrong", "symlink"])
def test_existing_ciphertext_requires_original_key_and_recovery(deployment, damage):
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    engine, path = deployment
    provider_key.initialize_key(engine)
    original = path.read_bytes()
    nonce = os.urandom(12)
    ciphertext = AESGCM(original).encrypt(nonce, b"retained-key", None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO provider_settings "
                "(revision, base_url, model, created_by, key_nonce, key_ciphertext) "
                "VALUES (1, 'https://example.com', 'synthetic', 'test', :nonce, :ciphertext)"
            ),
            {"nonce": nonce, "ciphertext": ciphertext},
        )
    if damage == "missing":
        path.unlink()
    elif damage == "unreadable":
        path.chmod(0)
    elif damage == "malformed":
        path.write_bytes(b"broken")
    elif damage == "wrong":
        path.write_bytes(b"x" * 32)
    else:
        target = path.with_name("escrow")
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(provider_key.ProviderKeyError):
        provider_key.initialize_key(engine)
    if damage == "missing":
        assert not path.exists(), "never replace a lost encryption key"
    if damage == "symlink":
        path.unlink()
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(original)
    path.chmod(0o600)
    provider_key.initialize_key(engine)
    assert provider_key.load_key() == original


def test_environment_key_override_preserves_existing_deployment(deployment, monkeypatch):
    import base64

    engine, path = deployment
    original = b"e" * 32
    monkeypatch.setenv("X_INSIGHT_PROVIDER_DATA_KEY", base64.b64encode(original).decode())
    provider_key.initialize_key(engine)
    assert provider_key.load_key() == original
    assert not path.exists()
    monkeypatch.setenv("X_INSIGHT_PROVIDER_DATA_KEY", "invalid")
    with pytest.raises(provider_key.ProviderKeyError):
        provider_key.initialize_key(engine)
    assert not path.exists(), "an invalid explicit override cannot trigger replacement"
