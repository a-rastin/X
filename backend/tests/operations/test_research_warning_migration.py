"""Research-warning acknowledgement column (issue 24, migration 0029)."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from x_insight import db

INSERT_USER = (
    "INSERT INTO users (id, username, role, active, password_hash, credential_revision, "
    "theme, created_at, updated_at, revision) "
    "VALUES (gen_random_uuid(), :username, :role, true, 'hash', 1, 'light', now(), now(), 1)"
)


@pytest.fixture()
def upgrade_database(monkeypatch):
    database_name = "warning_upgrade_" + uuid.uuid4().hex
    base = make_url(db.get_test_database_url())
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE \"{database_name}\" TEMPLATE template0 ENCODING 'UTF8'")
        )
    url = base.set(database=database_name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = create_engine(url)
    try:
        yield config, engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


def test_0028_to_0029_leaves_existing_accounts_unacknowledged(upgrade_database):
    config, engine = upgrade_database
    command.upgrade(config, "0028")
    # Migration 0002 already seeds the admin account; only the physician is added.
    with engine.begin() as connection:
        connection.execute(text(INSERT_USER), {"username": "dr_legacy", "role": "physician"})

    command.upgrade(config, "0029")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT username, research_warning_acknowledged_version FROM users "
                "ORDER BY username"
            )
        ).all()
        assert [tuple(row) for row in rows] == [("admin", None), ("dr_legacy", None)]
        column = connection.execute(
            text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'users' "
                "AND column_name = 'research_warning_acknowledged_version'"
            )
        ).one()
        assert tuple(column) == ("text", "YES")


def test_new_accounts_start_unacknowledged_and_downgrade_removes_column(upgrade_database):
    config, engine = upgrade_database
    command.upgrade(config, "0029")
    with engine.begin() as connection:
        connection.execute(text(INSERT_USER), {"username": "dr_new", "role": "physician"})
        acknowledged = connection.execute(
            text(
                "SELECT research_warning_acknowledged_version FROM users WHERE username = 'dr_new'"
            )
        ).scalar_one()
    assert acknowledged is None

    command.downgrade(config, "0028")

    with engine.connect() as connection:
        remaining = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'users' "
                "AND column_name = 'research_warning_acknowledged_version'"
            )
        ).scalar_one()
    assert remaining == 0
