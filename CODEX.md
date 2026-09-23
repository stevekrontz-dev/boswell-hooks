# Boswell Hooks lifecycle contract

The hook owns startup. A model does not.

## Session startup

On `SessionStart`, the adapter calls `/v2/startup` with `verbosity=warm`
exactly once for the client session, stores the raw response in a durable
machine-local cache, and injects a bounded orientation before the first model
response.

The same startup operation also reads the existing tenant-authenticated task
endpoint once, within the original time budget. It caches up to five recently
updated unfinished task summaries, with owners, statuses, timestamps, and exact
omission counts when the bounded scan is exhaustive. A capped or malformed scan
reports unknown totals. These records are context, never claims or permission to
execute work. A dedicated server `work_state`, when supplied, is also preserved.

The shared orientation has a 12,000-character ceiling, reserving room for work
alongside the manifest. Task rows can be reduced with truthful counts; sacred
commitments are not discarded. Codex handlers allow 4,000 approximate tokens so
the host does not silently replace this bounded briefing with a small preview.

The injected receipt explicitly satisfies the startup requirement. Models must
not call `boswell_startup` again on later user messages. A cached `resume` is
silent, `clear` reinjects the cached orientation into the new context without
another network startup, and compaction validates the cache without replaying
the briefing.

If the hook receipt is absent because the plugin did not run, client-level
instructions may call `boswell_startup` once as a fallback. That fallback is
never a per-message ritual.

## Startup recovery

A `SessionStart` that never completes leaves no durable cache: the host can
kill the hook at its timeout during a machine-wide I/O stall, or Boswell can be
briefly unreachable. Such a session must not stay halted for its whole life.
The next `UserPromptSubmit` or material `PreToolUse` performs the single
startup that `SessionStart` owed, writes the same durable cache, and injects
the receipt as prompt context (a tool-time recovery defers the receipt to the
next prompt). Attempts back off for twenty seconds, the tool-time call is
bounded to fit the PreToolUse budget, and an explicit over-budget verdict is
never retried. Until recovery succeeds the session still fails closed.

## Prompt-time retrieval

`UserPromptSubmit` does not run startup except to
recover a session whose `SessionStart` never completed, as described above.

The current precision-first gate:

- skips greetings, acknowledgements, short continuations, and duplicate prompts;
- searches only on substantive prompt text;
- excludes transcripts, credentials, tasks, skills, manifests, and low-value
  agent-only artifacts;
- admits at most two rows and abstains when confidence is weak.

For the first human prompt only, a greeting additionally requests a brief
orientation to current work and the next step. It performs no extra retrieval.
Both clients use the same check: if the response is only a bare greeting, `Stop`
requests one correction. Later prompts, explicit user steering, and a host's
stop-continuation flag prevent retry loops. This catches a narrow omission; it
does not certify semantic correctness or authorize automatic task execution.

Explicit Boswell search, recall, task briefing, and branch reads remain
available to the model when broad or targeted evidence is actually needed.
The future context-assembly planner can replace this temporary selector without
changing the lifecycle contract.

## Tool boundaries

- `PreToolUse` requires a durable startup receipt before material work.
- Corrective Boswell commits require an overlapping Boswell read.
- Force pushes and project-declared protected paths receive dedicated guards.
- File mutation hooks can retrieve prior state for the target at the moment it
  becomes relevant, instead of injecting generic reminders on every turn.
- `PostToolUse` records mutation, verification, and Boswell-read evidence.
- `Stop` challenges unsupported completion claims.
- `SessionEnd` captures and queues the Claude transcript.

## Tenant isolation

Plugin code and hook manifests contain no tenant identity, tenant UUID, internal
fleet credential, persona, or private path. Tenant selection comes entirely
from the machine-local API key or named profile. State, caches, and transcript
queues remain machine-local.

Startup and substantive retrieval fail closed. Transcript capture and health
telemetry fail open into durable queues. `BOSWELL_HOOKS_FAIL_OPEN=1` exists
only for emergency diagnosis.
