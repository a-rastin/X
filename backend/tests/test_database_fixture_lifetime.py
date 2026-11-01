"""Test that migrated database engines do not retain pools across modules."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_migrated_engine_pool_is_released_between_modules(tmp_path):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("requires an explicitly configured disposable test database")

    plugin = tmp_path / "pool_lifetime_probe.py"
    plugin.write_text(
        """import pytest

engine = None

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    global engine
    if item.name == "test_valid_unicode_name_after_nfc_passes":
        engine = item.funcargs["migrated_test_engine"]

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.name == "test_admin_creates_and_retrieves_physician_safe_fields":
        assert engine is not None
        assert engine.pool.checkedin() == 0, "previous module retained idle connections"
"""
    )
    backend = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(tmp_path), env.get("PYTHONPATH")]))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pool_lifetime_probe",
            "tests/http/test_patients.py::test_valid_unicode_name_after_nfc_passes",
            "tests/http/test_physicians.py::test_admin_creates_and_retrieves_physician_safe_fields",
            "-q",
            "--tb=short",
        ],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
