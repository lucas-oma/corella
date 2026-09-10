#!/usr/bin/env python
"""API test server — a small FastAPI stand-in for "the other system" on
the receiving end of the pre/post call-type hooks (see API.md and
app/services/admin/call_hooks.py). Committed on purpose, not a scratch
file: `tests/test_call_hooks_integration.py` runs this exact app as a
real subprocess and asserts against real HTTP responses, and it's just
as usable by hand against the real running dev stack.

Manual use — point a call type's pre_call_url/post_call_url at it from
inside the api/worker containers via `host.docker.internal`, since it
runs on your host machine, not on the corella_default docker network:

    pre_call_url  = http://host.docker.internal:9199/pre
    post_call_url = http://host.docker.internal:9199/post

Run it:

    cd server && .venv/bin/uvicorn scripts.api_test_server:app --port 9199 --reload

Every request is logged to the console (method, path, the three
mandatory headers pulled out specifically, and the body) and kept
in-memory — GET /requests to inspect everything received so far as JSON,
or DELETE /requests to clear it between test runs.

Behavior:
  - Any path containing "pre"  -> 200, returns a fake CRM-lookup-shaped
    text body, for testing pre_call_use_as_context.
  - Any path containing "fail" -> 500, for testing that a broken pre/post
    hook is swallowed gracefully rather than breaking meeting
    creation/report generation.
  - Any path containing "slow" -> sleeps 8s before responding (longer
    than the default pre_call_timeout_seconds=5s), for testing the
    timeout path.
  - Everything else (including "post") -> 200, {"ok": true} — logs
    whatever body was sent (the full structured payload, if
    post_call_send_full_payload is on) so you can eyeball it.
"""

import asyncio
import json
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(title="Corella API test server")

_MANDATORY_HEADERS = ["x-corella-app-url", "x-corella-meeting-id", "x-corella-user-id"]
_log: list[dict] = []


def _print_request(method: str, path: str, headers: dict, body: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"{datetime.now(UTC).isoformat()}  {method} {path}")
    print("-- mandatory headers --")
    for h in _MANDATORY_HEADERS:
        present = h in {k.lower() for k in headers}
        value = next((v for k, v in headers.items() if k.lower() == h), None)
        marker = "✓" if present else "✗ MISSING"
        print(f"  {h}: {value}  [{marker}]")
    other = {k: v for k, v in headers.items() if k.lower() not in _MANDATORY_HEADERS}
    if other:
        print("-- other headers --")
        for k, v in other.items():
            if k.lower() in ("host", "content-length", "accept", "accept-encoding", "user-agent", "connection"):
                continue
            print(f"  {k}: {v}")
    print("-- body --")
    try:
        print(json.dumps(json.loads(body), indent=2)[:4000])
    except (json.JSONDecodeError, TypeError):
        print(body[:2000] if body else "(empty)")
    print("=" * 70)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(path: str, request: Request):
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8", errors="replace")
    headers = dict(request.headers)

    if path == "requests":
        if request.method == "DELETE":
            _log.clear()
            return JSONResponse({"cleared": True})
        return JSONResponse(_log)

    entry = {
        "at": datetime.now(UTC).isoformat(),
        "method": request.method,
        "path": f"/{path}",
        "headers": headers,
        "body": body,
        "mandatory_headers_present": {
            h: any(k.lower() == h for k in headers) for h in _MANDATORY_HEADERS
        },
    }
    _log.append(entry)
    _print_request(request.method, f"/{path}", headers, body)

    if "fail" in path:
        return JSONResponse({"error": "simulated failure"}, status_code=500)
    if "slow" in path:
        await asyncio.sleep(8)
    if "pre" in path:
        return PlainTextResponse(
            "CRM lookup: Acme Corp is a Fortune 500 prospect, deal size $250k, "
            "champion is Jane Doe, last contacted 3 days ago about renewal terms."
        )
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9199)
