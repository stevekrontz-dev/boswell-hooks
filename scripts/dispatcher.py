#!/usr/bin/env python3
"""boswell-hooks plugin: single hook entry point.

Invoked through the Claude hook catalog's `python`/`python3` launcher fallback.

Reads the hook JSON from stdin ONCE and routes to in-process handlers (one
Python process per event instead of one per command). Every handler is wrapped
fail-open: a handler that raises must never break the session. Only the
PreToolUse and Stop handlers may emit decision JSON on stdout.

All handler state lives machine-local under ~ (see each module), never inside
this synced plugin directory.
"""
import sys
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _read_input():
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass  # fail-open: a broken handler must not break the session


def _session_start(data):
    import boswell
    import transcript_monitor
    _safe(boswell.session_start)
    # Markers dropped 2026-06-06 (all pointed at data the LLM already has or that
    # doesn't exist):
    #  - load_sacred: boswell_startup (mandated by CLAUDE.md) already returns the
    #    sacred_manifest full-text — was a redundant double-load.
    #  - load_tool_registry: no curated tool_registry exists in Boswell, and the
    #    harness already surfaces the full tool/skill/MCP inventory at startup.
    # check_pending now drains the queue in Python first (commit_memory), and
    # only emits a fallback marker if commits failed and entries remain.
    _safe(transcript_monitor.check_pending)


def _user_prompt(data):
    # Dormant: the CHECK_EXPIRING_PRIORITIES marker was dropped 2026-06-06.
    # It asked the LLM to search for `priority_until`-expiring commits, but that
    # field does not exist in the data model; the real expiring signal
    # (expiring_bookmarks) already ships in the boswell_startup payload. The
    # UserPromptSubmit hook registration was removed from hooks.json, so this
    # handler is no longer invoked — kept (no-op) for one-line restore if a
    # genuine per-prompt, data-backed nudge is wanted later.
    return


def _post_tool(data):
    tool = data.get("tool_name") or ""
    file_path = ""
    ti = data.get("tool_input")
    if isinstance(ti, dict):
        file_path = ti.get("file_path") or ti.get("path") or ""
    import boswell
    _safe(boswell.log_tool, tool, file_path)
    if tool == "Bash":
        import transcript_monitor
        _safe(transcript_monitor.heartbeat)
    # Record qualifying Boswell reads (search/recall/semantic_search/fetch) into
    # the per-session read-state ledger that corrective_gate consults. Without
    # this the gate has no evidence ledger to check and silently allows every
    # corrective write — the gate would exist but never fire.
    import readstate
    _safe(readstate.record, data)


def _pre_tool(data):
    # Emits a PreToolUse decision (deny/ask) on stdout. Two independent,
    # mutually-exclusive guards: git_guard fires only on Bash `git push`,
    # corrective_gate fires only on the Boswell commit tool. Each is fail-open
    # (returns None when not applicable / on error), so only one can ever
    # produce a decision for a given call.
    #
    # corrective_gate + readstate were carried forward from the v1 plugin
    # (~/.claude/skills/boswell-hooks) on 2026-07-15. v2 shipped git_guard but
    # dropped both, so neither plugin was a superset: v1 had the
    # read-before-corrective-write gate and a DEAD `import git_guard`; v2 had a
    # working git_guard and no corrective gate. INSTALL.md advertises
    # "read-before-corrective-write governance", so v2 alone did not match its
    # own documentation. This merge is what both files claimed to be.
    result = None
    try:
        import git_guard
        result = git_guard.evaluate(data)
    except Exception:
        result = None
    if result is None:
        try:
            import corrective_gate
            result = corrective_gate.evaluate(data)
        except Exception:
            result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _stop(data):
    # The ONLY handler permitted to emit decision JSON on stdout.
    try:
        import done_gate
        result = done_gate.evaluate(data)
    except Exception:
        result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _session_end(data):
    import boswell
    import transcript_monitor
    _safe(boswell.session_end)
    # sync_session removed: it POSTed to a /sync endpoint that 404s (the real
    # route is /v2/sync with a different payload). The actual session record is
    # the transcript capture below, not this dead call.
    _safe(transcript_monitor.capture)
    # Drain the just-captured card (and any backlog) to Boswell in Python, so a
    # transcript never sits waiting on the LLM to honor a marker next session.
    _safe(transcript_monitor.flush_pending_transcripts)


_ROUTES = {
    "SessionStart": _session_start,
    "UserPromptSubmit": _user_prompt,
    "PreToolUse": _pre_tool,
    "PostToolUse": _post_tool,
    "Stop": _stop,
    "SessionEnd": _session_end,
}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    data = _read_input()
    handler = _ROUTES.get(event)
    if handler:
        handler(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
