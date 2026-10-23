"""Explicit schema migration and approved bundled data setup for local/prod."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from x_insight.ddi.__main__ import main as ddi_main


def main() -> int:
    command.upgrade(Config("alembic.ini"), "head")
    return ddi_main(["bootstrap"])


if __name__ == "__main__":
    raise SystemExit(main())
