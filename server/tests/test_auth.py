import pytest

from app.core.config import get_settings


@pytest.mark.asyncio
async def test_register_then_login_round_trip(app_client):
    register = await app_client.post(
        "/api/auth/register",
        json={"email": "newuser@example.com", "password": "testpass123", "full_name": "New User"},
    )
    assert register.status_code == 201
    assert "access_token" in register.json()

    login = await app_client.post(
        "/api/auth/login", json={"email": "newuser@example.com", "password": "testpass123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "newuser@example.com"
    assert me.json()["role"] == "member"  # self-serve registration never grants admin


@pytest.mark.asyncio
async def test_login_with_wrong_password_rejected(app_client):
    await app_client.post(
        "/api/auth/register",
        json={"email": "someone@example.com", "password": "correctpass", "full_name": "Someone"},
    )
    login = await app_client.post(
        "/api/auth/login", json={"email": "someone@example.com", "password": "wrongpass"}
    )
    assert login.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_email_registration_rejected(app_client):
    payload = {"email": "dupe@example.com", "password": "testpass123", "full_name": "Dupe"}
    first = await app_client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = await app_client.post("/api/auth/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_me_rejects_missing_or_bad_token(app_client):
    no_token = await app_client.get("/api/auth/me")
    assert no_token.status_code == 401

    bad_token = await app_client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert bad_token.status_code == 401


@pytest.mark.asyncio
async def test_public_registration_disabled_blocks_register(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_public_registration", False)
    response = await app_client.post(
        "/api/auth/register",
        json={"email": "blocked@example.com", "password": "testpass123", "full_name": "Blocked"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_auth_config_reflects_the_real_setting(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_public_registration", False)
    response = await app_client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json()["allow_public_registration"] is False
