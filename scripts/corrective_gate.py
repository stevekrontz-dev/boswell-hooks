"""Corrective-Write Guard (boswell-hooks plugin) — the read-before-write gate.

PreToolUse handler on the Boswell commit tool (mcp ...boswell_commit). This is
"Option C": the hard PreToolUse blocker the soft fix-intent injector
(commit 0f1816b4, 2026-05-09) said it would need and deferred. Architecture is
cloned from git_guard.py: pure-string inspection, no network/subprocess/latency,
fail-open, DENY on match. The soft injector stays as the advisory layer; this is
the enforcement layer.

SCOPE — corrective writes ONLY. A net-new append (no existing fact contradicted)
passes through ungated with zero added latency. The gate fires only when the
commit payload looks like a correction/supersession AND there is no recent
in-session Boswell read of the fact being corrected.

Decision:
  * Not the commit tool                         -> None  (not ours)
  * Commit, but not corrective                  -> None  (net-new append: PASS)
  * Corrective + a token-overlapping read       -> None  (evidence present: PASS)
  * Corrective + NO qualifying read at all      -> DENY
  * Corrective + reads exist but none overlap   -> DENY
  * Any exception                               -> None  (FAIL-OPEN)

Evidence comes from readstate.py's per-session ledger (a PostToolUse hook on the
read tools). Overlap is checked against the UNION of each read's query AND
response tokens (the fact-token usually lives in the response).

Phantom-marker discipline (Workstream A, commit 12acf43e): this gate references
only fields that actually ship on the boswell_commit tool — branch, content,
message, content_type, tags — and `content` is handled as object OR JSON string
(the schema allows both). No invented fields.
"""
import re
import json

try:
    import readstate
except Exception:  # pragma: no cover
    readstate = None

# --- corrective-intent detection (pure string) -----------------------------
# Strong markers that imply replacing/contradicting an existing fact. NOTE the
# deliberate omission of bare 'update' and 'fix': both are extremely common in
# legitimate net-new status commits ("UPDATE: shipped X", "FIX: deployed Y"),
# so triggering on them would tax the cheap append path. Task-3 tuning per plan.
_MARKER_WORDS = (
    "correct", "corrects", "corrected", "correction", "corrections",
    "supersede", "supersedes", "superseded", "superseding", "supersession",
    "wrong", "incorrect", "mistaken", "mistake", "actually", "overwrite",
    "overwrites", "replaces", "replaced", "replacing", "misstated", "errata",
)
_MARKER_PHRASES = (
    "no longer", "instead of", "rather than", "used to be", "previously stated",
    "earlier note", "earlier commit", "my error", "i was wrong", "not actually",
    "this corrects", "this supersedes",
)
_CORRECTIVE_RE = re.compile(
    r"\b(" + "|".join(_MARKER_WORDS) + r")\b", re.IGNORECASE)
_PHRASE_RE = re.compile(
    "|".join(re.escape(p) for p in _MARKER_PHRASES), re.IGNORECASE)

# Structured corrective signal: a content dict carrying one of these keys is a
# correction regardless of prose (e.g. the 2026-06-22 fixture used wrong_fact /
# right_fact / supersedes).
_CORRECTIVE_KEYS = {
    "correction", "corrects", "supersede", "supersedes", "superseded",
    "wrong_fact", "right_fact", "supersedes_commit", "replaces", "was_wrong",
}

# Marker tokens are stripped from the corrective payload's target tokens so the
# words "correction"/"supersedes" themselves can never create false overlap.
_MARKER_TOKENS = set(_MARKER_WORDS) | {
    w for p in _MARKER_PHRASES for w in p.split()}

# Overlap is "real evidence" if it shares a distinctive (long) token OR at least
# two significant tokens — a single short generic word (e.g. "domain") is not
# enough to claim the corrected fact was actually read.
_STRONG_LEN = 8
_MIN_OVERLAP = 2


def _is_commit_tool(tool_name):
    return "boswell_commit" in str(tool_name or "")


def _extract(tool_input):
    """Return (combined_text, content_obj_or_None) from a commit tool_input.
    `content` may be a dict or a JSON string; both are flattened to text."""
    if not isinstance(tool_input, dict):
        return ("", None)
    parts = []
    msg = tool_input.get("message")
    if isinstance(msg, str):
        parts.append(msg)
    ctype = tool_input.get("content_type")
    if isinstance(ctype, str):
        parts.append(ctype)
    tags = tool_input.get("tags")
    if isinstance(tags, list):
        parts.extend(t for t in tags if isinstance(t, str))

    content = tool_input.get("content")
    content_obj = None
    if isinstance(content, dict):
        content_obj = content
        try:
            parts.append(json.dumps(content, ensure_ascii=False))
        except Exception:
            parts.append(str(content))
    elif isinstance(content, str):
        parts.append(content)
        # content may be a JSON string — try to recover its keys for key-based
        # corrective detection.
        try:
            maybe = json.loads(content)
            if isinstance(maybe, dict):
                content_obj = maybe
        except Exception:
            pass
    return (" ".join(parts), content_obj)


def _is_corrective(text, content_obj):
    if _CORRECTIVE_RE.search(text) or _PHRASE_RE.search(text):
        return True
    if isinstance(content_obj, dict):
        for k in content_obj.keys():
            if str(k).lower() in _CORRECTIVE_KEYS:
                return True
    return False


def _target_tokens(text):
    if readstate is None:
        return set()
    return readstate.tokenize(text) - _MARKER_TOKENS


def _has_evidence(target, read_tokens):
    overlap = target & read_tokens
    if not overlap:
        return False
    if any(len(t) >= _STRONG_LEN for t in overlap):
        return True
    return len(overlap) >= _MIN_OVERLAP


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def evaluate(data):
    """Return a PreToolUse deny decision dict, or None to allow. Fail-open."""
    try:
        if not _is_commit_tool(data.get("tool_name")):
            return None
        text, content_obj = _extract(data.get("tool_input"))
        if not _is_corrective(text, content_obj):
            return None  # net-new append — PASS ungated

        session_id = data.get("session_id") or "nosession"
        if readstate is None:
            return None  # can't consult evidence -> fail-open
        had_read, read_tokens = readstate.recent_read_tokens(session_id)
        target = _target_tokens(text)

        if had_read and _has_evidence(target, read_tokens):
            return None  # the corrected fact was read this session — PASS

        hint = ", ".join(sorted((t for t in target if len(t) >= 5),
                                key=len, reverse=True)[:4]) or "the fact"
        if not had_read:
            why = ("no qualifying Boswell read happened this session "
                   "(boswell_startup does not count).")
        else:
            why = ("you searched/read other things this session, but nothing "
                   "that overlaps the fact you are correcting.")
        return _deny(
            "Corrective write blocked: " + why + " Before superseding a fact in "
            "permanent memory, read its current state — run boswell_search / "
            "boswell_recall / fetch on it (e.g. \"" + hint + "\"), confirm what "
            "is actually stored, then retry the commit. (Read-Before-Write "
            "Gate; corrective writes only — net-new appends are never gated.)")
    except Exception:
        return None  # FAIL-OPEN: never block legitimate work on a bug


if __name__ == "__main__":
    # Lightweight self-test of the corrective classifier (gate decisions are
    # exercised end-to-end in test_corrective_gate.py against a real ledger).
    samples = [
        ("CORRECTION: Steve DOES own tintinstitute.com; supersedes prior", True),
        ("SHIPPED: new dashboard v2 went live", False),
        ("UPDATE: rolled the comps forward to Q4", False),
        ("FIX: deployed the patch to atlas", False),
        ("This supersedes commit 2d6e7c6f — the mapping was wrong", True),
        ("Net-new: first capture of tintwaco.com via Hunter", False),
    ]
    print("corrective-intent classifier:")
    for msg, expected in samples:
        got = _is_corrective(msg, None)
        flag = "OK " if got == expected else "FAIL"
        print(f"  [{flag}] expected={expected!s:5s} got={got!s:5s}  {msg}")
