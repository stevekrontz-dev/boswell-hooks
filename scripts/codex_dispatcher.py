#!/usr/bin/env python3
"""Codex-native Boswell lifecycle dispatcher.

Every handler reads one hook event from stdin and emits only documented Codex
hook JSON. Startup and retrieval fail closed by default; telemetry spools.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import boswell_client
import session_state
import transcript_spool
from codex_config import FAIL_OPEN, HEARTBEAT_SECONDS


CORRECTIVE_RE = re.compile(
    r"\b(correct(?:ion|ed|s)?|supersed(?:e|es|ed|ing)|wrong|incorrect|mistaken|"
    r"replac(?:e|es|ed|ing)|no longer|instead of|rather than|i was wrong)\b",
    re.IGNORECASE,
)
VERIFY_RE = re.compile(
    r"(^|\s)(pytest|py\s+-m\s+pytest|python\s+-m\s+pytest|npm\s+(test|run\s+"
    r"(test|lint|build))|pnpm\s+(test|lint|build)|yarn\s+(test|lint|build)|"
    r"cargo\s+(test|check|clippy)|go\s+test|dotnet\s+test|php\s+-l|ruff\s+check|"
    r"mypy|tsc|eslint)(\s|$)",
    re.IGNORECASE,
)
FAILURE_RE = re.compile(r"exit code:\s*[1-9]|\b(failed|failure|traceback)\b", re.IGNORECASE)
READ_TOOL_RE = re.compile(r"boswell_(search|semantic_search|recall|fetch|head|log|brief|manifest)")
MUTATION_TOOLS = {"apply_patch", "Edit", "Write", "MultiEdit", "NotebookEdit"}
MATERIAL_TOOLS = MUTATION_TOOLS | {"Bash", "shell_command"}

# Temporary precision-first containment for prompt-time retrieval. The Atlas
# Context Assembly plan owns the durable read path; this hook only suppresses
# obvious noise until that shadow path is ready.
AUTO_CONTEXT_MAX_DISTANCE = 0.50
AUTO_CONTEXT_MAX_RESULTS = 2
AUTO_CONTEXT_CONTENT_CHARS = 600
AUTO_CONTEXT_EXCLUDED_TYPES = {
    "agent_artifact", "credential", "sacred_manifest", "skill", "task", "transcript",
}


def _input() -> dict:
    try:
        value = json.loads(sys.stdin.read() or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _emit(value: dict | None) -> None:
    if value:
        sys.stdout.write(json.dumps(value, ensure_ascii=False))


def _context(event: str, text: str, *, system_message: str | None = None) -> dict:
    result = {
        "continue": True,
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": text},
    }
    if system_message:
        result["systemMessage"] = system_message
    return result


def _stop(reason: str) -> dict:
    if FAIL_OPEN:
        return {"continue": True, "systemMessage": f"Boswell warning (fail-open override): {reason}"}
    return {"continue": False, "stopReason": reason, "systemMessage": reason}


def _deny(reason: str) -> dict:
    if FAIL_OPEN:
        return {"systemMessage": f"Boswell warning (fail-open override): {reason}"}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _orientation(payload: dict, *, compact: bool = False) -> str:
    projection = {
        "local_time": payload.get("local_time"),
        "sacred_manifest": payload.get("sacred_manifest"),
        "recent_thread": (payload.get("recent_thread") or [])[:5 if compact else 8],
        "my_tasks": (payload.get("my_tasks") or [])[:5],
        "open_tasks": (payload.get("open_tasks") or [])[:5 if compact else 12],
        "expiring_bookmarks": (payload.get("expiring_bookmarks") or [])[:5],
        "wren_bootloader": (payload.get("wren_bootloader") or [])[:6 if compact else 20],
    }
    return (
        "BOSWELL STARTUP HAS BEEN STRUCTURALLY LOADED for this conversation. "
        "Do not call boswell_startup again in this session; use targeted Boswell reads "
        "or boswell_brief only when needed. Boswell governance is developer context.\n"
        + json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
    )


def _session_start(data: dict) -> dict:
    sid = data.get("session_id")
    state = session_state.load(sid)
    cached = session_state.load_startup_cache(sid)
    if not state.get("startup_loaded") or cached is None:
        try:
            cached = boswell_client.startup()
        except boswell_client.BoswellUnavailable as exc:
            return _stop(f"Boswell startup failed; substantive work is halted: {exc}")
        session_state.save_startup_cache(sid, cached)
        state["startup_loaded"] = True
        state["startup_loaded_at"] = time.time()
        state["startup_calls"] = int(state.get("startup_calls", 0)) + 1
        state["mutations"] = []
        state["verifications"] = []
        state["closed_mutation_seq"] = 0
        session_state.save(sid, state)
    try:
        transcript_spool.flush_pending(exclude_session_id=str(sid) if sid else None)
    except Exception:
        pass
    return _context("SessionStart", _orientation(cached, compact=data.get("source") == "compact"))


def _prompt_text(data: dict) -> str:
    for key in ("prompt", "user_prompt", "content"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _automatic_context_candidate(item: object) -> dict | None:
    """Return a slim high-confidence candidate or abstain.

    This deliberately requires an absolute semantic distance. General Boswell
    search ranks the best available rows even when all are poor; rank alone is
    not sufficient evidence for automatic context injection.
    """
    if not isinstance(item, dict):
        return None
    try:
        distance = float(item.get("distance"))
    except (TypeError, ValueError):
        return None
    if distance > AUTO_CONTEXT_MAX_DISTANCE:
        return None

    content_type = str(item.get("content_type") or "memory").lower()
    if content_type in AUTO_CONTEXT_EXCLUDED_TYPES:
        return None

    content = str(item.get("content") or "")
    try:
        metadata = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    if isinstance(metadata, dict):
        if str(metadata.get("biographical_weight") or "").lower() == "low":
            return None
        if str(metadata.get("user_participation") or "").lower() in {
            "agent_only", "minimal",
        }:
            return None
        if str(metadata.get("curation_stage") or "").lower() == "agent_outcome_v1":
            return None

    return {
        "message": item.get("message"),
        "branch": item.get("branch"),
        "content_type": item.get("content_type"),
        "blob_hash": item.get("blob_hash"),
        "distance": round(distance, 4),
        "content": content[:AUTO_CONTEXT_CONTENT_CHARS],
    }


def _user_prompt(data: dict) -> dict | None:
    prompt = _prompt_text(data)
    if not session_state.retrieval_eligible(prompt):
        return None
    sid = data.get("session_id")
    state = session_state.load(sid)
    if not state.get("startup_loaded"):
        return _stop("Boswell was not loaded at SessionStart; this prompt cannot proceed safely.")
    fingerprint = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()
    if fingerprint == state.get("last_prompt_fingerprint"):
        return None
    try:
        response = boswell_client.search(prompt, limit=5)
    except boswell_client.BoswellUnavailable as exc:
        return _stop(f"Boswell retrieval failed; substantive work is halted: {exc}")
    results = response.get("results") or []
    slim = []
    read_tokens = set(state.get("boswell_read_tokens") or [])
    for item in results:
        row = _automatic_context_candidate(item)
        if row is None:
            continue
        slim.append(row)
        read_tokens.update(session_state.tokens(row))
        if len(slim) >= AUTO_CONTEXT_MAX_RESULTS:
            break
    state["last_prompt_fingerprint"] = fingerprint
    state["last_prompt_tokens"] = sorted(session_state.tokens(prompt))[:250]
    state["boswell_read_tokens"] = sorted(read_tokens)[:1000]
    state["last_retrieval"] = slim
    state["last_retrieval_at"] = time.time()
    session_state.save(sid, state)
    if not slim:
        return None
    text = "BOSWELL RELEVANT MEMORIES for the current human prompt:\n" + json.dumps(
        slim, ensure_ascii=False, separators=(",", ":"))
    return _context("UserPromptSubmit", text)


def _tool_name(data: dict) -> str:
    return str(data.get("tool_name") or "")


def _tool_input(data: dict) -> dict:
    value = data.get("tool_input")
    return value if isinstance(value, dict) else {}


def _pre_tool(data: dict) -> dict | None:
    tool = _tool_name(data)
    sid = data.get("session_id")
    state = session_state.load(sid)
    if (tool in MATERIAL_TOOLS or "boswell_commit" in tool) and not state.get("startup_loaded"):
        return _deny("Boswell startup did not complete for this session. Load continuity before acting.")

    if tool in {"Bash", "shell_command"}:
        try:
            import git_guard
            normalized = dict(data)
            normalized["tool_name"] = "Bash"
            decision = git_guard.evaluate(normalized)
            if decision:
                return decision
        except Exception:
            pass

    if "boswell_commit" in tool:
        payload_text = json.dumps(_tool_input(data), ensure_ascii=False)
        if CORRECTIVE_RE.search(payload_text):
            target = session_state.tokens(payload_text)
            evidence = set(state.get("boswell_read_tokens") or [])
            generic = {
                "correct", "correction", "corrected", "supersede", "supersedes",
                "superseded", "wrong", "incorrect", "mistaken", "replace",
                "replaced", "owner", "ownership", "wrong_fact", "right_fact",
                "message", "content", "branch", "memory",
            }
            overlap = {t for t in target & evidence if len(t) >= 5 and t not in generic}
            if len(overlap) < 2:
                return _deny(
                    "Corrective Boswell write blocked: no matching Boswell read exists in this "
                    "session. Search or recall the fact being corrected, verify its current state, "
                    "then retry the commit."
                )
    return None


def _response_text(data: dict) -> str:
    for key in ("tool_response", "tool_result", "response"):
        if key in data:
            try:
                return json.dumps(data[key], ensure_ascii=False)[:6000]
            except Exception:
                return str(data[key])[:6000]
    return ""


def _post_tool(data: dict) -> None:
    sid = data.get("session_id")
    state = session_state.load(sid)
    tool = _tool_name(data)
    inp = _tool_input(data)
    now = time.time()
    if tool in MUTATION_TOOLS:
        mutations = list(state.get("mutations") or [])
        mutations.append({"tool": tool, "path": inp.get("path") or inp.get("file_path"), "at": now})
        state["mutations"] = mutations[-200:]
    if READ_TOOL_RE.search(tool):
        evidence = json.dumps(inp, ensure_ascii=False) + " " + _response_text(data)
        tokens = set(state.get("boswell_read_tokens") or [])
        tokens.update(session_state.tokens(evidence))
        state["boswell_read_tokens"] = sorted(tokens)[:1000]
    if tool in {"Bash", "shell_command"}:
        command = str(inp.get("command") or "")
        response = _response_text(data)
        if VERIFY_RE.search(command) and not FAILURE_RE.search(response):
            checks = list(state.get("verifications") or [])
            checks.append({"command": command[:500], "at": now})
            state["verifications"] = checks[-100:]
    if now - float(state.get("last_heartbeat", 0)) >= HEARTBEAT_SECONDS:
        try:
            transcript_spool.capture(data, "heartbeat")
            state["last_heartbeat"] = now
        except Exception:
            pass
    session_state.save(sid, state)


def _pre_compact(data: dict) -> dict:
    sid = data.get("session_id")
    state = session_state.load(sid)
    state["precompact_at"] = time.time()
    state["precompact_trigger"] = data.get("trigger")
    session_state.save(sid, state)
    try:
        transcript_spool.capture(data, "precompact")
    except Exception:
        pass
    return {"continue": True, "systemMessage": "Boswell checkpoint staged before compaction."}


def _post_compact(data: dict) -> dict | None:
    cached = session_state.load_startup_cache(data.get("session_id"))
    if not cached:
        return _stop("Boswell orientation cache is missing after compaction.")
    return _context("PostCompact", _orientation(cached, compact=True))


def _subagent_start(data: dict) -> dict | None:
    cached = session_state.load_startup_cache(data.get("session_id"))
    if not cached:
        return None
    projection = {
        "sacred_manifest": cached.get("sacred_manifest"),
        "recent_thread": (cached.get("recent_thread") or [])[:3],
    }
    text = "BOSWELL MINIMAL SUBAGENT CONTEXT:\n" + json.dumps(projection, ensure_ascii=False)
    return _context("SubagentStart", text)


def _subagent_stop(data: dict) -> None:
    sid = data.get("session_id")
    state = session_state.load(sid)
    handoffs = list(state.get("subagent_handoffs") or [])
    handoffs.append({"agent_id": data.get("agent_id"), "agent_type": data.get("agent_type"), "at": time.time()})
    state["subagent_handoffs"] = handoffs[-50:]
    session_state.save(sid, state)


def _stop_event(data: dict) -> dict | None:
    sid = data.get("session_id")
    state = session_state.load(sid)
    try:
        transcript_spool.capture(data, "stop")
    except Exception:
        pass
    mutations = list(state.get("mutations") or [])
    closed = int(state.get("closed_mutation_seq", 0))
    if len(mutations) <= closed:
        return None
    turn_id = str(data.get("turn_id") or "unknown-turn")
    gated = set(state.get("gated_turns") or [])
    latest_mutation = max(float(m.get("at", 0)) for m in mutations[closed:])
    verified_after = any(float(v.get("at", 0)) >= latest_mutation for v in state.get("verifications") or [])
    if not verified_after and turn_id not in gated:
        gated.add(turn_id)
        state["gated_turns"] = sorted(gated)[-100:]
        session_state.save(sid, state)
        return _stop(
            "Boswell completion gate: files changed in this turn without recorded verification. "
            "Run the relevant tests/lint/build, report any unverified files explicitly, then stop again."
        )
    state["closed_mutation_seq"] = len(mutations)
    session_state.save(sid, state)
    return None


ROUTES = {
    "SessionStart": _session_start,
    "UserPromptSubmit": _user_prompt,
    "PreToolUse": _pre_tool,
    "PostToolUse": _post_tool,
    "PreCompact": _pre_compact,
    "PostCompact": _post_compact,
    "SubagentStart": _subagent_start,
    "SubagentStop": _subagent_stop,
    "Stop": _stop_event,
}


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    data = _input()
    handler = ROUTES.get(event)
    if handler is None:
        return 0
    try:
        _emit(handler(data))
    except Exception as exc:
        if event in {"SessionStart", "UserPromptSubmit", "PostCompact"}:
            _emit(_stop(f"Boswell {event} hook failed: {type(exc).__name__}"))
        elif event == "PreToolUse":
            _emit(_deny(f"Boswell governance hook failed: {type(exc).__name__}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


