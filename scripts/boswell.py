"""Boswell - Automated Session Scribe for Claude Code (boswell-hooks plugin copy).
Logs session activity locally and syncs to the stevekrontz.com/boswell API.

Adapted for the plugin: activity log + state live machine-local under ~/.boswell
(NOT in the synced plugin dir). config is imported as a sibling module.
"""
import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CLAUDE_MD_PATH, CLAUDE_NOTES_PATH, BOSWELL_API_BASE, hook_api_key)

# Machine-local scribe state (never inside the synced plugin dir).
_LOCAL = Path.home() / ".boswell"
NOTES_FILE = CLAUDE_NOTES_PATH
LOG_FILE = _LOCAL / "activity.log"
STATE_FILE = _LOCAL / "state.json"

# Durable v3 REST base (was the dead stevekrontz.com/boswell host — that returns
# empty on /v2/health). All API calls now target BOSWELL_API_BASE from config.
BOSWELL_API = BOSWELL_API_BASE
API_KEY = os.environ.get("BOSWELL_API_KEY", "")


def commit_memory(branch, content, content_type="memory", message="", tags=None):
    """Commit a memory to Boswell v3 via POST /v2/commit (server-to-server).

    Authenticates with the tenant-scoped `bos_` key (X-API-Key) from
    config.hook_api_key(); the commit lands on THAT key's tenant. This is the
    portable, MCP-independent write path (survives the v5 'MCP slim').

    Returns (ok: bool, commit_hash_or_error). Fail-closed: missing key, non-201,
    or any exception → (False, reason). Never raises — callers depend on this to
    decide whether to drop or retain queued work.
    """
    import requests
    key = hook_api_key()
    if not key:
        return False, "no_api_key"
    # NOTE: the /v2/commit field is `type`, not `content_type` (app.py create_commit).
    payload = {
        "branch": branch,
        "content": content,
        "type": content_type,
        "message": message or f"{content_type} commit",
        "tags": tags or [],
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": key,
        "User-Agent": "Boswell/1.0 (ClaudeCode hook)",
    }
    try:
        resp = requests.post(f"{BOSWELL_API}/v2/commit", json=payload,
                             headers=headers, timeout=15)
        if resp.status_code == 201:
            try:
                ch = resp.json().get("commit_hash", "")
            except Exception:
                ch = ""
            log(f"COMMIT ok branch={branch} type={content_type} hash={ch[:12]}")
            return True, ch
        log(f"COMMIT failed {resp.status_code}: {resp.text[:200]}")
        return False, f"http_{resp.status_code}"
    except Exception as e:
        log(f"COMMIT exception: {e}")
        return False, str(e)


def ensure_dirs():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def get_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"session_start": None, "actions": []}


def save_state(state):
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def log(message):
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def session_start():
    save_state({"session_start": datetime.now().isoformat(), "actions": []})
    log("SESSION START")
    print(f"\n{'='*60}")
    print("BOSWELL ACTIVE - Session recording started")
    print(f"Session notes: {NOTES_FILE}")
    print(f"{'='*60}\n")
    print("READ THESE FOR CONTEXT:")
    for f in (str(CLAUDE_MD_PATH), str(NOTES_FILE)):
        if os.path.exists(f):
            print(f"  - {f}")
    print()


def session_end(summary=None):
    state = get_state()
    log("SESSION END")
    if summary:
        append_session_notes(summary, state)


def log_action(action_type, details=""):
    state = get_state()
    state["actions"].append({
        "time": datetime.now().isoformat(),
        "type": action_type,
        "details": details,
    })
    save_state(state)
    log(f"ACTION: {action_type} - {details}")


def log_tool(tool_name, file_path=""):
    log_action(f"tool:{tool_name}", file_path)


def append_session_notes(summary, state):
    if not NOTES_FILE.exists():
        return
    content = NOTES_FILE.read_text(encoding="utf-8")
    marker = "---\n\n## Template for New Sessions"
    start_time = state.get("session_start", "")
    if start_time:
        start_time = datetime.fromisoformat(start_time).strftime("%I:%M %p")
    entry = f"""---

## Session: {datetime.now().strftime("%B %d, %Y")} - Auto-logged

**Started:** {start_time}
**Ended:** {datetime.now().strftime("%I:%M %p")}

### Summary
{summary}

### Actions Logged
"""
    actions = state.get("actions", [])
    if actions:
        counts = {}
        for a in actions:
            t = a.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        for t, c in counts.items():
            entry += f"- {t}: {c}\n"
    else:
        entry += "- No actions logged\n"
    entry += "\n"
    if marker in content:
        NOTES_FILE.write_text(content.replace(marker, entry + marker), encoding="utf-8")
        log(f"Appended session notes to {NOTES_FILE}")


def detect_project():
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split("/")[-1].replace(".git", "")
    except Exception:
        pass
    return os.path.basename(os.getcwd())


def summarize_actions(actions):
    counts = {}
    for a in actions:
        t = a.get("type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return {"total_actions": len(actions), "by_type": counts}


def extract_modified_files(actions):
    files = set()
    for a in actions:
        d = a.get("details", "")
        if d and ("/" in d or "\\" in d):
            files.add(d)
    return list(files)


def sync_session():
    import requests
    state = get_state()
    payload = {
        "source": "claude-code-tintatlanta-windows",
        "session_start": state.get("session_start"),
        "session_end": datetime.now().isoformat(),
        "project": detect_project(),
        "actions_summary": summarize_actions(state.get("actions", [])),
        "files_modified": extract_modified_files(state.get("actions", [])),
        "decisions": [], "blockers": [], "next_steps": [],
    }
    headers = {"Content-Type": "application/json",
               "User-Agent": "Boswell/1.0 (ClaudeCode Session Sync)"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        resp = requests.post(f"{BOSWELL_API}/sync", json=payload, headers=headers, timeout=15)
        result = resp.json()
        print(f"Synced to Boswell: session {result.get('session_id')}")
        log(f"SYNC: session_id={result.get('session_id')}")
    except Exception as e:
        log(f"SYNC FAILED: {e}")


if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if cmd == "start":
        session_start()
    elif cmd == "end":
        session_end(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else None)
    elif cmd == "log":
        log_action(sys.argv[2] if len(sys.argv) > 2 else "unknown",
                   " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "")
    elif cmd == "tool":
        log_tool(sys.argv[2] if len(sys.argv) > 2 else "unknown",
                 sys.argv[3] if len(sys.argv) > 3 else "")
    elif cmd == "sync":
        sync_session()
    else:
        print("Usage: boswell.py <start|end|log|tool|sync>")
        sys.exit(1)
