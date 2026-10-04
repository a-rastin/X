"""Pure assessment evaluation (S08, seam T2; plan.md §5, FR-11–13).

No I/O, no database: :func:`load_definition` validates a versioned
definition dict and :func:`evaluate` maps ``(definition, answers)`` to::

    {status, missing_item_ids, item_errors, scores, findings, definition_version}

Statuses distinguish ``unanswered`` (nothing answered), ``partial`` (some
required input missing or any answer error), ``complete`` (every required
item validly answered), ``not_assessed`` (explicit ``__skipped`` Skip — never
zero/minimum), and ``bypassed`` (diagnosis-only explicit ``__bypass``).
Observed negatives (``"no"`` / ``1`` = absent) are always distinct from
unanswered: a missing answer suppresses its aggregates to null, it never
becomes zero.

Rule model: a small allowlisted declarative rule set per versioned
instrument plus explicit typed implementations below — not a general rules
platform. No ``eval``/``exec``: any ``expression``/``eval``/``exec`` key
anywhere in a definition is a :class:`DefinitionError`, as is an unknown
instrument, value type, completion rule, or result-rule operator.
"""

from __future__ import annotations

from typing import Any

SKIP_KEY = "__skipped"
BYPASS_KEY = "__bypass"

_ALLOWED_INSTRUMENTS = ("synthetic", "diagnosis", "panss", "cssrs")

_ALLOWED_VALUE_TYPES = frozenset(
    {
        "yes_no",
        "yes_no_unknown",
        "scale_1_7",
        "frequency_1_5",
        "duration_1_5",
        "control_0_5",
        "deterrent_0_5",
        "reason_0_5",
        "lethality_0_5",
        "potential_0_2",
    }
)

_ALLOWED_TOP_KEYS = frozenset(
    {
        "instrument",
        "id",
        "version",
        "title",
        "description",
        "items",
        "completion_rules",
        "result_rules",
        "periods",
        "source",
        "release",
    }
)

_ALLOWED_ITEM_KEYS = frozenset(
    {
        "id",
        "prompt",
        "value_type",
        "required",
        "required_if",
        "source_locator",
        "experimental_paraphrase",
    }
)

_ALLOWED_CONDITION_OPS = frozenset({"eq", "any_of", "all_of"})

_ALLOWED_COMPLETION_TYPES = frozenset({"all_required"})

# Per-instrument allowlisted result-rule operators (validated at load; the
# typed ``_results_*`` implementations below give them meaning).
_ALLOWED_RESULT_OPS: dict[str, frozenset[str]] = {
    "synthetic": frozenset({"count"}),
    "diagnosis": frozenset(
        {
            "criterion_a",
            "criterion_b",
            "criterion_c",
            "criterion_d",
            "criterion_e",
            "criterion_f",
            "all_criteria",
        }
    ),
    "panss": frozenset({"sum_subset", "total"}),
    "cssrs": frozenset(
        {
            "max_endorsed",
            "intensity_separate",
            "behavior_separate",
            "lethality_separate",
            "flag_recent_45_or_behavior",
            "flag_any_13_or_history",
        }
    ),
}

_FORBIDDEN_KEYS = frozenset(
    {
        "expression",
        "eval",
        "exec",
        "script",
        "code",
        "lambda",
        "function",
        "python",
        "javascript",
        "__import__",
    }
)


class DefinitionError(ValueError):
    """A definition is structurally or semantically invalid (load-time)."""


def _forbidden_scan(node: Any) -> None:
    """Reject arbitrary executable expressions anywhere in a definition."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_KEYS:
                raise DefinitionError(f"executable construct {key!r} is not allowed")
            _forbidden_scan(value)
    elif isinstance(node, list):
        for entry in node:
            _forbidden_scan(entry)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise DefinitionError(message)


def _validate_condition(condition: Any, item_ids: set[str], where: str) -> None:
    _require(isinstance(condition, dict), f"{where} must be an object")
    op = condition.get("op")
    _require(op in _ALLOWED_CONDITION_OPS, f"{where} has unknown operator {op!r}")
    if op == "eq":
        _require(set(condition) == {"op", "item", "value"}, f"{where} 'eq' shape invalid")
        _require(condition["item"] in item_ids, f"{where} references undeclared item")
    else:
        _require(set(condition) == {"op", "conditions"}, f"{where} {op!r} shape invalid")
        _require(
            isinstance(condition["conditions"], list) and condition["conditions"],
            f"{where} {op!r} needs a nonempty condition list",
        )
        for sub in condition["conditions"]:
            _validate_condition(sub, item_ids, where)


def load_definition(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a definition dict; return it unchanged when valid.

    Raises :class:`DefinitionError` for unknown instruments, value types,
    completion/result operators, dangling item references, duplicate item
    IDs, and arbitrary executable expressions.
    """
    _require(isinstance(raw, dict), "definition must be an object")
    _forbidden_scan(raw)
    unknown_top = set(raw) - _ALLOWED_TOP_KEYS
    _require(not unknown_top, f"unknown top-level keys: {sorted(unknown_top)}")
    instrument = raw.get("instrument")
    _require(instrument in _ALLOWED_INSTRUMENTS, f"unknown instrument {instrument!r}")
    assert instrument is not None
    _require(isinstance(raw.get("id"), str) and raw["id"], "definition needs a string id")
    _require(
        isinstance(raw.get("version"), str) and raw["version"],
        "definition needs a string version",
    )
    items = raw.get("items")
    _require(isinstance(items, list) and items, "definition needs a nonempty item list")
    assert isinstance(items, list)
    seen: set[str] = set()
    for item in items:
        _require(isinstance(item, dict), "each item must be an object")
        unknown_item = set(item) - _ALLOWED_ITEM_KEYS
        _require(not unknown_item, f"unknown item keys: {sorted(unknown_item)}")
        item_id = item.get("id")
        _require(isinstance(item_id, str) and item_id, "each item needs a string id")
        _require(item_id not in seen, f"duplicate item id {item_id!r}")
        _require(not item_id.startswith("__"), f"reserved item id {item_id!r}")
        seen.add(item_id)
        _require(
            item.get("value_type") in _ALLOWED_VALUE_TYPES,
            f"item {item_id!r} has unknown value type {item.get('value_type')!r}",
        )
        _require(
            isinstance(item.get("prompt"), str) and item["prompt"],
            f"item {item_id!r} needs a prompt",
        )
        required = item.get("required", False)
        required_if = item.get("required_if")
        _require(isinstance(required, bool), f"item {item_id!r} 'required' must be boolean")
        if required:
            _require(
                required_if is None, f"item {item_id!r} cannot combine required with required_if"
            )
        if required_if is not None:
            _validate_condition(required_if, seen_or_all(items), f"item {item_id!r} required_if")
    # ``required_if`` may forward-reference items declared later, so validate
    # references against the full id set in a second pass.
    for item in items:
        if item.get("required_if") is not None:
            _validate_condition(item["required_if"], seen, f"item {item['id']!r} required_if")
    completion = raw.get("completion_rules")
    _require(isinstance(completion, dict), "completion_rules must be an object")
    assert isinstance(completion, dict)
    _require(
        completion.get("type") in _ALLOWED_COMPLETION_TYPES,
        f"unknown completion rule {completion.get('type')!r}",
    )
    rules = raw.get("result_rules")
    _require(isinstance(rules, list) and rules, "result_rules must be a nonempty list")
    assert isinstance(rules, list)
    allowed_ops = _ALLOWED_RESULT_OPS[instrument]
    for rule in rules:
        _require(isinstance(rule, dict), "each result rule must be an object")
        _require(rule.get("op") in allowed_ops, f"unknown rule operator {rule.get('op')!r}")
    return raw


def seen_or_all(items: list[dict[str, Any]]) -> set[str]:
    """Item ids visible so far (first pass); full set is checked afterwards."""
    return {
        item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


# --- Answer validation ---


def _valid_value(value_type: str, value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if value_type in ("yes_no",):
        return value in ("yes", "no")
    if value_type == "yes_no_unknown":
        return value in ("yes", "no", "unknown")
    ranges = {
        "scale_1_7": (1, 7),
        "frequency_1_5": (1, 5),
        "duration_1_5": (1, 5),
        "control_0_5": (0, 5),
        "deterrent_0_5": (0, 5),
        "reason_0_5": (0, 5),
        "lethality_0_5": (0, 5),
        "potential_0_2": (0, 2),
    }
    low, high = ranges[value_type]
    return isinstance(value, int) and low <= value <= high


def _condition_holds(condition: dict[str, Any], answers: dict[str, Any]) -> bool:
    op = condition["op"]
    if op == "eq":
        return answers.get(condition["item"]) == condition["value"]
    if op == "any_of":
        return any(_condition_holds(sub, answers) for sub in condition["conditions"])
    return all(_condition_holds(sub, answers) for sub in condition["conditions"])


def _required_ids(definition: dict[str, Any], valid: dict[str, Any]) -> list[str]:
    """Required item ids in declaration order (conditional on valid answers)."""
    required: list[str] = []
    for item in definition["items"]:
        if item.get("required", False):
            required.append(item["id"])
        elif item.get("required_if") is not None and _condition_holds(item["required_if"], valid):
            required.append(item["id"])
    return required


# --- Instrument result implementations (typed per versioned instrument) ---


def _null_scores(instrument: str) -> dict[str, Any]:
    if instrument == "synthetic":
        return {"yes_count": None}
    if instrument == "panss":
        return {"positive": None, "negative": None, "general": None, "total": None}
    if instrument == "cssrs":
        return {
            "ideation_severity_recent": None,
            "ideation_severity_lifetime": None,
            "ideation_severity_max": None,
        }
    return {}


def _results_synthetic(definition: dict[str, Any], valid: dict[str, Any]) -> tuple[dict, dict]:
    counts: dict[str, Any] = {}
    for rule in definition["result_rules"]:
        counts[rule["as"]] = sum(
            1 for item_id in rule["of"] if valid.get(item_id) == rule["equals"]
        )
    endorsed = sorted(item_id for item_id, value in valid.items() if value == "yes")
    return counts, {"endorsed": endorsed}


def _results_panss(definition: dict[str, Any], valid: dict[str, Any]) -> tuple[dict, dict]:
    del definition  # subscales are fixed by the versioned instrument, not the JSON
    positive_ids = [f"P{i}" for i in range(1, 8)]
    negative_ids = [f"N{i}" for i in range(1, 8)]
    general_ids = [f"G{i}" for i in range(1, 17)]

    def _subscale(ids: list[str]) -> int | None:
        values = [valid[item_id] for item_id in ids if isinstance(valid.get(item_id), int)]
        if len(values) != len(ids):
            return None
        return sum(values)

    positive = _subscale(positive_ids)
    negative = _subscale(negative_ids)
    general = _subscale(general_ids)
    total: int | None = None
    if positive is not None and negative is not None and general is not None:
        total = positive + negative + general
    scores = {"positive": positive, "negative": negative, "general": general, "total": total}
    findings: dict[str, Any] = {"assessment_window": "previous_7_days"}
    if total is not None:
        if total <= 57:
            band = {"range": "30-57", "label": "Below the commonly cited mild-illness anchor"}
        elif total <= 74:
            band = {"range": "58-74", "label": "Mildly ill"}
        elif total <= 94:
            band = {"range": "75-94", "label": "Moderately ill"}
        elif total <= 115:
            band = {"range": "95-115", "label": "Markedly ill"}
        else:
            band = {"range": ">=116", "label": "Severely ill"}
        findings["total_band"] = {
            **band,
            "informational_only": True,
            "not_a_treatment_gate": True,
        }
    return scores, findings


def _tri(value: Any) -> bool | None:
    """Three-valued mapping: yes→True, no→False, unknown/missing→None."""
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _kleene_and(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is True and right is True:
        return True
    return None


def _kleene_or(left: bool | None, right: bool | None) -> bool | None:
    if left is True or right is True:
        return True
    if left is False and right is False:
        return False
    return None


def _kleene_not(value: bool | None) -> bool | None:
    return None if value is None else (not value)


def _state(value: bool | None) -> str:
    return "met" if value is True else ("not_met" if value is False else "unknown")


def _results_diagnosis(definition: dict[str, Any], valid: dict[str, Any]) -> tuple[dict, dict]:
    del definition  # criterion logic is fixed by the versioned instrument
    core = ["a_delusions", "a_hallucinations", "a_disorganized_speech"]
    all_a = core + ["a_disorganized_behavior", "a_negative_symptoms"]
    yes_a = sum(1 for item_id in all_a if valid.get(item_id) == "yes")
    core_yes = sum(1 for item_id in core if valid.get(item_id) == "yes")
    unknown_a = sum(1 for item_id in all_a if valid.get(item_id) == "unknown")
    unknown_core = sum(1 for item_id in core if valid.get(item_id) == "unknown")
    if yes_a >= 2 and core_yes >= 1:
        criterion_a: bool | None = True
    elif yes_a + unknown_a < 2 or core_yes + unknown_core < 1:
        criterion_a = False
    else:
        criterion_a = None
    criterion_b = _tri(valid.get("b_functional_decline"))
    criterion_c = _kleene_and(
        _tri(valid.get("c_six_months")),
        _kleene_or(
            _tri(valid.get("c_active_month")), _tri(valid.get("c_shortened_by_intervention"))
        ),
    )
    criterion_d = _tri(valid.get("d_mood_exclusion"))
    criterion_e = _tri(valid.get("e_substance_medical_exclusion"))
    criterion_f = _kleene_or(
        _kleene_not(_tri(valid.get("f_autism_present"))),
        _tri(valid.get("f_prominent_psychosis")),
    )
    criteria = {
        "A": _state(criterion_a),
        "B": _state(criterion_b),
        "C": _state(criterion_c),
        "D": _state(criterion_d),
        "E": _state(criterion_e),
        "F": _state(criterion_f),
    }
    states = [criterion_a, criterion_b, criterion_c, criterion_d, criterion_e, criterion_f]
    if all(state is True for state in states):
        overall: str | None = "criteria_satisfied"
    elif any(state is False for state in states):
        overall = "below_threshold"
    else:
        overall = "indeterminate"
    findings = {
        "criteria": criteria,
        "overall": overall,
        "symptom_count_alone_insufficient": True,
    }
    return {}, findings


_CSSRS_IDEATION_LEVELS = (1, 2, 3, 4, 5)
_CSSRS_BEHAVIOR = ("actual", "interrupted", "aborted", "preparatory", "nssi")
_CSSRS_SUICIDAL_BEHAVIOR = ("actual", "interrupted", "aborted", "preparatory")


def _results_cssrs(definition: dict[str, Any], valid: dict[str, Any]) -> tuple[dict, dict]:
    del definition  # severity/flag logic is fixed by the versioned instrument
    recent = [
        level for level in _CSSRS_IDEATION_LEVELS if valid.get(f"css_i{level}_recent") == "yes"
    ]
    lifetime = [
        level for level in _CSSRS_IDEATION_LEVELS if valid.get(f"css_i{level}_lifetime") == "yes"
    ]
    severity_recent = max(recent) if recent else 0
    severity_lifetime = max(lifetime) if lifetime else 0
    scores = {
        "ideation_severity_recent": severity_recent,
        "ideation_severity_lifetime": severity_lifetime,
        "ideation_severity_max": max(severity_recent, severity_lifetime),
    }
    # Intensity, behavior, and lethality stay separate: no composite score.
    intensity = (
        {
            "frequency": valid.get("css_frequency"),
            "duration": valid.get("css_duration"),
            "controllability": valid.get("css_controllability"),
            "deterrents": valid.get("css_deterrents"),
            "reasons": valid.get("css_reasons"),
        }
        if any(valid.get(f"css_i{level}_recent") == "yes" for level in _CSSRS_IDEATION_LEVELS)
        else None
    )
    behavior = {
        window: {
            category: valid.get(f"css_beh_{category}_{window}") for category in _CSSRS_BEHAVIOR
        }
        for window in ("recent", "lifetime")
    }
    lethality = {
        window: {
            "actual": valid.get(f"css_leth_actual_{window}"),
            "potential": valid.get(f"css_leth_potential_{window}"),
        }
        for window in ("recent", "lifetime")
    }
    flags: list[str] = []
    any_ideation = bool(recent or lifetime)
    any_history_behavior = any(
        valid.get(f"css_beh_{category}_lifetime") == "yes" for category in _CSSRS_SUICIDAL_BEHAVIOR
    ) or any(valid.get(f"css_beh_nssi_{window}") == "yes" for window in ("recent", "lifetime"))
    recent_high_ideation = severity_recent >= 4
    recent_behavior = any(
        valid.get(f"css_beh_{category}_recent") == "yes" for category in _CSSRS_SUICIDAL_BEHAVIOR
    )
    if not any_ideation and not any_history_behavior and not recent_behavior:
        flags.append("no_positive_items")
    if any_ideation or any_history_behavior or recent_behavior:
        flags.append("clinical_review")
    if recent_high_ideation or recent_behavior:
        flags.append("high_risk_alert")
    findings = {
        "intensity": intensity,
        "behavior": behavior,
        "lethality": lethality,
        "flags": flags,
        "windows": {
            "recent_ideation": "past_month",
            "recent_behavior": "past_3_months",
            "lifetime": "lifetime",
        },
        "notes": [
            "Ideation severity is the highest endorsed level; lower levels are never inferred.",
            "Intensity, behavior, and lethality stay separate; no composite score.",
            "Immediate emergency concern (current intent/plan, attempt in progress, "
            "inability to stay safe) is clinician-determined, never a calculated flag.",
            "A negative screen does not prove absence of risk.",
        ],
    }
    return scores, findings


_RESULTS = {
    "synthetic": _results_synthetic,
    "diagnosis": _results_diagnosis,
    "panss": _results_panss,
    "cssrs": _results_cssrs,
}


def evaluate(definition: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Evaluate validated answers against a loaded definition.

    Answer-side problems (undeclared item IDs, invalid values) are reported
    in ``item_errors`` with aggregates suppressed — never raised, never
    zero-filled. Definition-side problems raise :class:`DefinitionError` at
    :func:`load_definition` time.
    """
    if not isinstance(answers, dict):
        raise DefinitionError("answers must be an object")
    instrument = definition["instrument"]
    items = {item["id"]: item for item in definition["items"]}

    skipped = answers.get(SKIP_KEY) is True
    bypassed = answers.get(BYPASS_KEY) is True
    item_answers = {key: value for key, value in answers.items() if not key.startswith("__")}
    unknown_reserved = [
        key for key in answers if key.startswith("__") and key not in (SKIP_KEY, BYPASS_KEY)
    ]

    item_errors: dict[str, list[str]] = {}
    for key in unknown_reserved:
        item_errors[key] = ["Unknown reserved answer key."]
    for key in item_answers:
        if key not in items:
            item_errors[key] = ["Unknown item."]
        elif not _valid_value(items[key]["value_type"], item_answers[key]):
            item_errors[key] = [f"Invalid value for {items[key]['value_type']}."]
    valid = {
        key: value for key, value in item_answers.items() if key in items and key not in item_errors
    }

    if skipped and (bypassed or item_answers or unknown_reserved):
        item_errors.setdefault(SKIP_KEY, []).append("Skip must not accompany other answers.")
    if bypassed and instrument != "diagnosis":
        item_errors.setdefault(BYPASS_KEY, []).append("Bypass is only supported for diagnosis.")
    elif bypassed and item_answers:
        item_errors.setdefault(BYPASS_KEY, []).append("Bypass must not accompany item answers.")

    if not item_errors:
        if skipped:
            return {
                "status": "not_assessed",
                "missing_item_ids": [
                    item["id"] for item in definition["items"] if item.get("required")
                ],
                "item_errors": {},
                "scores": _null_scores(instrument),
                "findings": {"skipped": True},
                "definition_version": definition["version"],
            }
        if bypassed:
            return {
                "status": "bypassed",
                "missing_item_ids": [],
                "item_errors": {},
                "scores": {},
                "findings": {"bypassed": True},
                "definition_version": definition["version"],
            }

    required = _required_ids(definition, valid)
    missing = [item_id for item_id in required if item_id not in valid]
    if item_errors or missing:
        status = "unanswered" if (not valid and not item_errors) else "partial"
        if status == "partial" and not item_errors and instrument == "panss":
            # PANSS reports each finished subscale while the total stays
            # suppressed (a missing answer is null, never zero). Diagnosis
            # and C-SSRS report nothing until complete: a partial verdict
            # there would mislabel incomplete work.
            scores, findings = _results_panss(definition, valid)
            scores["total"] = None
            findings.pop("total_band", None)
            return {
                "status": status,
                "missing_item_ids": missing,
                "item_errors": item_errors,
                "scores": scores,
                "findings": findings,
                "definition_version": definition["version"],
            }
        return {
            "status": status,
            "missing_item_ids": missing,
            "item_errors": item_errors,
            "scores": _null_scores(instrument),
            "findings": {},
            "definition_version": definition["version"],
        }
    scores, findings = _RESULTS[instrument](definition, valid)
    return {
        "status": "complete",
        "missing_item_ids": [],
        "item_errors": {},
        "scores": scores,
        "findings": findings,
        "definition_version": definition["version"],
    }
