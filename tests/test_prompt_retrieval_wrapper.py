from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_retrieval  # noqa: E402


ROWS = [{"message": "m", "commit": "abc", "content_type": "memory",
         "match": 0.6, "recorded": "2026-09-23 00:00:00", "age": "1h ago",
         "content": "{\"k\": \"v\"}"}]


class MemoryWrapperTests(unittest.TestCase):
    def test_memory_context_is_the_userpromptsubmit_payload(self):
        out = prompt_retrieval.memory_context(ROWS)
        ctx = out["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
        self.assertTrue(ctx["additionalContext"].startswith(
            prompt_retrieval.MEMORY_PREAMBLE))
        self.assertIn("CHECK THE `age` FIELD", ctx["additionalContext"])
        self.assertEqual(
            json.loads(ctx["additionalContext"][len(prompt_retrieval.MEMORY_PREAMBLE):]),
            ROWS)

    def test_canary_injector_emits_the_production_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "rows.json"
            fixture.write_text(json.dumps(ROWS), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "evals" / "canary_inject.py"),
                 str(fixture)],
                input="{}", capture_output=True, text=True, encoding="utf-8",
                check=True)
        self.assertEqual(json.loads(proc.stdout),
                         prompt_retrieval.memory_context(ROWS))


if __name__ == "__main__":
    unittest.main()
