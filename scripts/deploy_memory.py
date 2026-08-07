"""Deploy-target memory (boswell-hooks plugin).

PreToolUse handler on Bash. When the command is `git push <remote>`, it asks
Boswell what is known about THAT remote and injects the answer before the push
runs. It never denies.

WHY THIS EXISTS (2026-08-07, Steve watching deliberately):

Steve said "push it and deploy". The push went to `staging`, which had been
DECOMMISSIONED on 2026-06-17 — a fact Boswell held twice, one of which Steve
had personally confirmed. Two failed pushes and a diagnostic SSH connection
went into rediscovering it. He let it run on purpose, "waiting to see if a hook
would bring you the answer."

None could. UserPromptSubmit retrieval fires on the USER'S prompt, and the
prompt was four words — "push it and deploy" — with nothing for either search
leg to grab. The retrieval hook worked correctly and had nothing to work with.

The dangerous moment was never the prompt. It was the tool call. There was
already a PreToolUse lane watching Bash `git push` (git_guard), but it only
checks for force-push and waves everything else through; it had never asked
Boswell anything. This is that missing question.

Same shape as read_before_code, different key: that lane keys on the FILE about
to be edited, this one keys on the REMOTE about to be pushed to.

WHY IT RESOLVES THE REMOTE TO A HOST:
"staging" is a generic word that retrieves nothing useful. The .git/config URL
turns it into "tintwoodstock.com", which is the token the decommission records
are actually written about. Reading .git/config is a plain file read — no
subprocess, no network, no git invocation.

WHY IT RUNS THE SUPERSESSION FILTER:
Measured on the real corpus, the top-ranked row for this exact query is the
RETIRED protocol, with the record that retires it at rank 2. Injecting raw
relevance would have handed the model the stale instructions first. See
supersession.py.
"""
import json
import os
import re
from pathlib import Path

SEARCH_LIMIT = 40
SEARCH_TIMEOUT = 6.0          # PreToolUse budget is 10s; leave headroom
MAX_RESULTS = 3
# Verify a longer shortlist than we inject; see evaluate().
VERIFY_POOL = 5
MAX_WALK_UP = 12

# `git push [-flags] [remote [refspec...]]`. Stops at a shell separator so a
# compound command cannot drag unrelated words in.
_PUSH_RE = re.compile(r"\bgit\s+push\b([^\n;&|]*)")
_REMOTE_URL_RE = re.compile(r'\[remote\s+"([^"]+)"\]([^\[]*)', re.S)
_URL_RE = re.compile(r"^\s*url\s*=\s*(\S+)", re.M)


def _visible(command):
    """The part of the command the shell executes, not the data it carries.

    CAUGHT LIVE 2026-08-07, on this lane's own first backtest: the test drove a
    payload through a heredoc, and the lane fired on
        python - <<'PY' ... git push staging main ... PY
    which is not a push at all — it is a string inside a script. Scanning the
    raw command means every Bash call that merely MENTIONS a push retrieves and
    injects. empty_result already solved this; reuse it rather than re-derive a
    second, subtly different notion of what the shell can see.
    """
    try:
        import empty_result
        return empty_result._shell_visible(command or "")
    except Exception:
        return command or ""


def _remote_name(command):
    """The remote a `git push` targets, or None when it is implicit."""
    match = _PUSH_RE.search(_visible(command))
    if not match:
        return None
    for token in match.group(1).split():
        if token.startswith("-"):
            continue
        return token.strip("'\"")
    return None


def _git_dir(start):
    try:
        here = Path(start)
        for _ in range(MAX_WALK_UP):
            candidate = here / ".git"
            if candidate.is_dir():
                return candidate
            if candidate.is_file():        # worktree pointer file
                return None
            if here.parent == here:
                break
            here = here.parent
    except Exception:
        pass
    return None


def _remote_host(git_dir, remote):
    """Host for a remote, from .git/config. '' when it cannot be determined."""
    try:
        text = (git_dir / "config").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    for name, body in _REMOTE_URL_RE.findall(text):
        if name != remote:
            continue
        found = _URL_RE.search(body)
        if not found:
            return ""
        url = found.group(1)
        # ssh://host/path | user@host:path | https://host/path | host:path
        url = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
        if "@" in url.split("/")[0]:
            url = url.split("@", 1)[1]
        return url.split("/")[0].split(":")[0]
    return ""


def _query(repo, remote, host):
    terms = [t for t in (repo, remote, host) if t]
    seen, ordered = set(), []
    for term in terms:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    return " ".join(ordered + ["deploy", "push"])


def _created(row):
    value = row.get("created_at") or row.get("recorded") or ""
    return value if isinstance(value, str) else ""


def _slim(row):
    """Trim a row to what a deploy decision needs."""
    content = row.get("content")
    if isinstance(content, (dict, list)):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except Exception:
            content = str(content)
    if isinstance(content, str) and len(content) > 900:
        content = content[:900] + "..."
    return {
        "commit": (row.get("commit_hash") or "")[:12],
        "recorded": _created(row)[:10],
        "message": row.get("message"),
        "content": content,
    }


def _context(text):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }


def evaluate(data):
    """Return a PreToolUse additionalContext payload, or None. Never denies."""
    try:
        if (data or {}).get("tool_name") != "Bash":
            return None
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command")
        if not isinstance(command, str) or "push" not in command:
            return None

        remote = _remote_name(command)
        if not remote:
            return None

        cwd = data.get("cwd") or os.getcwd()
        git_dir = _git_dir(cwd)
        repo = ""
        host = ""
        if git_dir is not None:
            repo = git_dir.parent.name
            host = _remote_host(git_dir, remote)

        try:
            import boswell_client
            response = boswell_client.search(
                _query(repo, remote, host),
                limit=SEARCH_LIMIT, timeout=SEARCH_TIMEOUT)
        except Exception:
            return None          # a push must never be blocked on retrieval

        rows = response.get("results") or []
        if not rows:
            return None

        withheld = []
        try:
            import supersession
            rows, withheld = supersession.filter_rows(rows)
        except Exception:
            supersession = None

        # Newest first. For deploy facts recency IS the ranking that matters:
        # a server is decommissioned or a method retired at a POINT IN TIME, and
        # the newest record about a target is the only one describing it now.
        rows.sort(key=_created, reverse=True)
        # Verify a slightly LONGER shortlist than we intend to inject, so a row
        # dropped as superseded costs a candidate rather than an injection slot.
        # Same reasoning as the retrieval relevance floor: with only three slots,
        # spending one on a row that gets withheld is a third of the budget.
        rows = rows[:VERIFY_POOL]

        # Prove the shortlist is current. The set-local pass above cannot see a
        # superseder that shares no vocabulary with this query, which is exactly
        # how a row I had already retired got ranked first on the first run.
        if supersession is not None and rows:
            try:
                rows, late = supersession.verify_current(
                    rows, boswell_client.search)
                withheld = list(withheld) + list(late)
            except Exception:
                pass
        rows = rows[:MAX_RESULTS]

        try:
            audit = supersession.note(withheld) if supersession else ""
        except Exception:
            audit = ""

        rows = [_slim(r) for r in rows]
        if not rows:
            return None

        header = (
            "BOSWELL — what is known about the push target `%s`%s, retrieved "
            "before this push runs.\n\nNEWEST FIRST. If any row says this "
            "target is decommissioned, unreachable, or that the deploy method "
            "changed, STOP and tell Steve rather than running the command. "
            "Deploy protocols go stale: check for a newer record before "
            "following an older one, whatever it is labelled.\n"
            % (remote, (" (" + host + ")") if host else ""))
        if audit:
            header += audit + "\n"
        return _context(header + json.dumps(rows, ensure_ascii=False, indent=1))
    except Exception:
        return None  # FAIL-OPEN


if __name__ == "__main__":
    for cmd in ("git push staging main",
                "git push --force production main",
                "git push origin HEAD",
                "git push",
                "echo 'git push staging' > notes.txt"):
        print("%-46s -> remote=%r" % (cmd, _remote_name(cmd)))
