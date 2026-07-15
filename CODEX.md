# Codex lifecycle

The Codex plugin is discovered through `.codex-plugin/plugin.json` and the
default `hooks/hooks.json`. Claude Code is installed from the isolated
`claude/` runtime root, whose own default `hooks/hooks.json` points back to the
shared scripts. The event catalogs must live under distinct plugin roots because
both runtimes auto-discover the same default hook filename; Codex has compaction
and subagent hooks but no `SessionEnd` event.

## Closed loop

1. `SessionStart` calls `/v2/startup` once per `session_id`, caches the raw
   response, and injects the governed projection before the first response.
2. `UserPromptSubmit` skips greetings and short follow-ups, then applies a
   temporary precision-first gate to hybrid retrieval: only strong semantic
   matches are eligible, noisy content classes are excluded, and at most two
   memories are injected. Explicit Boswell search remains the broad-recall path.
3. `PreToolUse` blocks material work without startup, blocks unsafe force pushes,
   and requires a matching read before corrective Boswell commits.
4. `PostToolUse` maintains mutation, verification, and Boswell-read ledgers.
5. `PreCompact` spools a checkpoint; `PostCompact` restores cached orientation
   without calling startup again.
6. `Stop` spools the latest transcript and blocks once when changed files have
   no recorded test, lint, or build evidence.

Startup and retrieval fail closed. Transcript capture and telemetry fail open
into a machine-local queue. Set `BOSWELL_HOOKS_FAIL_OPEN=1` only for emergency
diagnosis.

## Authentication

The bridge prefers `BOSWELL_API_KEY` or `~/.boswell/hook_key` (`X-API-Key`).
Steve's single-tenant machines can fall back to `~/.boswell/.internal-secret`.
Secrets are never stored in the plugin or hook output.

State defaults to `~/.boswell/codex-hooks` and raw transcripts to
`~/boswell-transcripts/<machine>/<YYYY-MM>/`.
