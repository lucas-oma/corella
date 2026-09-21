# Security Policy

Corella is a self-hosted meeting assistant. It stores call recordings, transcripts, speaker voice embeddings, and user/org secrets. Treat anything that can leak or change that data as a security issue.

## Supported versions

Corella is pre-1.0 (`0.x.y`) and makes no compatibility promise — see [VERSIONING.md](VERSIONING.md). Security fixes land on `main` and ship in the next cut to `release`. Older `0.x` versions are not patched.

| Version | Supported |
| ------- | --------- |
| Latest published `0.x.y` | :white_check_mark: |
| `main` (unreleased) | :white_check_mark: |
| Older `0.x.y` | :x: |

If you are running a fork or an untagged build, include the commit SHA in the report.

## Reporting a vulnerability

Do not open a public GitHub issue, discussion, or pull request for a security problem.

Report privately with GitHub's advisory form:

https://github.com/lucas-oma/corella/security/advisories/new

That opens a private thread with the maintainers. There is no email reporting path.

### What to include

- Affected version, tag, or commit SHA
- Which surface is involved (API, WebSocket, web UI, worker, call-type hooks, storage, auth, …)
- What you expected vs. what happened, and why it is a security issue
- Steps to reproduce against a **local** instance (Docker Compose or a throwaway deploy you control)
- Impact: who can do what they should not be able to do
- Any known mitigation or patch

A minimal proof of concept helps. Do not include other people's recordings, transcripts, credentials, or production data.

### What happens next

- You should get an acknowledgement within **a couple of days**.
- After that, we will say whether the report is accepted, declined, or needs more information, usually within **7 days**.
- If accepted, we will fix it on `main`, include it in the next `release` cut, and publish a GitHub Security Advisory. Public disclosure timing is coordinated with you.
- If declined, we will say why (not a vulnerability, already fixed, out of scope, or not enough information).
- There is no bug bounty and no payment. Credit in the advisory is available if you want it.

## Scope

In scope: issues in this repository (`server/`, `web/`, `packages/corella-live`, Docker Compose, CI) that let someone bypass auth, escalate privileges, read or change another org's or user's data, steal secrets or recordings, or otherwise break the isolation the product claims.

Examples:

- Auth bypass or privilege escalation (`super_admin`, org owner/admin/member, JWT vs API key)
- Cross-organization or cross-group data leakage (meetings, transcripts, knowledge base, voice identities)
- Exposure of API keys, provider credentials, org secrets, JWTs, or stored recordings
- Path traversal or arbitrary file read/write under audio or knowledge-base storage
- SSRF or secret interpolation bugs in call-type hooks
- XSS or injection in Corella's own code
- Live WebSocket authentication or session-binding failures

Out of scope:

- Vulnerabilities only in third-party services (Anthropic, OpenAI, Gemini, Deepgram, Hugging Face, Ollama, …)
- Issues that already require host access, a leaked `.env` / `JWT_SECRET`, or a misconfigured deployment
- Missing security headers or theoretical findings with no working impact
- Volume / denial-of-service against a self-hosted instance with no auth bypass
- Social engineering, physical access, or testing against someone else's running Corella instance

## Safe harbor

Good-faith research against an instance **you** operate is welcome. Do not access data that is not yours, and do not degrade a production instance you do not own. We will not pursue legal action against researchers who follow this policy.

## Operators

If you run Corella, you are responsible for the instance: a strong unique `JWT_SECRET`, not exposing Postgres / Redis / Qdrant to the internet, keeping images updated, and restricting `CORS_ORIGINS` / `PUBLIC_APP_URL` to your real origin. Configuration mistakes are not Corella vulnerabilities — see [`.env.example`](.env.example) and the [README](README.md).
