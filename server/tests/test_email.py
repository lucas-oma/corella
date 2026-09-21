"""Resend invite emails — unconfigured is a no-op; a failed send never raises."""

from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import get_settings
from app.services.email import (
    email_invites_enabled,
    invite_accept_url,
    render_invite_html,
    render_invite_text,
    send_invite_email,
)

_EXPIRES = datetime(2026, 9, 28, tzinfo=UTC)


def test_email_invites_disabled_without_both_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    monkeypatch.setattr(get_settings(), "resend_from_email", None)
    assert email_invites_enabled() is False
    monkeypatch.setattr(get_settings(), "resend_api_key", None)
    monkeypatch.setattr(get_settings(), "resend_from_email", "corella@example.com")
    assert email_invites_enabled() is False


def test_email_invites_enabled_when_key_and_from_are_set(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    monkeypatch.setattr(get_settings(), "resend_from_email", "corella@example.com")
    assert email_invites_enabled() is True


def test_invite_accept_url_uses_public_app_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_app_url", "https://corella.example.com/")
    assert invite_accept_url("abc") == "https://corella.example.com/invite/abc"


def test_invite_templates_are_plain_and_escape_html():
    text = render_invite_text(
        organization_name="Acme Org",
        inviter_name="Ada",
        role="member",
        url="https://corella.example.com/invite/tok",
        expires_at=_EXPIRES,
    )
    assert "Ada invited you to join Acme Org as a member." in text
    assert "https://corella.example.com/invite/tok" in text
    assert "!" not in text

    html_body = render_invite_html(
        organization_name='Acme <script>alert(1)</script>',
        inviter_name="Ada & co",
        role="admin",
        url="https://corella.example.com/invite/tok",
        expires_at=_EXPIRES,
    )
    assert "<script>" not in html_body
    assert "Acme &lt;script&gt;alert(1)&lt;/script&gt;" in html_body
    assert "Ada &amp; co" in html_body
    assert "an admin" in html_body
    assert 'href="https://corella.example.com/invite/tok"' in html_body
    assert "#0B1B33" in html_body
    assert "Accept invite" in html_body


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = '{"id":"ok"}'):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    last: dict | None = None
    response: _FakeResponse | Exception = _FakeResponse()

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeAsyncClient.last = {"url": url, "headers": headers, "json": json}
        if isinstance(_FakeAsyncClient.response, Exception):
            raise _FakeAsyncClient.response
        return _FakeAsyncClient.response


@pytest.mark.asyncio
async def test_send_invite_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", None)
    monkeypatch.setattr(get_settings(), "resend_from_email", None)
    _FakeAsyncClient.last = None
    monkeypatch.setattr("app.services.email.httpx.AsyncClient", _FakeAsyncClient)
    sent = await send_invite_email(
        to="new@example.com",
        organization_name="Acme",
        inviter_name="Ada",
        role="member",
        token="tok",
        expires_at=_EXPIRES,
    )
    assert sent is False
    assert _FakeAsyncClient.last is None


@pytest.mark.asyncio
async def test_send_invite_posts_to_resend(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    monkeypatch.setattr(get_settings(), "resend_from_email", "corella@example.com")
    monkeypatch.setattr(get_settings(), "public_app_url", "https://corella.example.com")
    _FakeAsyncClient.response = _FakeResponse()
    _FakeAsyncClient.last = None
    monkeypatch.setattr("app.services.email.httpx.AsyncClient", _FakeAsyncClient)

    sent = await send_invite_email(
        to="new@example.com",
        organization_name="Acme",
        inviter_name="Ada",
        role="member",
        token="tok",
        expires_at=_EXPIRES,
    )
    assert sent is True
    call = _FakeAsyncClient.last
    assert call is not None
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer re_test"
    assert call["json"]["from"] == "Corella <corella@example.com>"
    assert call["json"]["to"] == ["new@example.com"]
    assert call["json"]["subject"] == "Join Acme on Corella"
    assert "https://corella.example.com/invite/tok" in call["json"]["text"]
    assert "Accept invite" in call["json"]["html"]


@pytest.mark.asyncio
async def test_send_invite_returns_false_on_http_and_network_errors(monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_api_key", "re_test")
    monkeypatch.setattr(get_settings(), "resend_from_email", "corella@example.com")
    monkeypatch.setattr("app.services.email.httpx.AsyncClient", _FakeAsyncClient)

    _FakeAsyncClient.response = _FakeResponse(status_code=403, text="invalid")
    assert (
        await send_invite_email(
            to="new@example.com",
            organization_name="Acme",
            inviter_name="Ada",
            role="member",
            token="tok",
            expires_at=_EXPIRES,
        )
        is False
    )

    _FakeAsyncClient.response = httpx.ConnectError("boom")
    assert (
        await send_invite_email(
            to="new@example.com",
            organization_name="Acme",
            inviter_name="Ada",
            role="member",
            token="tok",
            expires_at=_EXPIRES,
        )
        is False
    )
