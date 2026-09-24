"""Dependency-free REST client used by command hooks (never exposes secrets)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from codex_config import AGENT_ID, API_BASE, REQUEST_TIMEOUT, TIMEZONE, auth_headers

_BOUND_HEADERS = None
_BOUND_IDENTITY = None


def bind_auth(headers, identity):
    global _BOUND_HEADERS, _BOUND_IDENTITY
    _BOUND_HEADERS, _BOUND_IDENTITY = dict(headers), identity


class BoswellUnavailable(RuntimeError):
    """Boswell could not be reached or did not answer usefully.

    Carries `status` (int HTTP code) when the failure was an HTTP response, and
    None when it was a transport/parse failure.
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class BoswellAuthRejected(BoswellUnavailable):
    """The tenant credential was rejected while Boswell remained reachable.

    This remains a BoswellUnavailable subclass for compatibility, while callers
    can distinguish a re-keying problem from a substrate outage.
    """


def _request(method: str, path: str, *, params: dict | None = None,
             payload: dict | None = None, timeout: float | None = None,
             expected_binding: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "boswell-hooks/2.2"}
    selected = auth_headers()
    from tenant_binding import current_binding
    identity = current_binding(selected)
    if ((_BOUND_IDENTITY is not None and identity != _BOUND_IDENTITY)
            or (expected_binding is not None and identity != expected_binding)):
        raise BoswellAuthRejected('Tenant credential changed; request refused')
    headers.update(_BOUND_HEADERS if _BOUND_HEADERS is not None else selected)
    if "X-API-Key" not in headers:
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


def startup(timeout: float | None = None) -> dict:
    from opening_briefing import TASK_LIMIT, task_snapshot
    budget = timeout if timeout is not None else REQUEST_TIMEOUT
    started = time.monotonic()
    response = _request("GET", "/v2/startup", params={
        "verbosity": "warm", "agent_id": AGENT_ID, "timezone": TIMEZONE,
    }, timeout=budget)
    if isinstance(response.get('work_briefing'), dict) and response['work_briefing'].get('contract') == 'cards-v1':
        return response
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise BoswellUnavailable("startup work briefing exceeded its time budget")
    # One read inside the startup time budget. Older servers' warm projection
    # can omit every recent task, even when the existing task endpoint has it.
    work = _request("GET", "/v2/tasks", params={"limit": TASK_LIMIT}, timeout=remaining)
    response["work_briefing"] = task_snapshot(work)
    return response


def search(query: str, limit: int = 5, timeout: float | None = None) -> dict:
    return _request("GET", "/v2/search", params={
        "q": query[:2000], "limit": limit, "mode": "hybrid", "depth": "surface",
    }, timeout=timeout)


def commit(*, branch: str, content: dict, content_type: str,
           message: str, tags: list[str], expected_binding: str | None = None) -> dict:
    return _request("POST", "/v2/commit", payload={
        "branch": branch,
        "content": content,
        "type": content_type,
        "message": message,
        "tags": tags,
    }, expected_binding=expected_binding)

