"""Isolated browser fixtures; actual signing remains a public HTTP operation."""

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.http.test_signing import (
    _accept_body,
    _package,
    _publish_limited,
    _run_bounded_times,
    _valid_cpt,
)
from x_insight import db
from x_insight.app import app
from x_insight.identity.service import ensure_default_admin


def main():
    url = os.environ["E2E_ESTABLISHED_DATABASE_URL"]
    parsed = urlparse(url.replace("+psycopg", ""))
    assert (parsed.hostname, parsed.port, parsed.path) == (
        "127.0.0.1",
        55524,
        "/xi_established_browser_test",
    ), "Only the explicitly isolated browser database is permitted."
    os.environ["DATABASE_URL"] = url
    os.environ["TEST_DATABASE_URL"] = url
    engine = db.get_engine()

    if sys.argv[1] == "init":
        ensure_default_admin(engine)
        with tempfile.TemporaryDirectory(prefix="xi-established-ddi-") as folder:
            print(json.dumps(_publish_limited(engine, Path(folder))))
    elif sys.argv[1] == "legacy":
        encounter = sys.argv[2]
        definition = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "content/history/history.established_case_clozapine.v1.json"
            ).read_text()
        )
        values = {
            field["source_path"]: field["permitted"][0]
            for field in definition["fields"]
        }
        history = {
            "values": values,
            "provenance": {
                key: {"source": "clinician_entry", "author_id": "legacy-fixture"}
                for key in values
            },
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE encounters SET draft_data=jsonb_set(draft_data, '{history}', CAST(:history AS jsonb)) WHERE id=CAST(:id AS uuid)"
                ),
                {"history": json.dumps(history), "id": encounter},
            )
    elif sys.argv[1] == "prepare":
        encounter, username, password = sys.argv[2:]
        client = TestClient(app)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password, "role": "physician"},
        )
        assert login.status_code == 200, login.text
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        endpoint = f"/api/v1/encounters/{encounter}"
        current = client.get(endpoint).json()
        data = current["draft_data"]
        data["history"] = {"values": {"h_e2e_a": "yes", "h_e2e_b": "no"}}
        data["gate"] = "true"
        saved = client.patch(
            endpoint,
            json={"draft_data": data},
            headers={**headers, "If-Match": f'"{current["revision"]}"'},
        )
        assert saved.status_code == 200, saved.text
        started = client.post(
            endpoint + "/generation-batches",
            json={"package": _package("e2e_clozapine", "h_e2e_a", "h_e2e_b")},
            headers={**headers, "If-Match": f'"{saved.json()["revision"]}"'},
        )
        assert started.status_code == 202, started.text
        # Earlier failed fixture attempts may still have queued work. Every
        # fixture has this same synthetic graph; drain bounded work until the
        # target's public workflow reports complete, without success-row writes.
        for _ in range(8):
            provider, outcomes = _run_bounded_times(engine, _valid_cpt(), 1)
            provider.stop()
            batch = client.get(
                f"/api/v1/generation-batches/{started.json()['batch']['id']}"
            ).json()
            if batch["workflow"]["complete"]:
                break
        assert batch["workflow"]["complete"], (batch, outcomes)
        run = started.json()["question_runs"][0]
        review = client.get(f"/api/v1/question-runs/{run['id']}/review").json()
        accepted = client.post(
            f"/api/v1/question-runs/{run['id']}/acceptance",
            json=_accept_body(review),
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
        print(json.dumps({"batch_id": started.json()["batch"]["id"]}))


if __name__ == "__main__":
    main()
