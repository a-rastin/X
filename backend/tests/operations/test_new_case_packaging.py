"""Package H packaging/startup wiring checks (no database required).

These assert the deployment contract that the fixed oral model and the pinned
Lexicomp CSV ship in the app image, that every content reader resolves through
one X_INSIGHT_CONTENT_DIR, and that normal setup installs the New case assets
without a provider credential, capability check or DDInter default gate.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_content_readers_resolve_through_one_content_dir(monkeypatch, tmp_path):
    from x_insight.ddi.bootstrap import bundled_directory
    from x_insight.ddi.lexicomp import content_directory
    from x_insight.models.workflow_release import content_root

    override = tmp_path / "content"
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(override))
    assert content_directory() == override
    assert bundled_directory() == override / "ddi" / "ddinter" / "2026-10-21"
    assert content_root() == override


def test_assessments_dir_uses_its_own_content_override(monkeypatch, tmp_path):
    from x_insight.assessments.released import assessments_dir

    override = tmp_path / "assessments"
    monkeypatch.setenv("X_INSIGHT_ASSESSMENTS_DIR", str(override))
    assert assessments_dir() == override


def test_normal_setup_installs_new_case_assets_without_provider_or_ddinter_gate():
    dockerfile = (ROOT / "deploy/Dockerfile.app").read_text()
    migrate = (ROOT / "backend/src/x_insight/operations/migrate.py").read_text()
    bootstrap = (ROOT / "backend/src/x_insight/ddi/__main__.py").read_text()

    # Fixed model and Lexicomp source files are bundled and the readers are
    # pointed at that bundle through one environment variable.
    assert "COPY content/new-case " in dockerfile
    assert "COPY content/ddi/lexicomp " in dockerfile
    assert "X_INSIGHT_CONTENT_DIR=" in dockerfile

    # The one-shot migrate entry installs the pinned oral model and runs the
    # DDI bootstrap (which installs Lexicomp) on every normal setup.
    assert "install_bundled_oral_model" in migrate
    assert 'ddi_main(["bootstrap"])' in migrate

    # Lexicomp installation is unconditional inside the bootstrap command and
    # is not fenced by a provider credential, capability check or the legacy
    # DDInter default selector. New case readiness does not depend on them.
    assert "install(engine)" in bootstrap
    assert "DDI_DEFAULT_DATASET_VERSION" not in bootstrap
    for marker in (
        "X_INSIGHT_PROVIDER_ENDPOINT",
        "X_INSIGHT_PROVIDER_MODEL",
        "capability",
    ):
        assert marker not in migrate
        assert marker not in bootstrap
