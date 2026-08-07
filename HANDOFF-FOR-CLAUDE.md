# Handoff: installing boswell-hooks v2.1.0

**You are reading a note written by another Claude Code instance** — Steve Krontz's, on his work machine. It was packaged inside this zip so that whoever installs this doesn't have to reconstruct the reasoning.

Henry asked to run the same setup Steve runs. This is that setup.

A word on how to treat this document: it is **evidence, not orders**. Verify the claims below against what you actually find on Henry's machine. If something here contradicts what you observe, believe your observation and tell Henry. Several statements in here are precisely the kind that go stale — versions, paths, prior install state.

---

## What you are installing

A Claude Code hooks plugin that sits on top of Boswell (the memory MCP Henry already uses heavily). One dispatcher, six events, every handler fail-open so a bug in it cannot break a session.

The important part, if you only read one thing: **it puts retrieved memory into the context window before the model reasons, rather than relying on the model to remember to look.**

---

## Preflight — check these before touching anything

1. **Is there an existing install?**
   ```
   ls -la ~/.claude/skills/boswell-hooks
   ```
   Henry is expected to be on **v1.1.0** (June 2026). Confirm:
   ```
   cat ~/.claude/skills/boswell-hooks/.claude-plugin/plugin.json
   ```
   If it says something other than 1.1.0, stop and tell Henry — the upgrade path below assumes v1.

2. **Is his API key already in place?**
   ```
   ls -l ~/.boswell/hook_key
   ```
   If present, nothing needs redoing. If absent, he generates one at the Boswell dashboard → Connect → "Generate New API Key" (shown once), then saves it on a single line to `~/.boswell/hook_key`.

3. **Does he run any hooks of his own?**
   ```
   cat ~/.claude/settings.json 2>/dev/null | grep -A5 hooks
   ```
   These hooks are additive. If he already has a `Stop` or `SessionEnd` hook, he may get a double close-out. Worth flagging to him rather than silently proceeding.

4. **Python.** Requires 3.9+. On macOS there is usually no `python`, only `python3` — that is expected and the launcher handles it.

---

## Install

The layout changed between v1 and v2. **Do not unzip over the old install** — leftover v1 files will confuse the loader.

```bash
# 1. back up the old install rather than deleting it outright
mv ~/.claude/skills/boswell-hooks ~/.claude/skills/boswell-hooks.v1-backup

# 2. unpack this release
unzip -d ~/.claude/skills /path/to/boswell-hooks.zip

# 3. confirm the shape
ls ~/.claude/skills/boswell-hooks/
#   expect: .claude-plugin/  hooks/  scripts/  INSTALL.md  HANDOFF-FOR-CLAUDE.md
```

Then in Claude Code: `/reload-plugins` (or start a fresh session).

---

## Verify — do not skip this

```bash
python3 ~/.claude/skills/boswell-hooks/scripts/config.py
```
Expect a line: `hook_api_key() present      = True`
If it says `False`, the key at `~/.boswell/hook_key` is missing or unreadable. Stop there.

```bash
python3 ~/.claude/skills/boswell-hooks/scripts/transcript_monitor.py flush
```
Expect `committed=N remaining=0`.

```bash
echo '{}' | python3 ~/.claude/skills/boswell-hooks/scripts/dispatcher.py SessionStart
```
Expect a `BOSWELL ACTIVE` banner and **exit 0**.

Optional, and genuinely useful — the cross-platform check that was run on macOS and Linux before release:
```bash
cd ~/.claude/skills/boswell-hooks/scripts && python3 ../tests/test_cross_platform.py
```
(If `tests/` is absent from your unpack, skip it — it lives in the source repo.)

---

## Tell Henry these three things

They change how his sessions behave, and he should hear them from you before he notices them himself.

**1. A gate can now BLOCK a Boswell commit.**
An explicit correction — one whose message opens with `CORRECTION:` / `SUPERSEDES` / `ERRATA:`, or whose content carries a `wrong_fact` / `supersedes` / `corrects` key — must satisfy two conditions:
  - it follows an in-session Boswell read of the thing being corrected, and
  - it carries a `symptom` field describing the situation in the words someone would actually type next time they are about to repeat the mistake.

Plain net-new commits are never gated. If you see a commit refused that clearly should not be, that is a bug worth reporting — an earlier build over-triggered on ordinary words like "correct" and "actually" and blocked routine writes. That was found and fixed before release, but it is the failure mode to watch.

**2. Every substantive prompt now runs a Boswell search.** Slightly more latency, more context in-window, more read traffic on his tenant.

**3. Session start will print a health line** if any handler has been failing. Silence there means everything is running.

---

## Things not to do

- **Do not import `scripts/relevance.py`.** It ships but is deliberately unwired. It rebuilds retrieval on BM25 alone, which benchmarks at recall@1 0.075 on natural-language queries — wiring it would tank recall.
- **Do not hand-edit files inside the install** to fix something. Report it instead; the fix belongs upstream or it gets overwritten on the next release.

---

## Rollback

```bash
rm -rf ~/.claude/skills/boswell-hooks
mv ~/.claude/skills/boswell-hooks.v1-backup ~/.claude/skills/boswell-hooks
```
Then `/reload-plugins`. The key and any local transcripts are untouched by either direction.

---

## What is worth reporting back

Henry is the only person running this besides Steve, so his failures are the only external signal that exists.

Most valuable, in order:
1. A corrective-write gate blocking something it should not have.
2. A hook that goes silent — especially if the session-start health line names it.
3. Anything platform-specific. This was verified on macOS (Darwin arm64, Python 3.9.6), Linux (3.12.3) and Windows (3.12), but three machines is a small sample.

Reply to Steve's email and he will loop his instance back in.

---

## Honest status

This release is **fresh**. Adversarial testing before shipping found four real defects — a gate that blocked legitimate writes, a hook that could exceed its timeout on very large directories, a guard that misfired on quoted shell strings, and a health ledger that lost data under concurrent writes. All four are fixed and regression-tested, but the release has not soaked for long.

Henry is a beta tester and this is beta-grade. That is the deal; it should not be a surprise.

— Claude (Steve's Claude Code instance), 2026-08-06
