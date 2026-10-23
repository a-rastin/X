"""Released assessment packages (S08; plan.md §§4.3, 5).

Versioned JSON definitions live in ``content/assessments/`` (overridable
with ``X_INSIGHT_ASSESSMENTS_DIR``). Diagnosis and PANSS stay on their v1
files. The live C-SSRS instrument is ``cssrs.v2.json``. The workflow
release still hash-pins ``cssrs.v1.json``; this loader does not rewrite
that pin. Every payload served here is re-validated with
:func:`x_insight.assessments.engine.load_definition`. No owner review or
approval is recorded for these experimental releases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from x_insight.assessments.engine import (
    DefinitionError,
    load_definition,
    require_live_cssrs_definition,
)

RELEASED_TYPES = ("diagnosis", "panss", "cssrs")
_RELEASED_FILES = {
    "diagnosis": "diagnosis.v1.json",
    "panss": "panss.v1.json",
    "cssrs": "cssrs.v2.json",
}


def assessments_dir() -> Path:
    """Directory holding the released assessment definition files."""
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
    path = assessments_dir() / _RELEASED_FILES[assessment_type]
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    definition = load_definition(raw)
    if definition["id"] != assessment_type:
        raise DefinitionError(f"definition id {definition['id']!r} mismatches {assessment_type!r}")
    if assessment_type == "cssrs":
        require_live_cssrs_definition(definition)
    return definition
