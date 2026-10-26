"""Reuse the external public HTTPS provider fixture across browser and backend checks."""
import runpy
from pathlib import Path

public_provider_https = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "e2e" / "provider_tls_fixture.py")
)["public_provider_https"]
