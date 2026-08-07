#!/usr/bin/env python3
"""boswell-hooks plugin: single hook entry point.

Invoked through the Claude hook catalog's `python`/`python3` launcher fallback.

Reads the hook JSON from stdin ONCE and routes to in-process handlers (one
Python process per event instead of one per command). Every handler is wrapped
fail-open: a handler that raises must never break the session.

WHICH EVENTS MAY WRITE TO STDOUT (re-verified against the harness 2026-08-06,
not inherited):
  * PreToolUse  — decision JSON (deny/ask) and/or additionalContext.
  * Stop        — decision JSON. The done-gate.
  * UserPromptSubmit — additionalContext. Live since 2026-08-04.
  * PostToolUse — additionalContext. Live-verified 2026-08-06.

This block used to read "Only the PreToolUse and Stop handlers may emit". That
was true when it was written in June and silently stopped being true when
prompt_retrieval started injecting on UserPromptSubmit; nobody re-checked it, so
it sat here as a false constraint that would have talked the next author out of
a working mechanism. Steve, 2026-08-06: "the first run at a contract may be
stale due to progression." Verify this list against the harness before trusting
it — do not inherit it either.

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
    # Fail-open: a broken handler must never break the session. But `except:
    # pass` also made a handler that raises EVERY time indistinguishable from
    # one correctly staying quiet, which is how b003a5c1 hid a dead transcript
    # pipeline for three weeks. Still swallowed, now recorded.
    try:
        fn(*args)
    except Exception as exc:
        try:
            import hook_health
            hook_health.note_error(getattr(fn, "__name__", str(fn)), exc)
        except Exception:
            pass
        return
    try:
        import hook_health
        hook_health.note_ok(getattr(fn, "__name__", str(fn)))
    except Exception:
        pass


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
    # Surface any handler that has been failing open. SessionStart is the only
    # moment this is worth session context: it is once per session, and a hook
    # that is silently dead has been dead since before this session started.
    try:
        import hook_health
        notice = hook_health.report()
        if notice:
            print("\n" + notice + "\n")
    except Exception:
        pass


def _user_prompt(data):
    # REVIVED 2026-08-04. History: this handler carried a
    # CHECK_EXPIRING_PRIORITIES marker that asked the LLM to search for
    # `priority_until`-expiring commits — a field that does not exist in the
    # data model. The marker was dropped 2026-06-06 and the UserPromptSubmit
    # registration was pulled with it, which left the Claude surface with ZERO
    # per-turn Boswell contact for two months while Codex kept its retrieval.
    #
    # The replacement is deliberately NOT another marker. prompt_retrieval runs
    # a real Boswell search on the prompt and injects the hits, so the model
    # gets data it does not have rather than an instruction it will skim. That
    # distinction is the whole point of the original 2026-06-06 removal, and of
    # the STRUCTURAL-NOT-ASPIRATIONAL commitment.
    try:
        import prompt_retrieval
        result = prompt_retrieval.evaluate(data)
    except Exception:
        result = None
    if result:
        sys.stdout.write(json.dumps(result))


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
    # PostToolUse may also return additionalContext. The "only PreToolUse and
    # Stop may emit" line in this module's docstring is a June artifact that
    # UserPromptSubmit already disproved when prompt_retrieval began injecting
    # on 2026-08-04; it was never re-checked against the harness. Verified
    # 2026-08-06 rather than inherited.
    try:
        import empty_result
        result = empty_result.evaluate(data)
    except Exception:
        result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _pre_tool(data):
    # Emits a PreToolUse decision (deny/ask) or an additionalContext injection
    # on stdout. THREE independent, mutually-exclusive lanes: git_guard fires
    # only on Bash `git push`, corrective_gate only on the Boswell commit tool,
    # read_before_code only on the file-mutation tools. Each is fail-open
    # (returns None when not applicable / on error), so only one can ever
    # produce output for a given call.
    #
    # corrective_gate + readstate were carried forward from the v1 plugin
    # (~/.claude/skills/boswell-hooks) on 2026-07-15. v2 shipped git_guard but
    # dropped both, so neither plugin was a superset: v1 had the
    # read-before-corrective-write gate and a DEAD `import git_guard`; v2 had a
    # working git_guard and no corrective gate. INSTALL.md advertises
    # "read-before-corrective-write governance", so v2 alone did not match its
    # own documentation. This merge is what both files claimed to be.
    #
    # read_before_code is a THIRD, non-overlapping lane (2026-08-04): it fires
    # only on the file-mutation tools, which neither guard above matches, and it
    # never denies — it injects the Boswell prior state for the file as
    # additionalContext so the stored state is in the window before the edit
    # exists. Steve's ask was "beaten in the head with grok before coding every
    # turn"; a per-turn banner is the context-marker failure mode the sacred
    # STRUCTURAL-NOT-ASPIRATIONAL commitment names, so this carries the data
    # instead of the instruction. Ordered last: the two DENY gates get first
    # refusal, and injection can never mask a block.
    result = None
    try:
        import git_guard
        result = git_guard.evaluate(data)
    except Exception:
        result = None
    # protected_paths is a FOURTH deny lane (2026-08-07). It spans both Bash and
    # the mutation tools, because the incident that motivated it (M5 session
    # a214e3fa) destroyed files through a SCRIPT — a mutation-tool guard never
    # saw it. No-op unless the project ships a .boswell-protect file, so it
    # costs nothing on installs that never opt in.
    if result is None:
        try:
            import protected_paths
            result = protected_paths.evaluate(data)
        except Exception:
            result = None
    if result is None:
        try:
            import corrective_gate
            result = corrective_gate.evaluate(data)
        except Exception:
            result = None
    if result is None:
        try:
            import read_before_code
            result = read_before_code.evaluate(data)
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
