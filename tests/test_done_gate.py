"""Truth table for the Stop-hook done gate.

The gate blocks a stop only when all three hold: a file was mutated in THIS
turn, nothing verified it afterwards, and the closing message opens by
declaring completion. Everything else must pass through — the defect being
fixed here (measured 2026-08-07) was a gate that fired on turn 2 of a 208-turn
session and never once fired on a session's actual final turn.

Case 5 is the regression that killed the first attempt at this rule: "That's
it." used as ordinary explanatory prose, mid-paragraph.
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import done_gate as dg  # noqa: E402


def _transcript(tools, closing, tmpdir):
    """Write a one-turn transcript: human prompt, tool calls, closing text."""
    lines = [{"type": "user", "message": {"role": "user", "content": "go"}}]
    for name, payload in tools:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": payload}]}})
    lines.append({"type": "assistant", "message": {"content": [
        {"type": "text", "text": closing}]}})
    path = os.path.join(tmpdir, "t.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(line) for line in lines))
    return path


EDIT = ("Edit", {"file_path": "/p/pricing.php"})
PYTEST = ("Bash", {"command": "python -m pytest tests/"})


def _run(tools, closing, tmpdir, tag, **extra):
    payload = {"session_id": "unit-" + tag,
               "transcript_path": _transcript(tools, closing, tmpdir)}
    payload.update(extra)
    return dg.evaluate(payload)


def run_checks():
    results = []
    tmpdir = tempfile.mkdtemp()
    # The gate drops its once-per-session sentinel in gettempdir(). Point that
    # at this run's scratch dir, or a second run inherits the first run's
    # sentinels and every BLOCK case silently turns into an allow.
    tempfile.tempdir = tmpdir

    def ok(cond, msg):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + msg)

    def blocks(tag, tools, closing, **extra):
        return _run(tools, closing, tmpdir, tag, **extra) is not None

    # --- fires -------------------------------------------------------------
    ok(blocks("1", [EDIT], "Done. Pricing now returns 0 for windshield."),
       "1 claim + unverified mutation -> BLOCK")
    ok(blocks("9", [PYTEST, EDIT], "Shipped — the calculator is correct now."),
       "9 verification BEFORE the edit proves nothing -> BLOCK")

    # --- passes through ----------------------------------------------------
    ok(not blocks("2", [EDIT, PYTEST], "Done. Pricing is fixed."),
       "2 verified after the edit -> allow")
    ok(not blocks("3", [("Read", {"file_path": "/p/x.php"})], "Done. Nothing to change."),
       "3 completion claim with no mutation this turn -> allow")
    ok(not blocks("4", [EDIT], "Next I need to wire the coverage enum through book.php."),
       "4 unverified mutation with no claim -> allow")
    ok(not blocks("5", [EDIT], "It's a list of who's using your name. That's it. "
                               "Here is what the parser does next."),
       "5 'That's it' as mid-paragraph prose -> allow  (June regression)")
    ok(not blocks("6", [EDIT], "Done.", stop_hook_active=True),
       "6 stop_hook_active -> allow (no re-entry)")
    ok(not blocks("8", [EDIT], "Done.", transcript_path="/nope/missing.jsonl"),
       "8 unreadable transcript -> allow (cannot establish facts)")
    ok(not blocks("10", [EDIT], "I looked at the calculator, the booking page and the "
                                "estimate API to see how coverage flows through. Done."),
       "10 claim past the opening window -> allow")

    # sentinel: same session cannot be gated twice
    tag = "7"
    first = blocks(tag, [EDIT], "Done. Pricing fixed.")
    second = blocks(tag, [EDIT], "Done. Pricing fixed.")
    ok(first and not second, "7 gates once per session, then allows")

    # helpers
    ok(dg._claims_done("Done. x") and not dg._claims_done("...and that's it. More."),
       "claim anchored to the opening only")
    ok(dg._unverified_mutations([("Edit", "", "/p/a.php"), ("Bash", "php -l /p/a.php", "")]) == [],
       "php -l counts as verification")
    ok(dg._unverified_mutations([("Edit", "", "/p/a.php")]) == ["a.php"],
       "unverified mutation names the file")

    print("\n%d/%d passed" % (sum(results), len(results)))
    return results


def test_done_gate():
    results = run_checks()
    assert results, "no checks ran"
    assert all(results), "%d of %d done-gate checks failed" % (
        len(results) - sum(results), len(results))


if __name__ == "__main__":
    sys.exit(0 if all(run_checks()) else 1)
