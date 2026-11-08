"""The Fledge integration's tests, against a real Home Assistant core.

    uv venv --python 3.13 .venv && uv pip install -r requirements_test.txt
    .venv/bin/pytest
"""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
