"""Machine-local configuration for the Codex Boswell hook adapter."""
from __future__ import annotations

import os
from pathlib import Path


HOME = Path.home()
PLUGIN_DATA = Path(os.environ.get("PLUGIN_DATA") or os.environ.get(
    "BOSWELL_HOOK_STATE", str(HOME / ".boswell" / "codex-hooks")))
STATE_ROOT = PLUGIN_DATA / "sessions"
ARCHIVE_ROOT = Path(os.environ.get(
    "BOSWELL_TRANSCRIPTS_ARCHIVE", str(HOME / "boswell-transcripts")))
API_BASE = os.environ.get(
    "BOSWELL_API_BASE",
    "https://delightful-imagination-production-f6a1.up.railway.app",
).rstrip("/")
HOOK_KEY_FILE = Path(os.environ.get(
    "BOSWELL_HOOK_KEY_FILE", str(HOME / ".boswell" / "hook_key")))
TENANT_PROFILE_ROOT = Path(os.environ.get(
    "BOSWELL_TENANT_PROFILE_ROOT", str(HOME / ".boswell" / "tenants")))
DEFAULT_TENANT_FILE = Path(os.environ.get(
    "BOSWELL_DEFAULT_TENANT_FILE", str(HOME / ".boswell" / "default_tenant")))
INTERNAL_SECRET_FILE = Path(os.environ.get(
    "BOSWELL_INTERNAL_SECRET_FILE", str(HOME / ".boswell" / ".internal-secret")))
AGENT_ID = os.environ.get("BOSWELL_AGENT_ID", "Codex-Root")
FAIL_OPEN = os.environ.get("BOSWELL_HOOKS_FAIL_OPEN", "").lower() in {
    "1", "true", "yes", "on"
}
REQUEST_TIMEOUT = float(os.environ.get("BOSWELL_HOOK_TIMEOUT", "12"))
HEARTBEAT_SECONDS = int(os.environ.get("BOSWELL_HOOK_HEARTBEAT", "1800"))


def _first_secret(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                return value
    except OSError:
        return None
    return None


def _profile_name() -> str | None:
    """Resolve an explicit/default named tenant without trusting shell syntax."""
    name = os.environ.get("BOSWELL_TENANT", "").strip()
    if not name:
        name = _first_secret(DEFAULT_TENANT_FILE) or ""
    if not name:
        return None
    if not all(ch.isalnum() or ch in "-_" for ch in name):
        return None
    return name


def selected_tenant_profile() -> str | None:
    """Return the named tenant profile selected for this process, if any."""
    return _profile_name()


def auth_headers() -> dict[str, str]:
    """Prefer a named profile, then portable auth, then legacy fallbacks.

    A named profile deliberately outranks BOSWELL_API_KEY. This prevents a stale
    machine-wide environment variable from silently crossing tenant boundaries.
    Machines without profiles retain the original portable-key behavior.
    """
    profile = _profile_name()
    profile_key = _first_secret(TENANT_PROFILE_ROOT / f"{profile}.key") if profile else None
    if profile and not profile_key:
        return {}
    api_key = (
        profile_key
        or os.environ.get("BOSWELL_API_KEY", "").strip()
        or _first_secret(HOOK_KEY_FILE)
    )
    if api_key:
        return {"X-API-Key": api_key}
    internal = os.environ.get("BOSWELL_INTERNAL_SECRET", "").strip() or _first_secret(
        INTERNAL_SECRET_FILE)
    if internal:
        return {"X-Boswell-Internal": internal}
    return {}

