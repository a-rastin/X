"""Package C: signed-source resolution and frozen baseline adapters (pure).

Synthetic signed snapshots cover legacy v1-v3, New case v4, follow-up v5 and an
unsupported variant. A tiny fake session stands in for the two SQL reads; the
real PostgreSQL ordering/concurrency checks live in the HTTP suite.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from x_insight import contracts
from x_insight.cases import follow_up as f
from x_insight.cases import follow_up_baseline as b
from x_insight.ddi.lexicomp import drug_id

PATIENT = {"sex": "F", "age": 30, "clinical_status": "established", "birth_year": 1990}
VERSION = "lexicomp-v1"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Emulates ``ORDER BY signed_at DESC, id DESC`` for the snapshot query."""

    def __init__(self, snapshot_rows=None, concept_rows=None):
        self.snapshot_rows = snapshot_rows or []
        self.concept_rows = concept_rows or []

    def execute(self, statement, params=None):
        if params is not None and "version" in params:
            return _Result(self.concept_rows)
        ordered = sorted(
            self.snapshot_rows,
            key=lambda row: (row["signed_at"], str(row["id"])),
            reverse=True,
        )
        return _Result(ordered)


def _snapshot(schema, draft_data, *, workflow="legacy-v1", kind="follow_up"):
    return {
        "schema_version": schema,
        "workflow_version": workflow,
        "encounter": {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "workflow_version": workflow,
            "created_at": "2026-05-01T10:00:00Z",
        },
        "patient": {"sex": "F"},
        "draft_data": draft_data,
        "secondary_plan": {"clozapine_included": "unknown"},
        "proposal": {"payload": {"sections": {}}},
    }


def _row(schema, draft_data, *, variant="legacy", signed_at=None, **overrides):
    snapshot = _snapshot(schema, draft_data)
    row = {
        "id": uuid.uuid4(),
        "encounter_id": uuid.uuid4(),
        "patient_id": uuid.uuid4(),
        "snapshot": snapshot,
        "snapshot_hash": "a" * 64,
        "signed_at": signed_at or contracts.utcnow(),
        "batch_id": None,
        "proposal_id": None,
        "new_case_proposal_id": None,
        "follow_up_proposal_id": None,
    }
    if variant == "legacy":
        row["batch_id"] = uuid.uuid4()
        row["proposal_id"] = uuid.uuid4()
    elif variant == "new-case-v4":
        row["new_case_proposal_id"] = uuid.uuid4()
    elif variant == "follow-up-v5":
        row["follow_up_proposal_id"] = uuid.uuid4()
    row.update(overrides)
    return row


def _source(row, ordinal=2):
    return {"row": row, "ordinal": ordinal, "signed_count": ordinal - 1}


# --- Variant classification ---


def test_classify_variant_covers_all_supported_signatures():
    for schema, variant, expected in (
        ("signed-encounter-v1", "legacy", "legacy"),
        ("signed-encounter-v2", "legacy", "legacy"),
        ("signed-encounter-v3", "legacy", "legacy"),
        ("signed-encounter-v4", "new-case-v4", "new-case-v4"),
        ("signed-encounter-v5", "follow-up-v5", "follow-up-v5"),
    ):
        assert b.classify_variant(_row(schema, {}, variant=variant)) == expected
    assert b.classify_variant(_row("signed-encounter-v9", {}, variant="legacy")) == "unsupported"
    # A mismatched reference column fails closed too.
    assert b.classify_variant(_row("signed-encounter-v4", {}, variant="legacy")) == "unsupported"


def test_no_prior_signed_visit_is_a_valid_empty_case():
    baseline = b.project_baseline(None, None, PATIENT, lexicomp_version=VERSION)
    assert baseline["has_source"] is False
    assert baseline["source_status"] == "no_prior_signed_visit"
    assert baseline["source"] is None
    assert baseline["carry"] == {}
    assert baseline["unmatched_medications"] == []
    assert baseline["prior_labs"] == []
    draft = f.fresh_draft(2026, {}, baseline)
    assert draft["follow_up"]["baseline"]["summary"] == {}


def test_latest_signed_source_uses_stable_id_tie_break_and_ordinal():
    moment = contracts.utcnow()
    rows = [
        _row("signed-encounter-v3", {}, signed_at=moment - timedelta(days=2)),
        _row("signed-encounter-v4", {}, variant="new-case-v4", signed_at=moment),
        _row("signed-encounter-v4", {}, variant="new-case-v4", signed_at=moment),
    ]
    source = b.latest_signed_source(_Session(snapshot_rows=rows), uuid.uuid4())
    assert source is not None
    assert source["ordinal"] == 4  # number of prior signed encounters + 1
    assert source["signed_count"] == 3
    # Two rows share signed_at: the larger id wins (stable, deterministic).
    tied = [r for r in rows if r["signed_at"] == moment]
    assert source["row"]["id"] == max(tied, key=lambda r: str(r["id"]))["id"]
    assert b.latest_signed_source(_Session(snapshot_rows=[]), uuid.uuid4()) is None


# --- New case v4 adapter ---


def _v4_draft():
    return {
        "new_case": {
            "harm": {"self": True, "others": False},
            "heart_failure": True,
            "tobacco": False,
            "psychiatric": {"present": True, "conditions": ["depression"], "other_text": "note"},
            "substances": {
                "present": True,
                "marijuana": True,
                "other": True,
                "other_text": "kept verbatim",
            },
            "bmi": {
                "height_cm": "170",
                "weight_kg": "80",
                "source": "calculated",
                "manual_category": None,
            },
            "conditions": {
                name: {"manual": value, "manual_actor_id": None, "manual_recorded_at": None}
                for name, value in (
                    ("hepatic_failure", True),
                    ("renal_failure", None),
                    ("glucose_abnormality", False),
                    ("hyperlipidemia", None),
                    ("hyperprolactinemia", None),
                    ("pregnant", None),
                )
            },
            "breastfeeding": False,
            "focal_neurological_deficit": False,
            "suspected_brain_lesion": False,
            "suspected_autoimmune_encephalitis": False,
            "considerations": {"seizure_risk": True, "qtc_risk": False},
            "labs": {"present": True, "values": {"prolactin": "30"}},
        },
        "panss": {"answers": {}},
        "cssrs": {"answers": {f"css_i{i}_recent": "no" for i in range(1, 6)}},
        "medications": {"entries": [{"catalog_drug_id": "oral:risperidone"}]},
    }


def test_new_case_v4_carries_only_equivalent_structured_fields():
    row = _row("signed-encounter-v4", _v4_draft(), variant="new-case-v4")
    session = _Session(concept_rows=[("oral:risperidone", "Risperidone")])
    baseline = b.project_baseline(session, _source(row), PATIENT, lexicomp_version=VERSION)
    assert baseline["source_status"] == "available"
    assert baseline["source"]["variant"] == "new-case-v4"
    carry = baseline["carry"]
    assert carry["heart_failure"] is True and carry["tobacco"] is False
    assert carry["psychiatric"]["conditions"] == ["depression"]
    assert carry["substances"]["other_text"] == "kept verbatim"
    # New Case's "Other" is not reclassified into newly named categories.
    assert carry["substances"]["opioids"] is False
    assert carry["substances"]["methamphetamine"] is False
    assert carry["conditions"]["hepatic_failure"] is True
    assert carry["conditions"]["renal_failure"] is None
    assert carry["seizure_risk"] is True and carry["qtc_risk"] is False
    assert carry["bmi"]["height_cm"] == "170"
    assert carry["medications"] == [{"catalog_drug_id": "oral:risperidone"}]
    assert carry["selection_dataset_version"] == VERSION
    # Labs become dated prior records, never today's lab entry.
    assert baseline["prior_labs"][0]["field"] == "prolactin"
    assert baseline["prior_labs"][0]["visit_date"] == row["snapshot"]["encounter"]["created_at"]
    assert "labs" not in carry
    # No prior assessment answer, harm flag, effect or free-text plan is copied.
    for forbidden in ("harm", "effects", "prior_plan", "current_antidepressant", "panss", "cssrs"):
        assert forbidden not in carry
    assert "new_case.heart_failure" in baseline["inherited"]
    assert baseline["inherited"]["new_case.heart_failure"]["snapshot_hash"] == row["snapshot_hash"]
    # Per-field provenance carries the source encounter/hash/date.
    assert baseline["inherited"]["new_case.conditions.hepatic_failure"]["source_encounter_id"]


def test_fresh_draft_from_v4_baseline_is_valid_typed_state():
    row = _row("signed-encounter-v4", _v4_draft(), variant="new-case-v4")
    baseline = b.project_baseline(
        _Session(concept_rows=[("oral:risperidone", "Risperidone")]),
        _source(row),
        PATIENT,
        lexicomp_version=VERSION,
    )
    draft = f.fresh_draft(2026, {}, baseline)
    f.structural_draft(draft)
    assert draft["follow_up"]["history"]["heart_failure"] is True
    assert draft["follow_up"]["history"]["conditions"]["hepatic_failure"]["inherited"] is True
    assert draft["follow_up"]["history"]["labs"]["values"] == {}  # fresh labs
    assert draft["panss"]["answers"] == {}  # fresh PANSS


# --- Follow-up v5 adapter (v5-after-v5) ---


def _v5_draft():
    fresh = f.fresh_draft(
        2026, {}, b.project_baseline(None, None, PATIENT, lexicomp_version=VERSION)
    )
    fu = fresh["follow_up"]
    h = fu["history"]
    h["heart_failure"] = True
    h["tobacco"] = True
    h["pregnant"] = False
    h["breastfeeding"] = False
    h["hospitalization_count"] = 2
    h["psychiatric"] = {"present": True, "conditions": ["ptsd"], "other_text": ""}
    h["current_antidepressant"] = False
    h["labs"] = {"present": True, "values": {"ldl": "130"}}
    h["conditions"]["hyperlipidemia"]["inherited"] = True
    fu["considerations"]["seizure_risk"] = True
    fu["considerations"]["qtc_risk"] = False
    fu["treatment"] = {
        "main_catalog_drug_id": drug_id("Olanzapine"),
        "formulation": "oral",
    }
    fresh["medications"]["entries"] = [{"catalog_drug_id": drug_id("Olanzapine")}]
    return fresh


def test_follow_up_v5_carry_forward_keeps_fresh_labs_and_valid_main():
    row = _row("signed-encounter-v5", _v5_draft(), variant="follow-up-v5")
    baseline = b.project_baseline(
        _Session(
            concept_rows=[
                (
                    drug_id("Olanzapine"),
                    "Olanzapine",
                )
            ]
        ),
        _source(row),
        PATIENT,
        lexicomp_version=VERSION,
    )
    carry = baseline["carry"]
    assert carry["heart_failure"] is True
    assert carry["hospitalization_count"] == 2
    assert carry["conditions"]["hyperlipidemia"] is True
    assert carry["conditions"]["hepatic_failure"] is None
    assert carry["seizure_risk"] is True
    assert carry["main_catalog_drug_id"] == drug_id("Olanzapine")
    assert carry["formulation"] == "oral"
    assert "labs" not in carry  # prior labs are frozen separately
    assert baseline["prior_labs"][0]["field"] == "ldl"
    assert "current_antidepressant" not in carry  # fresh conditional question
    fresh = f.fresh_draft(2026, {}, baseline)
    assert fresh["follow_up"]["history"]["labs"]["values"] == {}
    assert fresh["follow_up"]["history"]["current_antidepressant"] is None
    assert fresh["follow_up"]["history"]["hospitalization_count"] == 2


def test_v5_main_selection_dropped_when_not_valid_in_current_release():
    row = _row("signed-encounter-v5", _v5_draft(), variant="follow-up-v5")
    baseline = b.project_baseline(_Session(), _source(row), PATIENT, lexicomp_version=VERSION)
    assert baseline["carry"]["main_catalog_drug_id"] is None
    assert baseline["carry"]["formulation"] is None
    assert baseline["unmatched_medications"][0]["status"] == "pending"
    assert baseline["unmatched_medications"][0]["source_id"] == drug_id("Olanzapine")
    draft = f.fresh_draft(2026, {}, baseline)
    assert draft["medications"]["entries"] == []  # never an invalid current id
    assert draft["medications"]["pending"][0]["resolution"] == "pending"


# --- Legacy adapter and fail-closed behaviour ---


def test_legacy_adapter_does_not_guess_from_workflow_tristates():
    legacy = {
        "history": {
            "values": {
                "candidate/history/pharmacotherapy.v2/h_substance_contribution": "Present",
                "h_prior_response": "no_response",
            }
        },
        "panss": {"answers": {}},
        "cssrs": {"answers": {}},
        "medications": {"entries": [{"catalog_drug_id": "legacy:haloperidol"}]},
    }
    row = _row("signed-encounter-v3", legacy, variant="legacy")
    baseline = b.project_baseline(_Session(), _source(row), PATIENT, lexicomp_version=VERSION)
    assert baseline["source"]["variant"] == "legacy"
    # Only medication identity (unmatched) and prior scores survive; retired BN
    # tristates are never guessed into clinical booleans.
    assert "heart_failure" not in baseline["carry"]
    assert "psychiatric" not in baseline["carry"]
    assert "conditions" not in baseline["carry"]
    assert baseline["carry"]["medications"] == []
    assert baseline["unmatched_medications"][0]["status"] == "pending"
    assert baseline["summary"]["comorbidities"] == []
    assert baseline["prior_scores"]["panss_total"] is None
    # Missing history is "not recorded", never a computed zero/no-risk score.
    assert baseline["prior_scores"]["cssrs_severity"] is None


def test_unsupported_variant_fails_closed_without_silent_fallback():
    row = _row("signed-encounter-v9", _v4_draft(), variant="legacy")
    baseline = b.project_baseline(_Session(), _source(row), PATIENT, lexicomp_version=VERSION)
    assert baseline["has_source"] is True
    assert baseline["source_status"] == "unsupported_variant"
    assert baseline["carry"] == {}
    assert baseline["summary"]["note"] == "unsupported_signed_variant"


def test_baseline_projection_stays_within_draft_bounds():
    row = _row("signed-encounter-v4", _v4_draft(), variant="new-case-v4")
    baseline = b.project_baseline(_Session(), _source(row), PATIENT, lexicomp_version=VERSION)
    draft = f.fresh_draft(2026, {}, baseline)
    from x_insight.cases import encounters

    encounters.validate_draft_data(draft)  # node/depth/byte bounds
    assert len(contracts.canonical_json(draft)) < 20_000
