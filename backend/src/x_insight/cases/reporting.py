"""CSV exports + printable signed-patient reports (S53, seam T1).

Plan.md §§2.1, 4.3, 10.1 (FR-03, FR-40):

- CSV: stable English headers, UTF-8, proper quoting via the ``csv``
  module, spreadsheet-formula neutralization for text fields, no
  credentials/hashes. Patient identifiers stay exact 10-digit text bytes
  (never a numeric column, never a ``="..."`` formula wrapper): CSV has no
  type information, so the docstring/import note tells operators to import
  the identifier column as Text in spreadsheets.
- Report: escaped signed-patient HTML assembled ONLY from signed records
  (live patient demographics for the current header + immutable snapshots /
  addenda via the S49 chart read path). Private drafts are never read here
  — no ``encounters.draft_data`` live body, no live notes table, no derived
  artifacts beyond what signing froze. Per question the report carries the
  complete original + final accepted CPTs/results/recommendations,
  network/template versions, an adjustment indicator, and the accepting
  physician/timestamps. Adjusted-then-reset keeps an explicit history
  indication while reporting final-equality separately. No PDF library, no
  LLM prose: sections come from stored baselines/results/templates only.
- Physician-report permission (plan §§1.3, 2.1: physician printing is
  PROPOSED, not confirmed): this module renders for any signed patient, but
  the route in :mod:`x_insight.cases.router` grants admin-only reads and
  denies physicians (403) until the owner confirms. Do NOT widen the route
  without that decision.

All persistence uses the caller's transaction; this module never commits.
Audit events are recorded by the routes at the command path with the S52
``operations.audit`` helpers (actor/time/target only, no keys or clinical
bodies beyond required refs).
"""

from __future__ import annotations

import csv
import html
import io
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import patients as patients_service
from x_insight.cases import signing as signing_service
from x_insight.cases import tables as cases_tables

PATIENT_CSV_HEADERS = [
    "patient_id",
    "identifier",
    "given_name",
    "family_name",
    "sex",
    "age",
    "clinical_status",
    "phone",
    "archived",
    "created_at",
    "updated_at",
]

PHYSICIAN_CSV_HEADERS = [
    "physician_id",
    "username",
    "role",
    "active",
    "theme",
]

# Cells starting with these execute as formulas/macros when a spreadsheet
# opens CSV directly. Prefixing with a single quote keeps the visible text
# while staying inert (no ``="..."`` wrapper is used anywhere, so the
# identifier column keeps exact ``0012345678`` bytes).
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")


def neutralize_cell(value: Any) -> str:
    """Spreadsheet-formula neutralization for one CSV text field.

    Non-strings stringify without a prefix (ints/bools cannot lead a
    formula); strings whose left-stripped first character is formula-like
    get a ``'`` prefix. Quotes/newlines are left to the ``csv`` module's
    proper quoting. Returns the display string.
    """
    if not isinstance(value, str):
        return "" if value is None else str(value)
    stripped = value.lstrip()
    if stripped[:1] in ("=", "+", "-", "@") or value[:1] in ("\t", "\r", "\n"):
        return "'" + value
    return value


def _bool_text(value: Any) -> str:
    return "true" if bool(value) else "false"


def patient_csv_row(patient: Mapping[str, Any]) -> list[str]:
    """One stable patient row (safe demographics only, never secrets)."""
    phone = patient.get("phone")
    return [
        str(patient.get("id", "")),
        # Identifier is exact 10-digit text bytes: raw, no formula wrapper.
        # Operators must import this column as Text (CSV carries no types).
        str(patient.get("identifier", "")),
        neutralize_cell(patient.get("given_name", "")),
        neutralize_cell(patient.get("family_name", "")),
        str(patient.get("sex", "")),
        str(int(patient.get("age", 0))),
        str(patient.get("clinical_status", "")),
        neutralize_cell("" if phone is None else phone),
        _bool_text(patient.get("archived", False)),
        str(patient.get("created_at", "")),
        str(patient.get("updated_at", "")),
    ]


def physician_csv_row(user: Mapping[str, Any]) -> list[str]:
    """One stable physician row (safe fields only: no hash/token/revision)."""
    return [
        str(user.get("id", "")),
        neutralize_cell(user.get("username", "")),
        str(user.get("role", "")),
        _bool_text(user.get("active", False)),
        neutralize_cell(user.get("theme", "")),
    ]


def build_patients_csv(patients: Sequence[Mapping[str, Any]]) -> bytes:
    """Pure CSV builder: stable headers, UTF-8, QUOTE_MINIMAL, CRLF rows."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(PATIENT_CSV_HEADERS)
    for patient in patients:
        writer.writerow(patient_csv_row(patient))
    return buffer.getvalue().encode("utf-8")


def build_physicians_csv(users: Sequence[Mapping[str, Any]]) -> bytes:
    """Pure CSV builder for the physician list (same quoting/encoding)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(PHYSICIAN_CSV_HEADERS)
    for user in users:
        writer.writerow(physician_csv_row(user))
    return buffer.getvalue().encode("utf-8")


def list_all_patients(session: Session) -> list[dict[str, Any]]:
    """All patients, stable ``(created_at, id)`` order, archived included.

    The ``archived`` column distinguishes rows; exports are admin-only so no
    stranger filter applies. Reads the owning cases table only.
    """
    rows = (
        session.execute(
            select(cases_tables.patients).order_by(
                cases_tables.patients.c.created_at, cases_tables.patients.c.id
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def list_all_physicians(session: Session) -> list[dict[str, Any]]:
    """All physician accounts via the identity owning module (paged, filtered).

    :func:`identity.service.list_physicians` lists every account row, so this
    filters ``role == 'physician'`` and walks the bounded pages to cover more
    than one page without inventing a new query path.
    """
    from x_insight.identity import service as identity_service

    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        items, total = identity_service.list_physicians(session, limit=100, offset=offset)
        for item in items:
            if str(item.get("role", "")) == "physician":
                collected.append(dict(item))
        offset += len(items)
        if offset >= int(total) or not items:
            break
    return collected


def get_report_data(session: Session, patient_id: uuid.UUID) -> dict[str, Any]:
    """Signed-only report source (404 when the patient is missing).

    Reads the current patient row (header demographics), the signed
    chronology, and — per signed encounter — the immutable snapshot plus
    append-only addenda through the S49 chart path. Never touches live
    draft bodies, the live notes table, or derived worker artifacts.
    """
    patient = patients_service.get_patient(session, patient_id)
    if patient is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    signed = encounters_service.list_signed_for_patient(session, patient_id)
    snapshots: list[dict[str, Any]] = []
    addenda: list[dict[str, Any]] = []
    for row in signed:
        stored = signing_service.get_snapshot(session, row["id"])
        if stored is not None:
            snapshots.append(signing_service.safe_snapshot(stored))
        for entry in signing_service.list_addenda(session, row["id"]):
            addenda.append(signing_service.safe_addendum(entry))
    return {
        "patient": patients_service.safe_patient(patient),
        "signed_encounters": [encounters_service.safe_signed_reference(row) for row in signed],
        "signed_snapshots": snapshots,
        "addenda": addenda,
    }


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow an unknown JSON value to a dict (mypy-clean ``{}`` fallback)."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Narrow an unknown JSON value to a list (mypy-clean ``[]`` fallback)."""
    return value if isinstance(value, list) else []


def _json_pre(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return _e(text)


def _cpt_tables_html(tables: Any) -> str:
    if not isinstance(tables, list) or not tables:
        return "<p>None recorded.</p>"
    parts: list[str] = []
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        node = _e(table.get("node_id", ""))
        parents = table.get("parent_ids") or []
        states = table.get("states") or []
        rows = table.get("rows") or []
        parts.append(f"<h5>Node {node} (parents: {_e(list(parents))})</h5>")
        parts.append('<table border="1"><thead><tr><th>Parent states</th>')
        for state in list(states):
            parts.append(f"<th>{_e(state)}</th>")
        parts.append("</tr></thead><tbody>")
        for row in list(rows) if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            parts.append("<tr>")
            parts.append(f"<td>{_e(list(row.get('parent_states') or []))}</td>")
            for pct in list(row.get("percentages") or []):
                parts.append(f"<td>{_e(pct)}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    return "".join(parts) if parts else "<p>None recorded.</p>"


def _posteriors_html(posteriors: Any) -> str:
    if not isinstance(posteriors, list) or not posteriors:
        return "<p>None recorded.</p>"
    parts = [
        '<table border="1"><thead><tr><th>Node</th><th>State</th>'
        "<th>Probability</th></tr></thead><tbody>"
    ]
    for post in posteriors:
        if not isinstance(post, Mapping):
            continue
        node = _e(post.get("node_id", ""))
        states = list(post.get("states") or [])
        probs = list(post.get("probabilities") or [])
        if not states:
            parts.append(f"<tr><td>{node}</td><td colspan='2'>None recorded.</td></tr>")
            continue
        for index, state in enumerate(states):
            prob = probs[index] if index < len(probs) else ""
            parts.append(f"<tr><td>{node}</td><td>{_e(state)}</td><td>{_e(prob)}</td></tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _adjustment_label(question: dict[str, Any]) -> tuple[str, str]:
    """(indicator, final-equals-original) for one frozen question.

    ``review_revision`` above the initial value proves adjustment history
    even when the final tables equal the original (adjusted-then-reset).
    Equality and history are reported as separate lines so a reset is never
    mistaken for "never adjusted".
    """
    status = str(question.get("status", ""))
    if status != "ready":
        return f"Not applicable ({status or 'unknown'})", "n/a"
    raw_baseline = question.get("original_baseline")
    baseline: dict[str, Any] = raw_baseline if isinstance(raw_baseline, dict) else {}
    original = baseline.get("validated_tables")
    current = question.get("current_tables")
    revision_id = question.get("current_cpt_revision_id")
    try:
        review_rev = int(question.get("review_revision", 1))
    except Exception:
        review_rev = 1
    equals = "yes" if current is not None and current == original else "no"
    if revision_id is None:
        return "Unchanged original — no physician adjustment", equals
    if current is not None and current == original:
        return (
            f"Adjusted then reset — final equals original; adjustment history retained "
            f"(review revision {review_rev})",
            equals,
        )
    return "Physician-adjusted — final differs from original", equals


def build_patient_report_html(data: dict[str, Any], *, generated_at: str) -> str:
    """Pure escaped HTML assembler over :func:`get_report_data` output.

    Every interpolated value is ``html``-escaped (markup stays inert,
    including ``<script>`` in names/notes/plans). Complete per-question
    original + accepted CPTs/results/recommendations, versions, adjustment
    indicators, and physician/timestamps are rendered; private drafts are
    excluded by construction (the input holds signed records only).
    """
    raw_patient = data.get("patient")
    patient: dict[str, Any] = raw_patient if isinstance(raw_patient, dict) else {}
    raw_signed = data.get("signed_encounters")
    signed: list[Any] = list(raw_signed) if isinstance(raw_signed, list) else []
    raw_snapshots = data.get("signed_snapshots")
    snapshots: list[Any] = list(raw_snapshots) if isinstance(raw_snapshots, list) else []
    raw_addenda = data.get("addenda")
    addenda: list[Any] = list(raw_addenda) if isinstance(raw_addenda, list) else []
    by_encounter: dict[str, list[dict[str, Any]]] = {}
    for entry in addenda:
        if isinstance(entry, dict):
            by_encounter.setdefault(str(entry.get("encounter_id", "")), []).append(dict(entry))

    out: list[str] = []
    out.append(
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Patient report {_e(patient.get('identifier', ''))}</title>"
        "<style>@media print{.encounter{page-break-before:always}"
        ".qtable{page-break-inside:auto}tr{page-break-inside:avoid}}</style>"
        "</head><body>"
    )
    out.append(
        "<p><strong>Research prototype — must not be used as the sole basis "
        "for treating patients.</strong></p>"
    )
    out.append(f"<h1>Patient report {_e(patient.get('identifier', ''))}</h1>")
    out.append('<h2>Current demographics</h2><table border="1"><tbody>')
    for key in (
        "identifier",
        "given_name",
        "family_name",
        "sex",
        "age",
        "clinical_status",
        "phone",
        "archived",
        "created_at",
        "updated_at",
    ):
        out.append(f"<tr><th>{_e(key)}</th><td>{_e(patient.get(key, ''))}</td></tr>")
    out.append("</tbody></table>")
    out.append(
        f"<p>Report generated at {_e(generated_at)}. Private drafts excluded — "
        "signed records only.</p>"
    )

    out.append("<h2>Signed chronology</h2>")
    if not signed:
        out.append("<p>No signed encounters.</p>")
    else:
        out.append("<ol>")
        for ref in signed:
            if not isinstance(ref, dict):
                continue
            out.append(
                f"<li>{_e(ref.get('kind', ''))} — signed record {_e(ref.get('id', ''))} "
                f"(created {_e(ref.get('created_at', ''))}, "
                f"revision {_e(ref.get('revision', ''))})</li>"
            )
        out.append("</ol>")

    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        inner = _as_dict(snap.get("snapshot"))
        encounter = _as_dict(inner.get("encounter"))
        proposal = _as_dict(inner.get("proposal"))
        questions = _as_list(inner.get("questions"))
        notes = _as_list(inner.get("notes"))
        draft_data = _as_dict(inner.get("draft_data"))
        enc_id = str(snap.get("encounter_id", ""))
        out.append('<div class="encounter">')
        out.append(f"<h2>Signed encounter {_e(enc_id)}</h2>")
        out.append(
            f"<p>Kind: {_e(encounter.get('kind', ''))} | "
            f"Signer: {_e(snap.get('signer_username', ''))} "
            f"({_e(snap.get('signer_id', ''))}) at {_e(snap.get('signed_at', ''))} | "
            f"Snapshot: {_e(snap.get('snapshot_hash', ''))} | "
            f"Encounter revision: {_e(snap.get('encounter_revision', ''))}</p>"
        )
        out.append(
            f"<h3>Secondary plan (revision {_e(snap.get('secondary_plan_revision', ''))})</h3>"
            f"<pre>{_e(snap.get('secondary_plan_text', ''))}</pre>"
        )
        out.append("<h3>Original proposal</h3>")
        sections = _as_list(proposal.get("sections"))
        if not sections:
            out.append("<p>No proposal sections recorded.</p>")
        for section in sections:
            if not isinstance(section, dict):
                continue
            out.append(
                f"<h4>Question {_e(section.get('question_key', ''))} (original recommendation)</h4>"
            )
            out.append(f"<pre>{_e(section.get('section_text', ''))}</pre>")
            out.append(_posteriors_html(section.get("posteriors")))
            out.append(
                f"<p>Network version: {_e(section.get('network_version', ''))} | "
                f"Template version: {_e(section.get('template_version', ''))} | "
                f"Query nodes: {_e(list(section.get('query_nodes') or []))}</p>"
            )
        skipped = _as_list(proposal.get("skipped"))
        if skipped:
            out.append("<h4>Skipped questions</h4><ul>")
            for entry in skipped:
                if isinstance(entry, dict):
                    out.append(
                        f"<li>{_e(entry.get('question_key', ''))}: "
                        f"{_e(entry.get('reason', ''))}</li>"
                    )
            out.append("</ul>")
        warnings = _as_list(proposal.get("coverage_warnings"))
        if warnings:
            out.append("<h4>DDI coverage warnings</h4><ul>")
            for warning in warnings:
                out.append(f"<li>{_e(warning)}</li>")
            out.append("</ul>")
        ddi = _as_dict(proposal.get("ddi_report"))
        pairs = _as_list(ddi.get("pairs"))
        if pairs:
            out.append(
                '<h4>DDI pairs</h4><table border="1"><thead><tr><th>Drug A</th>'
                "<th>Drug B</th><th>Status</th><th>Highest severity</th>"
                "<th>Coverage basis</th></tr></thead><tbody>"
            )
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                out.append(
                    "<tr><td>"
                    + _e(pair.get("drug_a", ""))
                    + "</td><td>"
                    + _e(pair.get("drug_b", ""))
                    + "</td><td>"
                    + _e(pair.get("status", ""))
                    + "</td><td>"
                    + _e(pair.get("highest_known_severity", ""))
                    + "</td><td>"
                    + _e(pair.get("coverage_basis", ""))
                    + "</td></tr>"
                )
            out.append("</tbody></table>")
        history_values: Any = None
        medications_values: Any = None
        if draft_data:
            history_section = _as_dict(draft_data.get("history"))
            if history_section:
                history_values = history_section.get("values", history_section)
            medications_section = _as_dict(draft_data.get("medications"))
            if medications_section:
                medications_values = medications_section.get("medications", medications_section)
        if history_values is not None or medications_values is not None:
            out.append("<h3>Signed clinical content (frozen)</h3>")
            if history_values is not None:
                out.append("<h4>History values</h4>")
                out.append(f"<pre>{_json_pre(history_values)}</pre>")
            if medications_values is not None:
                out.append("<h4>Medications</h4>")
                out.append(f"<pre>{_json_pre(medications_values)}</pre>")
        out.append('<div class="qtable"><h3>Clinical questions (original vs accepted)</h3>')
        if not questions:
            out.append("<p>No questions recorded.</p>")
        for question in questions:
            if not isinstance(question, dict):
                continue
            key = _e(question.get("question_key", ""))
            indicator, equals = _adjustment_label(question)
            original = _as_dict(question.get("original_baseline"))
            raw_result = question.get("current_result")
            current_result: dict[str, Any] | None = (
                raw_result if isinstance(raw_result, dict) else None
            )
            raw_acceptance = question.get("acceptance")
            acceptance: dict[str, Any] | None = (
                raw_acceptance if isinstance(raw_acceptance, dict) else None
            )
            out.append(f"<h4>Question {key} — status {_e(question.get('status', ''))}</h4>")
            if str(question.get("gate_reason", "")):
                out.append(f"<p>Gate reason: {_e(question.get('gate_reason', ''))}</p>")
            out.append(f"<p>Adjustment: {_e(indicator)}</p>")
            out.append(f"<p>Final equals original: {_e(equals)}</p>")
            out.append(
                f"<p>Pinned versions: {_json_pre(question.get('pinned_versions') or {})} | "
                f"Review revision: {_e(question.get('review_revision', ''))} | "
                f"Calculation state: {_e(question.get('calculation_state', ''))}</p>"
            )
            out.append("<h5>Saved patient inputs (projection)</h5>")
            out.append(f"<pre>{_json_pre(question.get('projection') or {})}</pre>")
            out.append("<h5>Original CPTs</h5>")
            out.append(_cpt_tables_html(original.get("validated_tables")))
            out.append("<h5>Original result</h5>")
            out.append(_posteriors_html(original.get("posteriors")))
            out.append(f"<pre>{_e(original.get('section_text', ''))}</pre>")
            out.append(
                f"<p>Original versions — network: {_e(original.get('network_version', ''))}, "
                f"template: {_e(original.get('template_version', ''))}, "
                f"prompt: {_e(original.get('prompt_version', ''))}</p>"
            )
            out.append("<h5>Final accepted CPTs</h5>")
            out.append(_cpt_tables_html(question.get("current_tables")))
            out.append("<h5>Final accepted result</h5>")
            if current_result is None:
                out.append("<p>Original result stands (unchanged or baseline-equal reset).</p>")
                out.append(_posteriors_html(original.get("posteriors")))
                out.append(f"<pre>{_e(original.get('section_text', ''))}</pre>")
            else:
                out.append(_posteriors_html(current_result.get("posteriors")))
                out.append(f"<pre>{_e(current_result.get('section_text', ''))}</pre>")
                out.append(
                    f"<p>Result versions — network: "
                    f"{_e(current_result.get('network_version', ''))}, "
                    f"template: {_e(current_result.get('template_version', ''))}</p>"
                )
            if acceptance is None:
                out.append("<p>Acceptance: none recorded.</p>")
            else:
                out.append(
                    f"<p>Accepted by {_e(acceptance.get('actor_username', ''))} at "
                    f"{_e(acceptance.get('created_at', ''))} "
                    f"(result {_e(acceptance.get('result_kind', ''))})</p>"
                )
        out.append("</div>")
        if notes:
            out.append("<h3>Signed notes (frozen)</h3><ul>")
            for note in notes:
                if not isinstance(note, dict):
                    continue
                out.append(
                    f"<li>[{_e(note.get('page', ''))}] {_e(note.get('author_display', ''))} "
                    f"at {_e(note.get('created_at', ''))}: {_e(note.get('text', ''))}</li>"
                )
            out.append("</ul>")
        enc_addenda = by_encounter.get(enc_id, [])
        if enc_addenda:
            out.append("<h3>Addenda</h3><ul>")
            for entry in enc_addenda:
                out.append(
                    f"<li>{_e(entry.get('author_display', ''))} "
                    f"at {_e(entry.get('created_at', ''))}: {_e(entry.get('text', ''))}</li>"
                )
            out.append("</ul>")
        out.append("</div>")
    out.append("</body></html>")
    return "".join(out)
