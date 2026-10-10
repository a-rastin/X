"""Generation start/read/review routes (S40+S44+S45, T1/T8).

``POST /api/v1/encounters/{id}/generation-batches`` — author-only freeze
(physician + CSRF, ``If-Match`` with the current encounter revision,
optional ``Idempotency-Key``). Carries the synthetic question package
inline for this engineering proof (S39 bundles + S24 registry supply
pinned packages later). Returns ``202`` with the immutable batch and its
single projected question run. S44: same-fingerprint triggers reuse the
active run (terminal batches admit a fresh batch, never resurrect); a
different fingerprint supersedes the prior active generation (old jobs
cancelled, history kept, never 409); a full queue is ``429``. ``409`` is
idempotency-key conflict only. Failures (401/403/404/409/412/422/429)
create no partial rows.

``GET /api/v1/generation-batches/{id}`` — author-only read of the frozen
projection plus derived freshness (``stale`` when later relevant edits
moved the analysis fingerprint; note-only edits stay fresh) plus S44
queue visibility (``job``/``attempts``/``queue`` with busy state, no
fencing tokens) plus S45 baseline/transparency (``baseline``/
``transparency`` from persisted data, null until success). Old snapshots
remain readable history. No general patient snapshot endpoint exists and
no direct-record provider path is added here.

``GET /api/v1/question-runs/{id}/review`` — author-only question review
(original/validated CPTs, posteriors, section, five transparency fields,
freshness, adjustable flag). Failed originals expose no adjustable
baseline (``baseline`` null, ``adjustable`` false).
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.db import get_session
from x_insight.identity import service as identity_service
from x_insight.reasoning import snapshots as snapshots_service

router = APIRouter()


def get_request_id(request: Request) -> str:
    scope_id = request.scope.get("request_id")
    if isinstance(scope_id, str) and scope_id:
        return scope_id
    return request.headers.get("x-request-id") or contracts.new_request_id()


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    field_errors: dict[str, list[str]] | None = None,
    retryable: bool = False,
) -> _JSONResponse:
    body = contracts.ErrorBody(
        code=code,
        message=message,
        field_errors=field_errors or {},
        request_id=request_id,
        retryable=retryable,
    )
    return _JSONResponse(status_code=status_code, content=body.model_dump())


def _require_user(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    return user


def _require_physician_mutation(
    request: Request, session: Session
) -> dict[str, Any] | JSONResponse:
    raw_token = request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    if not raw_token:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    row = identity_service.get_session_row(session, raw_token)
    if row is None or row.get("revoked_at") is not None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    presented = request.headers.get(identity_service.CSRF_HEADER_NAME, "")
    expected = row.get("csrf_token", "")
    if not presented or not expected or not hmac_module.compare_digest(presented, expected):
        return error_response(403, "FORBIDDEN", "CSRF validation failed.", get_request_id(request))
    user = identity_service.get_session_user(session, raw_token)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    if user.get("role") != "physician" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Physician access required.", get_request_id(request)
        )
    return user


def _idempotency_key_or_none(request: Request) -> str | None:
    raw = request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER)
    return contracts.parse_idempotency_key(raw)


def _idempotency_conflict(request_id: str) -> JSONResponse:
    return error_response(
        409,
        "IDEMPOTENCY_CONFLICT",
        "Idempotency-Key was already used with a different request body.",
        request_id,
    )


class GenerationStartRequest(BaseModel):
    """Synthetic start body: one package or an ordered workflow (extra=forbid)."""

    model_config = {"extra": "forbid"}

    package: dict[str, Any] | None = Field(default=None, min_length=1)
    packages: list[dict[str, Any]] | None = Field(default=None, min_length=1)


def _batch_response(
    batch: dict[str, Any],
    runs: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    queue_view: dict[str, Any] | None = None,
    *,
    status_code: int,
    baseline: dict[str, Any] | None = None,
    transparency: dict[str, Any] | None = None,
    baselines: list[dict[str, Any]] | None = None,
    proposal: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "batch": snapshots_service.safe_batch(batch),
        "question_runs": [snapshots_service.safe_run(run) for run in runs],
    }
    if freshness is not None:
        content["freshness"] = freshness
    if queue_view is not None:
        # S44 queue visibility (T8): job progress + busy state, no tokens.
        content["job"] = queue_view.get("job")
        content["jobs"] = queue_view.get("jobs", [])
        content["attempts"] = queue_view.get("attempts", 0)
        content["queue"] = queue_view.get("queue")
    # S45: persisted baseline + five transparency fields (null until success;
    # kept as the first run for single-question compat).
    content["baseline"] = baseline
    content["transparency"] = transparency
    # S46: per-run baselines + immutable proposal + derived workflow view
    # (partial labeled incomplete, never mistaken for review-ready).
    if baselines is not None:
        content["baselines"] = baselines
    if proposal is not None or workflow is not None:
        content["proposal"] = proposal
        content["workflow"] = workflow
    else:
        content["proposal"] = None
    return JSONResponse(status_code=status_code, content=content)


@router.post("/encounters/{encounter_id}/generation-batches", status_code=202)
def start_generation(
    encounter_id: uuid.UUID,
    payload: GenerationStartRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    # S46 workflows: exactly one of package/packages (distinct keys in order).
    if (payload.package is None) == (payload.packages is None):
        return error_response(
            422,
            "VALIDATION_FAILED",
            "Supply package or packages, not both.",
            request_id,
            {"package": ["Supply package or packages, not both."]},
        )
    assert payload.package is not None or payload.packages is not None
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        if payload.packages is None:
            assert payload.package is not None
            request_hash = snapshots_service.idempotency_request_hash(
                encounter_id, expected, payload.package
            )
        else:
            request_hash = snapshots_service.idempotency_workflow_hash(
                encounter_id, expected, list(payload.packages)
            )
        stored = identity_service.lookup_idempotency(
            session,
            operation=snapshots_service.GENERATION_START_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            replay = dict(stored["response_body"])
            return JSONResponse(status_code=int(stored["response_status"]), content=replay)
    if payload.packages is None:
        assert payload.package is not None
        batch, runs = snapshots_service.start_generation_batch(
            session,
            author=physician,
            encounter_id=encounter_id,
            expected_revision=expected,
            package=payload.package,
            request_id=request_id,
        )
    else:
        batch, runs = snapshots_service.start_generation_batch(
            session,
            author=physician,
            encounter_id=encounter_id,
            expected_revision=expected,
            packages=list(payload.packages),
            request_id=request_id,
        )
    response_body: dict[str, Any] = {
        "batch": snapshots_service.safe_batch(batch),
        "question_runs": [snapshots_service.safe_run(run) for run in runs],
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=snapshots_service.GENERATION_START_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=202,
            response_body=response_body,
        )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/generation-batches/{batch_id}")
def read_generation(
    batch_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    from x_insight.reasoning import coordinator as coordinator_module
    from x_insight.reasoning import queue as queue_module

    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    batch, runs, freshness = snapshots_service.get_generation_batch(session, batch_id, user)
    queue_view = queue_module.get_batch_queue_view(session, batch_id)
    # S45 compat: first-run baseline + transparency (null until success).
    baseline: dict[str, Any] | None = None
    transparency: dict[str, Any] | None = None
    if runs:
        stored = coordinator_module.get_baseline(session, runs[0]["id"])
        if stored is not None:
            baseline = coordinator_module.safe_baseline(stored)
            transparency = coordinator_module.build_transparency(runs[0], stored)
    # S46: ordered per-run baselines + immutable proposal + workflow view.
    # Partial runs expose sections but label incomplete (never review-ready).
    baselines = coordinator_module.list_baselines_for_batch(session, batch_id)
    proposal_row = coordinator_module.get_proposal(session, batch_id)
    proposal = coordinator_module.safe_proposal(proposal_row) if proposal_row is not None else None
    workflow = coordinator_module.build_workflow_view(session, batch, runs)
    # Always expose the keys once S45 tables exist (null until success),
    # so T8 consumers need not branch on presence.
    return _batch_response(
        batch,
        runs,
        freshness,
        queue_view,
        status_code=200,
        baseline=baseline,
        transparency=transparency,
        baselines=baselines,
        proposal=proposal,
        workflow=workflow,
    )


@router.get("/question-runs/{run_id}/review")
def read_question_review(
    run_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Author-only question review (S45+S48a+S48b, seam T1).

    Returns the frozen run, its batch, the immutable baseline (or null),
    ``adjustable`` (true only with a baseline), the five transparency
    fields (or null), derived freshness, and queue visibility. S48a adds
    every root/conditional row with original/current values, full parent
    assignments and read-only outputs (``original_tables``/
    ``current_tables``/``revisions``/``review_revision``). S48b adds exact
    current/displayed-result revision IDs and freshness
    (``calculation_state``/``displayed_result_revision_id``/
    ``current_result_matches``/``input_freshness`` plus
    ``calculation_result``/``displayed_result``/``calculation_results`` and
    local-job visibility). Earlier successes stay labeled by their own
    revision and are never presented as solving current CPTs. Wrong-author
    reads are 403 without content; missing runs are 404. Failed originals
    expose no adjustable baseline.
    """
    from sqlalchemy import select

    from x_insight.probability_review import service as review_service
    from x_insight.reasoning import coordinator as coordinator_module
    from x_insight.reasoning import queue as queue_module
    from x_insight.reasoning import tables as reasoning_tables

    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    run_row = (
        session.execute(
            select(reasoning_tables.question_runs).where(
                reasoning_tables.question_runs.c.id == run_id
            )
        )
        .mappings()
        .first()
    )
    if run_row is None:
        return error_response(404, "NOT_FOUND", "Question run not found.", get_request_id(request))
    run = dict(run_row)
    batch, runs, freshness = snapshots_service.get_generation_batch(session, run["batch_id"], user)
    # get_generation_batch enforces author-only (403 for strangers).
    stored = coordinator_module.get_baseline(session, run_id)
    baseline = coordinator_module.safe_baseline(stored) if stored is not None else None
    transparency = (
        coordinator_module.build_transparency(run, stored) if stored is not None else None
    )
    queue_view = queue_module.get_batch_queue_view(session, run["batch_id"])
    # S48a: original/current CPTs with full parent assignments, revision
    # history, optimistic pointer, and read-only output marker.
    raw_revisions = review_service.list_revisions(session, run_id)
    revisions = [review_service.safe_revision(row) for row in raw_revisions]
    state_row = review_service.get_review_state(session, run_id)
    latest = review_service.get_latest_revision(session, run_id)
    current_tables = review_service.current_tables_for_run(stored, latest)
    original_tables = list(baseline["validated_tables"]) if baseline is not None else None
    # S48b: derived calculation view (no stored columns) + local jobs/results.
    raw_results = review_service.list_calculation_results(session, run_id)
    safe_results = [review_service.safe_calculation_result(row) for row in raw_results]
    local_jobs = [
        queue_module.safe_job(job) for job in queue_module.list_local_jobs_for_run(session, run_id)
    ]
    # Raw jobs for derivation (diagnostics carry revision binding).
    raw_local_jobs = queue_module.list_local_jobs_for_run(session, run_id)
    view = review_service.derive_calculation_view(
        session,
        run=run,
        baseline=stored,
        revisions=[dict(r) for r in raw_revisions],
        results=[dict(r) for r in raw_results],
        local_jobs=[dict(j) for j in raw_local_jobs],
    )
    input_freshness = {
        "stale": bool(freshness.get("stale", False)),
        "reason": str(freshness.get("reason", "current")),
        "current_fingerprint": str(freshness.get("current_fingerprint", "")),
    }
    content: dict[str, Any] = {
        "question_run": snapshots_service.safe_run(run),
        "batch": snapshots_service.safe_batch(batch),
        "baseline": baseline,
        "adjustable": baseline is not None,
        "transparency": transparency,
        "freshness": freshness,
        "input_freshness": input_freshness,
        "job": queue_view.get("job"),
        "attempts": queue_view.get("attempts", 0),
        "queue": queue_view.get("queue"),
        "original_tables": original_tables,
        "current_tables": current_tables,
        "current_cpt_revision_id": str(latest["id"]) if latest is not None else None,
        "displayed_result_revision_id": view.get("displayed_result_revision_id"),
        "current_result_matches": bool(view.get("current_result_matches", False)),
        "calculation_state": str(view.get("calculation_state", "unchanged")),
        "calculation_result": view.get("calculation_result"),
        "displayed_result": view.get("displayed_result"),
        "calculation_results": safe_results,
        "local_jobs": local_jobs,
        "review_revision": int(state_row["review_revision"])
        if state_row is not None
        else review_service.INITIAL_REVIEW_REVISION,
        "revisions": revisions,
        "cpt_hash": str(latest["cpt_hash"]) if latest is not None else None,
        "outputs_read_only": True,
    }
    # Current local job for convenience (null when baseline/reset-reuse).
    current_id = str(latest["id"]) if latest is not None else None
    current_job = None
    if current_id is not None:
        for job in local_jobs:
            diag = None
            for raw in raw_local_jobs:
                if str(raw.get("id")) == str(job.get("id")):
                    diag = (
                        raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {}
                    )
                    break
            if diag is not None and str(diag.get("cpt_revision_id")) == current_id:
                current_job = job
                break
    content["local_job"] = current_job
    return JSONResponse(status_code=200, content=content)
