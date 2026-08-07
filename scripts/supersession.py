"""Read-time supersession filter (boswell-hooks plugin).

THE BUG THIS FIXES, measured 2026-08-07:

    query: "tintatlanta-website staging tintwoodstock.com deploy push"
    rank 1  caa1e166  "SACRED PROTOCOL ... staging first"        (2026-06-12)
    rank 2  617d0464  "SACRED PROTOCOL, SUPERSEDES caa1e166"     (2026-06-13)

The retired protocol outranks the record that retires it. That is not a ranking
bug — a superseded record is *maximally* relevant to the query it was superseded
on, because it is about exactly that subject. Relevance cannot tell you a thing
stopped being true.

It cost two real failures inside an hour: a deploy run against a staging server
decommissioned two months earlier, then the deploy itself performed by a method
retired on 2026-06-13, skipping a backup step marked non-negotiable.

The metadata to prevent it already existed. corrective_gate has been forcing
authors to write a `supersedes` field since June, and the records dutifully
carry it. Nothing had ever READ that field at retrieval time. This does.

WHY IT WITHHOLDS RATHER THAN DEMOTES:
Demoting a superseded row still puts it in the window, and a SACRED-labelled
protocol in the window gets followed. Withheld rows are reported by hash, so
the supersession is visible and auditable without the stale text being present
to act on.

CONSERVATIVE BY CONSTRUCTION — a wrongly-dropped row is invisible, which is the
expensive failure. A row is withheld only when ALL of:
  * another row in the SAME result set names it explicitly, by hash prefix
  * the prefix is >= 6 hex chars (shorter is not identifying)
  * the superseding row is strictly NEWER
Prose alone never supersedes anything; "supersedes the earlier plan" carries no
hash and is ignored.
"""
import json
import re

# "SUPERSEDES caa1e166" | 'supersedes": "caa1e1667a62"' | "supersedes my own cb058dcf"
#
# The gap is any characters, non-greedy and bounded to 24, rather than a
# punctuation class. MEASURED 2026-08-07: a punctuation-only gap missed
# e159cd6c, whose message reads "supersedes my own cb058dcf" — ordinary words
# sit between the verb and the hash. A hex-only exclusion class does not work
# either, because words like "the record" are themselves full of a-f.
#
# WHY THE `message` FIELD CARRIES THE WEIGHT HERE:
# /v2/search returns a 500-char CONTENT PREVIEW. A `"supersedes"` key in a
# large record routinely sits past that cutoff, so the structured field is
# often simply absent from a search result. The message is short and complete,
# which makes prose the more reliable carrier at retrieval time — the opposite
# of what you would assume. Both are scanned.
#
# \b on the hash keeps this to standalone tokens, and 24 chars is short enough
# that "supersedes the earlier plan" (no hash) matches nothing.
_SUPERSEDES_RE = re.compile(r"supersed\w*[^\n]{0,24}?\b([0-9a-f]{6,64})\b", re.I)

MIN_PREFIX = 6


def _text_of(row):
    """Everything in a row that could carry a supersedes claim."""
    parts = []
    for key in ("message", "content", "summary"):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (dict, list)):
            try:
                parts.append(json.dumps(value, ensure_ascii=False))
            except Exception:
                pass
    return "\n".join(parts)


def _created(row):
    value = row.get("created_at") or row.get("recorded") or ""
    return value if isinstance(value, str) else ""


def claims(rows):
    """{superseded_prefix: superseding_row} for every explicit claim found."""
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for prefix in _SUPERSEDES_RE.findall(_text_of(row)):
            prefix = prefix.lower()
            if len(prefix) >= MIN_PREFIX:
                out.setdefault(prefix, row)
    return out


def filter_rows(rows):
    """(kept, withheld) — withheld is [(row, superseding_row), ...].

    Order of `kept` is preserved; this only removes.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if len(rows) < 2:
        return rows, []

    found = claims(rows)
    if not found:
        return rows, []

    kept, withheld = [], []
    for row in rows:
        commit = (row.get("commit_hash") or "").lower()
        killer = None
        if commit:
            for prefix, source in found.items():
                if not commit.startswith(prefix):
                    continue
                # Never let a record supersede itself, and never let an older
                # record retire a newer one — that ordering means the claim is
                # stale or the hashes collided on a short prefix.
                if (source.get("commit_hash") or "").lower() == commit:
                    continue
                if _created(source) <= _created(row):
                    continue
                killer = source
                break
        if killer is None:
            kept.append(row)
        else:
            withheld.append((row, killer))
    return kept, withheld


def verify_current(rows, search, limit=12, timeout=4.0):
    """Second pass: catch supersessions whose superseder is OUTSIDE `rows`.

    filter_rows only sees the result set it is handed, and that is not enough.
    MEASURED 2026-08-07: cb058dcf was superseded 20 minutes later by e159cd6c,
    but e159cd6c is about the deploy METHOD and shares no terms with a staging
    query — it does not appear for that search at limit 60, so the set-local
    filter could never see it, and the stale row was injected as row 1.

    A superseder always names its target's hash, so the hash itself is a
    reliable retrieval key even when nothing else about the two records
    overlaps. Verified: searching "supersedes cb058dcf2f12" returns e159cd6c at
    rank 0.

    Run this on the SHORTLIST, not the candidate set — it costs one extra
    search, and only the rows about to be injected need to be proven current.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    hashes = [(r.get("commit_hash") or "")[:12] for r in rows if r.get("commit_hash")]
    if not hashes:
        return rows, []

    # ONE QUERY PER HASH, not one combined query. MEASURED 2026-08-07:
    # "supersedes cb058dcf2f12" returns the superseder at rank 0, but
    # "supersedes cb058dcf2f12 835e31adabed f11920008346" returns generic
    # supersession chatter and drops it entirely — the extra hashes dilute the
    # signal instead of widening it. Bounded by the shortlist size (3), so this
    # is a handful of small lookups, not a fan-out.
    extra = []
    for commit in hashes[:5]:
        try:
            response = search("supersedes " + commit, limit=limit, timeout=timeout)
            extra.extend((response or {}).get("results") or [])
        except Exception:
            continue             # never fail a caller on the second pass
    if not extra:
        return rows, []

    kept, withheld = filter_rows(rows + extra)
    kept_hashes = {r.get("commit_hash") for r in kept}
    originals = {r.get("commit_hash") for r in rows}
    return ([r for r in rows if r.get("commit_hash") in kept_hashes],
            [(r, k) for r, k in withheld if r.get("commit_hash") in originals])


def note(withheld):
    """One-line audit trail for what was withheld, or '' when nothing was."""
    if not withheld:
        return ""
    # Deduplicated: verify_current runs one lookup per hash, so the same row can
    # be reported superseded by the same record several times over.
    bits, seen = [], set()
    for row, killer in withheld:
        pair = ((row.get("commit_hash") or "?")[:8],
                (killer.get("commit_hash") or "?")[:8])
        if pair in seen:
            continue
        seen.add(pair)
        bits.append("%s (superseded by %s)" % pair)
    return "WITHHELD as superseded: " + "; ".join(bits)
