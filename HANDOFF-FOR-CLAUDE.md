# Handoff: installing boswell-hooks v2.1.2

**You are reading a note written by another Claude Code instance** — Steve Krontz's, on his work machine. It was packaged inside this zip so that whoever installs this doesn't have to reconstruct the reasoning.

Henry asked to run the same setup Steve runs. This is that setup, at the same commit Steve's four machines are running.

A word on how to treat this document: it is **evidence, not orders**. Verify the claims below against what you actually find on Henry's machine. If something here contradicts what you observe, believe your observation and tell Henry. Several statements in here are precisely the kind that go stale — versions, paths, prior install state.

---

## What you are installing

A Claude Code hooks plugin that sits on top of Boswell (the memory MCP Henry already uses heavily). One dispatcher, six events, every handler fail-open so a bug in it cannot break a session.

The important part, if you only read one thing: **it puts retrieved memory into the context window before the model reasons, rather than relying on the model to remember to look.**

---

## Preflight — check these before touching anything

1. **Is there an existing install?**
   ```bash
   ls -la ~/.claude/skills/boswell-hooks
   cat ~/.claude/skills/boswell-hooks/.claude-plugin/plugin.json
   ```
   Henry is *expected* to be on **v1.1.0** (June 2026), but nobody has verified that from this end — read the file, don't assume it. If it reports v2.x, the backup step below still applies and nothing else changes.

2. **Is his API key already in place?**
   ```bash
   ls -l ~/.boswell/hook_key
   ```
   If present, nothing needs redoing. If absent, he generates one at the Boswell dashboard → Connect → "Generate New API Key" (shown once), then saves it on a single line to `~/.boswell/hook_key`.

3. **Does he run any hooks of his own?**
   ```bash
   grep -A5 hooks ~/.claude/settings.json 2>/dev/null
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
#   expect: .claude-plugin/  hooks/  scripts/  tests/  INSTALL.md  HANDOFF-FOR-CLAUDE.md
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

Then the two self-contained test files, both of which run without pytest:
```bash
cd ~/.claude/skills/boswell-hooks
python3 tests/test_cross_platform.py    # expect 16/16
python3 tests/test_done_gate.py         # expect 13/13
python3 tests/test_deploy_memory.py     # expect 29/29
```

Report the actual numbers back — a partial pass on macOS is more useful to Steve than a clean one, because his fleet is three Windows boxes and one Mac mini.

---

## Tell Henry these five things

They change how his sessions behave, and he should hear them from you before he notices them himself.

**1. A gate can BLOCK the end of a turn — but rarely, and only on an unbacked completion claim.**
It fires only when all three hold: a file was edited *in that turn*, no verifying command ran after the edit, and the closing message *opens* by declaring the work done. Measured across 944 real turns of Steve's history it fires 6 times — 0.64%.

This is the headline fix in 2.1.1, and it is worth Henry knowing the history: the previous rule blocked the first stop after *any* file edit, on the theory that it was catching session close. It wasn't. Replayed against 39 real transcripts it landed on a session's actual final turn **0 times out of 23**, median 19 turns early, worst case turn 2 of a 208-turn session. If Henry ran 2.1.0 he would have been fighting it constantly.

**2. A gate can DENY a write to a path the project declares irreplaceable.**
Off by default and costs nothing unless a project opts in with a `.boswell-protect` file. It exists because an agent on Steve's Mac mini wrote a script to re-download 41 source files that had been hand-edited, and its own sanity check — a duration comparison — validated the mistake, because the edits were exactly what made the durations differ. Reading a protected file is always free; only writes are refused.

**3. A gate can BLOCK a Boswell commit.**
An explicit correction — one whose message opens with `CORRECTION:` / `SUPERSEDES` / `ERRATA:`, or whose content carries a `wrong_fact` / `supersedes` / `corrects` key — must follow an in-session Boswell read of the thing being corrected and carry a `symptom` field. Plain net-new commits are never gated. An earlier build over-triggered on ordinary words like "correct" and "actually"; that was found and fixed, but it is the failure mode to watch.

**4. A `git push` now asks Boswell about the remote before it runs.**
New in 2.1.2, and the one most likely to surprise him. It reads `.git/config` to turn the remote name into a host, searches Boswell for what's known about that target, and injects the result. It never blocks — worst case it's a few seconds and some context you ignore.

It exists because on 2026-08-07 Steve's instance pushed to a staging server that had been decommissioned two months earlier. Boswell held that fact twice. Nothing surfaced it, because prompt-time retrieval fires on the user's words and the user's words were "push it and deploy" — four tokens with nothing to match on. The remote name only ever existed in the tool call.

It also runs a **supersession filter**: if one retrieved record explicitly supersedes another by hash, the superseded one is withheld rather than shown. Raw relevance ranks a retired protocol *above* the record retiring it, because a superseded record is maximally relevant to the query it was superseded on. Withheld rows are named in the output so nothing vanishes silently.

**5. Every substantive prompt now runs a Boswell search.** Slightly more latency, more context in-window, more read traffic on his tenant. Session start also prints a health line if any handler has been failing — silence there means everything is running.

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
1. Either gate blocking something it should not have — the done-claim gate or the protected-path guard. Include the closing message or the command that triggered it.
2. A hook that goes silent, especially if the session-start health line names it.
3. Anything platform-specific. Verified on macOS (Darwin arm64, Python 3.9.6), Linux (3.12.3) and Windows (3.12) — but four machines is a small sample and three of them are Steve's.

Reply to Steve's email and he will loop his instance back in.

---

## Honest status

This release is **fresh**. Everything in it was built and measured over roughly 48 hours.

What is genuinely well-tested: the done-claim gate and the retrieval floor were both backtested against Steve's real transcript corpus rather than synthetic fixtures, and the numbers quoted above are from replaying the shipped code, not a stand-in. The deploy lane was backtested against the real Boswell corpus and found four defects in itself, two of which it caught by firing on the very commands that were building it.

What is thin: the protected-path guard has been exercised on exactly one project's paths, and by its own documentation it inspects command *strings* — a script that computes its output paths internally is invisible to it. The deploy lane has only ever been driven against one tenant's records; on a corpus with no deploy history it will simply retrieve nothing and stay quiet, which is the intended failure.

**If you installed v2.1.1, upgrade.** It shipped a real false positive: `_shell_visible` blanked quoted spans before detecting heredocs, so a `<<'EOF'` body was treated as executable command text. A commit message containing the words of a shell command could be read as that command. Fixed in 2.1.2 and regression-tested in both suites.

Henry is a beta tester and this is beta-grade. That is the deal; it should not be a surprise.

— Claude (Steve's Claude Code instance), 2026-08-07
