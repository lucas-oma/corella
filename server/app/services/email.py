"""Optional Resend client — invite emails only, for now.

Hand-rolled httpx against Resend's REST emails endpoint rather than their
SDK, same shape as the OpenAI/Gemini LLM clients. Unconfigured or a
failed send is a skipped side effect, never a raised error: minting the
invite token is the product; email is the convenience.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"

# Brand tokens from BRANDING.md, inlined — email clients ignore stylesheets.
_SURFACE = "#FAFAF9"
_RAISED = "#FFFFFF"
_BORDER = "#E4E4E1"
_INK = "#12141A"
_INK_MUTED = "#5B5F6B"
_INK_SUBTLE = "#8B8F99"
_ACCENT = "#0B1B33"
_ACCENT_FG = "#FAFAF9"

_ROLE_PHRASE = {
    "admin": "an admin",
    "member": "a member",
    "owner": "owner",
}


def email_invites_enabled() -> bool:
    settings = get_settings()
    return bool(settings.resend_api_key and settings.resend_from_email)


def invite_accept_url(token: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/invite/{token}"


def _from_header(settings) -> str:
    return f"Corella <{settings.resend_from_email}>"


def _role_phrase(role: str) -> str:
    return _ROLE_PHRASE.get(role, role)


def _expires_label(expires_at: datetime) -> str:
    return expires_at.strftime("%d %b %Y")


def render_invite_text(
    *,
    organization_name: str,
    inviter_name: str,
    role: str,
    url: str,
    expires_at: datetime,
) -> str:
    return (
        f"{inviter_name} invited you to join {organization_name} as {_role_phrase(role)}.\n\n"
        f"Accept the invite:\n{url}\n\n"
        f"This link expires on {_expires_label(expires_at)}. "
        "If you weren't expecting this, you can ignore it."
    )


def render_invite_html(
    *,
    organization_name: str,
    inviter_name: str,
    role: str,
    url: str,
    expires_at: datetime,
) -> str:
    org = html.escape(organization_name)
    inviter = html.escape(inviter_name)
    href = html.escape(url, quote=True)
    phrase = html.escape(_role_phrase(role))
    expires = html.escape(_expires_label(expires_at))
    # Tables + inline CSS: Gmail/Outlook strip most <style> and flexbox.
    # Georgia stands in for Newsreader (serif headlines only). System sans
    # stands in for Inter. No remote logo — clients block images by default.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Join {org} on Corella</title>
</head>
<body style="margin:0;padding:0;background-color:{_SURFACE};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_SURFACE};">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" style="width:480px;max-width:100%;">
          <tr>
            <td style="padding:0 0 24px 0;text-align:center;">
              <p style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:28px;line-height:1.2;color:{_INK};">Corella</p>
            </td>
          </tr>
          <tr>
            <td style="background-color:{_RAISED};border:1px solid {_BORDER};border-radius:8px;padding:32px 28px;">
              <p style="margin:0 0 8px 0;font-family:Georgia,'Times New Roman',serif;font-size:20px;line-height:1.3;color:{_INK};">Join {org}</p>
              <p style="margin:0 0 24px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;color:{_INK_MUTED};">
                {inviter} invited you to join {org} as {phrase}.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="background-color:{_ACCENT};border-radius:8px;">
                    <a href="{href}" style="display:inline-block;padding:12px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1;color:{_ACCENT_FG};text-decoration:none;">
                      Accept invite
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:24px 0 0 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;line-height:1.5;color:{_INK_SUBTLE};">
                This link expires on {expires}. If you weren't expecting this, you can ignore it.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


async def send_invite_email(
    *,
    to: str,
    organization_name: str,
    inviter_name: str,
    role: str,
    token: str,
    expires_at: datetime,
) -> bool:
    """True if Resend accepted the message. False if unconfigured or the
    request failed — callers must not fail the invite on False.
    """
    settings = get_settings()
    if not settings.resend_api_key or not settings.resend_from_email:
        return False

    url = invite_accept_url(token)
    text = render_invite_text(
        organization_name=organization_name,
        inviter_name=inviter_name,
        role=role,
        url=url,
        expires_at=expires_at,
    )
    html_body = render_invite_html(
        organization_name=organization_name,
        inviter_name=inviter_name,
        role=role,
        url=url,
        expires_at=expires_at,
    )
    payload = {
        "from": _from_header(settings),
        "to": [to],
        "subject": f"Join {organization_name} on Corella",
        "html": html_body,
        "text": text,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=payload,
            )
    except httpx.RequestError as e:
        logger.info("Invite email skipped for %s: %s", to, e)
        return False

    if response.status_code >= 400:
        logger.info(
            "Invite email skipped for %s: Resend %s %s",
            to,
            response.status_code,
            response.text[:300],
        )
        return False
    return True
