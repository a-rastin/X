"""Run public onboarding in both browsers against isolated real API databases.

Set TEST_DATABASE_URL to a PostgreSQL test role permitted to create databases;
invoke with the project's Python dependency environment. Upstream HTTP alone is
provided by provider-public-fixture.py. No real provider credentials are needed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def wait_for(url: str, process: subprocess.Popen) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("Fixture process exited; inspect retained logs")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError("Fixture startup timed out; inspect retained logs")


def main() -> None:
    if not os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("TEST_DATABASE_URL is required (dedicated PostgreSQL test service)")
    repo = Path(__file__).resolve().parents[1]
    source = make_url(os.environ["TEST_DATABASE_URL"])
    source = source.set(drivername="postgresql+psycopg")
    admin = create_engine(source, isolation_level="AUTOCOMMIT")
    subprocess.run(["npm", "--prefix", "web", "run", "build"], cwd=repo, check=True)
    run_dir = Path(tempfile.mkdtemp(prefix="x-insight-provider-e2e-"))
    print(f"Browser fixture logs retained in {run_dir}", flush=True)
    for browser in ("chromium", "firefox"):
        database = f"provider_e2e_{uuid.uuid4().hex}"
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}" ENCODING \'UTF8\' TEMPLATE template0'))
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": source.set(database=database).render_as_string(hide_password=False),
            "X_INSIGHT_PROVIDER_ALLOWED_HOSTS": "",
            "X_INSIGHT_PROVIDER_DATA_KEY": "",
            "X_INSIGHT_PROVIDER_DATA_KEY_FILE": str(run_dir / browser / "provider.key"),
            "PROVIDER_E2E_API_PORT": "18083",
            "API_PROXY_TARGET": "http://127.0.0.1:18083",
            "E2E_BASE_URL": "http://127.0.0.1:15183",
        })
        processes = []
        try:
            with (run_dir / f"{browser}-api.log").open("w") as api_log, (run_dir / f"{browser}-web.log").open("w") as web_log:
                api = subprocess.Popen([sys.executable, str(repo / "e2e/provider-public-fixture.py")], env=env, cwd=repo, stdout=api_log, stderr=subprocess.STDOUT)
                processes.append(api)
                wait_for("http://127.0.0.1:18083/api/v1/ready", api)
                web = subprocess.Popen(["npm", "--prefix", "web", "run", "preview", "--", "--host", "127.0.0.1", "--port", "15183", "--strictPort"], env=env, cwd=repo, stdout=web_log, stderr=subprocess.STDOUT, start_new_session=True)
                processes.append(web)
                wait_for("http://127.0.0.1:15183/api/v1/health", web)
                subprocess.run(["npx", "playwright", "test", "--config", "e2e/playwright.provider-public.config.ts", f"--project={browser}", "--reporter=line"], env=env, cwd=repo, check=True)
                # Existing form regressions also hit the real API, including local
                # approval, failure presentation, key clear, auth and CSRF recovery.
                subprocess.run(["npx", "playwright", "test", "e2e/provider-settings.spec.ts", f"--project={browser}", "--reporter=line", "--workers=1"], env=env, cwd=repo, check=True)
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    if process is processes[0]:
                        process.terminate()
                    else:
                        import signal
                        os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
    admin.dispose()


if __name__ == "__main__":
    main()
