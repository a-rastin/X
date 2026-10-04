"""Cases subsystem: patient registry and encounter workflows (plan.md §§2.2-2.3).

S06 owns patient registration and directory search here
(:mod:`x_insight.cases.patients`, HTTP in :mod:`x_insight.cases.router`,
storage below, migration ``0004``). Draft editing, signing, notes, and
follow-up encounters land in later sessions without changing this contract.
"""

from __future__ import annotations
