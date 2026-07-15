# boswell-hooks — Install Guide

Boswell's base workflow for Codex and Claude Code: structural startup context,
prompt-time retrieval, read-before-corrective-write governance, transcript
capture, compaction recovery, completion verification, and git-push safety.

## Prerequisites

- Codex or Claude Code
- Python 3.10+
- A Boswell tenant and tenant-scoped `bos_...` API key

The Codex adapter uses only Python's standard library. The legacy Claude
transcript path still uses `requests`.

## Authentication

Put the tenant API key on one line in this machine-local file:

```text
~/.boswell/hook_key
```

`BOSWELL_API_KEY` overrides the file. Steve's single-tenant fleet may instead
use `~/.boswell/.internal-secret`; that fallback is not portable to tenants.

Never place credentials inside the plugin, `hooks.json`, or a repository.

## Codex installation

Personal development installs live at `~/plugins/boswell-hooks` and are exposed
by `~/.agents/plugins/marketplace.json`.

```powershell
codex plugin add boswell-hooks@personal
```

Open `/hooks`, inspect the exact command-hook definitions, and trust them. Hook
trust is hash-bound, so changed definitions require review again. Start a new
thread after installation or update; lifecycle hooks are loaded at thread
startup.

The plugin automatically discovers `hooks/hooks.json`. Do not add a `hooks`
field to `.codex-plugin/plugin.json`.

## Claude Code installation

Install the repository's `claude/` subdirectory as the Claude plugin root. Do
not install the repository root into Claude Code: both Claude and Codex
auto-discover `hooks/hooks.json`, and the root file is Codex-specific.

For a personal checkout at `~/plugins/boswell-hooks`, point the Claude skills
entry at `~/plugins/boswell-hooks/claude`, then reload plugins. A tiny shim
inside that isolated runtime root resolves the checkout and dispatches into the
shared scripts.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BOSWELL_API_KEY` | — | Tenant key; overrides files |
| `BOSWELL_HOOK_KEY_FILE` | `~/.boswell/hook_key` | Tenant-key file |
| `BOSWELL_INTERNAL_SECRET_FILE` | `~/.boswell/.internal-secret` | Steve-only fallback |
| `BOSWELL_API_BASE` | Production Railway API | Boswell deployment |
| `BOSWELL_AGENT_ID` | `Codex-Root` | Agent-specific startup tasks |
| `BOSWELL_HOOK_STATE` | `~/.boswell/codex-hooks` | State and outbound queue |
| `BOSWELL_TRANSCRIPTS_ARCHIVE` | `~/boswell-transcripts` | Raw transcript archive |
| `BOSWELL_HOOKS_FAIL_OPEN` | unset | Emergency diagnostic override |

Startup and retrieval fail closed by default. Transcript capture and telemetry
remain queued locally on failure. See `CODEX.md` for the lifecycle contract.

## Verification

```powershell
python -m unittest discover -s tests -v
python -m json.tool hooks/hooks.json
python -m json.tool claude/hooks/hooks.json
python scripts/codex_dispatcher.py SessionStart
```

The dispatcher expects hook JSON on stdin; the final command without stdin is
only a failure-policy probe. Normal invocation is owned by Codex.
