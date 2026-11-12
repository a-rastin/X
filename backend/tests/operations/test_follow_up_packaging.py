"""Follow-up content and its pinned dependencies ship in the app image."""

import shutil
from pathlib import Path

from x_insight.cases.follow_up_content import load_content

ROOT = Path(__file__).resolve().parents[3]


def test_packaged_content_loads_outside_repository(monkeypatch, tmp_path):
    dockerfile = (ROOT / "deploy/Dockerfile.app").read_text()
    content = tmp_path / "content"
    for directory in ("follow-up", "new-case", "ddi/lexicomp"):
        assert f"COPY content/{directory} ./content/{directory}" in dockerfile
        shutil.copytree(ROOT / "content" / directory, content / directory)
    assert "X_INSIGHT_CONTENT_DIR=/srv/app/content" in dockerfile
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(content))
    monkeypatch.chdir(tmp_path)
    assert load_content()["manifest"]["workflow_version"] == "follow-up-v1"
