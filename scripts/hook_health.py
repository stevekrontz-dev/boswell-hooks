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

STATE_NAME = "hook_health.json"
REPORT_WINDOW_DAYS = 7
MAX_ERR_CHARS = 240


def _path():
    return STATE_ROOT / STATE_NAME


def _load():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state):
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def note_error(handler, exc):
    """Record that a fail-open handler swallowed an exception."""
    try:
        # Coerce: a non-string key survives dict access but serialises to a
        # bogus JSON key ("null"), producing a ledger entry nothing can clear.
        handler = str(handler or "unknown")
        state = _load()
        entry = state.get(handler)
        if not isinstance(entry, dict):
            entry = {}
        entry["errors"] = int(entry.get("errors", 0)) + 1
        entry["last_error_at"] = time.time()
        detail = "%s: %s" % (type(exc).__name__, exc)
        entry["last_error"] = detail[:MAX_ERR_CHARS]
        state[handler] = entry
        _save(state)
    except Exception:
        pass


def note_ok(handler):
    """Record a clean run, so a recovered handler stops being reported."""
    try:
        # Coerce: a non-string key survives dict access but serialises to a
        # bogus JSON key ("null"), producing a ledger entry nothing can clear.
        handler = str(handler or "unknown")
        state = _load()
        entry = state.get(handler)
        if not isinstance(entry, dict):
            entry = {}
        entry["ok"] = int(entry.get("ok", 0)) + 1
        entry["last_ok_at"] = time.time()
        state[handler] = entry
        _save(state)
    except Exception:
        pass


def report():
    """Return a short human-readable health warning, or None when all is well.

    A handler is reported when it has errored inside the window AND has not had
    a clean run since. A handler that threw once and has worked ever since is
    not a problem worth spending session context on.
    """
    try:
        state = _load()
        cutoff = time.time() - (REPORT_WINDOW_DAYS * 86400)
        broken = []
        for handler, entry in sorted(state.items()):
            if not isinstance(entry, dict):
                continue
            last_err = entry.get("last_error_at") or 0
            if last_err < cutoff:
                continue
            if (entry.get("last_ok_at") or 0) >= last_err:
                continue  # recovered
            age_h = max(0, int((time.time() - last_err) / 3600))
            broken.append("  - %s: %d error(s), last %dh ago -> %s"
                          % (handler, int(entry.get("errors", 0)), age_h,
                             entry.get("last_error", "?")))
        if not broken:
            return None
        return ("BOSWELL HOOK HEALTH — these handlers are failing open, which "
                "means they are silently doing nothing. This is the b003a5c1 "
                "failure class (a swallowed exception hid a dead transcript "
                "pipeline for three weeks). Treat any guarantee they provide "
                "as ABSENT until fixed:\n" + "\n".join(broken))
    except Exception:
        return None
