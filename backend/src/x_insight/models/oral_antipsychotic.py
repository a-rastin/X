"""Fully observed, hash-pinned experimental oral model; not clinical validation."""

from __future__ import annotations

import hashlib
import math
from array import array
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache, partial

from lxml import etree

PARENTS = (
    "Smoking",
    "Marijuana_Smoking",
    "Hepatic_Failure",
    "Renal_Failure",
    "Sedative_Effects_Concern",
    "Seizure_Risk",
    "Glucose_Abnormalities",
    "Hyperlipidemia",
    "Weight_Gain_Risk",
    "QTc_Prolongation_Risk",
    "Orthostatic_Hypotension_Risk",
    "Hyperprolactinemia",
    "Tardive_Dyskinesia",
    "Dystonia",
    "Parkinsonism",
    "Akathisia",
)
OUTCOMES = (
    "Chlorpromazine",
    "Fluphenazine",
    "Haloperidol",
    "Loxapine",
    "Molindone",
    "Perphenazine",
    "Pimozide",
    "Thioridazine",
    "Thiothixene",
    "Trifluoperazine",
    "Aripiprazole",
    "Asenapine",
    "Brexpiprazole",
    "Cariprazine",
    "Clozapine",
    "Iloperidone",
    "Lurasidone",
    "Olanzapine",
    "Paliperidone",
    "Quetiapine",
    "Risperidone",
    "Ziprasidone",
)
MAX_BYTES = 32 * 1024 * 1024
ATTRIBUTION_ALGORITHM = "oral-root-prior-shapley-v1"
_TREATMENT_IDS = tuple(f"oral:{outcome.lower()}" for outcome in OUTCOMES)


@dataclass(frozen=True)
class Candidate:
    drug_id: str
    name: str
    probability: float
    rank: int
    removed_reason: str | None


@dataclass(frozen=True)
class OralRanking:
    distribution: tuple[Candidate, ...]
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class OralAttribution:
    algorithm: str
    treatment_id: str
    model_reference_probability: float
    posterior_probability: float
    contributions: tuple[tuple[str, float], ...]
    reconstruction_residual: float


@dataclass(frozen=True)
class _LoadedModel:
    table: memoryview[float]
    root_priors: tuple[tuple[float, float], ...]
    explain: Callable[[int, str], OralAttribution]


def _values(table: str | None, count: int, width: int) -> array:
    try:
        values = array("d", (float(value) for value in (table or "").split()))
    except ValueError as exc:
        raise ValueError("Invalid probability") from exc
    if len(values) != count or any(not math.isfinite(v) or v < 0 or v > 1 for v in values):
        raise ValueError("Invalid probability table")
    if any(abs(sum(values[index : index + width]) - 1) > 1e-12 for index in range(0, count, width)):
        raise ValueError("Probability row is not normalized")
    return values


@lru_cache(maxsize=1)
def _load_cached(source_hash: str, model_bytes: bytes) -> _LoadedModel:
    if len(model_bytes) > MAX_BYTES or hashlib.sha256(model_bytes).hexdigest() != source_hash:
        raise ValueError("Model size or SHA-256 mismatch")
    if b"<!DOCTYPE" in model_bytes.upper() or b"<!ENTITY" in model_bytes.upper():
        raise ValueError("DTD and entities are forbidden")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        root = etree.fromstring(model_bytes, parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError("Invalid model XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD and entities are forbidden")
    if (
        root.tag != "BIF"
        or len(root.findall("NETWORK")) != 1
        or any(child.tag != "NETWORK" for child in root if isinstance(child.tag, str))
    ):
        raise ValueError("Invalid model structure")
    network = root.find("NETWORK")
    assert network is not None
    if any(
        child.tag not in {"NAME", "PROPERTY", "VARIABLE", "DEFINITION"}
        for child in network
        if isinstance(child.tag, str)
    ):
        raise ValueError("Unexpected network structure")
    variables = network.findall("VARIABLE")
    definitions = network.findall("DEFINITION")
    if len(variables) != 17 or len(definitions) != 17:
        raise ValueError("Invalid model dimensions")
    variable_map = {v.findtext("NAME"): v for v in variables}
    definition_map = {d.findtext("FOR"): d for d in definitions}
    if set(variable_map) != set((*PARENTS, "Antipsychotic")) or set(definition_map) != set(
        variable_map
    ):
        raise ValueError("Invalid model variables")
    priors = []
    for name in (*PARENTS, "Antipsychotic"):
        variable, definition = variable_map[name], definition_map[name]
        if any(
            child.tag not in {"NAME", "OUTCOME", "PROPERTY"}
            for child in variable
            if isinstance(child.tag, str)
        ) or any(
            child.tag not in {"FOR", "GIVEN", "TABLE", "PROPERTY"}
            for child in definition
            if isinstance(child.tag, str)
        ):
            raise ValueError("Unexpected variable or definition structure")
        outcomes = tuple(node.text for node in variable.findall("OUTCOME"))
        parents = tuple(node.text for node in definition.findall("GIVEN"))
        if (
            len(variable.findall("NAME")) != 1
            or len(definition.findall("FOR")) != 1
            or len(definition.findall("TABLE")) != 1
        ):
            raise ValueError("Invalid variable or definition")
        if name == "Antipsychotic":
            if outcomes != OUTCOMES or parents != PARENTS:
                raise ValueError("Invalid target outcomes or parent order")
            target = _values(definition.findtext("TABLE"), (1 << 16) * len(OUTCOMES), len(OUTCOMES))
        else:
            if outcomes != ("No", "Yes") or parents:
                raise ValueError("Invalid root")
            no, yes = _values(definition.findtext("TABLE"), 2, 2)
            priors.append((no, yes))
    table = memoryview(target.tobytes()).cast("d")
    root_priors = tuple(priors)
    # This cache belongs to one validated model SHA and one algorithm version.
    # Evicting the loaded model also evicts its explanations.
    explain = lru_cache(maxsize=128)(partial(_explain, table, root_priors))
    return _LoadedModel(table, root_priors, explain)


def load_model(model_bytes: bytes, expected_sha256: str) -> None:
    _load_cached(expected_sha256, model_bytes)


def _evidence_row(evidence: Mapping[str, bool | str]) -> int:
    if set(evidence) != set(PARENTS):
        raise ValueError("Evidence must contain exactly the 16 factors")
    row = 0
    for name in PARENTS:
        value = evidence[name]
        if type(value) is bool:
            present = value
        elif value in ("No", "Yes") and isinstance(value, str):
            present = value == "Yes"
        else:
            raise ValueError(f"Invalid evidence for {name}")
        row = row * 2 + int(present)
    return row


def rank_oral(
    model_bytes: bytes,
    expected_sha256: str,
    evidence: Mapping[str, bool | str],
    unavailable: frozenset[str] = frozenset(),
) -> OralRanking:
    row = _evidence_row(evidence)
    table = _load_cached(expected_sha256, model_bytes).table
    values = table[row * len(OUTCOMES) : (row + 1) * len(OUTCOMES)]
    ordered = sorted(range(len(OUTCOMES)), key=lambda index: -values[index])
    distribution = tuple(
        Candidate(
            drug_id=_TREATMENT_IDS[index],
            name=OUTCOMES[index],
            probability=values[index],
            rank=rank,
            removed_reason="zero_probability"
            if values[index] == 0
            else ("unavailable" if _TREATMENT_IDS[index] in unavailable else None),
        )
        for rank, index in enumerate(ordered, 1)
    )
    return OralRanking(
        distribution, tuple(item for item in distribution if item.removed_reason is None)[:5]
    )


def _coalition_values(
    work: list[float], root_priors: tuple[tuple[float, float], ...], evidence_row: int
) -> None:
    """Transform a private CPT column in place, from assignments to coalition masks."""
    for index, (no, yes) in enumerate(root_priors):
        bit = 1 << (len(root_priors) - index - 1)
        observed_yes = bool(evidence_row & bit)
        for start in range(0, len(work), 2 * bit):
            for low in range(start, start + bit):
                high = low + bit
                a, b = work[low], work[high]
                work[low] = no * a + yes * b
                work[high] = b if observed_yes else a


def _shapley_values(coalitions: list[float], parent_count: int) -> tuple[float, ...]:
    weights = [1 / (parent_count * math.comb(parent_count - 1, k)) for k in range(parent_count)]
    return tuple(
        math.fsum(
            weights[mask.bit_count()] * (coalitions[mask + bit] - coalitions[mask])
            for start in range(0, len(coalitions), 2 * bit)
            for mask in range(start, start + bit)
        )
        for bit in (1 << index for index in reversed(range(parent_count)))
    )


def _explain(
    table: memoryview[float],
    root_priors: tuple[tuple[float, float], ...],
    evidence_row: int,
    treatment_id: str,
) -> OralAttribution:
    try:
        column = _TREATMENT_IDS.index(treatment_id)
    except ValueError as exc:
        raise ValueError("Unknown oral treatment ID") from exc
    coalitions: list[float] = list(table[column :: len(OUTCOMES)])
    _coalition_values(coalitions, root_priors, evidence_row)
    values = _shapley_values(coalitions, len(root_priors))
    reference, posterior = coalitions[0], coalitions[-1]
    return OralAttribution(
        algorithm=ATTRIBUTION_ALGORITHM,
        treatment_id=treatment_id,
        model_reference_probability=reference,
        posterior_probability=posterior,
        contributions=tuple(zip(PARENTS, values, strict=True)),
        reconstruction_residual=posterior - math.fsum((reference, *values)),
    )


def explain_oral(
    model_bytes: bytes,
    expected_sha256: str,
    evidence: Mapping[str, bool | str],
    treatment_id: str,
) -> OralAttribution:
    """Explain one raw oral posterior in probability units under the pinned root priors."""
    row = _evidence_row(evidence)
    return _load_cached(expected_sha256, model_bytes).explain(row, treatment_id)
