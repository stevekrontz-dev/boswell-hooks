"""Protected-path guard (boswell-hooks plugin).

PreToolUse handler. DENIES a write to a path the project has declared
irreplaceable, on BOTH the file-mutation tools and Bash.

WHY THIS EXISTS (M5 session a214e3fa, 2026-08-07, in that instance's own words):

    "The worst mistake I made tonight was writing a script to re-download 41
     source files. It would have replaced your *edited* cuts with full-length
     YouTube originals and desynced every lyric line in the library. Two files
     were already swapped before you said 'we edited the song lengths.' No hook
     stopped that. My own guard was a duration check — which was worthless,
     because the edits are exactly what makes durations differ."

That last sentence is the whole design brief. The agent's own sanity check was
derived from the same wrong assumption as the action, so it validated the
mistake. A guard that reasons about the file cannot help; only a guard that
knows the file is *declared* irreplaceable can.

WHY IT COVERS BASH, NOT JUST Edit/Write:
The destructive act there was a *script*, executed through Bash. A PreToolUse
guard on the mutation tools would not have seen it — which is precisely what
happened. git_guard already proves Bash is a workable seam for pure-string
denial, so this uses the same one.

WHY THE POLICY IS PROJECT-LOCAL:
"songs/*/source.mp4 is pinned to work/<name>/lines.json" is true of exactly one
project. Hard-coding it into a plugin that ships to other tenants would be
noise at best. Instead each project declares its own rules in a
`.boswell-protect` file, found by walking up from the target path. No file
means this handler is a no-op, so installs that never opt in pay nothing.

FORMAT (`.boswell-protect`, at the project root):

    # blank lines and #-comments ignored
    songs/*/source.mp4 :: lyric timings in work/<name>/lines.json are pinned to
                          this exact file; replacing it desyncs every line

Left of `::` is a glob matched against the path relative to the file's
directory. Right of `::` is the reason shown to the model when it is refused —
write it as the explanation you would want to read at 2am.

HONEST LIMITATION, stated here because a guard that oversells itself is worse
than none: this inspects the command STRING. A script that computes its output
paths internally (`python3 redownload.py`) is invisible at this seam. It
catches the inline case and the mutation-tool case. For genuinely irreplaceable
bytes, the durable backstop is filesystem permissions, not a hook.
"""
import fnmatch
import os
import re
from pathlib import Path

MUTATION_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CONFIG_NAME = ".boswell-protect"
# How far up to look for a policy file before giving up.
MAX_WALK_UP = 12
# Tokens in a Bash command that look like filesystem paths.
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_.~/\\-]{4,}")


def _find_config(start):
    """Nearest .boswell-protect at or above `start`. None when absent."""
    try:
        here = Path(start)
        if here.is_file() or here.suffix:
            here = here.parent
        for _ in range(MAX_WALK_UP):
            candidate = here / CONFIG_NAME
            if candidate.is_file():
                return candidate
            if here.parent == here:
                break
            here = here.parent
    except Exception:
        pass
    return None


def _load_rules(config_path):
    """[(glob, reason), ...] from a policy file. Malformed lines are skipped."""
    rules = []
    try:
        for raw in config_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            glob, _, reason = line.partition("::")
            glob = glob.strip()
            if glob:
                rules.append((glob, reason.strip() or "declared protected"))
    except Exception:
        return []
    return rules


def _match(path, config_dir, rules):
    """(glob, reason) for the first rule matching `path`, else None."""
    try:
        target = Path(path)
        try:
            rel = target.resolve().relative_to(Path(config_dir).resolve())
        except Exception:
            rel = target
        rel_posix = Path(rel).as_posix()
        name_only = Path(rel).name
        for glob, reason in rules:
            if fnmatch.fnmatch(rel_posix, glob) or fnmatch.fnmatch(name_only, glob):
                return glob, reason
    except Exception:
        pass
    return None


def _candidate_paths(data):
    """Paths this tool call might write, best-effort."""
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return []

    if tool in MUTATION_TOOLS:
        p = ti.get("file_path") or ti.get("path")
        return [p] if isinstance(p, str) and p else []

    if tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return []
        return _write_targets(cmd)

    return []


# Flags whose argument is an OUTPUT path, and flags whose argument is an INPUT.
_OUT_FLAGS = {"-o", "--output", "-of", "--out", "--output-file", "-O"}
_IN_FLAGS = {"-i", "--input", "-f", "--file"}
# Commands whose LAST positional argument is the destination.
_LAST_ARG_DEST = {"cp", "mv", "rsync", "install", "ln"}


def _write_targets(cmd):
    """Only the paths this command would WRITE.

    Position matters, and getting it wrong in the permissive direction is the
    cheaper failure. Measured 2026-08-07: a naive "protected path appears
    anywhere in a write-ish command" rule refused

        ffmpeg -i songs/<name>/source.mp4 -f wav work/<name>/audio.wav

    which merely READS the protected file — and is the project's normal daily
    workflow. A guard that blocks the routine path gets disabled within a day,
    and then protects nothing. Reading a protected file is always free.

    So a path counts only in a recognisable write position: after an output
    flag, after a redirect, as the destination of cp/mv/rsync, or piped to tee.
    Exotic forms are missed by design; see the module docstring on why the
    durable backstop for irreplaceable bytes is filesystem permissions.
    """
    targets = []
    try:
        # Redirections: > path, >> path
        targets += re.findall(r">>?\s*([A-Za-z0-9_.~/\\-]+)", cmd)
        # tee [flags] path...
        for m in re.finditer(r"\btee\b((?:\s+-\S+)*)((?:\s+[A-Za-z0-9_.~/\\-]+)*)", cmd):
            targets += m.group(2).split()

        tokens = cmd.split()
        for idx, tok in enumerate(tokens):
            if tok in _OUT_FLAGS and idx + 1 < len(tokens):
                targets.append(tokens[idx + 1])
            elif "=" in tok:
                flag, _, val = tok.partition("=")
                if flag in _OUT_FLAGS and val:
                    targets.append(val)

        # cp/mv/rsync/install SRC... DST  -> destination is the final argument
        for seg in re.split(r"\||&&|;|\n", cmd):
            parts = seg.split()
            if not parts:
                continue
            head = os.path.basename(parts[0])
            if head in _LAST_ARG_DEST:
                args = [p for p in parts[1:] if not p.startswith("-")]
                if len(args) >= 2:
                    targets.append(args[-1])
    except Exception:
        return []

    seen, out = set(), []
    for t in targets:
        t = t.strip("'\"")
        if t and t not in seen and ("/" in t or "." in t):
            seen.add(t)
            out.append(t)
    return out


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def evaluate(data):
    """Return a PreToolUse deny decision, or None. Fail-open, always."""
    try:
        candidates = _candidate_paths(data)
        if not candidates:
            return None

        cwd = data.get("cwd") or os.getcwd()
        for raw in candidates:
            # Resolve relative tokens against the session cwd so a bare
            # "songs/x/source.mp4" in a Bash command is still recognised.
            path = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
            config = _find_config(path)
            if config is None:
                continue
            rules = _load_rules(config)
            if not rules:
                continue
            hit = _match(path, config.parent, rules)
            if not hit:
                continue
            glob, reason = hit
            return _deny(
                "Protected path refused: " + Path(raw).as_posix() + "\n\n"
                + reason + "\n\n"
                "This path is declared protected in " + config.as_posix()
                + " (rule: " + glob + "). It is protected because it cannot be "
                "regenerated from anything else in the repo — re-deriving it "
                "produces a DIFFERENT file that other artifacts are still "
                "pinned to.\n\n"
                "Do not work around this by renaming, writing to a temp path "
                "and moving it, or asking a script to do it. If replacing it is "
                "genuinely correct, say so to Steve and let him unblock it, and "
                "regenerate whatever was pinned to it in the same breath."
            )
        return None
    except Exception:
        return None  # FAIL-OPEN: never block real work on a bug in this file
