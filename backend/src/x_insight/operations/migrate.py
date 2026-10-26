"""Explicit schema migration and approved bundled data setup for local/prod."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from x_insight.ddi.__main__ import main as ddi_main


def main() -> int:
    command.upgrade(Config("alembic.ini"), "head")
    result = ddi_main(["bootstrap"])
    if result == 0:
        from x_insight.db import get_engine
        from x_insight.reasoning.provider_key import initialize_key

        initialize_key(get_engine())
    return result


if __name__ == "__main__":
    raise SystemExit(main())
