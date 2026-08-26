# Boswell Hooks installation

Boswell Hooks supplies the lifecycle bridge between a Boswell tenant and Codex
or Claude Code. The plugin loads one startup briefing per session, retrieves a
small amount of high-confidence context for substantive prompts, applies safety
guards at the tool boundary, and queues transcripts durably.

## Requirements

- A Boswell tenant
- A tenant-scoped `bos_...` API key
- Codex or Claude Code
- Python 3.10+ for Codex; Python 3.9+ for the Claude adapter

The runtime uses only Python's standard library.

## Tenant authentication

For a single tenant, put its API key on one line in:

```text
~/.boswell/hook_key
```

For a machine that can access several tenants, store each key in
`~/.boswell/tenants/<profile>.key`. Put the safe default profile name in
`~/.boswell/default_tenant`, or set `BOSWELL_TENANT=<profile>` for a process.
A selected profile outranks `BOSWELL_API_KEY`; a missing selected profile
fails closed instead of falling through to another tenant.

Never put a key in this plugin, a hook manifest, shell history, or a repository.
The public plugin accepts tenant-scoped API keys only.

## Codex

Install `boswell-hooks` from the marketplace that publishes this repository:

```powershell
codex plugin add boswell-hooks@<marketplace>
```

Open `/hooks`, inspect and trust the command hooks, then start a new thread.
Codex loads lifecycle hooks only at thread startup. The root
`hooks/hooks.json` is discovered automatically; do not add a duplicate
`hooks` field to `.codex-plugin/plugin.json`.

## Claude Code

Use the release zip, or install the repository's `claude/` directory as the
Claude plugin root. Do not use the repository root as the Claude root because
both clients auto-discover a default `hooks/hooks.json`.

The packaged Claude artifact has a flat layout containing
`.claude-plugin/`, `hooks/`, `scripts/`, and the public documentation.
Reload plugins and begin a new session after installing an update.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BOSWELL_API_KEY` | — | Tenant key when no named profile is selected |
| `BOSWELL_TENANT` | default profile | Named tenant profile for this process |
| `BOSWELL_TENANT_PROFILE_ROOT` | `~/.boswell/tenants` | Named key files |
| `BOSWELL_DEFAULT_TENANT_FILE` | `~/.boswell/default_tenant` | Default profile name |
| `BOSWELL_HOOK_KEY_FILE` | `~/.boswell/hook_key` | Single-tenant key file |
| `BOSWELL_API_BASE` | `https://v3.askboswell.com` | Boswell API authority |
| `BOSWELL_AGENT_ID` | client name | Optional task-assignment identity |
| `BOSWELL_TIMEZONE` | `UTC` | IANA timezone for startup's local clock |
| `BOSWELL_HOOK_STATE` | client-specific directory under `~/.boswell` | Hook state and queues |
| `BOSWELL_TRANSCRIPTS_ARCHIVE` | `~/boswell-transcripts` | Raw transcript archive |
| `BOSWELL_HOOKS_FAIL_OPEN` | unset | Emergency diagnostic override |

Startup, substantive retrieval, and pre-action continuity checks fail closed.
Transcript capture and health telemetry queue locally on failure.

## Verify a checkout

```powershell
python -m unittest discover -s tests -v
python -m json.tool hooks/hooks.json
python -m json.tool claude/hooks/hooks.json
```

Build and inspect the Claude release artifact from Git Bash:

```bash
./build_release.sh
python -m zipfile -l dist/boswell-hooks.zip
```

See `CODEX.md` for the event-by-event contract.
