"""Suppressed-error empty-result guard (boswell-hooks plugin).

PostToolUse(Bash) handler. When a command hid its own errors AND came back with
nothing, it says so — because an empty result from a silenced command is not a
negative finding, it is an unknown, and the two are indistinguishable at the
point where the model draws its conclusion.

WHY THIS EXISTS (measured, session 615d701f, 2026-08-06):
  * `find "C:/Users/Steve/.claude" -name "dispatcher.py" 2>/dev/null | head -5`
    returned nothing. Reported to Steve as "no dispatcher.py anywhere".
    PowerShell then found four. The find had failed, not searched.
  * `ls -la "C:/Users/Steve/.claude" | head -20` truncated before `skills/`.
    Reported as "the plugin isn't installed". It was installed, in
    ~/.claude/skills/boswell-hooks, behind a Windows junction.
Both were absence-of-evidence reported as evidence-of-absence, in a diagnosis
Steve was relying on. Neither is a reasoning failure the model can be reminded
out of — the shell genuinely returned nothing, and nothing looks like an answer.

WHY IT CARRIES DATA AND NOT A REMINDER:
Per STRUCTURAL-NOT-ASPIRATIONAL, a standing "be careful with 2>/dev/null" note
is wallpaper. This fires only on the exact commands where the ambiguity is real
and names the specific suppression it saw, so it is a fact about THIS command.

Deliberately narrow. It stays silent when:
  * the command produced any output at all (there is something to reason about);
  * nothing was suppressed (an empty result is then a real negative);
  * the command is a writer/mutator (silence is the expected success case for
    rm/mkdir/cp/git add — flagging those would be pure noise);
  * an explicit failure was already surfaced (a nonzero exit is not ambiguous).

Fail-open everywhere: any error returns None.
"""
import re

# Only for commands whose PURPOSE is to answer a question. A silent `rm` or
# `mkdir` is success, not ambiguity.
_QUERY_CMDS = (
    "find", "grep", "rg", "ls", "cat", "head", "tail", "which", "type",
    "select-string", "get-childitem", "gci", "dir", "test", "stat", "file",
    "awk", "sed", "wc", "diff", "git log", "git diff", "git status",
    "git ls-files", "git branch", "git remote", "git show",
)

# Shell-specific ways of throwing errors away.
_SUPPRESSORS = (
    "2>/dev/null",
    "2> /dev/null",
    "2>&-",
    "2>$null",
    "2> $null",
    "-erroraction silentlycontinue",
    "-ea silentlycontinue",
    "-erroraction ignore",
)


def _command(data):
    ti = (data or {}).get("tool_input")
    if isinstance(ti, dict):
        cmd = ti.get("command")
        if isinstance(cmd, str):
            return cmd
    return ""


def _output(data):
    """Best-effort stdout text across tool_response shapes."""
    tr = (data or {}).get("tool_response")
    if isinstance(tr, str):
        return tr
    if isinstance(tr, dict):
        parts = []
        for key in ("stdout", "output", "content", "stderr", "result"):
            val = tr.get(key)
            if isinstance(val, str):
                parts.append(val)
        return "\n".join(parts)
    return ""


def _failed(data):
    """An explicit failure is already unambiguous; leave it alone."""
    tr = (data or {}).get("tool_response")
    if isinstance(tr, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            val = tr.get(key)
            if isinstance(val, int) and val != 0:
                return True
        if tr.get("is_error") or tr.get("error"):
            return True
    return False


# A suppressor only counts as SUPPRESSION when the shell would read it as
# redirection. Measured 2026-08-06, two false positives:
#
#   grep "2>/dev/null" notes.txt        <- it is the SEARCH STRING
#   cat <<EOF ... 2>/dev/null ... EOF   <- it is heredoc DATA
#
# Both returned empty and both fired, telling the model its perfectly sound
# command was an unreliable unknown. A guard that cries wolf on ordinary greps
# is noise, and noise is how a guard stops being read at all — the exact failure
# this hook exists to avoid.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*")


def _shell_visible(cmd_low):
    """The part of the command the shell parses as syntax, not data.

    Quoted spans become spaces (preserving token boundaries), and everything
    from a heredoc introducer onward is dropped — real redirections appear
    before it, the body after it is payload.
    """
    visible = _QUOTED_RE.sub(" ", cmd_low)
    m = _HEREDOC_RE.search(visible)
    if m:
        visible = visible[:m.start()]
    return visible


def _suppressors_in(cmd_low):
    visible = _shell_visible(cmd_low)
    return [s for s in _SUPPRESSORS if s in visible]


def _is_query(cmd_low):
    # Check each pipeline/segment head, so `foo && find ... ` still counts.
    for segment in re.split(r"\|\||&&|\||;|\n", cmd_low):
        head = segment.strip().lstrip("(").strip()
        for name in _QUERY_CMDS:
            if head.startswith(name):
                return True
    return False


def evaluate(data):
    """Return a PostToolUse additionalContext payload, or None."""
    try:
        if (data or {}).get("tool_name") != "Bash":
            return None
        cmd = _command(data)
        if not cmd:
            return None
        cmd_low = cmd.lower()

        found = _suppressors_in(cmd_low)
        if not found:
            return None
        if not _is_query(cmd_low):
            return None
        if _failed(data):
            return None
        if _output(data).strip():
            return None

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "EMPTY RESULT FROM A SILENCED COMMAND — this command "
                    "discarded its own errors (" + ", ".join(found) + ") and "
                    "returned no output. That is an UNKNOWN, not a negative "
                    "result: a path that does not exist, a permission error, a "
                    "bad flag and a genuine no-match all look identical here. "
                    "Do not report this as 'nothing found' or 'not installed' "
                    "or 'does not exist'. Re-run it without the suppression, or "
                    "confirm with a different tool, before drawing a conclusion "
                    "from the silence."
                ),
            }
        }
    except Exception:
        return None  # FAIL-OPEN
