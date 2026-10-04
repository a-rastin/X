"""Released assessment packages (S08; plan.md §§4.3, 5).

Versioned JSON definitions live in ``content/assessments/<type>.v1.json``
(next to this repository, overridable with ``X_INSIGHT_ASSESSMENTS_DIR``).
Every payload served here is re-validated with
:func:`x_insight.assessments.engine.load_definition`, so serving can never
drift from the T2 evaluation contract. No owner review or approval is
required or recorded for these experimental releases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from x_insight.assessments.engine import DefinitionError, load_definition

RELEASED_TYPES = ("diagnosis", "panss", "cssrs")


def assessments_dir() -> Path:
    """Directory holding the released ``<type>.v1.json`` packages."""
    override = os.environ.get("X_INSIGHT_ASSESSMENTS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "content" / "assessments"


def get_released_definition(assessment_type: str) -> dict[str, Any]:
    """Load and validate the released definition for ``assessment_type``.

    Raises :class:`KeyError` for unknown types and
    :class:`DefinitionError` for invalid payloads.
    """
    if assessment_type not in RELEASED_TYPES:
        raise KeyError(assessment_type)
    path = assessments_dir() / f"{assessment_type}.v1.json"
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    definition = load_definition(raw)
    if definition["id"] != assessment_type:
        raise DefinitionError(f"definition id {definition['id']!r} mismatches {assessment_type!r}")
    return definition
