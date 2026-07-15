"""Test matrix for the Read-Before-Write Gate (corrective_gate + readstate).

Runs against an isolated, temporary STATE_ROOT so it never touches real hook
state. Covers the plan's matrix (a)-(d) plus the two reconstructed regression
fixtures from the 2026-06-22 session (neither bad commit persisted — see CW's
provenance note — so they are rebuilt faithfully from the documented shape).

Run:  python test_corrective_gate.py   (exit 0 = all pass)
"""
import os
import sys
import json
import tempfile

# Isolate state BEFORE importing the modules (config caches STATE_ROOT at import).
_TMP = tempfile.mkdtemp(prefix="rbw_gate_test_")
os.environ["BOSWELL_HOOK_STATE"] = os.path.join(_TMP, "state")

import readstate          # noqa: E402
import corrective_gate    # noqa: E402

COMMIT = "mcp__Boswell-Railway__boswell_commit"
SEARCH = "mcp__Boswell-Railway__boswell_search"
STARTUP = "mcp__Boswell-Railway__boswell_startup"

_results = []


def _check(name, condition, detail=""):
    _results.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"   <-- {detail}"
    print(line)


def _record_read(session_id, query, response, tool=SEARCH):
    """Simulate a PostToolUse read event landing in the ledger."""
    readstate.record({
        "tool_name": tool,
        "session_id": session_id,
        "tool_input": {"query": query},
        "tool_response": response,
    })


def _commit(session_id, message, content):
    return {
        "tool_name": COMMIT,
        "session_id": session_id,
        "tool_input": {
            "branch": "boswell",
            "message": message,
            "content": content,
        },
    }


def _is_deny(result):
    return (isinstance(result, dict)
            and result.get("hookSpecificOutput", {})
            .get("permissionDecision") == "deny")


# --- plan matrix -----------------------------------------------------------
print("Plan test matrix:")

# (a) corrective commit, NO read -> DENY
r = corrective_gate.evaluate(_commit(
    "case-a", "CORRECTION: this supersedes the prior fact, it was wrong",
    {"wrong_fact": "x", "right_fact": "y", "subject": "acme_widget_sku"}))
_check("(a) corrective + no read -> DENY", _is_deny(r), repr(r))

# (b) corrective commit, recent OVERLAPPING read -> PASS
_record_read("case-b", "acme widget sku pricing",
             '{"message":"acme_widget_sku is stored as SKU-4417"}')
r = corrective_gate.evaluate(_commit(
    "case-b", "CORRECTION: supersedes prior — acme_widget_sku was wrong",
    {"wrong_fact": "SKU-4417", "right_fact": "SKU-9920",
     "subject": "acme_widget_sku"}))
_check("(b) corrective + overlapping read -> PASS", r is None, repr(r))

# (c) net-new append, no read -> PASS
r = corrective_gate.evaluate(_commit(
    "case-c", "SHIPPED: new dashboard v2 went live on atlas",
    {"what": "dashboard", "status": "live"}))
_check("(c) net-new append -> PASS", r is None, repr(r))

# (c2) net-new whose prose contains 'update'/'fix' -> still PASS (not corrective)
r = corrective_gate.evaluate(_commit(
    "case-c2", "UPDATE: rolled comps forward; FIX deployed to atlas",
    {"what": "quarterly refresh"}))
_check("(c2) 'update'/'fix' prose stays net-new -> PASS", r is None, repr(r))

# (d) hook error -> FAIL-OPEN (None). Force recent_read_tokens to raise.
_orig = readstate.recent_read_tokens
try:
    readstate.recent_read_tokens = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("boom"))
    r = corrective_gate.evaluate(_commit(
        "case-d", "CORRECTION: supersedes prior, was wrong",
        {"wrong_fact": "x", "right_fact": "y"}))
    _check("(d) hook error -> fail-open PASS", r is None, repr(r))
finally:
    readstate.recent_read_tokens = _orig

# (e) only boswell_startup happened (excluded) -> corrective DENY
readstate.record({"tool_name": STARTUP, "session_id": "case-e",
                  "tool_input": {}, "tool_response":
                  '{"sacred_manifest":"...","acme_widget_sku":"noise"}'})
r = corrective_gate.evaluate(_commit(
    "case-e", "CORRECTION: supersedes prior acme_widget_sku, was wrong",
    {"wrong_fact": "x", "right_fact": "y", "subject": "acme_widget_sku"}))
_check("(e) startup-only (not a qualifying read) -> DENY", _is_deny(r), repr(r))

# --- regression fixtures (2026-06-22 session) ------------------------------
print("\nRegression fixtures (the two fabricated corrective commits):")

# The fabricated correction payload, shared by A and B (only the ledger differs).
FIX_MSG = ("CORRECTION: supersedes prior naming_flag — Steve does NOT own "
           "tintinstitute.com")
FIX_CONTENT = {
    "wrong_fact": "Steve owns tintinstitute.com",
    "right_fact": "tintinstitute.com is not owned",
    "supersedes": "prior brand-architecture note",
    "naming_flag": "ownership",
}

# Fixture A — the canonical DENY: ledger has only startup + GENERIC searches
# (ShopFix / tint shops / domains inventory). Note an incidental shared generic
# word ("domains") is present to prove a weak, non-distinctive overlap is NOT
# accepted as evidence — only a distinctive entity token or >=2 tokens count.
readstate.record({"tool_name": STARTUP, "session_id": "fixture-A",
                  "tool_input": {}, "tool_response": "sacred manifest bulk load"})
_record_read("fixture-A", "ShopFix Academy tint shop services",
             '{"message":"ShopFix Academy training for tint shops"}')
_record_read("fixture-A", "domains inventory list",
             '{"message":"104 domains in the eNom inventory list"}')
rA = corrective_gate.evaluate(_commit("fixture-A", FIX_MSG, FIX_CONTENT))
_check("Fixture A (no read of tintinstitute fact) -> DENY", _is_deny(rA),
       repr(rA))

# Fixture B — the canonical PASS: same payload, but the ledger includes the
# qualifying read that actually surfaced the fact (commit bf3fe3e2).
_record_read("fixture-B", "domains owned tintinstitute.com",
             '{"message":"BRAND ARCHITECTURE: Steve owns the domain for every '
             'leg. Correction: he DOES own tintinstitute.com",'
             '"commit_hash":"bf3fe3e2"}')
rB = corrective_gate.evaluate(_commit("fixture-B", FIX_MSG, FIX_CONTENT))
_check("Fixture B (same payload, tintinstitute WAS read) -> PASS", rB is None,
       repr(rB))

# --- summary ---------------------------------------------------------------
passed = sum(1 for _, ok in _results if ok)
total = len(_results)
print(f"\n{passed}/{total} checks passed")
if rA and _is_deny(rA):
    print("\nSample DENY reason (Fixture A):\n  " +
          rA["hookSpecificOutput"]["permissionDecisionReason"])
sys.exit(0 if passed == total else 1)
