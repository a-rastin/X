import pytest

from x_insight import contracts
from x_insight.cases.patients import validate_registration_payload


@pytest.mark.parametrize("status", ["first_time", "established"])
def test_birth_year_computes_age_for_each_clinical_status(status):
    year = contracts.utcnow().year - 30
    fields = validate_registration_payload(
        identifier="0012345678",
        given_name="Ali",
        family_name="Reza",
        sex="M",
        birth_year=year,
        clinical_status=status,
    )
    assert fields["birth_year"] == year
    assert fields["age"] == 30
    assert fields["clinical_status"] == status


@pytest.mark.parametrize("status", ["first_time", "established"])
def test_birth_year_registration_uses_same_name_rules(status):
    with pytest.raises(contracts.ContractError):
        validate_registration_payload(
            identifier="0012345678",
            given_name="Anne-Marie",
            family_name="Reza",
            sex="M",
            birth_year=contracts.utcnow().year - 30,
            clinical_status=status,
        )
