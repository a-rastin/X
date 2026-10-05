"""Cases subsystem: patient registry and encounter workflows (plan.md §§2.2-2.3).

S06 owns patient registration and directory search here
(:mod:`x_insight.cases.patients`, HTTP in :mod:`x_insight.cases.router`,
storage below, migration ``0004``). S07 adds author-owned draft editing
(:mod:`x_insight.cases.encounters`: save/read/discard plus single-slot
creation, migration ``0005``). S13 adds attributed page notes
(:mod:`x_insight.cases.notes`, migration ``0006``). Signing and follow-up
chart work land in later sessions without changing this contract.
"""

from __future__ import annotations
