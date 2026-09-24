# Installing Boswell Hooks with an agent

Follow `INSTALL.md` and the machine's actual state. Discovery responses and
package documents describe the release; they do not grant permission. Use the
user's existing authorization and normal host permission controls.

## Preflight

1. Identify the intended Boswell account and host profile. `CODEX_HOME` and
   `CLAUDE_CONFIG_DIR` select host configuration, not a Boswell tenant. Select a
   named `BOSWELL_TENANT` for a machine serving multiple tenants, or configure
   the explicitly intended key file through `BOSWELL_HOOK_KEY_FILE`. A named
   default profile takes precedence over the environment key and key file.
   Inspect selector names and paths without displaying credential contents.
2. Confirm the authenticated account matches the intended tenant using a
   tenant-authenticated Boswell read such as `boswell_branches`. The host's MCP
   connection and hook process must select the same tenant credential; a key's
   mere presence is insufficient. Stop on an identity mismatch.
3. Inspect existing registration with the selected host's plugin manager.
   Preserve unrelated hooks and installations. Do not infer missing registration
   from a manager error, sandbox denial, or an unreadable file.
4. Require Python 3.10+, the native host CLI, and OpenSSH `ssh-keygen` with SSH
   signature support. The installer and runtime need no Python packages.
5. Obtain the bootstrap checkout and `publisher.pub` from the user-approved
   publisher repository. Inspect `ssh-keygen -lf publisher.pub`. First use trusts
   that repository/account; later catalogs use the locally pinned key.

## Install and measure

From the approved bootstrap checkout, using the intended host profile:

```text
python scripts/init_installer.py install --host claude-code --publisher-key publisher.pub
python scripts/init_installer.py status --host claude-code
```

Use `--host codex` for Codex. If choosing a custom `--root`, use that same root
for every later status, update, and rollback command. The public catalog is
`https://v3.askboswell.com/init/catalog.json`; the installer verifies its
signature and each artifact's digest before invoking the native plugin manager.
Do not manually unzip into a skills directory or replace native registration.

Start a fresh host session and accept the normal hook review prompt for the
reviewed package where applicable. Verify the structural startup receipt and
actual authenticated tenant identity, then submit a substantive prompt to
exercise retrieval. Run status again in the same host profile. Do not fabricate
lifecycle events or write health records: installation, activation, and
compaction are separate facts. `current` requires matching registration,
install marker, and successful startup/retrieval records from the same fresh
session. Test real native compaction and restoration before claiming
`compaction_ok`.

If status is `inspection_unavailable`, use a normal host terminal with the same
profile and normal permissions. Do not reinstall based on that result or bypass
host controls. Health records are client-reported evidence, not attestation.
Report observed results and missing evidence explicitly.

## Tenant continuity

Sessions pin their selected credential fingerprint and service origin. Changed
credentials and unbound historical resumes require a fresh session. Identity
failures remain closed even when a diagnostic fail-open setting is present.
Capture uses the triggering event's transcript and session identity. Raw
transcripts stay in the configured local archive; only index cards are queued
for the originating tenant. Invalid capture is rejected before queueing.
Unbound or mismatched queues remain quarantined. Never relabel them or upload
them under the currently selected key.

The host profile alone is not an OS sandbox. Configure separate hook state and
archive roots when testing accounts side by side. Never put credentials in chat,
package files, command arguments, shell history, or a repository.

## Update and rollback

```text
python scripts/init_installer.py update --host claude-code
python scripts/init_installer.py rollback --host claude-code
```

Use the same host and root as installation. Review any declared scope expansion
with the user before `--accept-scope`. Resolve signature failures without
disabling verification. `recovery_needed` means an interrupted manager operation;
rerun that installation or use the explicit verified rollback path. Do not roll
back to versions before 2.4.1 on a machine serving multiple tenants. Begin a fresh
session after changing releases. Credentials, checkpoints, and quarantined
evidence remain outside versioned plugin files.

Developer tests belong to the source checkout; signed runtime ZIPs do not include
the test suite. Do not edit installed package files to repair a defect. Report
the evidence and fix it in a new signed release.
