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
a working mechanism. Verify this list against the harness before trusting
it — do not inherit it either.

All handler state lives machine-local under ~ (see each module), never inside
this synced plugin directory.
"""
import sys
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("BOSWELL_AGENT_ID", "Claude Code")
os.environ.setdefault(
    "BOSWELL_HOOK_STATE", str(Path.home() / ".boswell" / "claude-hooks"))

# Claude persists each additionalContext over 10,000 characters to disk.
# Leave room for recovery instructions and bounded health notices inline.
CLAUDE_ORIENTATION_MAX_CHARS = 9000
CLAUDE_CONTEXT_MAX_CHARS = 9800


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
    # one correctly staying quiet, which once hid a dead transcript pipeline for
    # weeks. Still swallowed, now recorded.
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
    """Load and emit the same durable one-time startup receipt as Codex."""
    import codex_dispatcher

    result = codex_dispatcher._session_start(data, max_chars=CLAUDE_ORIENTATION_MAX_CHARS)
    if result is None:
        return

    notices = []
    try:
        import transcript_monitor
        notice = transcript_monitor.check_pending()
        if notice:
            notices.append(notice)
    except Exception as exc:
        try:
            import hook_health
            hook_health.note_error("check_pending", exc)
        except Exception:
            pass
    try:
        import hook_health
        notice = hook_health.report()
        if notice:
            notices.append(notice)
    except Exception:
        pass

    if notices:
        output = result.get("hookSpecificOutput")
        if isinstance(output, dict) and output.get("additionalContext"):
            extra = "\n" + "\n".join(notices)
            room = max(0, CLAUDE_CONTEXT_MAX_CHARS - len(output["additionalContext"]))
            if len(extra) > room:
                marker = "\n[Health notices shortened for host context budget.]"
                extra = extra[:max(0, room - len(marker))] + marker[:room]
            output["additionalContext"] += extra
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


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
    # distinction is the whole point of structural hook design.
    # The shared gate recovers a session whose SessionStart never completed
    # (host timeout during an I/O stall, brief outage) by performing the one
    # startup it owed and returning the receipt; otherwise it fails closed.
    try:
        import codex_dispatcher
        gate = codex_dispatcher.prompt_startup_gate(data, max_chars=CLAUDE_ORIENTATION_MAX_CHARS)
    except Exception:
        gate = {
            "continue": False,
            "stopReason": "Boswell startup continuity is missing for this session.",
            "systemMessage": "Boswell startup continuity is missing for this session.",
        }
    if gate is not None:
        sys.stdout.write(json.dumps(gate, ensure_ascii=True))
        return
    import opening_briefing
    opening = opening_briefing.on_prompt(data)
    if opening is not None:
        sys.stdout.write(json.dumps(opening, ensure_ascii=True))
        return
    try:
        import prompt_retrieval
        result = prompt_retrieval.evaluate(data)
    except Exception:
        result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _post_tool(data):
    tool = data.get("tool_name") or ""
    if tool == "Bash":
        import transcript_monitor
        _safe(transcript_monitor.heartbeat, data.get('transcript_path'), data.get('session_id'))
    # Record qualifying Boswell reads (search/recall/semantic_search/fetch) into
    # the per-session read-state ledger that corrective_gate consults. Without
    # this the gate has no evidence ledger to check and silently allows every
    # corrective write — the gate would exist but never fire.
    import readstate
    _safe(readstate.record, data)
    # ...and into the SEPARATE ledger the shared continuity lane checks. Since
    # v2.2.0 Claude's PreToolUse runs codex_dispatcher._pre_tool first, whose
    # corrective check reads session_state["boswell_read_tokens"], which nothing
    # on the Claude side ever wrote — so every corrective commit was refused.
    import codex_dispatcher
    _safe(codex_dispatcher.record_read, data)
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
    # exists. A per-turn banner becomes wallpaper, so this carries the data
    # instead of the instruction. Ordered last: the two DENY gates get first
    # refusal, and injection can never mask a block.
    # Structural continuity is the first lane. Claude and Codex share the same
    # durable session receipt, so a material tool cannot run if SessionStart did
    # not complete or its cache disappeared.
    try:
        import codex_dispatcher
        result = codex_dispatcher._pre_tool(data)
    except Exception:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Boswell startup governance could not verify this session."
                ),
            }
        }
    if result is not None:
        sys.stdout.write(json.dumps(result))
        return

    result = None
    try:
        import git_guard
        result = git_guard.evaluate(data)
    except Exception:
        result = None
    # protected_paths is a FOURTH deny lane (2026-08-07). It spans both Bash and
    # the mutation tools, because the motivating incident destroyed files
    # through a SCRIPT — a mutation-tool guard never
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
    # deploy_memory is a FIFTH lane (2026-08-07), injection not denial. It keys
    # on the REMOTE a `git push` targets, where read_before_code keys on the
    # file an edit targets. It exists because on 2026-08-07 a push went to a
    # staging server decommissioned two months earlier: Boswell held that fact
    # twice, but UserPromptSubmit retrieval fires on the user's prompt and the
    # prompt was "push it and deploy" — four words with nothing to match on.
    # The tool call was the only moment that carried the target's name.
    # Ordered after git_guard so a force-push DENY still wins outright.
    if result is None:
        try:
            import deploy_memory
            result = deploy_memory.evaluate(data)
        except Exception:
            result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _stop(data):
    # The ONLY handler permitted to emit decision JSON on stdout.
    import opening_briefing
    opening = opening_briefing.on_stop(data)
    if opening is not None:
        sys.stdout.write(json.dumps(opening, ensure_ascii=True))
        return
    try:
        import done_gate
        result = done_gate.evaluate(data)
    except Exception:
        result = None
    if result:
        sys.stdout.write(json.dumps(result))


def _session_end(data):
    import transcript_monitor
    # sync_session removed: it POSTed to a /sync endpoint that 404s (the real
    # route is /v2/sync with a different payload). The actual session record is
    # the transcript capture below, not this dead call.
    _safe(transcript_monitor.capture, data.get('transcript_path'), data.get('session_id'))
    # Drain the just-captured card (and any backlog) to Boswell in Python, so a
    # transcript never sits waiting on the LLM to honor a marker next session.
    _safe(transcript_monitor.flush_pending_transcripts)
    import transcript_spool
    _safe(transcript_spool.flush_pending)


def _pre_compact(data):
    import codex_dispatcher
    result=codex_dispatcher._pre_compact({**data,"client":"claude"})
    if result is not None:
        sys.stdout.write(json.dumps(result,ensure_ascii=True))


_ROUTES = {
    "SessionStart": _session_start,
    "UserPromptSubmit": _user_prompt,
    "PreToolUse": _pre_tool,
    "PostToolUse": _post_tool,
    "Stop": _stop,
    "SessionEnd": _session_end,
    "PreCompact": _pre_compact,
}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    data = _read_input()
    handler = _ROUTES.get(event)
    if handler:
        import tenant_binding
        try:
            tenant_binding.bind_session(data, create=event == 'SessionStart')
        except tenant_binding.TenantBindingError as exc:
            if event == 'PreToolUse':
                result = {'hookSpecificOutput': {'hookEventName': event,
                    'permissionDecision': 'deny', 'permissionDecisionReason': str(exc)}}
            else:
                result = {'continue': False, 'stopReason': str(exc)}
            sys.stdout.write(json.dumps(result))
            sys.exit(2)
        handler(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
