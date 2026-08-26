# Boswell Hooks lifecycle contract

The hook owns startup. A model does not.

## Session startup

On `SessionStart`, the adapter calls `/v2/startup` with `verbosity=warm`
exactly once for the client session, stores the raw response in a durable
machine-local cache, and injects a bounded orientation before the first model
response.

The injected receipt explicitly satisfies the startup requirement. Models must
not call `boswell_startup` again on later user messages. A cached `resume` is
silent, `clear` reinjects the cached orientation into the new context without
another network startup, and compaction validates the cache without replaying
the briefing.

If the hook receipt is absent because the plugin did not run, client-level
instructions may call `boswell_startup` once as a fallback. That fallback is
never a per-message ritual.

## Prompt-time retrieval

`UserPromptSubmit` is retrieval-only. It does not run startup.

The current precision-first gate:

- skips greetings, acknowledgements, short continuations, and duplicate prompts;
- searches only on substantive prompt text;
- excludes transcripts, credentials, tasks, skills, manifests, and low-value
  agent-only artifacts;
- admits at most two rows and abstains when confidence is weak.

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
