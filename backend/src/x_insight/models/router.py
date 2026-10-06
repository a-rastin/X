"""Model version administration HTTP routes (S24, seam T1; plan.md §§4.3, 7.3).

Admin-only (401 unauthenticated, 403 non-admin, CSRF on POST). All failures
use the standard ``contracts.ErrorBody`` (never secrets/tracebacks).

Routes (all under ``/api/v1``):

- ``GET/POST /networks`` — admin import (immutable first version) + list.
- ``GET /networks/{id}/versions`` / ``POST /networks/{id}/versions`` —
  version history + immutable editing (new row, prior bytes/hash kept).
- ``POST /network-versions/{id}/validate`` — separate
  structural/semantic/content/admission reports (S21+S22+S25); XSD success
  stays structural, never executable/clinically valid.
- ``GET /network-versions/{id}/graph`` — read-only ordered graph, no edits.
- ``GET /network-versions/{id}/xml`` — exact preserved bytes.
- ``POST /model-bundles/activate``, ``/rollback`` — atomic pointer move
  with revision (If-Match/ETag) + transaction-scoped audit; synthetic
  complete bundles until S39.

``Idempotency-Key`` (optional) replays same key + same body, 409 on changed
body — same contract as ``physicians.create``. ``If-Match`` guards the
mutable pointer only (immutable versions need no revision).
"""

from __future__ import annotations

import hmac as hmac_module
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.db import get_session
from x_insight.identity import service as identity_service
from x_insight.models import registry as registry_module

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


def _current_user(request: Request, session: Session) -> dict[str, Any] | None:
    return identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )


def _require_admin(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = _current_user(request, session)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    if user.get("role") != "admin" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Administrator access required.", get_request_id(request)
        )
    return user


def _require_admin_mutation(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
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
    if user.get("role") != "admin" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Administrator access required.", get_request_id(request)
        )
    return user


def _idempotency_key_or_none(request: Request) -> str | None:
    raw = request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER)
    return contracts.parse_idempotency_key(raw)


def _idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int):
        try:
            response.headers["ETag"] = contracts.format_etag(revision)
        except ValueError:
            pass
    version = body.get("version")
    if isinstance(version, dict) and isinstance(version.get("version_number"), int):
        pass
    return response


def _idempotency_conflict(request_id: str) -> JSONResponse:
    return error_response(
        409,
        "IDEMPOTENCY_CONFLICT",
        "Idempotency-Key was already used with a different request body.",
        request_id,
    )


class ReviewPayload(BaseModel):
    model_config = {"extra": "forbid"}

    reviewer: str = Field(default="", max_length=100)
    decision: str = Field(default="draft", max_length=20)
    date: str = Field(default="", max_length=64)


class NetworkCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=100)
    xml_text: str = Field(min_length=1, max_length=1_048_576)
    review: ReviewPayload | None = None


class VersionCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    xml_text: str = Field(min_length=1, max_length=1_048_576)
    review: ReviewPayload | None = None


class ValidateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    package: dict[str, Any] | None = None


class BundleSelection(BaseModel):
    model_config = {"extra": "forbid"}

    network_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=1, max_length=64)


class BundleRequest(BaseModel):
    model_config = {"extra": "forbid"}

    workflow: str = Field(min_length=1, max_length=32)
    selections: list[BundleSelection] = Field(min_length=1, max_length=32)
    review: ReviewPayload


def _safe_version(version: dict[str, Any]) -> dict[str, Any]:
    item = dict(version)
    item.pop("source_bytes", None)
    return item


@router.get("/networks")
def list_networks(
    request: Request,
    session: Session = Depends(get_session),
    limit: int | None = None,
    offset: int | None = None,
) -> JSONResponse:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    try:
        items, total = registry_module.list_networks(session, limit=limit, offset=offset)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )
    return JSONResponse(status_code=200, content={"items": items, "total": total})


@router.post("/networks", status_code=201)
def import_network(
    payload: NetworkCreateRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    body = {
        "name": payload.name,
        "xml_text": payload.xml_text,
        "review": payload.review.model_dump() if payload.review else None,
    }
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = identity_service.idempotency_request_hash(body)
        stored = identity_service.lookup_idempotency(
            session, operation="networks.create", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    try:
        xml_bytes = registry_module.coerce_xml_bytes(payload.xml_text)
        review = payload.review.model_dump() if payload.review else None
        from x_insight.operations import audit as audit_module

        result = registry_module.create_network(
            session,
            name=payload.name,
            xml_bytes=xml_bytes,
            review=review,
            actor=str(admin["username"]),
        )
        audit_module.record_audit(
            session,
            operation="networks.create.success",
            actor=str(admin["username"]),
            request_id=request_id,
            details={
                "network_id": str(result["network"]["id"]),
                "version_id": str(result["version"]["id"]),
            },
        )
        session.flush()
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    response_body = {
        "network": {
            "id": str(result["network"]["id"]),
            "name": result["network"]["name"],
            "created_at": result["network"].get("created_at"),
        },
        "version": _safe_version(result["version"]),
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation="networks.create",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
        )
    return JSONResponse(status_code=201, content=response_body)


@router.get("/networks/{network_id}/versions")
def list_versions(
    network_id: str,
    request: Request,
    session: Session = Depends(get_session),
    limit: int | None = None,
    offset: int | None = None,
) -> JSONResponse:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    try:
        items, total = registry_module.list_versions(
            session, network_id=network_id, limit=limit, offset=offset
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )
    safe = [{**item, "source_bytes": None} if "source_bytes" in item else item for item in items]
    for entry in safe:
        entry.pop("source_bytes", None)
    return JSONResponse(
        status_code=200, content={"network_id": network_id, "items": safe, "total": total}
    )


@router.post("/networks/{network_id}/versions", status_code=201)
def create_version(
    network_id: str,
    payload: VersionCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    body = {
        "network_id": network_id,
        "xml_text": payload.xml_text,
        "review": payload.review.model_dump() if payload.review else None,
    }
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        import uuid as _uuid

        try:
            target = _uuid.UUID(str(network_id))
        except ValueError:
            return error_response(404, "NOT_FOUND", "Network not found.", request_id)
        request_hash = identity_service.idempotency_request_hash(body, target_id=target)
        stored = identity_service.lookup_idempotency(
            session, operation="networks.version.create", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    try:
        xml_bytes = registry_module.coerce_xml_bytes(payload.xml_text)
        review = payload.review.model_dump() if payload.review else None
        from x_insight.operations import audit as audit_module

        version = registry_module.create_version(
            session,
            network_id=network_id,
            xml_bytes=xml_bytes,
            review=review,
            actor=str(admin["username"]),
        )
        audit_module.record_audit(
            session,
            operation="networks.version.create.success",
            actor=str(admin["username"]),
            request_id=request_id,
            details={
                "network_id": str(network_id),
                "version_id": str(version["id"]),
                "version_number": int(version["version_number"]),
            },
        )
        session.flush()
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    response_body = {"version": _safe_version(version)}
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation="networks.version.create",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
        )
    return JSONResponse(status_code=201, content=response_body)


@router.post("/network-versions/{version_id}/validate")
def validate_version(
    version_id: str,
    payload: ValidateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    user = _require_admin_mutation(request, session)
    if isinstance(user, JSONResponse):
        return user
    try:
        result = registry_module.validate_version(session, version_id, payload.package)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )
    return JSONResponse(status_code=200, content=result)


@router.get("/network-versions/{version_id}/graph")
def read_graph(
    version_id: str, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    try:
        data = registry_module.export_version_bytes(session, version_id)
        graph = registry_module.build_graph(data)
        version = registry_module.get_version(session, version_id)
        assert version is not None
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )
    return JSONResponse(
        status_code=200,
        content={
            "version_id": str(version["id"]),
            "network_id": str(version["network_id"]),
            "version_number": int(version["version_number"]),
            **graph,
        },
    )


@router.get("/network-versions/{version_id}/xml")
def export_xml(
    version_id: str, request: Request, session: Session = Depends(get_session)
) -> Response:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    try:
        data = registry_module.export_version_bytes(session, version_id)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )
    return Response(content=data, media_type="application/xml")


def _bundle_response(pointer: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow": pointer["workflow"],
        "revision": int(pointer["revision"]),
        "bundle": pointer["active_bundle"],
        "updated_at": pointer.get("updated_at"),
    }


@router.post("/model-bundles/activate")
def activate_bundle(
    payload: BundleRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    try:
        expected = contracts.parse_if_match(request.headers.get(contracts.IF_MATCH_HEADER))
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    body = {
        "workflow": payload.workflow,
        "selections": [entry.model_dump() for entry in payload.selections],
        "review": payload.review.model_dump(),
    }
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = identity_service.idempotency_request_hash(body)
        stored = identity_service.lookup_idempotency(
            session, operation="model_bundles.activate", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    try:
        pointer = registry_module.activate_bundle(
            session,
            workflow=payload.workflow,
            selections=[entry.model_dump() for entry in payload.selections],
            review=payload.review.model_dump(),
            actor=str(admin["username"]),
            request_id=request_id,
            expected_revision=expected,
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    response_body = _bundle_response(pointer)
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation="model_bundles.activate",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    response = JSONResponse(status_code=200, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(pointer["revision"]))
    return response


@router.post("/model-bundles/rollback")
def rollback_bundle(
    payload: BundleRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    try:
        expected = contracts.parse_if_match(request.headers.get(contracts.IF_MATCH_HEADER))
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    body = {
        "workflow": payload.workflow,
        "selections": [entry.model_dump() for entry in payload.selections],
        "review": payload.review.model_dump(),
    }
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = identity_service.idempotency_request_hash(body)
        stored = identity_service.lookup_idempotency(
            session, operation="model_bundles.rollback", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    try:
        pointer = registry_module.rollback_bundle(
            session,
            workflow=payload.workflow,
            selections=[entry.model_dump() for entry in payload.selections],
            review=payload.review.model_dump(),
            actor=str(admin["username"]),
            request_id=request_id,
            expected_revision=expected,
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    response_body = _bundle_response(pointer)
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation="model_bundles.rollback",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    response = JSONResponse(status_code=200, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(pointer["revision"]))
    return response
