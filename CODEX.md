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
5. `PreCompact` spools a checkpoint; `PostCompact` validates the durable startup
   cache without reinjecting stale orientation into model context.
6. `Stop` spools the latest transcript and blocks once when changed files have
   no recorded test, lint, or build evidence.

Startup and retrieval fail closed. Transcript capture and telemetry fail open
into a machine-local queue. Set `BOSWELL_HOOKS_FAIL_OPEN=1` only for emergency
diagnosis.

## Authentication and tenant selection

Named tenant profiles live at `~/.boswell/tenants/<name>.key`. Set
`BOSWELL_TENANT=<name>` for an explicit session, or put the default profile name
in `~/.boswell/default_tenant`. A selected profile outranks
`BOSWELL_API_KEY`, preventing stale machine-wide environment variables from
silently crossing tenant boundaries. A missing explicit profile fails closed.

Machines without named profiles retain the portable `BOSWELL_API_KEY` then
`~/.boswell/hook_key` behavior. Steve's single-tenant machines can finally fall
back to `~/.boswell/.internal-secret`. Secrets are never stored in the plugin or
hook output.

State defaults to `~/.boswell/codex-hooks` and raw transcripts to
`~/boswell-transcripts/<machine>/<YYYY-MM>/`.
