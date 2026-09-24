#!/usr/bin/env python3
"""UserPromptSubmit hook used ONLY by the hook-trust canary eval.

Emits fixture rows through the production memory wrapper
(prompt_retrieval.memory_context), so the model sees exactly what a real
retrieval would show it. The eval registers this via `claude --settings`, next
to the real boswell-hooks plugin; it never touches Boswell itself.

Usage (as a hook command): python canary_inject.py <fixture.json>
"""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prompt_retrieval  # noqa: E402


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sys.stdout.write(json.dumps(prompt_retrieval.memory_context(rows)))


if __name__ == "__main__":
    main()
