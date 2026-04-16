"""Tests for web authentication (login page, session, rate limiting)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qbr_web.app import app
from qbr_web.auth import (
    _login_attempts,
    auth_enabled,
    check_rate_limit,
    hash_password,
    is_public_path,
    verify_credentials,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_state():
    from qbr_web.app import jobs

    jobs.clear()
    _login_attempts.clear()
    yield
    jobs.clear()
    _login_attempts.clear()


@pytest.fixture
def auth_env(monkeypatch):
    """Enable auth with known credentials."""
    test_hash = hash_password("testpass")
    monkeypatch.setenv("QBR_AUTH_USER", "testuser")
    monkeypatch.setenv("QBR_AUTH_PASSWORD_HASH", test_hash)
    yield


class TestAuthHelpers:
    def test_auth_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("QBR_AUTH_PASSWORD_HASH", raising=False)
        assert not auth_enabled()

    def test_auth_enabled_with_hash(self, monkeypatch):
        monkeypatch.setenv("QBR_AUTH_PASSWORD_HASH", "$2b$12$xxx")
        assert auth_enabled()

    def test_hash_and_verify(self):
        h = hash_password("secret123")
        assert h.startswith("$2")

    def test_verify_valid_credentials(self, auth_env):
        assert verify_credentials("testuser", "testpass")

    def test_verify_wrong_password(self, auth_env):
        assert not verify_credentials("testuser", "wrongpass")

    def test_verify_wrong_username(self, auth_env):
        assert not verify_credentials("other", "testpass")

    def test_verify_no_config(self, monkeypatch):
        monkeypatch.delenv("QBR_AUTH_PASSWORD_HASH", raising=False)
        assert not verify_credentials("user", "pass")

    def test_rate_limit_allows_initial_attempts(self):
        for _ in range(5):
            assert check_rate_limit("1.2.3.4")

    def test_is_public_path(self):
        assert is_public_path("/login")
        assert is_public_path("/logout")
        assert is_public_path("/healthz")
        assert is_public_path("/static/foo.css")
        assert not is_public_path("/")
        assert not is_public_path("/analyze")


class TestAuthDisabled:
    """When no env vars set, auth is disabled — all routes accessible."""

    def test_index_accessible(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_analyze_accessible(self, client):
        resp = client.post("/analyze", follow_redirects=False)
        assert resp.status_code == 303  # redirect to /jobs/{id}


class TestAuthEnabled:
    def test_unauthenticated_redirects_to_login(self, auth_env, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    def test_healthz_still_public(self, auth_env, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_login_page_renders(self, auth_env, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Sign In" in resp.text

    def test_valid_login(self, auth_env, client):
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "testpass", "next": "/"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

    def test_invalid_login_shows_error(self, auth_env, client):
        resp = client.post(
            "/login",
            data={"username": "testuser", "password": "wrong", "next": "/"},
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.text

    def test_logout_clears_session(self, auth_env, client):
        # Login first
        client.post("/login", data={"username": "testuser", "password": "testpass", "next": "/"})
        # Logout
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]
        # Next request should redirect to login
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]
