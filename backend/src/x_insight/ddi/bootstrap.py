"""Install the approved bundled DDInter release during explicit database setup.

Only original CSV bytes and the reviewed manifest are needed. Rebuilding private
staging avoids requiring Git LFS for the archived generated evidence artifact.
Existing installations keep their chosen release unless the bundled version is
explicitly selected as the configured default.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from x_insight.contracts import canonical_hash
from x_insight.ddi.ddinter import FORMAT, build_ddinter
from x_insight.ddi.publish import PublishRejected, list_releases, publish_release


def bundled_directory() -> Path:
    content = os.environ.get("X_INSIGHT_CONTENT_DIR")
    root = Path(content) if content else Path(__file__).resolve().parents[4] / "content"
    return root / "ddi" / "ddinter" / "2026-10-21"


def bootstrap_release(engine: Any, bundle: str | Path | None = None) -> dict[str, Any]:
    """Idempotently provision an empty database through the normal approval gate."""
    root = Path(bundle) if bundle is not None else bundled_directory()
    manifest_path = root / "owner-reviewed-manifest.json"
    candidate = json.loads((root / "candidate_dataset.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if candidate.get("format") != FORMAT or manifest.get("decision") != "approved_limited":
        raise PublishRejected("awaiting_review", "bundled DDInter release requires owner approval")
    content_hash = canonical_hash({"candidate": candidate, "approval": manifest})
    version = f"ddi-{content_hash[:12]}"
    releases = [
        row
        for row in list_releases(engine)
        if row["status"] in ("released_complete", "released_limited")
    ]
    if any(row["content_hash"] == content_hash for row in releases):
        return {"version": version, "content_hash": content_hash, "reused": True, "skipped": False}
    if releases and os.environ.get("DDI_DEFAULT_DATASET_VERSION", "").strip() != version:
        return {"version": version, "content_hash": content_hash, "reused": False, "skipped": True}
    with tempfile.TemporaryDirectory(prefix="ddi-bootstrap-") as folder:
        staging = Path(folder) / "candidate"
        build_ddinter(root / "sources", staging)
        rebuilt = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
        if rebuilt != candidate:
            raise PublishRejected("absent_provenance", "bundled source bytes or candidate changed")
        approval = staging / "owner-reviewed-manifest.json"
        approval.write_text(json.dumps(manifest), encoding="utf-8")
        result = publish_release(staging, approval, engine)
    return {**result, "skipped": False}
