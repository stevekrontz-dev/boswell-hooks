#!/usr/bin/env python3
"""Stop hook handler: the "Tested-and-Complete" gate.

WHAT CHANGED AND WHY (2026-08-07)
---------------------------------
The original rule was: block the FIRST Stop of any session in which a file was
mutated. That reads as a session-close gate, but Claude Code fires `Stop` at the
end of EVERY assistant turn — there is no "session is ending" signal available
here at all. So the gate was guessing, and it guessed wrong every time.

Measured against 39 real local transcripts (944 human turns, 23 of them with
file mutations):

    fired on the genuinely final turn ......  0 / 23
    median turns of work still to come ....  19
    worst case ............................ 206   (fired at turn 2 of 208)

Zero for twenty-three. That is the "fighting with the stop gate" Steve reported:
you edit one file early, and the gate demands a full six-point close-out report
while the actual work has barely started.

THE NEW TRIGGER
---------------
The gate's real purpose is not "the session is over" — it is the tenant rule
*Steve declares done; never claim something is done or verified unless Steve
signs off.* So it now fires on the CLAIM, not on the edit:

    1. a file was mutated IN THIS TURN, and
    2. no verifying command ran after that mutation, and
    3. the closing message OPENS by declaring completion.

All three. On the same 944 turns that fires 6 times (0.64%) — roughly once every
three or four sessions, and only when a completion claim genuinely had nothing
behind it.

WHAT WAS TRIED AND REJECTED
---------------------------
A looser version — "completion language anywhere in the closing message, with
any unverified mutation earlier in the session" — fired 3 times and TWO of those
were the phrase "That's it" used as ordinary explanatory prose ("It's a list of
who's using your name. That's it."). That is exactly how corrective_gate went
wrong in June: a structural demand hung on a loose prose heuristic. Hence the
two tight constraints above — the claim must OPEN the message, and the mutation
must be in the same turn. Both false positives die on those.

Also rejected: moving this to `SessionEnd`, which is where the session really
does end. SessionEnd cannot block, so the model gets no turn in which to produce
the report — it would convert the gate into a log line. Stop is the only seam
with teeth, so the fix had to be the trigger, not the event.

Fail-open by design: any error, or any transcript we cannot segment into turns,
allows the stop. A gate that cannot establish the facts does not get to block.
"""
import sys
import os
import json
import re
import tempfile
from pathlib import Path

MUTATION_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# How much of the tail to parse. One turn, even a heavy one, lives well inside
# this; transcripts themselves run to several MB and re-reading all of it on
# every single turn end is waste we pay for constantly.
TAIL_BYTES = 2_000_000

# A command that actually produces evidence: test runners, compilers, linters,
# deploys, a live request, a diff. Deliberately generous — the cost of missing
# one is a gate that fires when the work was in fact proven, which is the exact
# failure being fixed here.
VERIFY_RE = re.compile(
    r"\b(pytest|py_compile|unittest|php\s+-l|tsc\b|npm\s+(test|run)|node\s+--check|"
    r"curl\b|wget\b|git\s+push|docker\s+compose|rsync|scp\b|make\s+test|go\s+test|"
    r"cargo\s+test|ruff|eslint|shellcheck|git\s+diff|diff\s+-|mysql\b|psql\b)\b"
)

# A completion declaration, and only in the OPENING of the closing message.
# Anchored at the start precisely so mid-paragraph prose ("...that's it.")
# cannot reach it.
CLAIM_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*)?"
    r"(done|all done|all set|shipped|complete|completed|fixed|deployed|live|"
    r"that's it|that's done|ready to (?:ship|go))\b[.\s,:;!—-]",
    re.IGNORECASE,
)
CLAIM_WINDOW = 120

REASON = (
    "DONE-CLAIM GATE — you just declared this complete, but nothing ran against "
    "the file(s) you changed this turn{files}.\n\n"
    "Steve declares done, not you. Before stopping, either:\n"
    "  (a) prove it — run the check that fits what you touched (py_compile / php -l / "
    "tsc / the test / the real request against the live target) and show the output, or\n"
    "  (b) withdraw the claim — say plainly which parts are unverified and what would "
    "prove them.\n\n"
    "One line per file is enough. Do not restate the work; report its status."
)


def _read_tail(path):
    """Last TAIL_BYTES of the transcript as text, partial first line dropped."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - TAIL_BYTES)
        f.seek(start)
        blob = f.read()
    text = blob.decode("utf-8", errors="ignore")
    if start > 0:
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else ""
    return text


def _is_human_turn(entry):
    """True for a real user prompt; False for tool results fed back as 'user'."""
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "tool_result" not in kinds and "text" in kinds
    return False


def _last_turn(text):
    """(tool_calls, closing_text) for the turn in progress.

    tool_calls is [(tool_name, command_string), ...] in order. Returns None when
    no human turn boundary is present in the tail — we cannot scope the turn, so
    the caller must allow.
    """
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    start = None
    for i in range(len(entries) - 1, -1, -1):
        if _is_human_turn(entries[i]):
            start = i
            break
    if start is None:
        return None

    tools, closing = [], ""
    for entry in entries[start + 1:]:
        if entry.get("type") != "assistant":
            continue
        for block in entry.get("message", {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                data = block.get("input")
                cmd = data.get("command", "") if isinstance(data, dict) else ""
                target = ""
                if isinstance(data, dict):
                    target = data.get("file_path") or data.get("path") or ""
                tools.append((block.get("name"), cmd or "", target))
            elif block.get("type") == "text" and block.get("text", "").strip():
                closing = block["text"]
    return tools, closing


def _unverified_mutations(tools):
    """Files mutated this turn with no verifying command afterwards.

    Returns [] when nothing was mutated, or when a check ran after the last
    mutation. Position matters: a test run BEFORE the edit proves nothing about
    the edit.
    """
    last = None
    for i, (name, _cmd, _t) in enumerate(tools):
        if name in MUTATION_TOOLS:
            last = i
    if last is None:
        return []

    after = " ".join(cmd for name, cmd, _t in tools[last + 1:] if name == "Bash")
    if VERIFY_RE.search(after):
        return []

    files = []
    for name, _cmd, target in tools:
        if name in MUTATION_TOOLS and target:
            base = os.path.basename(target)
            if base not in files:
                files.append(base)
    return files


def _claims_done(closing):
    """True when the closing message OPENS by declaring completion."""
    if not closing:
        return False
    return bool(CLAIM_RE.search(" ".join(closing.split())[:CLAIM_WINDOW]))


def evaluate(data):
    """Return {'decision':'block','reason':...} to gate, or None to allow."""
    try:
        data = data or {}

        # Never re-enter our own block.
        if data.get("stop_hook_active"):
            return None

        sid = data.get("session_id") or "nosession"
        sentinel = Path(tempfile.gettempdir()) / ("claude_doneprompt_%s" % sid)
        if sentinel.exists():
            return None

        path = data.get("transcript_path")
        if not path or not os.path.isfile(path):
            return None  # cannot establish the facts -> allow

        turn = _last_turn(_read_tail(path))
        if turn is None:
            return None
        tools, closing = turn

        if not _claims_done(closing):
            return None
        files = _unverified_mutations(tools)
        if not files:
            return None

        try:
            sentinel.touch()
        except Exception:
            return None

        shown = ", ".join(files[:6]) + ("..." if len(files) > 6 else "")
        return {"decision": "block", "reason": REASON.format(files=" (" + shown + ")")}
    except Exception:
        return None  # FAIL-OPEN: a bug in this file must never block a stop


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
