# boswell-hooks — Install Guide

The Boswell **Base Workflow** for Claude Code: automatic session recording,
transcript capture that commits itself to your Boswell tenant, a session-close
"Tested-and-Complete" gate, and a git-push safety guard. Portable across
machines and tenants — the only per-machine thing is your API key.

## Prerequisites
- Claude Code installed.
- A Boswell account/tenant (sign up at the Boswell dashboard).
- Python 3 on PATH (the hooks are Python; `requests` is the only third-party dep).

## 1. Install the plugin
Place the `boswell-hooks/` directory under your Claude Code skills dir:

```
~/.claude/skills/boswell-hooks/
  .claude-plugin/plugin.json
  hooks/hooks.json
  scripts/...
  INSTALL.md
```

(Steve's machines sync this via the `~/.claude/skills` rail; a new tenant just
copies the directory in.) Then in Claude Code run `/reload-plugins` — you should
see `boswell-hooks` among the loaded plugins and its hooks counted.

## 2. Get your tenant-scoped API key (`bos_…`)
The hook commits to Boswell with **your** key, so commits land in **your**
tenant. Two ways to get one:

- **At signup** — `POST /v2/onboard/provision` returns `api_key` in its response.
- **Anytime** — Boswell dashboard → **Connect** → **Generate New API Key**
  (copy it immediately; it is shown once).

This is a standard tenant API key (`bos_…`); it is NOT the internal/admin secret.

## 3. Place the key
Write the key as a single line to a machine-local file (never synced, never
committed):

```
~/.boswell/hook_key
```

Example (PowerShell):
```powershell
Set-Content -Path "$HOME\.boswell\hook_key" -Value "bos_your_key_here" -NoNewline
```

Alternatively set the `BOSWELL_API_KEY` environment variable — it takes
precedence over the file.

## 4. Verify
```
python ~/.claude/skills/boswell-hooks/scripts/config.py
#   → BOSWELL_API_BASE = https://…  and  hook_api_key() present = True

python ~/.claude/skills/boswell-hooks/scripts/transcript_monitor.py flush
#   → flush: committed=N remaining=0   (0 remaining = all queued transcripts committed)
```
If `remaining > 0`, commits are failing — check the key and `BOSWELL_API_BASE`.
**Nothing is ever lost on failure:** entries are retained and a fallback marker
asks an authenticated Claude to commit them via MCP.

## Configuration (all optional, env-overridable)
| Variable | Default | Purpose |
|----------|---------|---------|
| `BOSWELL_API_KEY` | — | API key (overrides the key file) |
| `BOSWELL_HOOK_KEY_FILE` | `~/.boswell/hook_key` | key file location |
| `BOSWELL_API_BASE` | Boswell v3 Railway URL | point at your own deployment |
| `BOSWELL_HOOK_STATE` | `~/.claude/hooks/state` | hook state/queue dir |
| `BOSWELL_TRANSCRIPTS_ARCHIVE` | `~/boswell-transcripts` | raw transcript archive |
| `CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | where Claude Code session JSONLs live |

## What each hook does
- **SessionStart** — prints the Boswell banner; drains any pending transcripts to
  `/v2/commit` in Python (no LLM needed).
- **PreToolUse (Bash)** — blocks `git push --force` to deploy remotes
  (`production`/`staging`); asks before any bare `--force`. Fail-open.
- **PostToolUse** — logs tool activity; every ~30 min re-captures a growing session.
- **Stop** — the session-close Tested-and-Complete gate.
- **SessionEnd** — boswell end/sync + capture this session + flush to Boswell.

All state is machine-local under `~`; the synced plugin dir holds no secrets and
no per-machine state.
