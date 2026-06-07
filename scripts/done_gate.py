#!/usr/bin/env python3
"""Stop hook handler: the "Tested-and-Complete" session-close gate.

Blocks the stop EXACTLY ONCE per session to force a per-file verification
report, then allows every subsequent stop. Only gates sessions that actually
MUTATED files — greeting/orientation turns (boswell_startup only, no Edit/Write)
are never nagged.

`evaluate(data)` returns the decision dict (or None to allow) for the in-process
dispatcher. A __main__ entry keeps it runnable standalone for testing.

Fail-open by design: any error -> allow the stop (return None / exit 0).
Paired with the Boswell methodology "Tested-and-Complete Gate" (command-center).
"""
import sys
import os
import json
import re
import tempfile
from pathlib import Path

REASON = (
    "SESSION-CLOSE GATE — before this session ends, post the Tested-and-Complete report. "
    "For EVERY file touched this session, mark it VERIFIED COMPLETE (with evidence) or "
    "INCOMPLETE/UNTESTED (flagged) — no silent done. Confirm the gates that apply: "
    "(1) reuse-scoped (2) php -l clean (3) deployed + live commit hash matches "
    "(4) functionally proven on the real target with captured output "
    "(5) test artifacts cleaned up (6) Boswell recorded. If every touched file is already "
    "verified above, give the one-line per-file status and stop."
)

MUTATION_RE = re.compile(r'"name"\s*:\s*"(Edit|Write|MultiEdit|NotebookEdit)"')


def evaluate(data):
    """Return {'decision':'block','reason':...} to gate, or None to allow."""
    sid = (data or {}).get("session_id") or "nosession"
    sentinel = Path(tempfile.gettempdir()) / f"claude_doneprompt_{sid}"

    # Already prompted this session -> allow.
    if sentinel.exists():
        return None

    # Nothing to gate if no files were mutated this session. The greeting/
    # orientation turn (boswell_startup only) is BY DESIGN a no-work turn.
    # Fail-open: if transcript_path is missing/unreadable, fall through and gate.
    tp = (data or {}).get("transcript_path")
    if tp and os.path.isfile(tp):
        try:
            with open(tp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if not MUTATION_RE.search(content):
                return None
        except Exception:
            pass  # unreadable -> gate (fail-open toward the original posture)

    # Record that we've prompted; fail open if we can't write the sentinel.
    try:
        sentinel.touch()
    except Exception:
        return None

    return {"decision": "block", "reason": REASON}


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    result = evaluate(data)
    if result:
        sys.stdout.write(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
