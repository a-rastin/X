"""B05: provider capability is an explicit saved setting."""

import pytest
from pydantic import ValidationError

from x_insight.reasoning.provider_config import ProviderSettingsWrite


def test_capability_mode_is_accepted_and_validated():
    body = {"base_url": "https://example.test/v1", "model": "fictional"}
    assert ProviderSettingsWrite(**body, capability_mode="json").capability_mode == "json"
    assert ProviderSettingsWrite(**body, capability_mode="schema").capability_mode == "schema"
    with pytest.raises(ValidationError):
        ProviderSettingsWrite(**body, capability_mode="other")
