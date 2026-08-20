#!/usr/bin/env python3
"""protected_paths: deny overwrites of existing declared files, allow the rest.

The guard exists for ONE hazard: destroying a file that cannot be regenerated
and that other artifacts are pinned to. Two failure modes are equally bad —
letting a destructive write through, and denying ordinary work until someone
switches the guard off. Both directions are asserted here.
"""
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import protected_paths as pp  # noqa: E402

passed = failed = 0


def check(label, got, want):
    global passed, failed
    if bool(got) == want:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL {label}: expected {'deny' if want else 'allow'}")


def bash(root, cmd):
    return pp.evaluate({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": root})


def write(root, path):
    return pp.evaluate({"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": root})


root = tempfile.mkdtemp()
try:
    (pathlib.Path(root) / ".boswell-protect").write_text(
        "songs/*/source.mp4 :: pinned; re-deriving desyncs everything\n")
    have = pathlib.Path(root) / "songs" / "have_it"
    have.mkdir(parents=True)
    (have / "source.mp4").write_bytes(b"IRREPLACEABLE")
    (pathlib.Path(root) / "songs" / "brand_new").mkdir(parents=True)

    # --- must DENY: the file is there and something is pinned to it ---
    check("truncate", bash(root, "cp /dev/null songs/have_it/source.mp4"), True)
    check("redirect", bash(root, "echo x > songs/have_it/source.mp4"), True)
    # the near-miss that motivated the guard: stage to temp, then move over
    check("temp-then-move", bash(root, "mv -f /tmp/new.mp4 songs/have_it/source.mp4"), True)
    check("absolute path", bash(root, f"cp /dev/null {have / 'source.mp4'}"), True)
    check("Write tool", write(root, str(have / "source.mp4")), True)

    # --- must ALLOW: nothing to destroy ---
    # A first-time download is the project's normal daily work. Denying it
    # protects nothing (no timings exist yet) and makes the guard a nuisance.
    check("first download", bash(root, "yt-dlp URL -o songs/brand_new/source.mp4"), False)
    # Reads must stay free or the guard breaks the tools used on these files
    # constantly, and gets disabled.
    check("read (ffprobe)", bash(root, "ffprobe songs/have_it/source.mp4"), False)
    check("undeclared sibling", bash(root, "rm songs/have_it/notes.txt"), False)
    check("no config above", bash(tempfile.mkdtemp(), "cp /dev/null songs/x/source.mp4"), False)

    # --- fail-open: a malformed event must never wedge the session ---
    check("garbage event", pp.evaluate({"tool_name": "Bash"}), False)
    check("empty event", pp.evaluate({}), False)
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
