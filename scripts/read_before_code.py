"""Read-Before-Code injector (boswell-hooks plugin).

PreToolUse handler on the file-mutation tools (Edit / Write / MultiEdit /
NotebookEdit). It does NOT block. When a mutation is about to land on a file the
session has no Boswell evidence for, this hook runs the search ITSELF against
/v2/search and returns the hits as `additionalContext`, then gets out of the way.

WHY INJECTION AND NOT A REMINDER (Steve, 2026-08-04):
  * A per-turn "remember to grok before coding" banner is precisely the thing
    the tenant's STRUCTURAL-NOT-ASPIRATIONAL commitment forbids — "never
    delegated to the model via context markers it will ignore." A fixed string
    becomes wallpaper within a handful of turns and measurably changes nothing.
  * A hard DENY taxes legitimate work and puts a permission prompt in front of
    Steve, violating DONT-HAND-STEVE-CEREMONY (zero-cost-to-Steve guardrails).
  * Carrying the memory into the window requires no cooperation from the model.
    The stored state is simply present before the edit exists. That is the only
    version of this that survives an agent which forgets to ask.

The companion gates stay as they are: git_guard (irreversible pushes) and
corrective_gate (read-before-WRITE on permanent memory) both DENY. This one is
the read-before-CODE layer and it only ever adds context.

Design constraints inherited from the plugin:
  * Fail-open everywhere. A retrieval failure, a missing key, a dead network —
    all return None (silent allow). Never break an edit on a hook bug.
  * Evidence comes from readstate.py's existing per-session ledger. No new
    bookkeeping substrate.
  * At most ONE injection per (session, file). Re-editing the same file in a
    session does not re-query; the context is already in the window.
  * State lives machine-local under config.STATE_ROOT, never in the synced
    plugin dir.
"""
import json
import re
import time
from pathlib import Path

try:
    import readstate
except Exception:  # pragma: no cover - never break the hook on an import
    readstate = None

try:
    from config import STATE_ROOT
except Exception:  # pragma: no cover
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

MUTATION_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Retrieval budget. hooks.json allows PreToolUse 10s; stay well inside it so a
# slow Boswell can never be felt as editor latency.
SEARCH_TIMEOUT = 8.0
# Fetch DEEP, filter HARD. Measured 2026-08-04: for "does this computer match
# the rest of the fleets boswell hooks versions", the correction that answers it
# sits at rank 38 and the governing gate commit at rank 44 — both invisible at
# any sane top-k. Boswell's ranking buries recent, on-topic rows under older
# loosely-related ones, so a small window returns noise while the answer is two
# pages down. The grounding gate is precise enough to sift a large candidate set
# (it dropped 100% of the off-topic rows in testing), so the cheap correct move
# is to widen the net and let grounding do the discriminating.
SEARCH_LIMIT = 50

# Precision floor, mirroring the Codex-side automatic-context contract. Boswell
# ranks the best available rows even when all are poor; rank alone is not
# evidence worth spending context on.
# Weak backstop only. Grounding (_grounded) is what decides relevance now; this
# just rejects rows that are far by BOTH measures. Raised from 0.55 because the
# measured distribution (0.51-0.73, median 0.63) made a tight cutoff reject
# well-grounded rows for no reason.
MAX_DISTANCE = 0.72
MAX_RESULTS = 3
# Weight of the search's own rank when selecting which admitted rows to inject.
# Swept against the 7-case backtest twice. Before truncation handling existed,
# 6 was the smallest weight reaching the then-ceiling of 4/7. With truncation
# trust enabled the ceiling moved to 5/7 and 10 is the smallest weight that
# reaches it — and 10 still retains the rank-34 credential ruling, so the deep
# rows this whole mechanism exists to rescue are not traded away for it.
RANK_WEIGHT = 10.0

# A row whose content preview arrived truncated cannot be judged fairly by
# lexical grounding: the search matched the FULL record, we only see ~700
# alphabetically-ordered characters of it. Measured 90% of rows arrive this
# way. Inside this rank the search's own judgement is better evidence than our
# fragment, so grounding is not allowed to veto. Deeper than this we keep
# requiring grounding, because rank alone is exactly the noise the gate exists
# to filter. Swept 0/1/2/3/5/8: 3 is where the windshield case is recovered.
TRUNCATED_TRUST_RANK = 3

# Upper bound on directory entries examined when listing a new file's peers.
# 4,000 costs ~180 ms; the whole point is to stay far under the 2s of headroom
# the 10s PreToolUse budget leaves after SEARCH_TIMEOUT. Normal source
# directories are orders of magnitude smaller, so this only ever bites on
# upload/spool/node_modules-shaped directories, where a partial sample is still
# useful and a killed hook is not.
MAX_SIBLING_SCAN = 4000
# How deep a distance-less (BM25-only) row may sit and still be admitted.
LEXICAL_TOP_N = 2
CONTENT_CHARS = 700
EXCLUDED_TYPES = {"credential", "sacred_manifest", "skill", "task", "transcript"}

# Path noise that carries no project identity and would poison the query.
_PATH_STOP = {
    "users", "home", "steve", "projects", "src", "lib", "app", "scripts",
    "node_modules", "dist", "build", "test", "tests", "temp", "tmp", "claude",
    "documents", "desktop", "appdata", "local", "roaming", "python", "site",
    "packages", "index", "main", "utils", "common", "config",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_HEXCHARS = frozenset("0123456789abcdef")


def _is_hashy(tok):
    """True for uuid/hash fragments, which are path noise that would poison the
    query. Verified need (2026-08-04): a scratchpad path contributed
    'bac2cd78', '97ea', '417e' from its session-id directory, consuming half
    the term budget and turning the search into garbage.

    Rule: all-hex AND contains a digit. The digit requirement spares ordinary
    hex-letter words ('faced', 'added', 'decade') that carry real meaning,
    while catching every uuid chunk regardless of length.
    """
    return bool(set(tok) <= _HEXCHARS and any(c.isdigit() for c in tok))

# Grounding thresholds, CHOSEN BY MEASUREMENT not intuition (2026-08-04, swept
# against 8 real prompts from Steve's own session):
#   strong>=8 / overlap>=2 -> let a BIOGRAPHY row about an insurance gap through
#                             on "is that the right method of action here?"
#   strong>=8 / overlap>=3 -> killed that noise but went SILENT on "check all
#                             the boswell hooks on this machine", the single
#                             most valuable case in the set
#   strong>=9 / overlap>=3 -> same loss
#   strong>=7 / overlap>=3 -> kills the noise AND keeps the hooks case. Chosen.
# Retune only by re-running that sweep; a threshold picked by feel is what got
# the earlier distance filter wrong.
GROUND_STRONG_LEN = 7
GROUND_MIN_OVERLAP = 3

MEMO_NAME = "read_before_code.json"


def _memo_path():
    return STATE_ROOT / "readstate" / MEMO_NAME


def _load_memo():
    try:
        value = json.loads(_memo_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(session_id, target):
    """Record that this (session, file) already got its injection. Best-effort:
    a failed memo just means a possible duplicate injection later, never a
    broken edit."""
    try:
        memo = _load_memo()
        # Drop entries older than a day so the memo cannot grow without bound.
        cutoff = time.time() - 86400
        memo = {k: v for k, v in memo.items()
                if isinstance(v, (int, float)) and v > cutoff}
        memo[f"{session_id}::{target}"] = time.time()
        path = _memo_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(memo), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _already_injected(session_id, target):
    return f"{session_id}::{target}" in _load_memo()


def _target_path(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _path_terms(target):
    """Distinctive terms from a path: the file stem plus meaningful ancestors.

    'C:/Users/Steve/plugins/boswell-hooks/scripts/git_guard.py'
        -> ['git', 'guard', 'boswell', 'hooks', 'plugins']

    Ancestors are walked NEAREST-FIRST: the directory immediately containing
    the file describes it far better than the drive root does, and the term
    budget is small enough that ordering decides what survives.
    """
    try:
        p = Path(target)
    except Exception:
        return []
    raw = [p.stem] + list(reversed(p.parent.parts))
    terms = []
    for chunk in raw:
        for tok in _TOKEN_RE.findall(str(chunk)):
            low = tok.lower()
            if len(low) < 3 or low in _PATH_STOP or low.isdigit():
                continue
            if _is_hashy(low):
                continue
            if low not in terms:
                terms.append(low)
    return terms[:6]


def _is_creation(data, target):
    """True when this call CREATES a file rather than editing one.

    Write is the only mutation tool that can bring a file into existence, and
    the harness refuses a Write over an unread existing file, so "Write + not
    on disk" is a reliable creation signal.
    """
    if (data.get("tool_name") or "") != "Write":
        return False
    try:
        return not Path(target).exists()
    except Exception:
        return False


def _sibling_names(target, limit=24):
    """The files that ALREADY live beside the target, same extension.

    This is injected as DATA, not folded into the search query. Measured
    2026-08-06: widening the Boswell query with sibling-derived TERMS does not
    work. The ruling that should have stopped windshield-tint.php (c235f94,
    "why did you build a whole page to do the same exact thing the quote system
    does") does not appear in the top 50 for ANY path-derived query — it shares
    almost no vocabulary with "windshield tint". Retrieval cannot be relied on
    to answer "does this already exist"; the directory listing can, and it is
    always correct.

    A model about to create windshield-tint.php next to get-quote.php,
    film-removal.php, flat-glass-quote.php and protection.php does not need to
    be told to think architecturally. It needs to be shown the four files it is
    about to duplicate. That is a fact it does not have, cheaply obtained.

    Cheap: one listdir, no recursion.
    """
    try:
        p = Path(target)
        parent, stem, suffix = p.parent, p.stem.lower(), p.suffix.lower()
        names = []
        scanned = 0
        capped = False
        for entry in parent.iterdir():
            # BOUNDED SCAN. Measured 2026-08-06: ~44 ms per 1,000 entries, so a
            # 50,000-entry directory costs ~2.2s — and SEARCH_TIMEOUT already
            # reserves 8s of the 10s PreToolUse budget in hooks.json, so the
            # hook would be KILLED mid-flight and the edit would proceed with no
            # context at all. Directories that size are real (uploads,
            # transcript spools, node_modules). Only 24 peers are ever shown, so
            # scanning the whole tree buys nothing worth a timeout.
            scanned += 1
            if scanned > MAX_SIBLING_SCAN:
                capped = True
                break
            # SAME EXTENSION ONLY. The peers of a new .php page are the other
            # .php pages, not .htaccess/.md/.json sitting in the same folder.
            # Mixing them yields exactly the noise measured on the first pass
            # ('htaccess', 'gmail', 'notes') and buries the real convention.
            if entry.suffix.lower() != suffix:
                continue
            if not entry.is_file() or entry.stem.lower() == stem:
                continue
            names.append(entry.stem)
    except Exception:
        return []

    # Peers that share a word with the new file are the ones most likely to
    # already own the job, so they must survive truncation. Alphabetical order
    # alone pushed protection.php and windshield-protection.php into "+13 more"
    # for a windshield quote page — the two files most worth looking at.
    target_tokens = {t.lower() for t in _TOKEN_RE.findall(stem) if len(t) >= 4}

    def _rank(name):
        toks = {t.lower() for t in _TOKEN_RE.findall(name) if len(t) >= 4}
        return (0 if (toks & target_tokens) else 1, name.lower())

    names.sort(key=_rank)
    return names[:limit], max(0, len(names) - limit), capped


def _has_evidence(session_id, terms):
    """True if this session already read something Boswell-side that overlaps
    the file being touched. Reuses the corrective gate's ledger verbatim."""
    if readstate is None or not terms:
        return False
    try:
        had, read_tokens = readstate.recent_read_tokens(session_id)
    except Exception:
        return False
    if not had:
        return False
    return bool({t for t in terms if len(t) >= 4} & read_tokens)


def _age(created_at):
    """Human-readable age of a memory, e.g. '3d ago'. Returns 'unknown' rather
    than raising on any unexpected format — an unparseable date must never cost
    us the row, only the age annotation."""
    try:
        from datetime import datetime, timezone
        raw = str(created_at or "").strip()
        if not raw:
            return "unknown"
        raw = raw.replace("Z", "+00:00").replace(" ", "T", 1)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
        if secs < 0:
            return "just now"
        for div, unit in ((86400 * 365, "y"), (86400 * 30, "mo"),
                          (86400, "d"), (3600, "h"), (60, "m")):
            if secs >= div:
                return "%d%s ago" % (int(secs // div), unit)
        return "just now"
    except Exception:
        return "unknown"


# Long-but-generic words. Grounding treats any token >=GROUND_STRONG_LEN as
# distinctive enough to admit a row on its own, and length turns out to be a
# poor proxy for that: "compare it to the home pcc" matched an Ollama adapter
# test purely because both contained the word "compare" (7 chars). These carry
# no entity identity, so they can never be the sole basis for a match. Kept
# local to grounding — readstate._STOPWORDS is shared with corrective_gate and
# is not mine to widen.
# CAREFUL: "generic" means generic IN THIS TENANT, not in English. A first cut
# of this list included machine/install/version/current/running and immediately
# broke "check all the boswell hooks on this machine" — in Steve's domain a
# machine is a fleet box, an install is a plugin deployment, and a version is
# the thing the whole fleet audit turns on. Only words that can never name an
# entity here belong below.
_GENERIC_LONG = frozenset("""
compare compared comparing between because before after should would could
another through without already anything everything something nothing
however therefore actually probably possible generally
problem problems question questions example examples
different difference against during specific specifically
""".split())
_TOKEN_MIN_DISTINCT = 4


def _fold(tokens):
    """Collapse trivial plurals so 'fleets' and 'fleet' compare equal. Only
    touches tokens long enough that the trailing 's' is unlikely to be part of
    the stem, and leaves '...ss' alone (class, process)."""
    out = set()
    for t in tokens:
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            out.add(t[:-1])
        else:
            out.add(t)
    return out


def _grounded(query_tokens, item):
    """Lexical grounding gate — the actual relevance test.

    Measured 2026-08-04 against 13 real prompts / 60 returned rows: distances
    ran 0.51-0.73 with a 0.63 median and NO gap between useful and useless
    rows. A distance threshold there is a guess wearing a decimal point — at
    0.55 it admitted 6% of rows, at 0.65 it admitted mostly noise. Proof it was
    meaningless: asked "we have a supersession method in boswell right?", the
    distance filter happily admitted a biography synthesis about Kenneth's ALS
    and a v5 entity-extraction batch record.

    Semantic distance says "these embeddings are near." It does not say "this
    row is about the thing you asked." Overlap on distinctive terms does.

    Same contract corrective_gate has used in production to decide whether a
    read actually covered a fact: one strong (>=8 char) shared token, or two
    significant ones. Reused rather than reinvented so both gates agree on what
    "about the same thing" means.
    """
    if not query_tokens or readstate is None:
        return True  # no basis to judge -> don't block on it
    try:
        row_tokens = readstate.tokenize(
            str(item.get("message") or "") + " " + str(item.get("content") or "")[:2000])
    except Exception:
        return True
    # Fold trivial plurals before comparing. Measured cost of not doing this:
    # "does this computer match the rest of the FLEETS boswell hooks" failed to
    # match a commit whose text says FLEET, and the gate went silent on exactly
    # the question it had the answer to. Applied here only — readstate.tokenize
    # is shared with corrective_gate and is not mine to loosen.
    overlap = _fold(query_tokens) & _fold(row_tokens)
    if not overlap:
        return False
    if any(len(t) >= GROUND_STRONG_LEN and t not in _GENERIC_LONG
           for t in overlap):
        return True
    return len(overlap - _GENERIC_LONG) >= GROUND_MIN_OVERLAP


def _content_truncated(item):
    """True when the row's `content` arrived cut off mid-JSON.

    /v2/search returns a PREVIEW of content (~700 chars, keys alphabetically
    ordered), not the whole record. Measured 2026-08-06 across three real
    queries: 135 of 150 rows (90%) arrive unparseable, i.e. truncated.

    That matters because grounding re-judges relevance from the text it can
    see, while the SEARCH matched against the full indexed record. A row can
    therefore rank #0 on a word that is not in the fragment at all. Live case:
    the restated quote-architecture ruling (4bbedc24) ranked #0 for "couldnt
    book just a front windshield" because its `symptom` field says exactly
    that — and `symptom` sorts alphabetically past the preview cutoff, so the
    grounding gate saw no "windshield" anywhere and refused the single most
    relevant row in the result set.
    """
    content = item.get("content")
    if not isinstance(content, str) or not content:
        return False
    stripped = content.strip()
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
        return False
    except Exception:
        return True


def _ground_strength(query_tokens, item):
    """HOW STRONGLY a row is grounded — the number _grounded throws away.

    _grounded answers yes/no and discards the magnitude, so callers taking the
    first N admitted rows were really taking the SHALLOWEST N, not the best N.
    This returns the magnitude so selection can rank on it.

    Strong (>=GROUND_STRONG_LEN) distinctive tokens are weighted above merely
    numerous ones: one shared "windshield" means more than three shared
    four-letter words.
    """
    if not query_tokens or readstate is None:
        return 0.0
    try:
        row_tokens = readstate.tokenize(
            str(item.get("message") or "") + " " + str(item.get("content") or "")[:2000])
    except Exception:
        return 0.0
    overlap = (_fold(query_tokens) & _fold(row_tokens)) - _GENERIC_LONG
    if not overlap:
        return 0.0
    strong = sum(1 for t in overlap if len(t) >= GROUND_STRONG_LEN)
    return float(strong * 2 + len(overlap))


def select_rows(results, query_tokens, max_results=MAX_RESULTS):
    """Pick the BEST admitted rows, not the shallowest ones.

    WHY THIS EXISTS (backtested 2026-08-06 over 7 documented failures spanning
    pricing, trading, ML training, repo hygiene, secrets and the tint quote
    funnel): the governing memory was inside the top 50 for 5 of the 7, but
    reached the model for only 3. The two that were found and dropped were the
    worktree-merge-debt post-mortem at rank 19 and Steve's June credential
    ruling at rank 34 — the exact decision whose absence had already caused
    "I re-opened it twice" (c56a374b).

    They were dropped because the caller walked the results in RANK order and
    stopped at the first three that passed the gate, so three shallow,
    weakly-grounded rows crowded out the row that actually answered the
    question. That defeated the deliberate widening of SEARCH_LIMIT to 50,
    which exists precisely because on-topic rows sit 20-45 deep.

    Admission is unchanged — _slim is still the single contract, and a row that
    fails grounding is still refused. Only the ORDER of what survives changes.

    Selection BLENDS grounding strength with the search's own rank rather than
    replacing one with the other. Measured on the same 7 cases:

        pure rank order (previous behaviour) ... 3/7
        pure grounding strength ................ 3/7   (recovered the rank-34
                                                        credential ruling but
                                                        lost the rank-1
                                                        dead-man post-mortem)
        blended, RANK_WEIGHT >= 6 .............. 4/7   no regressions

    Ranking purely on grounding throws away RRF's hybrid signal, which is real:
    the dead-man post-mortem was the single best row for its prompt and sat at
    rank 1, yet scored below a wordier neighbour that happened to share more
    tokens. Ranking purely on rank throws away topical precision. Only the
    blend keeps both. Raising max_results to 4 or 5 was measured too and buys
    nothing over the blend, so the injection stays small.
    """
    scored = []
    for rank, item in enumerate(results or []):
        row = _slim(item, rank, query_tokens)
        if row is None:
            # Truncation rescue: a shallow row whose content preview was cut
            # off gets judged on the search's ranking instead of on a fragment
            # that may not contain the matching words at all. This is not a
            # softening of the gate — it is refusing to draw a conclusion from
            # evidence we can see is incomplete.
            if not (_content_truncated(item) and rank < TRUNCATED_TRUST_RANK):
                continue
            row = _slim(item, rank, None)
            if row is None:
                continue
        # Rank bonus decays fast: rank 0 -> +6.0, rank 1 -> +3.0, rank 5 -> +1.0,
        # rank 34 -> +0.17. Shallow rows keep the edge they have earned without
        # being able to bury a strongly-grounded deep row outright.
        bonus = RANK_WEIGHT / (1.0 + rank)
        scored.append((_ground_strength(query_tokens, item) + bonus, -rank, row))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [row for _, _, row in scored[:max_results]]


def _slim(item, rank, query_tokens=None):
    """Return a slim high-confidence row, or None to abstain.

    /v2/search is hybrid RRF, and `distance` ONLY ships on rows that came
    through the vector leg — a row matched purely by BM25 has no distance at
    all (verified live 2026-08-04: the top hit for "boswell hooks dispatcher"
    was lexical, distance=None, and was also the most relevant row returned).
    Requiring a float distance therefore discards the best lexical hits and
    quietly turns this whole hook into a no-op. Two admission rules:

      * vector row  -> keep if distance is inside MAX_DISTANCE
      * lexical row -> keep only at the very top of the ranking (BM25's first
        couple of rows are strong signal; deeper ones are noise)

    `branch` is deliberately not carried: the endpoint returns it as null on
    every row, and a null field is just context noise (phantom-marker
    discipline — reference only what actually ships).
    """
    if not isinstance(item, dict):
        return None
    if str(item.get("content_type") or "memory").lower() in EXCLUDED_TYPES:
        return None
    # Grounding is checked BEFORE distance: it is the load-bearing filter now,
    # and distance is demoted to a weak tiebreak that only rejects the truly far.
    if not _grounded(query_tokens, item):
        return None

    raw_distance = item.get("distance")
    try:
        distance = float(raw_distance)
    except (TypeError, ValueError):
        distance = None

    if distance is not None:
        if distance > MAX_DISTANCE:
            return None
        score = round(distance, 4)
    else:
        if rank >= LEXICAL_TOP_N:
            return None
        if item.get("rrf_score") is None and item.get("reranked_score") is None:
            return None
        score = "lexical"

    return {
        "message": item.get("message"),
        "commit": str(item.get("commit_hash") or "")[:12],
        "content_type": item.get("content_type"),
        "match": score,
        # created_at is carried DELIBERATELY (added 2026-08-04 after Steve
        # asked). Every retrieved row is a claim frozen at a moment, and an
        # undated claim reads as current fact. On 2026-08-04 retrieval ranked a
        # 40-minute-old, already-corrected fleet audit at #1 with nothing in the
        # payload to signal it was stale — the model had no way to discount it.
        # A visible date is the cheapest possible staleness defence.
        "recorded": str(item.get("created_at") or "")[:19] or "unknown",
        "age": _age(item.get("created_at")),
        "content": str(item.get("content") or "")[:CONTENT_CHARS],
    }


def _context(text):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }


def evaluate(data):
    """Return a PreToolUse additionalContext payload, or None. Never denies,
    never raises."""
    try:
        if (data.get("tool_name") or "") not in MUTATION_TOOLS:
            return None
        target = _target_path(data.get("tool_input"))
        if not target:
            return None
        session_id = data.get("session_id") or "nosession"
        if _already_injected(session_id, target):
            return None

        terms = _path_terms(target)
        if not terms:
            return None

        # CREATING a file is a different act from editing one, and until
        # 2026-08-06 this hook could not tell them apart.
        #
        # Editing asks "what do I already know about this file". Topical
        # evidence answers that, so the shortcut below is right for edits.
        # Creating asks "should this exist, and does this codebase already have
        # a way to do it" — a question no amount of reading about the SUBJECT
        # can answer, because the answer lives in the codebase's conventions.
        #
        # Measured failure (session 615d701f): a dozen Boswell searches on
        # "windshield"/"tint" satisfied _has_evidence, so the creation of a new
        # top-level windshield-tint.php was memo'd SILENTLY. Boswell already
        # held the ruling (c235f94, "why did you build a whole page to do the
        # same exact thing the quote system does") and the model never saw it.
        # The page was built, reviewed by Steve, and thrown away.
        #
        # So on a creation: topical evidence does not license a new surface,
        # and the query is widened with the neighbours that define what this
        # directory already is.
        creating = _is_creation(data, target)
        if not creating and _has_evidence(session_id, terms):
            # The session already read Boswell on this subject. Nothing to add;
            # memo it so we don't re-check on every subsequent edit.
            _remember(session_id, target)
            return None

        query_terms = list(terms)

        try:
            import boswell_client
            response = boswell_client.search(
                " ".join(query_terms), limit=SEARCH_LIMIT, timeout=SEARCH_TIMEOUT)
        except Exception:
            return None  # Boswell unreachable / unkeyed -> silent allow

        query_tokens = readstate.tokenize(" ".join(query_terms)) if readstate else set()
        rows = select_rows(response.get("results") or [], query_tokens)

        # Memo regardless of hit count: a miss means Boswell holds nothing for
        # this file, and re-querying on the next edit would buy nothing.
        _remember(session_id, target)

        if creating:
            # The peer list is the load-bearing part and it does NOT depend on
            # retrieval succeeding, so this branch fires even with zero rows.
            peers, extra, peers_capped = _sibling_names(target)
            parts = ["You are about to CREATE " + Path(target).name
                     + ", which does not exist yet. Creating a file is an "
                     "architectural act: the question is not what you know "
                     "about the subject, it is whether this codebase already "
                     "has a surface that does this job."]
            if peers:
                listing = ", ".join(peers)
                if extra:
                    listing += ", +%d more" % extra
                if peers_capped:
                    listing += " (large directory — listing is a partial sample)"
                parts.append(
                    "EXISTING " + (Path(target).suffix or "file")
                    + " FILES IN THAT SAME DIRECTORY: " + listing
                    + "\nIf one of these already does what you are about to "
                      "build, extend it instead of adding a sibling. Adding a "
                      "parallel surface duplicates whatever funnel/flow the "
                      "existing one owns, and the two then drift.")
            if rows:
                parts.append(
                    "BOSWELL PRIOR STATE (path-matched; may be loose — the "
                    "governing decision is often NOT retrievable from a new "
                    "file's name, so treat silence here as no evidence, not as "
                    "approval):\n"
                    + json.dumps(rows, ensure_ascii=False, indent=1))
            return _context("\n\n".join(parts))

        if not rows:
            return None

        return _context(
            "BOSWELL PRIOR STATE for " + Path(target).name + " — retrieved "
            "automatically because this session had not read Boswell on this "
            "subject before editing it. Reconcile your change against what is "
            "already recorded here; if it contradicts one of these, say so "
            "explicitly rather than silently overwriting the behavior:\n"
            + json.dumps(rows, ensure_ascii=False, indent=1))
    except Exception:
        return None  # FAIL-OPEN


if __name__ == "__main__":
    # Offline self-test of the pure helpers (no network).
    print("path term extraction:")
    for sample in (
        r"C:\Users\Steve\plugins\boswell-hooks\scripts\git_guard.py",
        r"C:\Users\Steve\Projects\tintatlanta\crm\inbox.php",
        "/home/steve/agents/foreman.py",
    ):
        print(f"  {sample}\n    -> {_path_terms(sample)}")
    print("\ntool routing:")
    for tn in ("Edit", "Write", "Read", "Bash", "NotebookEdit"):
        print(f"  {tn:14s} -> {tn in MUTATION_TOOLS}")
