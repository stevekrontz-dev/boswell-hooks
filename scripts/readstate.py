"""Per-session read-state ledger (boswell-hooks plugin).

Companion to corrective_gate.py. A PostToolUse hook records every QUALIFYING
Boswell read (boswell_search / boswell_recall / boswell_semantic_search /
fetch) into a machine-local, per-session JSONL ledger. The PreToolUse
corrective-write gate then consults that ledger to decide whether a fresh read
of the fact being corrected actually happened in-session.

Why a ledger and not a transcript scan: a PreToolUse hook cannot see prior tool
calls — only this call's payload + session_id + a path to the transcript. The
Stop gate (done_gate.py) proves session_id and transcript_path are both on the
hook payload, so a transcript scan is *possible*, but scanning a growing JSONL
on every commit is heavier and brittle (the transcript shape moves). A
purpose-built ledger is light, deterministic, and — crucially — lets us capture
the read's RESPONSE tokens (PostToolUse receives tool_response), so the gate can
check that the fact being corrected was actually read, not merely that *some*
read happened. (The fact-token usually appears in the response, not the query.)

Design constraints inherited from the plugin:
  * Pure-string / local-fs only. No network, no subprocess, no latency.
  * Fail-open everywhere: a broken ledger must never break the session, and a
    missing ledger simply means "no evidence" (the gate handles that).
  * State lives machine-local under config.STATE_ROOT, never in the synced
    plugin dir.

boswell_startup is DELIBERATELY NOT a qualifying read: it is passive bulk
context load, not a targeted read of the fact being corrected. If it counted,
every session would trivially satisfy the gate.
"""
import os
import re
import json
import time
from pathlib import Path

try:
    from config import STATE_ROOT
except Exception:  # pragma: no cover - config import must never break the hook
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

LEDGER_DIR = STATE_ROOT / "readstate"

# Recency window (seconds) the gate considers "fresh". 0 = whole session (the
# ledger is already per-session, so 0 means "any read this session counts").
# Env-tunable so a tenant can tighten it without touching code.
try:
    RECENCY_SECONDS = int(os.environ.get("BOSWELL_GATE_RECENCY_SECONDS", "0"))
except Exception:
    RECENCY_SECONDS = 0

# How much of a read's response we tokenize. The fact-token we care about is
# near the top of any Boswell result; cap to keep ledger lines small.
RESPONSE_CAP = 6000

MIN_TOKEN_LEN = 4  # drop tokens shorter than this (the/com/and noise)

# Generic words that carry no entity identity — excluded so they can't create
# spurious overlap between an unrelated read and a corrective payload.
_STOPWORDS = {
    "this", "that", "with", "from", "have", "into", "your", "their", "about",
    "memory", "commit", "branch", "content", "boswell", "tenant", "steve",
    "true", "false", "null", "type", "tags", "message", "claude", "search",
    "result", "results", "value", "field", "fields", "data", "note", "notes",
    "date", "created", "updated", "http", "https", "json", "object", "string",
}

# Qualifying read tools, matched by suffix so a differently-aliased MCP server
# on the home machine still resolves (e.g. mcp__Boswell-Railway__boswell_search).
_READ_SUFFIXES = ("boswell_search", "boswell_recall", "boswell_semantic_search")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HEXCHARS = frozenset("0123456789abcdef")


def _is_noise(tok):
    """Drop tokens that carry no entity identity: pure numbers (years, counts,
    rrf-score fragments) and long all-hex strings (commit/blob/uuid hashes).
    These otherwise inflate the ledger and could create spurious overlap. An
    8-char floor on the hex test spares ordinary hex-letter words (decade,
    facade) while catching hash/uuid chunks."""
    if tok.isdigit():
        return True
    if len(tok) >= 8 and all(c in _HEXCHARS for c in tok):
        return True
    return False


def is_qualifying_read(tool_name):
    """True iff this tool call is a targeted Boswell read whose subject we
    should remember. Excludes boswell_startup (passive bulk load)."""
    if not tool_name:
        return False
    name = str(tool_name)
    low = name.lower()
    if low.endswith("boswell_startup"):
        return False
    if any(low.endswith(s) for s in _READ_SUFFIXES):
        return True
    # Boswell's MCP `fetch` (recall-by-hash). Require it to belong to a Boswell
    # server so a generic WebFetch never counts.
    if low.endswith("__fetch") and "boswell" in low:
        return True
    return False


def tokenize(text):
    """Lowercase, split on non-alphanumerics, drop short tokens + stopwords.
    Domains split naturally: 'tintinstitute.com' -> {'tintinstitute'} ('com'
    is below MIN_TOKEN_LEN)."""
    if text is None:
        return set()
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    out = set()
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < MIN_TOKEN_LEN or tok in _STOPWORDS:
            continue
        if _is_noise(tok):
            continue
        out.add(tok)
    return out


def _sanitize(session_id):
    sid = str(session_id or "nosession")
    return re.sub(r"[^A-Za-z0-9._-]", "_", sid)[:120]


def _ledger_path(session_id):
    return LEDGER_DIR / (_sanitize(session_id) + ".jsonl")


def _read_query_text(tool_input):
    """Pull the human-meaningful query/identifier out of a read's tool_input."""
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for k in ("query", "id", "hash", "commit", "keywords", "context"):
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    return " ".join(parts)


def record(data):
    """PostToolUse handler. Append a ledger line for a qualifying read.

    Never raises (the dispatcher also wraps it, belt-and-suspenders). A failure
    to record just means the gate sees less evidence — it does not break work.
    """
    try:
        tool = data.get("tool_name") or ""
        if not is_qualifying_read(tool):
            return
        session_id = data.get("session_id") or "nosession"
        ti = data.get("tool_input")
        query_text = _read_query_text(ti)

        resp = data.get("tool_response")
        if resp is not None and not isinstance(resp, str):
            try:
                resp = json.dumps(resp, ensure_ascii=False)
            except Exception:
                resp = str(resp)
        resp_text = (resp or "")[:RESPONSE_CAP]

        # Union of query + response tokens — the fact-token is most often in the
        # response, so we must index both (per CW's guard against false negatives).
        tokens = tokenize(query_text) | tokenize(resp_text)
        if not tokens:
            return

        entry = {
            "ts": int(time.time()),
            "tool": str(tool).split("__")[-1],  # short name
            "tokens": sorted(tokens),
        }
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        with open(_ledger_path(session_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # fail-open


def recent_read_tokens(session_id, recency_seconds=None):
    """Return (had_qualifying_read, union_of_tokens) for this session's ledger,
    restricted to entries within recency_seconds (0/None = whole session).

    Fail-open: any read error -> (False, empty set), i.e. "no evidence", which
    the gate treats conservatively. The gate itself decides DENY/PASS."""
    if recency_seconds is None:
        recency_seconds = RECENCY_SECONDS
    path = _ledger_path(session_id)
    try:
        if not path.exists():
            return (False, set())
        cutoff = (int(time.time()) - recency_seconds) if recency_seconds else 0
        had = False
        union = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if cutoff and int(entry.get("ts", 0)) < cutoff:
                continue
            had = True
            toks = entry.get("tokens") or []
            if isinstance(toks, list):
                union.update(toks)
        return (had, union)
    except Exception:
        return (False, set())


if __name__ == "__main__":
    # Smoke test: simulate a couple of reads then dump the ledger view.
    import tempfile
    sid = "selftest-session"
    LEDGER_DIR = Path(tempfile.gettempdir()) / "readstate_selftest"
    # reset
    try:
        for p in LEDGER_DIR.glob("*.jsonl"):
            p.unlink()
    except Exception:
        pass

    print("is_qualifying_read checks:")
    for tn in ("mcp__Boswell-Railway__boswell_search",
               "mcp__Boswell-Railway__boswell_semantic_search",
               "mcp__Boswell-Railway__fetch",
               "mcp__Boswell-Railway__boswell_recall",
               "mcp__Boswell-Railway__boswell_startup",
               "Read", "WebFetch"):
        print(f"  {tn:48s} -> {is_qualifying_read(tn)}")

    record({"tool_name": "mcp__Boswell-Railway__boswell_search",
            "session_id": sid,
            "tool_input": {"query": "domains owned tintinstitute.com"},
            "tool_response": '{"message":"he DOES own tintinstitute.com"}'})
    had, toks = recent_read_tokens(sid)
    print(f"\nhad_read={had}; 'tintinstitute' in tokens -> "
          f"{'tintinstitute' in toks}")
    print("tokens:", sorted(toks))
