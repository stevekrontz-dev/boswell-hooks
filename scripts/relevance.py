"""Relevance admission for Boswell retrieval — BM25 rarity + branch routing.

REPLACES the hand-rolled lexical gate that lived in read_before_code._grounded.

WHY THE OLD GATE DIED (Steve, 2026-08-04): it decided a token was "distinctive"
by STRING LENGTH (>=7 chars). Measured consequence — the query "defcon 1", the
single most precise question you can ask about that subsystem, returned SILENCE
while 35 of the 50 rows it fetched were genuinely about DEFCON. "defcon" is six
characters, so the one word that WAS the entire subject did not count as
distinctive. Meanwhile "compare" (seven characters, meaningless) sailed through
and needed a hand-written stoplist patch. Length was never measuring rarity; it
was a proxy that had to be patched every time reality disagreed with it.

WHAT REPLACES IT — two signals the corpus already computes:

  1. bm25_rank. BM25 *is* inverse document frequency. It already knows "defcon"
     is rare in this tenant and "compare" is everywhere. It ships on every
     lexically-matched row and was being parsed and thrown away. Ranking by it
     puts the right rows first: for "boswell hooks fleet versions" the governing
     GATE commit is #2 by BM25 and #34 by the API's own ordering.

  2. Branch. Steve hand-curated ~100 branches. Scoping a query to the right one
     turns a 50-row dragnet into the 2 rows that answer it (measured: the same
     query returns the gate at rank 0 and its correction at rank 1 under
     branch=command-center). That is a human-assigned topical label — strictly
     better evidence than any classifier guessing at the same thing.

KNOWN SHAPE OF THE DATA (verified live, do not assume otherwise):
  * A row carries EITHER bm25_rank OR distance, never both — hybrid RRF merges
    two disjoint legs. Only ~20-30 of 50 rows have BM25 at all.
  * /v2/search returns `branch` as NULL on every row, so branch cannot be
    filtered client-side. It must be passed as a query parameter and the server
    filters server-side.
  * Branch filtering is applied POST-retrieval: `limit` is the global dig depth
    and the branch cull happens after, which is why limit=10 with a branch can
    return zero rows. Always dig deep when scoping.
  * boswell_validate_routing has been broken since 2026-07-20 (permission denied
    creating branch_fingerprints), so routing cannot lean on it.
"""
import os
import re

# BM25 floor for admission, chosen by measurement (see tune_relevance.py).
try:
    BM25_FLOOR = float(os.environ.get("BOSWELL_BM25_FLOOR", "0.30"))
except Exception:
    BM25_FLOOR = 0.30

# A vector-only row (no BM25) is admitted ONLY when it came from a routed
# branch — otherwise it is an embedding's opinion with no lexical corroboration,
# which is exactly what produced the biography-about-ALS noise.
MAX_RESULTS = 3
SEARCH_LIMIT = 50

# Branches that never carry answers to a working question.
BRANCH_DENY = {"transcripts", "raw", "signals", "scratchpad", "contradictions",
               "cc-sessions", "cw-sessions", "_endpoint_test_branch",
               "_meter_test", "_test_keen", "_cortex_eval_v1", "calibration"}

_WORD = re.compile(r"[a-z0-9]+")


def _norm(name):
    """Branch name -> comparable token set. 'tint-atlanta' -> {tint, atlanta};
    'meridian.instinct-log' -> {meridian, instinct, log}."""
    return set(_WORD.findall(str(name).lower()))


def route(text, branch_names, cwd=""):
    """Return branch names this text plausibly belongs to, best first.

    Name matching only — boswell_branches does not expose the branch
    descriptions that were populated as semantic anchors in April, so there is
    nothing richer to match against from here. A branch is a candidate when the
    text contains ALL the distinctive words of its name ('tint atlanta' matches
    tint-atlanta; a bare 'tint' does not, because it would equally match
    tint-empire and tint_butler and routing to all of them is not routing).
    """
    hay = set(_WORD.findall(str(text).lower())) | set(_WORD.findall(str(cwd).lower()))
    if not hay:
        return []
    scored = []
    for name in branch_names:
        if name in BRANCH_DENY or name.startswith(("char.", "crew.", "_")):
            continue
        parts = {p for p in _norm(name) if len(p) >= 3}
        if not parts or not parts <= hay:
            continue
        # Longer, more specific names win: 'tint-atlanta' over 'atlas'.
        scored.append((len(parts), sum(len(p) for p in parts), name))
    scored.sort(reverse=True)
    out = []
    for _, _, name in scored:
        if name not in out:
            out.append(name)
    return out[:2]


def bm25(row):
    try:
        v = row.get("bm25_rank")
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def admit(row, routed=False):
    """(ok, reason). Lexical corroboration required unless branch-routed."""
    b = bm25(row)
    if b is None:
        if routed:
            return True, "vector row from a routed branch"
        return False, "no lexical match (vector-only, unrouted)"
    if b < BM25_FLOOR:
        return False, "bm25 %.4f < %.2f" % (b, BM25_FLOOR)
    return True, "bm25 %.4f" % b


def rank_key(row, routed=False):
    """Sort key — BM25 descending, routed rows ahead of unrouted at equal score.
    The API's own ordering is deliberately NOT trusted: it is what buried the
    answer at rank 34."""
    return (1 if routed else 0, bm25(row) or 0.0)
