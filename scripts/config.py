"""Machine-local paths for the Claude Code hook adapter."""

import os
from pathlib import Path


_HOME = Path.home()

BOSWELL_TRANSCRIPTS_ARCHIVE = Path(os.environ.get(
    "BOSWELL_TRANSCRIPTS_ARCHIVE", str(_HOME / "boswell-transcripts")))

# Hook bookkeeping never lives inside the plugin directory or crosses machines.
STATE_ROOT = Path(os.environ.get(
    "BOSWELL_HOOK_STATE", str(_HOME / ".boswell" / "claude-hooks")))
