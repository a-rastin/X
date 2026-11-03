"""Public startup commands with disposable files and external command adapters.

Docker is not required: these check make/shell orchestration and the Compose
dependency contract, not Docker health/completion semantics themselves.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ENV_KEYS = (
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "DB_PORT",
    "TEST_DB_PORT",
    "BACKEND_PORT",
    "WEB_PORT",
    "POSTGRES_PASSWORD",
    "DDI_DEFAULT_DATASET_VERSION",
    "API_PROXY_TARGET",
    "X_INSIGHT_PROVIDER_DATA_KEY",
)
ADAPTER = """import json, os, pathlib, sys, time
kind = pathlib.Path(sys.argv[0]).name
log = pathlib.Path(os.environ["STARTUP_LOG"])
def record(event):
    row = {"event": event, "args": sys.argv[1:], "cwd": pathlib.Path.cwd().name,
           "env": {key: os.environ.get(key) for key in ENV_KEYS}}
    with log.open("a") as handle:
        handle.write(json.dumps(row) + "\\n")
if kind == "docker":
    record("docker")
    if "ps" in sys.argv:
        if os.environ.get("OWN_RUNNING") == "1": print("owned-container")
    elif "up" in sys.argv and "--wait" in sys.argv:
        if os.environ.get("DB_SETUP_FAIL") == "1": sys.exit(21)
        time.sleep(.03)
        record("db_healthy")
elif kind == "uv" and "x_insight.operations.migrate" in sys.argv:
    record("setup")
    sys.exit(22 if os.environ.get("MIGRATION_FAIL") == "1" else 0)
elif kind == "uv" and "x_insight.reasoning" in sys.argv:
    record("worker")
    time.sleep(10)
elif kind == "uv":
    record("api")
    for _ in range(200):
        expected = ('"event": "web"', '"event": "worker"')
        if log.exists() and all(x in log.read_text() for x in expected):
            sys.exit(0)
        time.sleep(.01)
    sys.exit(23)
elif kind == "npm":
    record("web")
    time.sleep(10)
"""


@pytest.fixture
def checkout(tmp_path):
    # A red-control run can supply an archived Makefile without reverting production.
    source = os.environ.get("X_INSIGHT_STARTUP_TEST_MAKEFILE", ROOT / "Makefile")
    shutil.copyfile(source, tmp_path / "Makefile")
    for name in ("backend", "web", "bin"):
        (tmp_path / name).mkdir()
    for name in ("docker", "uv", "npm"):
        adapter = tmp_path / "bin" / name
        adapter.write_text(f"#!{sys.executable}\nENV_KEYS = {ENV_KEYS!r}\n" + ADAPTER)
        adapter.chmod(0o755)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://fixture:synthetic@127.0.0.1:64091/fixture\n"
        "TEST_DATABASE_URL=postgresql://fixture:synthetic@127.0.0.1:64092/fixture_test\n"
        "POSTGRES_PASSWORD=synthetic\nDB_PORT=64091\nTEST_DB_PORT=64092\n"
        "BACKEND_PORT=18091\nWEB_PORT=15091\nDDI_DEFAULT_DATASET_VERSION=ddi-fixture\n"
        "X_INSIGHT_PROVIDER_DATA_KEY=synthetic-key\n"
    )
    return tmp_path


def run_make(checkout, target="dev", overrides=None, arguments=()):
    env = {
        "PATH": str(checkout / "bin") + os.pathsep + os.defpath,
        "HOME": str(checkout),
        "LANG": "C.UTF-8",
        "STARTUP_LOG": str(checkout / "events.jsonl"),
        **(overrides or {}),
    }
    result = subprocess.run(
        [shutil.which("make"), target, *arguments],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    log = checkout / "events.jsonl"
    events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, events


def test_dev_loads_env_and_waits_for_database_and_ddi_before_servers(checkout):
    result, events = run_make(checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["event"] for row in events[:3]] == ["docker", "db_healthy", "setup"]
    assert events[0]["args"] == ["compose", "up", "-d", "--wait", "db", "db-test"]
    assert events[2]["args"] == ["run", "python", "-m", "x_insight.operations.migrate"]
    assert events[2]["cwd"] == "backend"
    assert {row["event"] for row in events[3:]} == {"api", "web", "worker"}
    for row in events:
        assert row["env"]["POSTGRES_PASSWORD"] == "synthetic"
        assert row["env"]["DATABASE_URL"].endswith(":64091/fixture")
        assert row["env"]["DDI_DEFAULT_DATASET_VERSION"] == "ddi-fixture"
        assert row["env"]["X_INSIGHT_PROVIDER_DATA_KEY"] == "synthetic-key"
    api = next(row for row in events if row["event"] == "api")
    web = next(row for row in events if row["event"] == "web")
    assert api["args"][-2:] == ["18091", "--reload"]
    assert web["args"] == ["run", "dev", "--", "--port", "15091", "--strictPort"]
    assert web["env"]["API_PROXY_TARGET"] == "http://localhost:18091"


@pytest.mark.parametrize("method", ["environment", "make_arguments"])
def test_explicit_urls_and_ports_override_env_file(checkout, method):
    explicit = {
        "DATABASE_URL": "postgresql://override@127.0.0.1:64093/override",
        "TEST_DATABASE_URL": "postgresql://override@127.0.0.1:64094/override_test",
        "DB_PORT": "64093",
        "TEST_DB_PORT": "64094",
        "BACKEND_PORT": "18093",
        "WEB_PORT": "15093",
        "POSTGRES_PASSWORD": "explicit-synthetic",
        "DDI_DEFAULT_DATASET_VERSION": "",
    }
    result, events = run_make(
        checkout,
        overrides=explicit if method == "environment" else None,
        arguments=[f"{key}={value}" for key, value in explicit.items()]
        if method == "make_arguments"
        else (),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {row["event"] for row in events} == {
        "docker",
        "db_healthy",
        "setup",
        "api",
        "web",
        "worker",
    }
    for row in events:
        assert {key: row["env"][key] for key in explicit} == explicit
    assert next(row for row in events if row["event"] == "web")["env"]["API_PROXY_TARGET"] == (
        "http://localhost:18093"
    )


@pytest.mark.parametrize("failure", ["DB_SETUP_FAIL", "MIGRATION_FAIL"])
def test_failed_setup_never_starts_backend_or_web(checkout, failure):
    result, events = run_make(checkout, overrides={failure: "1"})
    assert result.returncode != 0
    assert not {"api", "web", "worker"} & {row["event"] for row in events}
    assert ("setup" in {row["event"] for row in events}) == (failure == "MIGRATION_FAIL")


def test_missing_env_fails_before_external_commands(checkout):
    (checkout / ".env").unlink()
    result, events = run_make(checkout)
    assert result.returncode != 0
    assert "missing .env" in result.stderr
    assert events == []


@pytest.mark.parametrize("owned", [True, False])
def test_occupied_database_port_reuses_only_own_running_container(checkout, owned):
    # A temporary loopback listener represents an occupied database host port.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result, events = run_make(
            checkout, overrides={"DB_PORT": str(port), "OWN_RUNNING": "1" if owned else "0"}
        )
    assert events[0]["args"] == ["compose", "ps", "--status", "running", "-q", "db"]
    if owned:
        assert result.returncode == 0, result.stdout + result.stderr
        assert {"setup", "api", "web", "worker"} <= {row["event"] for row in events}
    else:
        assert result.returncode != 0
        assert "is occupied" in result.stderr
        assert len(events) == 1


@pytest.mark.parametrize(
    "failed_role,exit_status", [("api", 37), ("web", 37), ("worker", 37), ("worker", 0)]
)
def test_exited_process_stops_sibling_descendants(checkout, failed_role, exit_status):
    launcher = f"""#!{sys.executable}
import json, os, pathlib, socket, subprocess, sys, time
root = pathlib.Path({str(checkout)!r})
if 'x_insight.operations.migrate' in sys.argv: sys.exit(0)
role = 'web' if pathlib.Path(sys.argv[0]).name == 'npm' else 'api'
if 'x_insight.reasoning' in sys.argv: role = 'worker'
if role == {failed_role!r}:
    for _ in range(300):
        siblings = (name for name in ('api', 'web', 'worker') if name != role)
        if all((root / (name + '.json')).exists() for name in siblings):
            sys.exit({exit_status})
        time.sleep(.01)
    sys.exit(38)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
(root / (role + '.json')).write_text(json.dumps(child.pid))
child.wait()
"""
    for name in ("uv", "npm"):
        (checkout / "bin" / name).write_text(launcher)
    children = []
    try:
        result, _ = run_make(checkout)
        children = [json.loads(p.read_text()) for p in checkout.glob("*.json")]
        assert len(children) == 2, result.stdout + result.stderr
        assert result.returncode != 0
        if failed_role == "worker":
            assert "worker is not running" in result.stderr
        else:
            assert "Error 37" in result.stderr
            assert "worker is not running" not in result.stderr
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(descendant_running(pid) for pid in children):
            time.sleep(0.02)
        assert not any(descendant_running(pid) for pid in children)
    finally:
        for pid in children:
            if descendant_running(pid):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("abrupt", [False, True])
def test_worker_exit_before_supervisor_wait_is_reported(checkout, abrupt):
    makefile = checkout / "Makefile"
    recipe = makefile.read_text()
    assert "while :; do" in recipe
    makefile.write_text(recipe.replace("while :; do", "sleep 1; while :; do"))
    launcher = f"""#!{sys.executable}
import os, signal, sys, time
if 'x_insight.operations.migrate' in sys.argv: sys.exit(0)
if 'x_insight.reasoning' in sys.argv:
    if {abrupt!r}: os.kill(os.getpid(), signal.SIGKILL)
    sys.exit(19)
time.sleep(30)
"""
    for name in ("uv", "npm"):
        (checkout / "bin" / name).write_text(launcher)
    result, _ = run_make(checkout)
    assert result.returncode != 0
    assert "worker is not running" in result.stderr
    assert f"Error {137 if abrupt else 19}" in result.stderr


@pytest.mark.parametrize("shutdown_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_shutdown_cleans_all_process_groups(checkout, shutdown_signal):
    launcher = f"""#!{sys.executable}
import json, pathlib, subprocess, sys
root = pathlib.Path({str(checkout)!r})
if 'x_insight.operations.migrate' in sys.argv: sys.exit(0)
role = 'web' if pathlib.Path(sys.argv[0]).name == 'npm' else 'api'
if 'x_insight.reasoning' in sys.argv: role = 'worker'
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
(root / (role + '.json')).write_text(json.dumps(child.pid))
child.wait()
"""
    for name in ("uv", "npm"):
        (checkout / "bin" / name).write_text(launcher)
    env = {
        "PATH": str(checkout / "bin") + os.pathsep + os.defpath,
        "HOME": str(checkout),
        "LANG": "C.UTF-8",
        "STARTUP_LOG": str(checkout / "events.jsonl"),
    }
    process = subprocess.Popen(
        [shutil.which("make"), "dev"],
        cwd=checkout,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    children = []
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(list(checkout.glob("*.json"))) < 3:
            time.sleep(0.02)
        children = [json.loads(p.read_text()) for p in checkout.glob("*.json")]
        assert len(children) == 3
        os.killpg(process.pid, shutdown_signal)
        _, stderr = process.communicate(timeout=5)
        assert "worker is not running" not in stderr
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(descendant_running(pid) for pid in children):
            time.sleep(0.02)
        assert not any(descendant_running(pid) for pid in children)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for pid in children:
            if descendant_running(pid):
                os.kill(pid, signal.SIGKILL)


def descendant_running(pid):
    try:
        # A terminated orphan can briefly remain a zombie until the host reaps it.
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


def service_block(compose, name):
    return re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  [a-z][\w-]*:|^volumes:|\Z)", compose)[1]


def test_production_startup_targets_require_completed_automatic_migration(checkout):
    result, events = run_make(checkout, target="prod-up")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(events) == 1
    assert events[0]["args"] == [
        "compose",
        "-f",
        "compose.prod.yaml",
        "--env-file",
        ".env.prod",
        "up",
        "-d",
        "db",
        "app",
        "worker",
        "edge",
    ]
    compose = (ROOT / "compose.prod.yaml").read_text()
    for target in ("app", "worker"):
        # Direct `docker compose up app`/`up worker` follows this dependency too.
        block = service_block(compose, target)
        assert re.search(
            r"(?m)^      migrate:\n        condition: service_completed_successfully$", block
        )
        assert re.search(r"(?m)^      db:\n        condition: service_healthy$", block)
    migration = service_block(compose, "migrate")
    assert 'command: [".venv/bin/python", "-m", "x_insight.operations.migrate"]' in migration
    assert not re.search(r"(?m)^    profiles:", migration)
    assert re.search(r"(?m)^      db:\n        condition: service_healthy$", migration)
    assert "DDI_DEFAULT_DATASET_VERSION: ${DDI_DEFAULT_DATASET_VERSION:-}" in migration


def test_production_key_is_initialized_before_readonly_api_and_worker():
    compose = (ROOT / "compose.prod.yaml").read_text()
    initializer = service_block(compose, "migrate")
    assert (
        "X_INSIGHT_PROVIDER_DATA_KEY_FILE: /var/lib/x-insight/secrets/provider-data.key"
        in initializer
    )
    assert "- prod_secrets:/var/lib/x-insight/secrets\n" in initializer
    for name in ("app", "worker"):
        block = service_block(compose, name)
        assert "- prod_secrets:/var/lib/x-insight/secrets:ro" in block
        assert (
            "X_INSIGHT_PROVIDER_DATA_KEY_FILE: /var/lib/x-insight/secrets/provider-data.key"
            in block
        )
        assert "migrate:\n        condition: service_completed_successfully" in block
    assert "prod_secrets:" in compose
