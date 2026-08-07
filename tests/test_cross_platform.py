"""Cross-platform verification for boswell-hooks.

Two entry points, deliberately:

    pytest tests/                 # collected as test_cross_platform()
    python3 tests/test_cross_platform.py

The second matters because this file ships inside the release zip, so an
installer can verify their own machine without pytest.

Everything used to run at IMPORT time and end with sys.exit(). pytest imports
every tests/*.py in order to collect it, so that sys.exit propagated as an
INTERNALERROR and aborted the WHOLE suite — and pytest then reports "no tests
ran", which reads like an empty directory rather than a failure. Measured
2026-08-07: the suite had been dead since this file was added, and a commit
message asserted "Suite 32 passed" for a run that never happened. Hence the
run_checks() indirection: nothing executes on import.

Covers the platform-sensitive surfaces rather than re-running the unit suite:
POSIX path handling, directory listing, shell-command parsing, the append-only
ledger, and gate classification.
"""
import pathlib
import sys
import tempfile

# Importable from the repo root (pytest) or from inside scripts/ (shipped
# standalone use).
_HERE = pathlib.Path(__file__).resolve().parent
for _candidate in (_HERE.parent / "scripts", _HERE.parent, pathlib.Path(".")):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import corrective_gate as cg            # noqa: E402
import empty_result as er               # noqa: E402
import hook_health as hh                # noqa: E402
import read_before_code as rbc          # noqa: E402


def run_checks():
    """Run every check. Returns a list of bools; nothing runs on import."""
    results = []

    def ok(cond, msg):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + msg)

    print("python", sys.version.split()[0], "|", sys.platform)

    # --- path handling ------------------------------------------------------
    terms = rbc._path_terms("/Users/henry/projects/upshift/scrapers/vendor_feed.py")
    ok("vendor" in terms and "feed" in terms, "POSIX path terms -> %s" % terms)

    # --- creation detection + sibling listing -------------------------------
    d = pathlib.Path(tempfile.mkdtemp())
    for n in ("alpha_feed.py", "beta_feed.py", "gamma_loader.py", "notes.md"):
        (d / n).write_text("x")
    newf = str(d / "delta_feed.py")

    ok(rbc._is_creation({"tool_name": "Write"}, newf) is True,
       "creation detected")
    ok(rbc._is_creation({"tool_name": "Write"}, str(d / "alpha_feed.py")) is False,
       "existing file is not a creation")

    peers, extra, capped = rbc._sibling_names(newf)
    ok(set(peers) == {"alpha_feed", "beta_feed", "gamma_loader"} and not capped,
       "same-extension peers only -> %s" % peers)

    # --- shell parsing ------------------------------------------------------
    def fires(cmd, out="", code=0):
        return er.evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "tool_response": {"stdout": out, "exit_code": code},
        }) is not None

    ok(fires("grep -r foo . 2>/dev/null") is True, "real suppression fires")
    ok(fires('grep "2>/dev/null" f.txt') is False, "quoted suppressor ignored")
    ok(fires("cat <<EOF\n2>/dev/null\nEOF") is False, "heredoc body ignored")
    ok(fires("rm -f x 2>/dev/null") is False, "writer ignored")
    ok(fires("find . -name x 2>/dev/null", out="hit") is False, "output present")

    # --- append-only health ledger -----------------------------------------
    hh.STATE_ROOT = pathlib.Path(tempfile.mkdtemp())
    hh.note_error("h1", ValueError("boom"))
    hh.note_ok("h2")
    ok(len(hh._read()) == 2, "append-only ledger wrote 2 rows")
    ok("h1" in (hh.report() or ""), "report surfaces the failing handler")
    hh.note_ok("h1")
    ok(hh.report() is None, "recovery clears the report")

    # --- gate classification ------------------------------------------------
    ok(cg._explicitly_corrective("SHIPPED: the correct fix", {"a": 1}) is False,
       "net-new prose is not gated")
    ok(cg._explicitly_corrective("CORRECTION: supersedes prior", {"a": 1}) is True,
       "explicit correction is caught")
    ok(cg._has_symptom({"symptom": "you are about to rebuild an existing surface"})
       is True, "symptom field detected")

    print("\n%d/%d passed" % (sum(results), len(results)))
    return results


def test_cross_platform():
    """pytest entry point."""
    results = run_checks()
    assert results, "no checks ran"
    assert all(results), "%d of %d cross-platform checks failed" % (
        len(results) - sum(results), len(results))


if __name__ == "__main__":
    sys.exit(0 if all(run_checks()) else 1)
