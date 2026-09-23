"""Real shared formatter and session cache with synthetic transport boundaries."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_work_state_orientation import TENANT, WORKSPACE, startup_payload

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import codex_dispatcher as dispatcher
import session_state


class WorkStateLifecycleTests(unittest.TestCase):
    def test_real_cache_and_formatter_survive_clear_without_duplicate_startup(self):
        payload = startup_payload()
        original = copy.deepcopy(payload)
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(session_state, "STATE_ROOT", Path(temp)),
            mock.patch.object(dispatcher.boswell_client, "startup", return_value=payload) as startup,
            mock.patch.object(dispatcher.transcript_spool, "flush_pending", return_value=(0, 0)),
        ):
            first = dispatcher._session_start({"session_id": "example-session", "source": "startup"})
            text = first["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(json.loads(text.split("\n", 1)[1])["work_state"]["summary"]["unfinished_count"], 7)
            for source in ("resume", "startup"):
                self.assertIsNone(dispatcher._session_start({"session_id": "example-session", "source": source}))
            cleared = dispatcher._session_start({"session_id": "example-session", "source": "clear"})
            self.assertEqual(cleared["hookSpecificOutput"]["additionalContext"], text)
            self.assertEqual(session_state.load_startup_cache("example-session"), original)
            self.assertEqual(payload, original)
            self.assertEqual(startup.call_count, 1)

    def test_claude_uses_the_real_shared_work_state_formatter(self):
        spec = importlib.util.spec_from_file_location("work_state_claude_test", SCRIPTS / "dispatcher.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(session_state, "STATE_ROOT", Path(temp)),
            mock.patch.object(dispatcher.boswell_client, "startup", return_value=startup_payload()),
            mock.patch.object(dispatcher.transcript_spool, "flush_pending", return_value=(0, 0)),
            mock.patch("transcript_monitor.check_pending", return_value=None),
            mock.patch("hook_health.report", return_value=None),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            module._session_start({"session_id": "claude-example", "source": "startup"})
        receipt = json.loads(output.getvalue())
        projected = json.loads(receipt["hookSpecificOutput"]["additionalContext"].split("\n", 1)[1])
        self.assertEqual(projected["work_state"]["summary"]["unfinished_count"], 7)
        self.assertEqual(projected["work_state"]["unfinished_tasks"][1]["status"], "claimed")

    def test_generated_budget_unicode_and_count_invariants(self):
        # Reproducible generative coverage, no new runtime/test dependency.
        rng = random.Random(20260907)
        for index in range(160):
            total = rng.choice((0, 3, 7, 300, 2**40))
            payload = startup_payload(warm=bool(index % 2), total=total)
            raw = payload["work_state"]
            raw["projection"]["truncation"]["unfinished_tasks"] = total > len(raw["unfinished_tasks"])
            for task in raw["unfinished_tasks"]:
                task["title"] = rng.choice(("Example", "你好", "مرحبا", 'quote " and newline\n')) * rng.randrange(1, 8)
            payload["sacred_manifest"]["identity"] = "s" * rng.choice((20, 1000, 3500, 5500, 5700))
            original = copy.deepcopy(payload)
            with self.subTest(index=index, total=total):
                rendered = dispatcher._orientation(payload)
                self.assertLessEqual(len(rendered), dispatcher.ORIENTATION_MAX_CHARS)
                emitted = json.loads(rendered.split("\n", 1)[1])["work_state"]
                self.assertEqual(emitted["summary"]["unfinished_count"], total)
                self.assertEqual(emitted["summary"]["unfinished_returned"], len(emitted["unfinished_tasks"]))
                self.assertEqual(emitted["projection"]["scope"], {"tenant_id": TENANT, "workspace_id": WORKSPACE})
                if len(emitted["unfinished_tasks"]) < total:
                    self.assertTrue(emitted["projection"]["truncation"]["unfinished_tasks"])
                self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
