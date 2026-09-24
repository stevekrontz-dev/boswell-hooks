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

## Boswell-managed setup and updates

The public, signed release catalog is `https://v3.askboswell.com/init/catalog.json`.
An agent can call `boswell_init` to check local setup. The response is discovery
data, not permission to install. Existing user authorization and the host's
normal permission controls apply. Web-only clients do not need local hooks.

For the first installation, approve the publisher/repository identified by this
plugin's manifest, obtain its installer and `publisher.pub` from that repository,
and inspect the key fingerprint with `ssh-keygen -lf publisher.pub`. This is a
trust-on-first-use bootstrap: its authenticity depends on the approved repository
and account. Future catalogs are verified against the locally pinned key.

The installer requires Python 3.10+ and OpenSSH `ssh-keygen` with SSH signature
support. No Python packages or daemon are needed. From the approved checkout:

```text
python scripts/init_installer.py install --host claude-code --publisher-key publisher.pub
python scripts/init_installer.py status --host claude-code
python scripts/init_installer.py update --host claude-code
python scripts/init_installer.py rollback --host claude-code
```

Use `--host codex` for Codex. Use the same host profile for installation and later
updates. The installer verifies the signed catalog, artifact digest, host/Python
compatibility and archive paths, then registers a local verified marketplace
with the host plugin manager. It never runs instructions from package documents.
An expanded declared scope needs user approval before `--accept-scope`; declared
scope is a publisher statement, not an operating-system sandbox.

Credentials and compaction checkpoints remain outside versioned install trees.
The previous signed release is retained for explicit rollback. An interrupted
manager operation remains `recovery_needed`; rerun the same installation or
roll back. Verification failures must be resolved without disabling checks.

After installation, start a fresh host session (and use the host's normal hook
trust prompt where applicable). Status remains `installed_awaiting_activation`
until successful startup and retrieval run in the same fresh session. Lifecycle
records measure health, not tamper-proof attestation. Compaction readiness also
requires a successful checkpoint and restoration, tested during the pilot.
`current` means the manager registration and installed marker match, with
`startup_ok` and `retrieval_ok` measured in the same fresh session.
`compaction_ok` separately measures a checkpoint followed by restoration.

`inspection_unavailable` means status could not inspect the host plugin manager
or installed files. A sandbox, restricted permissions, unavailable executable,
timeout, or unexpected manager response can cause this; it does not establish
that the plugin needs repair. Rerun status from a normal host terminal with the
same `CODEX_HOME` or `CLAUDE_CONFIG_DIR` profile and normal permissions. Do not
bypass host controls. Updates stop before changing an existing installation when
inspection fails; `repair_needed` requires an observed registration or marker
mismatch. Previous health records do not turn an unavailable inspection into
verified readiness.

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

An invalid or unreadable tenant selector also fails closed. Each new session
pins the selected credential fingerprint and service origin; changing either
requires a fresh session. A session resumed from an older release without that
binding must also be replaced with a fresh session. This pins identity locally;
successful startup separately verifies that the server accepts the credential.

Transcript capture uses the event's exact session identity and transcript path.
Queued entries upload only with their original credential/origin binding.
Unbound legacy queues and entries from another credential remain quarantined;
do not relabel them with the current credential or upload them manually.
Key rotation also leaves old queued entries quarantined until explicitly
verified migration. Credentials and quarantined evidence are never packaged.

Version 2.4.1 introduces these tenant boundaries. Earlier releases lack them;
do not roll back to an earlier release on a machine serving multiple tenants.

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

If an agent is doing the install, hand it `AGENT-INSTALL.md` from the zip:
it carries the preflight, the unzip target, the verification commands, and
the rollback as a procedure.

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
