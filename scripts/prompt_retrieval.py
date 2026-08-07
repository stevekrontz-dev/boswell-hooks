"""Per-turn Boswell retrieval (boswell-hooks plugin).

UserPromptSubmit handler. On every substantive human prompt it runs a Boswell
search and injects the hits as `additionalContext`, so stored state is in the
window BEFORE the model reasons — not after it has already answered from
assumption.

WHY THIS EXISTS (Steve, 2026-08-04): "you are supposed to be beaten in the
fucking head with ask boswell and grok before coding EVERY FUCKING TURN."

WHY IT INJECTS DATA AND NEVER A REMINDER:
The obvious build is a per-turn string that says GROK BEFORE CODING. That is
precisely what the tenant's STRUCTURAL-NOT-ASPIRATIONAL commitment forbids —
"never delegated to the model via context markers it will ignore" — and
"injections carry data the model lacks, not tasks." A fixed string becomes
wallpaper within a handful of turns. Retrieved memories do not: they change
every turn and they carry facts the model provably does not have.

WHY IT IS NOT ENOUGH TO GATE ONLY THE EDIT PATH:
read_before_code.py covers mutations, so "grok before coding" is handled. But
on 2026-08-04 the model reported wrong fleet versions off stale git tracking
data without editing a single file — no mutation, no hook, no correction. Turns
where the model only reasons and answers are exactly where that drift lives.
This hook covers those.

BOSWELL-DOWN BEHAVIOUR:
The sacred commitment BOSWELL-DOWN-STOP says "Boswell unreachable = full stop:
halt, alert Steve, wait." The Codex adapter implements that by refusing the
turn. Doing that here would let a transient blip hard-block Steve's prompts,
which is its own failure. Instead this injects an explicit UNREACHABLE notice —
which is the *data* the model needs in order to honour the commitment itself
and tell Steve rather than silently proceeding as though it has memory.

Design constraints inherited from the plugin:
  * Fail-open on everything except an explicit Boswell outage, which is
    reported rather than swallowed.
  * Results are written into readstate's ledger, so a retrieval here counts as
    real evidence for corrective_gate and suppresses a duplicate query from
    read_before_code on the same subject.
  * State lives machine-local under config.STATE_ROOT.
"""
import hashlib
import json
import time
from pathlib import Path

try:
    import readstate
except Exception:  # pragma: no cover
    readstate = None

try:
    from config import STATE_ROOT
except Exception:  # pragma: no cover
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

SEARCH_TIMEOUT = 8.0
# See read_before_code.SEARCH_LIMIT: on-topic rows are routinely ranked 20-45
# deep, so a small window returns loosely-related noise while the actual answer
# is invisible. Widen the candidate set; the grounding gate is what selects.
SEARCH_LIMIT = 50
MAX_RESULTS = 3

STATE_NAME = "prompt_retrieval.json"


def _state_path():
    return STATE_ROOT / "readstate" / STATE_NAME


def _load_state():
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        cutoff = time.time() - 86400
        state = {k: v for k, v in state.items()
                 if isinstance(v, dict) and v.get("at", 0) > cutoff}
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _prompt_text(data):
    for key in ("prompt", "user_prompt", "content"):
        value = (data or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


TOPIC_KEEP = 24          # distinctive tokens carried forward as session subject
TOPIC_QUERY_TERMS = 8    # how many of them join a thin prompt's query


def _topic_tokens(text):
    """Distinctive tokens worth carrying between turns."""
    if readstate is None:
        return []
    try:
        return [t for t in readstate.tokenize(text) if len(t) >= 5]
    except Exception:
        return []


def _merge_topic(previous, fresh):
    """Newest-first rolling subject. Recency wins: the tokens from this turn go
    to the front, older ones survive behind them until they fall off the end.

    Why a rolling topic exists at all (Steve, 2026-08-04: "somehow it needs
    better context of the turns"): the hook receives ONE string. Real turns are
    not self-contained — "tldr", "try now", "is that the right method of action
    here?" carry their subject in the conversation, not in their words. Judged
    on the prompt alone those turns retrieve nothing, which looked like caution
    but was really blindness. The topic is what the last few turns were about.
    """
    merged = list(fresh)
    for t in previous or []:
        if t not in merged:
            merged.append(t)
    return merged[:TOPIC_KEEP]


def _eligible(prompt):
    """Skip greetings and bare continuations.

    Steve asked for EVERY turn. This gate is deliberately narrow: it only skips
    prompts that carry no retrievable subject of their own ("yes", "do it",
    "ok"), which inherit the active thread and would otherwise fire a global
    query for a single word and inject noise. Everything substantive retrieves.
    Reuses the Codex adapter's predicate so both surfaces agree on what counts.
    """
    try:
        import session_state
        return bool(session_state.retrieval_eligible(prompt))
    except Exception:
        # Conservative local fallback if session_state is unavailable.
        return len(prompt) >= 24


def _context(text):
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def evaluate(data):
    """Return a UserPromptSubmit additionalContext payload, or None."""
    try:
        prompt = _prompt_text(data)
        if not prompt:
            return None

        session_id = (data or {}).get("session_id") or "nosession"
        state = _load_state()
        entry = state.get(session_id) or {}
        topic = entry.get("topic") or []

        topic = _merge_topic(topic, _topic_tokens(prompt))

        # TOPIC CARRY-OVER IS DELIBERATELY NOT USED AS A QUERY (2026-08-04).
        # Steve asked for better cross-turn context and the mechanism below was
        # built to borrow the session subject on thin turns. MEASURED on a
        # replay of this session's real turns it was net-NEGATIVE: it fixed
        # "its a plugin" (found the gate + plugin history, previously silent)
        # but "try now" retrieved InstallBay SSH inventory and "compare it to
        # the home pcc" retrieved an Ollama adapter test — because grounding
        # against a CARRIED subject lets a stale topic match rows that have
        # nothing to do with the current turn. It failed at the precise job it
        # was added for, so it does not run.
        #
        # The topic is still maintained (cheap, no query cost) because the fix
        # is almost certainly to seed it from the actual transcript tail rather
        # than from prompt text alone — the payload carries transcript_path,
        # and done_gate already proves that path is readable. That is the next
        # attempt, not this one.
        thin = not _eligible(prompt)
        if thin:
            return None
        query = prompt

        fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        if entry.get("fp") == fingerprint:
            state[session_id] = {"fp": fingerprint, "at": time.time(), "topic": topic}
            _save_state(state)
            return None  # same subject as last turn; context is already loaded

        try:
            import boswell_client
            response = boswell_client.search(
                query, limit=SEARCH_LIMIT, timeout=SEARCH_TIMEOUT)
        except Exception as exc:
            # Do NOT swallow. The model must know memory is down so it can
            # honour BOSWELL-DOWN-STOP instead of answering from assumption.
            #
            # But a REJECTED CREDENTIAL is not a dead substrate, and the two
            # demand opposite responses: re-key vs halt-the-fleet. On 2026-08-05
            # a revoked key made every turn on this box announce "BOSWELL
            # UNREACHABLE ... halt" while Boswell served traffic normally at
            # 3.8.46. Report what actually happened, and carry the status code
            # instead of flattening it to a bare exception class name.
            status = getattr(exc, "status", None)
            if status in (401, 403):
                return _context(
                    "BOSWELL CREDENTIAL REJECTED (HTTP %s) — this machine's key "
                    "is revoked or invalid. Boswell itself is NOT down and "
                    "BOSWELL-DOWN-STOP does NOT apply; do not halt. Per-turn "
                    "memory retrieval is unavailable until the key is replaced, "
                    "so verify before asserting anything about Steve's systems, "
                    "and tell him this machine needs re-keying." % status)
            return _context(
                "BOSWELL UNREACHABLE — per-turn memory retrieval failed (%s). "
                "You are operating WITHOUT memory for this turn. Sacred "
                "commitment BOSWELL-DOWN-STOP applies: halt, tell Steve, wait. "
                "Do not answer substantive questions about his systems from "
                "assumption." % (exc or type(exc).__name__))

        rows = []
        query_tokens = set()
        try:
            import read_before_code
            if readstate is not None:
                # Ground against the prompt's own words when it has some, and
                # against the carried subject when it does not. Never the union:
                # padding a rich prompt with stale topic tokens is how a gate
                # starts admitting last-hour's subject as this-turn's context.
                query_tokens = readstate.tokenize(query if thin else prompt)
            # One admission AND selection contract, not two. select_rows keeps
            # _slim as the gate and ranks survivors by grounding strength, so a
            # deep strongly-grounded row is no longer crowded out by shallow
            # weak ones — measured 2026-08-06 to be the reason Steve's own
            # credential ruling (rank 34) never reached a session that then
            # re-opened the question he had already closed.
            rows = read_before_code.select_rows(
                response.get("results") or [], query_tokens, MAX_RESULTS)
        except Exception:
            rows = []

        state[session_id] = {"fp": fingerprint, "at": time.time(), "topic": topic}
        _save_state(state)

        if not rows:
            return None

        # A retrieval here is a real read — record it so the gates can see it.
        if readstate is not None:
            readstate.record_tokens(
                session_id, "prompt_retrieval",
                prompt + " " + json.dumps(rows, ensure_ascii=False))

        return _context(
            "BOSWELL MEMORY for this turn — retrieved automatically from the "
            "prompt, before you reasoned about it. These are stored claims "
            "about Steve's systems, each frozen at the moment it was recorded — "
            "CHECK THE `age` FIELD. An old row is a claim about the past, not "
            "current fact, and may since have been superseded by work these "
            "results do not include. Prefer a recent row over an old one, "
            "prefer live verification over both, and if your answer would "
            "contradict one of these, say so explicitly:\n"
            + json.dumps(rows, ensure_ascii=False, indent=1))
    except Exception:
        return None  # FAIL-OPEN


if __name__ == "__main__":
    print("eligibility:")
    for p in ("hi", "yes", "do it", "ok",
              "check all the boswell hooks on this machine",
              "what is angela's outbound cap"):
        print(f"  {p!r:52s} -> {_eligible(p)}")
