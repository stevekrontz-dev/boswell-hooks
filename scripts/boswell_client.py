"""Dependency-free REST client used by command hooks (never exposes secrets)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from codex_config import AGENT_ID, API_BASE, REQUEST_TIMEOUT, auth_headers


class BoswellUnavailable(RuntimeError):
    """Boswell could not be reached or did not answer usefully.

    Carries `status` (int HTTP code) when the failure was an HTTP response, and
    None when it was a transport/parse failure.
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class BoswellAuthRejected(BoswellUnavailable):
    """The credential was REJECTED (401/403) — Boswell itself is healthy.

    Deliberately a SUBCLASS: every existing `except BoswellUnavailable` handler
    keeps catching this unchanged. Callers that care can branch on it.

    WHY THIS EXISTS (2026-08-05): home revoked api_keys row 67fcf720 for a key
    exposure, not knowing the same secret was this machine's hook credential.
    Every hook then reported a flat "BOSWELL UNREACHABLE" and tripped sacred
    BOSWELL-DOWN-STOP — so a dead credential presented as a dead substrate and
    Steve was told Boswell was down while it served traffic at 3.8.46. The two
    demand opposite responses: re-key vs full stop. Never conflate them again.
    """


def _request(method: str, path: str, *, params: dict | None = None,
             payload: dict | None = None, timeout: float | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "boswell-hooks/2.0 Codex"}
    headers.update(auth_headers())
    if not any(k in headers for k in ("X-API-Key", "X-Boswell-Internal", "Authorization")):
        raise BoswellAuthRejected("no machine-local Boswell credential is configured")
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(
                request, timeout=timeout or REQUEST_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            if not isinstance(parsed, dict):
                raise BoswellUnavailable("Boswell returned a non-object response")
            return parsed
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise BoswellAuthRejected(
                f"Boswell rejected this machine's credential (HTTP {exc.code})",
                status=exc.code) from None
        raise BoswellUnavailable(f"Boswell HTTP {exc.code}", status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BoswellUnavailable(
            f"Boswell transport failure: {type(exc).__name__}") from None


def startup() -> dict:
    return _request("GET", "/v2/startup", params={
        "verbosity": "warm", "agent_id": AGENT_ID,
    })


def search(query: str, limit: int = 5, timeout: float | None = None) -> dict:
    return _request("GET", "/v2/search", params={
        "q": query[:2000], "limit": limit, "mode": "hybrid", "depth": "surface",
    }, timeout=timeout)


def commit(*, branch: str, content: dict, content_type: str,
           message: str, tags: list[str]) -> dict:
    return _request("POST", "/v2/commit", payload={
        "branch": branch,
        "content": content,
        "type": content_type,
        "message": message,
        "tags": tags,
    })

