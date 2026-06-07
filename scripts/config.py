"""Central path configuration for boswell-hooks scripts.

Reads from environment variables (optionally loaded from a sibling .env)
with sensible Path.home()-based defaults, so the plugin works on any machine
with no .env present. All paths resolve machine-local under ~ — never inside
the synced plugin directory.
"""
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENV_FILE = _HERE / ".env"


def _load_env_file():
    """Minimal .env parser. No python-dotenv dependency.
    Values already set in the process env take precedence."""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

_HOME = Path.home()

CLAUDE_MD_PATH = Path(os.environ.get(
    "CLAUDE_MD_PATH", str(_HOME / "Projects" / "CLAUDE.md")))
CLAUDE_NOTES_PATH = Path(os.environ.get(
    "CLAUDE_NOTES_PATH", str(_HOME / "Projects" / "CLAUDE-SESSION-NOTES.md")))
BOSWELL_DATA_ROOT = Path(os.environ.get(
    "BOSWELL_DATA_ROOT", str(_HOME / "boswell-data")))
BOSWELL_TRANSCRIPTS_ARCHIVE = Path(os.environ.get(
    "BOSWELL_TRANSCRIPTS_ARCHIVE", str(_HOME / "boswell-transcripts")))

# Machine-local state root for hook bookkeeping (queues, timers, checkpoints).
# NEVER inside the plugin dir — must not sync between machines.
STATE_ROOT = Path(os.environ.get(
    "BOSWELL_HOOK_STATE", str(_HOME / ".claude" / "hooks" / "state")))

# Boswell v3 REST substrate. /v2/* is the durable, MCP-independent surface
# (the layer the iOS thin client and these hooks both target). Env-overridable
# so a tenant can point at their own deployment without code changes.
BOSWELL_API_BASE = os.environ.get(
    "BOSWELL_API_BASE",
    "https://delightful-imagination-production-f6a1.up.railway.app").rstrip("/")

# Machine-local file holding the tenant-scoped `bos_` API key for server-to-server
# commits (X-API-Key). NOT synced — each machine/tenant has its own. This is the
# portability seam: identical plugin code, per-machine key file.
HOOK_KEY_FILE = Path(os.environ.get(
    "BOSWELL_HOOK_KEY_FILE", str(_HOME / ".boswell" / "hook_key")))


def hook_api_key():
    """Resolve the tenant-scoped `bos_` API key for hook→/v2 commits.

    Priority: env BOSWELL_API_KEY → ~/.boswell/hook_key (first non-empty line)
    → None. Returns None (never raises) when no key is configured, so callers
    degrade gracefully (e.g. transcript flush keeps the queue + re-emits the
    fallback marker instead of losing data).
    """
    env_key = os.environ.get("BOSWELL_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        for line in HOOK_KEY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return None


if __name__ == "__main__":
    print(f"CLAUDE_MD_PATH              = {CLAUDE_MD_PATH}")
    print(f"CLAUDE_NOTES_PATH           = {CLAUDE_NOTES_PATH}")
    print(f"BOSWELL_DATA_ROOT           = {BOSWELL_DATA_ROOT}")
    print(f"BOSWELL_TRANSCRIPTS_ARCHIVE = {BOSWELL_TRANSCRIPTS_ARCHIVE}")
    print(f"STATE_ROOT                  = {STATE_ROOT}")
    print(f"BOSWELL_API_BASE            = {BOSWELL_API_BASE}")
    print(f"HOOK_KEY_FILE               = {HOOK_KEY_FILE}")
    print(f"hook_api_key() present      = {bool(hook_api_key())}")
