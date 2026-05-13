"""Session-scoped readiness wait so each test doesn't carry its own retry loop."""

import pytest

from tests.e2e._common import wait_for_orchestrator


@pytest.fixture(scope="session", autouse=True)
def _orchestrator_ready():
    wait_for_orchestrator(timeout_seconds=90.0)
    yield
