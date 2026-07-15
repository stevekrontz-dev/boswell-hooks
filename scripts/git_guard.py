"""git push safety guard (boswell-hooks plugin).

PreToolUse(Bash) handler. Pure-string inspection of `git push` commands — no
network, no subprocess, no latency. Blocks the one IRREVERSIBLE footgun in
Steve's deploy setup: force-pushing to a HostGator deploy remote, which erases
the production auto-backup commits (they are NOT recoverable).

TWO DIFFERENT RULES — do not collapse them (docstring corrected 2026-07-15
after a self-test showed the text and the behavior disagreed):

  * DEPLOY remotes (production/staging): ANY force is DENIED, and that
    deliberately INCLUDES --force-with-lease. A lease only protects refs you
    have not fetched; once the backup bot's commits are in your local ref cache
    the lease check passes and the force still erases them. Boswell DEPLOY
    REFERENCE 868dea38 is absolute: "NEVER force-push (it erases the backups).
    Reconcile with a merge." There is no lease-shaped exception, so do not
    "fix" this by letting --force-with-lease through on these remotes.
  * EVERY OTHER remote (origin, feature repos): a bare --force/-f gets an ASK
    that nudges toward --force-with-lease. That nudge applies HERE only.

Why a guard and not discipline: the permission allowlist is wide-open
(Bash allowed, dangerous-mode prompt skipped), so this handler is the only
thing standing between an agent and an unrecoverable deploy-repo force-push.

Fail-open everywhere: if anything about parsing is uncertain, return None
(ALLOW). A guard that blocks legitimate work is worse than no guard. Note that
git itself already rejects non-fast-forward pushes, so this guard deliberately
covers only the case git does NOT protect you from — a force-push that succeeds.

Returns a PreToolUse decision dict, or None to stay silent (allow).
"""
import shlex

# HostGator deploy remotes where a force-push erases prod auto-backup commits.
# (Boswell DEPLOY REFERENCE 868dea38: "NEVER force-push (it erases the
# backups). Reconcile with a merge.")
DEPLOY_REMOTES = {"production", "staging"}
FORCE_TOKENS = {"--force", "-f", "--force-with-lease"}


def _split_segments(command):
    """Break a compound shell line into individual command segments so we can
    spot a `git push` buried in `git fetch && git push ...`."""
    s = command.replace("&&", "\n").replace("||", "\n")
    for sep in (";", "|"):
        s = s.replace(sep, "\n")
    return [seg.strip() for seg in s.split("\n") if seg.strip()]


def _analyze(segment):
    """Return dict describing a `git push` segment, or None if it isn't one."""
    try:
        tokens = shlex.split(segment)
    except Exception:
        return None  # unparseable → allow
    try:
        gi = tokens.index("git")
    except ValueError:
        return None
    if len(tokens) <= gi + 1 or tokens[gi + 1] != "push":
        return None
    args = tokens[gi + 2:]  # everything after `git push`
    force_any = any(t in FORCE_TOKENS for t in args) or any(
        t.startswith("+") for t in args)  # +refspec is also a force push
    bare_force = any(t in ("--force", "-f") for t in args)
    remote = next((t for t in args if not t.startswith("-")), "")
    return {"force_any": force_any, "bare_force": bare_force, "remote": remote}


def _decision(kind, reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,  # "deny" | "ask"
            "permissionDecisionReason": reason,
        }
    }


def evaluate(data):
    if (data.get("tool_name") or "") != "Bash":
        return None
    ti = data.get("tool_input")
    command = ti.get("command", "") if isinstance(ti, dict) else ""
    if "push" not in command:  # cheap pre-filter
        return None
    for seg in _split_segments(command):
        info = _analyze(seg)
        if not info:
            continue
        if info["force_any"] and info["remote"] in DEPLOY_REMOTES:
            return _decision(
                "deny",
                "BLOCKED: force-push to deploy remote '%s'. This erases the "
                "production auto-backup commits, which are NOT recoverable. "
                "Reconcile with a merge instead: fetch the remote, "
                "git merge %s/<branch>, then push WITHOUT --force. "
                "(Boswell DEPLOY REFERENCE 868dea38.)"
                % (info["remote"], info["remote"]))
        if info["bare_force"]:
            return _decision(
                "ask",
                "Force-push to '%s'. Prefer --force-with-lease (it won't "
                "clobber commits you haven't fetched). Proceed only if sure."
                % (info["remote"] or "(default remote)"))
    return None


if __name__ == "__main__":
    # Self-test harness: prints decisions for a few sample commands.
    import json
    cases = [
        "GIT_SSH_COMMAND='ssh -i k' git push production main",
        "git push --force production main",
        "git push production main --force-with-lease",
        "git fetch origin && git push origin main",
        "git push -f origin feature/x",
        "git push origin main",
        "git commit -m 'push the button'",
    ]
    for c in cases:
        print(repr(c), "->",
              json.dumps(evaluate({"tool_name": "Bash",
                                   "tool_input": {"command": c}})))
