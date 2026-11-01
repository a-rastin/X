"""Upgrade provider settings from the pre-capability schema."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from x_insight import db


def test_0026_to_0027_backfills_existing_settings(monkeypatch):
    database_name = "capability_upgrade_" + uuid.uuid4().hex
    base = make_url(db.get_test_database_url())
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    with admin.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE \"{database_name}\" TEMPLATE template0 ENCODING 'UTF8'")
        )
    try:
        url = base.set(database=database_name).render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(config, "0026")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO provider_settings (revision, base_url, model, created_by) "
                    "VALUES (1, 'https://example.test/v1', 'fictional', 'test')"
                )
            )
        command.upgrade(config, "0027")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT revision, capability_mode FROM provider_settings")
            ).one() == (1, "schema")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE provider_settings SET capability_mode = 'invalid'"))
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()
