"""Cross-platform verification for boswell-hooks v2.1.0.

Run from inside an unpacked release package's scripts/ directory:
    python3 xplat_test.py

Checks the platform-sensitive surfaces: POSIX path handling, directory
listing, shell-command parsing, the append-only ledger, and gate
classification. Import-only checks are done separately.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, ".")

RESULTS = []


def ok(cond, msg):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + msg)


import read_before_code as rbc          # noqa: E402
import empty_result as er               # noqa: E402
import hook_health as hh                # noqa: E402
import corrective_gate as cg            # noqa: E402

print("python", sys.version.split()[0], "|", sys.platform)

# --- 1. POSIX path term extraction -----------------------------------------
terms = rbc._path_terms("/Users/henry/projects/upshift/scrapers/vendor_feed.py")
ok("vendor" in terms and "feed" in terms, "POSIX path terms -> %s" % terms)

# --- 2. creation detection + sibling listing on a real POSIX directory ------
d = pathlib.Path(tempfile.mkdtemp())
for n in ("alpha_feed.py", "beta_feed.py", "gamma_loader.py", "notes.md"):
    (d / n).write_text("x")
newf = str(d / "delta_feed.py")

ok(rbc._is_creation({"tool_name": "Write"}, newf) is True,
   "creation detected on POSIX path")
ok(rbc._is_creation({"tool_name": "Write"}, str(d / "alpha_feed.py")) is False,
   "existing file is not a creation")

peers, extra, capped = rbc._sibling_names(newf)
ok(set(peers) == {"alpha_feed", "beta_feed", "gamma_loader"} and not capped,
   "same-extension peers only -> %s" % peers)

# --- 3. empty_result shell parsing -----------------------------------------
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

# --- 4. append-only health ledger on POSIX ---------------------------------
hh.STATE_ROOT = pathlib.Path(tempfile.mkdtemp())
hh.note_error("h1", ValueError("boom"))
hh.note_ok("h2")
rows = hh._read()
ok(len(rows) == 2, "append-only ledger wrote %d rows" % len(rows))
ok("h1" in (hh.report() or ""), "report surfaces the failing handler")
hh.note_ok("h1")
ok(hh.report() is None, "recovery clears the report")

# --- 5. corrective gate classification -------------------------------------
ok(cg._explicitly_corrective("SHIPPED: the correct fix", {"a": 1}) is False,
   "net-new prose is not gated")
ok(cg._explicitly_corrective("CORRECTION: supersedes prior", {"a": 1}) is True,
   "explicit correction is caught")
ok(cg._has_symptom({"symptom": "you are about to rebuild an existing surface"})
   is True, "symptom field detected")

print("\n%d/%d passed" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
