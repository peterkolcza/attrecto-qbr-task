"""Shared test fixtures — disable auth env vars by default for tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def disable_auth_by_default(monkeypatch):
    """Remove auth env vars before each test. Tests that need auth re-enable via their own fixtures."""
    # Only delete if test hasn't already set them (auth tests use their own fixture after this)
    for var in ("QBR_AUTH_USER", "QBR_AUTH_PASSWORD_HASH", "QBR_SESSION_SECRET"):
        monkeypatch.delenv(var, raising=False)
    yield


# Also remove at module load time (before app imports load .env)
for var in ("QBR_AUTH_USER", "QBR_AUTH_PASSWORD_HASH", "QBR_SESSION_SECRET"):
    os.environ.pop(var, None)
