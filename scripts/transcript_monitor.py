"""Transcript Monitor — CC Session Auto-Capture to Boswell (boswell-hooks plugin copy).
Consensus design: CC/CW /argue 2026-03-31.

Two-tier: raw JSONL archived to ~/boswell-transcripts/{machine}/{YYYY-MM}/, rich
index card queued for Claude to commit to Boswell at next session start.

Plugin adaptations: config is imported as a SIBLING module; all state/archive
paths resolve machine-local under ~ (never inside the synced plugin dir).
In the plugin wiring, capture() runs at SessionEnd (once) and heartbeat() runs
on PostToolUse(Bash) as a 30-min mid-session safety net.
"""
import os
import sys
import json
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from config import BOSWELL_TRANSCRIPTS_ARCHIVE, STATE_ROOT
except ImportError:
    BOSWELL_TRANSCRIPTS_ARCHIVE = Path.home() / "boswell-transcripts"
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

BOSWELL_BRANCH = "transcripts"
MACHINE_NAME = socket.gethostname().lower()
ARCHIVE_ROOT = BOSWELL_TRANSCRIPTS_ARCHIVE / MACHINE_NAME
# Route hook state through config.STATE_ROOT (env-overridable) instead of a
# hardcoded ~/.claude path, so the plugin is tenant/machine-portable.
STATE_FILE = STATE_ROOT / "transcript_monitor.json"
CLAUDE_PROJECTS = Path(os.environ.get(
    "CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
HEARTBEAT_INTERVAL = 1800  # 30 minutes
MAX_SUMMARY_LEN = 2000
MAX_TOOL_RESULT_LEN = 50000


def get_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_checkpoint": 0, "last_session_id": None, "last_file_size": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def find_current_session():
    best, best_mtime = None, 0
    if not CLAUDE_PROJECTS.exists():
        return None
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.rglob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime, best = mtime, f
            except OSError:
                continue
    return best


def parse_session(jsonl_path):
    messages, session_id, first_prompt, branch, version, user_texts = [], None, None, None, None, []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = entry.get("type")
            if etype == "user":
                msg = entry.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    if first_prompt is None:
                        first_prompt = content[:500]
                    user_texts.append(content)
                if session_id is None:
                    session_id = entry.get("sessionId")
                if branch is None:
                    branch = entry.get("gitBranch", "")
                if version is None:
                    version = entry.get("version", "")
            elif etype == "assistant":
                msg = entry.get("message", {})
                blocks = msg.get("content", [])
                if isinstance(blocks, list):
                    for block in blocks:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            text = block.get("content", "")
                            if isinstance(text, str) and len(text) > MAX_TOOL_RESULT_LEN:
                                block["content"] = text[:MAX_TOOL_RESULT_LEN] + "... [truncated]"
            messages.append(entry)
    summary_parts, char_count = [], 0
    for text in user_texts:
        remaining = MAX_SUMMARY_LEN - char_count
        if remaining <= 0:
            break
        chunk = text[:remaining]
        summary_parts.append(chunk)
        char_count += len(chunk)
    return {
        "session_id": session_id or jsonl_path.stem,
        "first_prompt": first_prompt or "",
        "summary": " | ".join(summary_parts),
        "message_count": len(messages),
        "branch": branch or "",
        "version": version or "",
        "messages": messages,
    }


def archive_session(jsonl_path, session_id):
    month_dir = ARCHIVE_ROOT / datetime.now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    archive_path = month_dir / f"{session_id}.jsonl"
    shutil.copy2(str(jsonl_path), str(archive_path))
    return str(archive_path)


def build_index_card(parsed, archive_path):
    return {
        "session_id": parsed["session_id"],
        "machine": MACHINE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message_count": parsed["message_count"],
        "branch": parsed["branch"],
        "version": parsed["version"],
        "first_prompt": parsed["first_prompt"],
        "summary": parsed["summary"],
        "archive_path": archive_path,
    }


def queue_for_boswell(index_card, session_id):
    queue_file = STATE_FILE.parent / "pending_transcripts.json"
    pending = []
    if queue_file.exists():
        try:
            pending = json.loads(queue_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pending = []
    pending = [p for p in pending if p.get("session_id") != session_id]
    pending.append({
        "session_id": session_id,
        "index_card": index_card,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    })
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    return True


def log(message):
    log_file = STATE_FILE.parent / "transcript_monitor.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def capture():
    session_file = find_current_session()
    if not session_file:
        log("No session file found")
        return
    parsed = parse_session(session_file)
    session_id = parsed["session_id"]
    archive_path = archive_session(session_file, session_id)
    log(f"Archived {session_id} -> {archive_path}")
    queue_for_boswell(build_index_card(parsed, archive_path), session_id)
    log(f"Queued {session_id} for Boswell commit")
    state = get_state()
    state["last_checkpoint"] = time.time()
    state["last_session_id"] = session_id
    state["last_file_size"] = session_file.stat().st_size
    save_state(state)


def heartbeat():
    state = get_state()
    if time.time() - state.get("last_checkpoint", 0) < HEARTBEAT_INTERVAL:
        return
    session_file = find_current_session()
    if not session_file:
        return
    if session_file.stat().st_size == state.get("last_file_size", 0):
        return
    log("Heartbeat triggered")
    capture()


def _queue_path():
    return STATE_FILE.parent / "pending_transcripts.json"


def _read_queue():
    qf = _queue_path()
    if not qf.exists():
        return []
    try:
        data = json.loads(qf.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_queue(entries):
    """Crash-safe queue rewrite (temp + atomic replace)."""
    qf = _queue_path()
    qf.parent.mkdir(parents=True, exist_ok=True)
    tmp = qf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    import os
    os.replace(str(tmp), str(qf))


def flush_pending_transcripts():
    """Commit queued transcript index-cards to Boswell IN PYTHON (no LLM).

    For each queued entry, POST to /v2/commit via boswell.commit_memory. On
    success the entry is dropped; on failure (no key, network, non-201) it is
    RETAINED so nothing is lost — the next session retries, and check_pending()
    re-emits the fallback marker so an authenticated LLM can still rescue it.

    Returns (committed_count, remaining_count). Never raises.
    """
    pending = _read_queue()
    if not pending:
        return (0, 0)
    try:
        import boswell
    except Exception as e:
        log(f"flush: boswell import failed: {e}")
        return (0, len(pending))

    survivors, committed = [], 0
    for entry in pending:
        card = entry.get("index_card")
        sid = entry.get("session_id", "?")
        if not card:
            continue  # malformed entry → drop silently
        machine = card.get("machine", MACHINE_NAME)
        fp = (card.get("first_prompt") or "").replace("\n", " ")[:80]
        ok, info = boswell.commit_memory(
            branch=BOSWELL_BRANCH,
            content=card,
            content_type="transcript",
            message=f"TRANSCRIPT: {sid[:8]} ({machine}, "
                    f"{card.get('message_count', '?')} msgs) — {fp}",
            tags=["transcript", "cc-session", machine],
        )
        if ok:
            committed += 1
            log(f"flush: committed {sid[:8]} -> {str(info)[:12]}")
        else:
            survivors.append(entry)
            log(f"flush: retained {sid[:8]} ({info})")
    if committed:
        _write_queue(survivors)
    return (committed, len(survivors))


def check_pending():
    """Drain the queue in Python first; only emit the LLM-fallback marker if
    commits failed (e.g. no `bos_` key yet) and entries remain."""
    committed, remaining = flush_pending_transcripts()
    if remaining <= 0:
        return
    queue_file = _queue_path()
    print(f"\n<!-- PENDING_TRANSCRIPTS: {remaining} -->")
    print("Claude Code: Python flush could not commit these (missing/invalid "
          "hook API key or network). Fallback - process them via MCP:")
    print(f"Queue file: {queue_file}")
    print("For each entry, call boswell_commit with branch='transcripts',")
    print("content=entry['index_card'], content_type='transcript',")
    print(f"tags=['transcript','cc-session','{MACHINE_NAME}'].")
    print("Then remove committed entries from the queue file.")
    print("<!-- END_PENDING_TRANSCRIPTS -->\n")


if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if cmd in ("stop", "capture"):
        capture()
    elif cmd == "heartbeat":
        heartbeat()
    elif cmd == "check_pending":
        check_pending()
    elif cmd == "flush":
        c, r = flush_pending_transcripts()
        print(f"flush: committed={c} remaining={r}")
    else:
        print("Usage: transcript_monitor.py <stop|heartbeat|capture|check_pending|flush>")
        sys.exit(1)
