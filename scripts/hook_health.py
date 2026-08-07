"""Make fail-open observable (boswell-hooks plugin).

Every handler in this plugin is wrapped fail-open, which is correct: a hook bug
must never break Steve's session. But the implementation was `except Exception:
pass`, and that makes a handler which raises on EVERY invocation
indistinguishable from one that is correctly staying quiet. The plugin's core
safety property doubles as a blindfold.

This is not hypothetical. Incident b003a5c1 (found 2026-08-04): `requests` went
missing after a Python upgrade and an unguarded call in
flush_pending_transcripts swallowed the failure. The home/iCloud transcript
pipeline was dead for three weeks and 3,704 sessions never reached Boswell.
Its own recorded lesson: "Fail-open + no health signal = undetectable rot. Two
of six fleet machines were silently broken tonight and both had been that way
for weeks."

Two more instances surfaced on 2026-08-06 in one session: the bundled
boswell.py kept pointing sessions at a scribe file deprecated a month earlier,
and prompt_retrieval had been deregistered for two months leaving the Claude
surface with zero per-turn Boswell contact. Nobody noticed either, because
nothing was watching.

So: keep failing open, but RECORD the failure and surface it at session start.
The handler still never raises into the session. It just stops being silent.

Design constraints:
  * This module must never raise. It is the thing that watches the watchers; if
    it throws it becomes the outage. Every public call is fully guarded.
  * State is machine-local under config.STATE_ROOT, never the synced plugin dir.
  * Bounded: one small JSON file, per-handler counters, a single last-error
    string. It never grows with session count.
  * Reports only what is actionable — a handler erroring in the last
    REPORT_WINDOW_DAYS. Old, resolved noise ages out on its own.
"""
import json
import time
from pathlib import Path

try:
    from config import STATE_ROOT
except Exception:  # pragma: no cover
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

STATE_NAME = "hook_health.jsonl"
REPORT_WINDOW_DAYS = 7
MAX_ERR_CHARS = 240
# Read bound — aggregation only needs recent lines, and an unbounded read would
# make the watcher itself the latency.
MAX_LINES = 4000
# Rewrite threshold. Pruning happens in report(), never on the append path.
MAX_BYTES = 512 * 1024


def _path():
    return STATE_ROOT / STATE_NAME


def _read():
    """Recent ledger lines. A torn line is skipped, never fatal."""
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_LINES:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("h"):
            rows.append(row)
    return rows


def _prune():
    """Keep the ledger bounded. Called only from report()."""
    try:
        path = _path()
        if not path.exists() or path.stat().st_size <= MAX_BYTES:
            return
        keep = _read()[-(MAX_LINES // 2):]
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in keep:
                fh.write(json.dumps(row) + "\n")
        tmp.replace(path)
    except Exception:
        pass


def _append(handler, ok, detail=None):
    """One append. NO read-modify-write.

    Measured 2026-08-06 with 8 concurrent writers: the previous
    load-mutate-save lost 318 of 320 updates and left only 2 of 8 handlers in
    the ledger. Worse than the lost counts, a concurrent writer could ERASE an
    error another handler had just recorded — the watcher silently dropping the
    exact evidence it exists to preserve, which is the b003a5c1 failure class
    reproduced inside the thing built to detect it.

    Small appends do not interleave that way, and this is the pattern
    readstate.py already uses for its per-session ledger. Reused rather than
    reinvented, and no locking to get wrong per-platform.

    Coerce the handler name: a non-string survives dict access but serialises
    to a bogus key nothing can later clear.
    """
    try:
        entry = {"h": str(handler or "unknown"), "ok": bool(ok),
                 "t": time.time()}
        if detail:
            entry["e"] = str(detail)[:MAX_ERR_CHARS]
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def note_error(handler, exc):
    """Record that a fail-open handler swallowed an exception."""
    _append(handler, False, "%s: %s" % (type(exc).__name__, exc))


def note_ok(handler):
    """Record a clean run, so a recovered handler stops being reported."""
    _append(handler, True)


def report():
    """Return a short human-readable health warning, or None when all is well.

    A handler is reported when it has errored inside the window AND has not had
    a clean run since. A handler that threw once and has worked ever since is
    not a problem worth spending session context on.
    """
    try:
        _prune()
        agg = {}
        for row in _read():
            entry = agg.setdefault(str(row.get("h")), {
                "errors": 0, "last_err": 0.0, "last_ok": 0.0, "detail": "?"})
            stamp = float(row.get("t") or 0)
            if row.get("ok"):
                entry["last_ok"] = max(entry["last_ok"], stamp)
            else:
                entry["errors"] += 1
                if stamp >= entry["last_err"]:
                    entry["last_err"] = stamp
                    entry["detail"] = row.get("e") or "?"

        cutoff = time.time() - (REPORT_WINDOW_DAYS * 86400)
        broken = []
        for handler, entry in sorted(agg.items()):
            last_err = entry["last_err"]
            if last_err < cutoff:
                continue
            if entry["last_ok"] >= last_err:
                continue  # recovered
            age_h = max(0, int((time.time() - last_err) / 3600))
            broken.append("  - %s: %d error(s), last %dh ago -> %s"
                          % (handler, entry["errors"], age_h, entry["detail"]))
        if not broken:
            return None
        return ("BOSWELL HOOK HEALTH — these handlers are failing open, which "
                "means they are silently doing nothing. This is the b003a5c1 "
                "failure class (a swallowed exception hid a dead transcript "
                "pipeline for three weeks). Treat any guarantee they provide "
                "as ABSENT until fixed:\n" + "\n".join(broken))
    except Exception:
        return None
