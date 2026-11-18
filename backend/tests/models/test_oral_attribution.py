import hashlib
import math
from itertools import permutations, product
from pathlib import Path

import pytest
from lxml import etree

from x_insight.models import oral_antipsychotic as oral

MODEL = Path(__file__).resolve().parents[3] / "content/new-case/v1/oral-antipsychotic.xml"
SHA = "f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a"
TOLERANCE = 1e-10  # Absolute probability units, shared with bundled-model checks.


def _enumerate_coalition(table, priors, evidence, observed):
    return math.fsum(
        probability
        * math.prod(
            priors[index][value] for index, value in enumerate(row) if index not in observed
        )
        for row, probability in zip(product((0, 1), repeat=len(priors)), table, strict=True)
        if all(row[index] == evidence[index] for index in observed)
    )


@pytest.mark.parametrize(
    ("table", "priors", "reference", "all_yes_contributions"),
    [
        ([0.2, 0.8], ((0.75, 0.25),), 0.35, (0.45,)),
        ([0.1, 0.3, 0.5, 0.7], ((0.75, 0.25), (0.4, 0.6)), 0.32, (0.3, 0.08)),
        (
            [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9],
            ((0.75, 0.25), (0.75, 0.25), (0.1, 0.9)),
            0.15,
            (0.375, 0.375, 0),
        ),
    ],
)
def test_coalitions_and_shapley_match_exhaustive_enumeration(
    table, priors, reference, all_yes_contributions
):
    n = len(priors)
    assert reference != pytest.approx(math.fsum(table) / len(table))
    assert reference != table[0]
    for evidence_row, evidence in enumerate(product((0, 1), repeat=n)):
        coalitions = table.copy()
        oral._coalition_values(coalitions, priors, evidence_row)
        for mask, actual in enumerate(coalitions):
            observed = {index for index in range(n) if mask & (1 << (n - index - 1))}
            assert actual == pytest.approx(
                _enumerate_coalition(table, priors, evidence, observed), abs=TOLERANCE, rel=0
            )
        expected = [0.0] * n
        for order in permutations(range(n)):
            observed = set()
            previous = reference
            for index in order:
                observed.add(index)
                current = _enumerate_coalition(table, priors, evidence, observed)
                expected[index] += (current - previous) / math.factorial(n)
                previous = current
        contributions = oral._shapley_values(coalitions, n)
        assert contributions == pytest.approx(expected, abs=TOLERANCE, rel=0)
        assert coalitions[0] == pytest.approx(reference, abs=TOLERANCE, rel=0)
        assert coalitions[-1] == table[evidence_row]
        assert math.fsum((reference, *contributions)) == pytest.approx(
            table[evidence_row], abs=TOLERANCE, rel=0
        )
        if not any(evidence):
            assert contributions[0] < 0
        if all(evidence):
            assert contributions == pytest.approx(all_yes_contributions, abs=TOLERANCE, rel=0)


def test_bundled_explanation_matches_ranking_and_pinned_priors():
    model = MODEL.read_bytes()
    baseline = dict.fromkeys(oral.PARENTS, False)
    scenario = {**baseline, "QTc_Prolongation_Risk": True}
    baseline_ranking = oral.rank_oral(model, SHA, baseline)
    scenario_ranking = oral.rank_oral(model, SHA, scenario)
    assert baseline_ranking.distribution[0].drug_id == "oral:chlorpromazine"
    assert all(item.probability == pytest.approx(1 / 22) for item in baseline_ranking.distribution)
    assert scenario_ranking.distribution[0].drug_id == "oral:aripiprazole"
    assert scenario_ranking.distribution[0].probability == pytest.approx(1 / 7)

    target = "oral:aripiprazole"
    patient = oral.explain_oral(model, SHA, baseline, target)
    simulated = oral.explain_oral(model, SHA, scenario, target)
    alternative = oral.explain_oral(model, SHA, scenario, "oral:chlorpromazine")
    assert patient.algorithm == "oral-root-prior-shapley-v1"
    assert patient.treatment_id == simulated.treatment_id == target
    assert patient.model_reference_probability == simulated.model_reference_probability
    for explanation, ranking in (
        (patient, baseline_ranking),
        (simulated, scenario_ranking),
        (alternative, scenario_ranking),
    ):
        assert explanation.posterior_probability == next(
            item.probability
            for item in ranking.distribution
            if item.drug_id == explanation.treatment_id
        )
        assert tuple(factor for factor, _ in explanation.contributions) == oral.PARENTS
        assert abs(explanation.reconstruction_residual) <= TOLERANCE
    assert abs(dict(patient.contributions)["QTc_Prolongation_Risk"]) > TOLERANCE

    definitions = {
        node.findtext("FOR"): node.findtext("TABLE").split()
        for node in etree.fromstring(model, etree.XMLParser(huge_tree=True)).findall(
            ".//DEFINITION"
        )
    }
    priors = [tuple(map(float, definitions[parent])) for parent in oral.PARENTS]
    target_column = list(map(float, definitions["Antipsychotic"][10::22]))
    reference = math.fsum(
        probability
        * math.prod(prior[(row >> (15 - index)) & 1] for index, prior in enumerate(priors))
        for row, probability in enumerate(target_column)
    )
    assert patient.model_reference_probability == pytest.approx(reference, abs=TOLERANCE, rel=0)
    assert oral.explain_oral(model, SHA, dict.fromkeys(oral.PARENTS, "No"), target) is patient
    assert oral.rank_oral(model, SHA, baseline) == baseline_ranking
    assert oral.rank_oral(model, SHA, scenario) == scenario_ranking
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        oral.explain_oral(model + b" ", SHA, baseline, target)
    with pytest.raises(ValueError, match="Unknown oral treatment ID"):
        oral.explain_oral(model, SHA, baseline, "lai:aripiprazole")

    different_model = model.replace(b"<TABLE>0.99 0.01</TABLE>", b"<TABLE>0.01 0.99</TABLE>", 1)
    different_sha = hashlib.sha256(different_model).hexdigest()
    changed = oral.explain_oral(different_model, different_sha, baseline, target)
    assert changed.posterior_probability == patient.posterior_probability
    assert (
        abs(changed.model_reference_probability - patient.model_reference_probability) > TOLERANCE
    )
