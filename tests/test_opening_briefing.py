"""Opening orientation must use current work without claiming authority or looping."""
import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import boswell_client
import codex_dispatcher as codex
import session_state


def payload():
    return {"sacred_manifest": {"identity": "Example", "active_commitments": []},
            "continuity": {}, "startup_integrity": {"status": "ok"}}


def tasks():
    return {"count": 4, "tasks": [
        {"id": "old", "title": "Old parity soak", "status": "open", "assigned_to": "Codex",
         "branch": "infra", "created_at": "2026-04-01", "updated_at": "2026-04-01"},
        {"id": "new", "title": "Manifest protection", "status": "open", "assigned_to": "Codex",
         "branch": "memory", "created_at": "2026-09-23", "updated_at": "2026-09-23"},
        {"id": "busy", "title": "Trust canary", "status": "claimed", "assigned_to": "CC",
         "branch": "memory", "created_at": "2026-09-22", "updated_at": "2026-09-22"},
        {"id": "done", "title": "Finished item", "status": "done", "updated_at": "2026-09-24"}]}


class StartupBriefingTests(unittest.TestCase):
    def test_startup_reads_work_once_and_keeps_latest_owner_and_status(self):
        original = copy.deepcopy(tasks())
        def request(method, path, **kwargs):
            return payload() if path == "/v2/startup" else copy.deepcopy(original)
        with mock.patch.object(boswell_client, "_request", side_effect=request) as transport:
            result = boswell_client.startup()
        self.assertIn("work_briefing", result)
        brief = result["work_briefing"]
        self.assertEqual(brief["unfinished_count"], 3)
        self.assertEqual(brief["tasks"][0]["id"], "new")
        self.assertEqual(brief["tasks"][1]["assigned_to"], "CC")
        self.assertEqual(brief["tasks"][1]["status"], "claimed")
        self.assertEqual([c.args[1] for c in transport.call_args_list], ["/v2/startup", "/v2/tasks"])
        self.assertNotIn("can_start", brief["tasks"][0])
        self.assertEqual(original, tasks())

    def test_task_transport_failure_does_not_certify_complete_startup(self):
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), boswell_client.BoswellUnavailable("offline")]):
            with self.assertRaises(boswell_client.BoswellUnavailable):
                boswell_client.startup()

    def test_capped_result_reports_unknown_total_and_lower_bound(self):
        rows = [{"id": str(i), "title": "Item", "status": "open"} for i in range(1000)]
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), {"count": 1000, "tasks": rows}]):
            brief = boswell_client.startup()["work_briefing"]
        self.assertIsNone(brief["unfinished_count"])
        self.assertEqual(brief["unfinished_seen"], 1000)
        self.assertTrue(brief["truncated"])
        self.assertLessEqual(len(brief["tasks"]), 5)

    def test_missing_tasks_are_unknown_not_empty(self):
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), {}]):
            brief = boswell_client.startup()["work_briefing"]
        self.assertIsNone(brief["unfinished_count"])
        self.assertEqual(brief["status"], "unavailable")

    def test_malformed_status_never_crashes_startup(self):
        for status in ([], {}, None, 4):
            with self.subTest(status=status):
                broken = {"count": 1, "tasks": [{"id": "bad", "status": status}]}
                with mock.patch.object(boswell_client, "_request", side_effect=[payload(), broken]):
                    brief = boswell_client.startup()["work_briefing"]
                self.assertEqual(brief["status"], "unavailable")
                self.assertIsNone(brief["unfinished_count"])

    def test_long_work_rows_reduce_honestly_without_discarding_manifest(self):
        rows = [{"id": str(i), "title": "t" * 180, "branch": "b" * 100,
                 "assigned_to": "a" * 100, "status": "open", "updated_at": "2026-09-23"}
                for i in range(5)]
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), {"count": 5, "tasks": rows}]):
            result = boswell_client.startup()
        result["sacred_manifest"]["identity"] = "s" * 8500
        projected = json.loads(codex._orientation(result).split("\n", 1)[1])
        brief = projected["work_briefing"]
        self.assertEqual(projected["sacred_manifest"], result["sacred_manifest"])
        self.assertLess(brief["returned"], 5)
        self.assertEqual(brief["omitted_count"], 5 - len(brief["tasks"]))
        self.assertTrue(brief["truncated"])

    def test_host_context_limit_covers_bounded_orientation(self):
        if not (ROOT / ".codex-plugin").is_dir():
            self.skipTest("Flat Claude package has no Codex hook catalog")
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
            self.assertGreaterEqual(hooks[event][0]["hooks"][0].get("additionalContextLimit", 2500), 4000)
        self.assertNotIn("additionalContextLimit", hooks["PostCompact"][0]["hooks"][0])

    def test_claude_budget_preserves_manifest_and_current_task(self):
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), tasks()]):
            result = boswell_client.startup()
        result["sacred_manifest"]["identity"] = "s" * 6000
        result["recent_thread"] = [{"message": "old" * 1000}] * 8
        rendered = codex._orientation(result, max_chars=9000)
        projected = json.loads(rendered.split("\n", 1)[1])
        self.assertLessEqual(len(rendered), 9000)
        self.assertEqual(projected["sacred_manifest"], result["sacred_manifest"])
        self.assertEqual(projected["work_briefing"]["tasks"][0]["id"], "new")

    def test_raw_briefing_survives_formatter_budget_with_manifest_intact(self):
        with mock.patch.object(boswell_client, "_request", side_effect=[payload(), tasks()]):
            result = boswell_client.startup()
        result["sacred_manifest"]["identity"] = "s" * 6800
        result["continuity"] = {"key_decisions_and_tensions": {"active_work": [{"title": "old"}]}}
        rendered = codex._orientation(result)
        projected = json.loads(rendered.split("\n", 1)[1])
        self.assertEqual(projected["sacred_manifest"], result["sacred_manifest"])
        self.assertEqual(projected["work_briefing"]["tasks"][0]["id"], "new")
        self.assertLessEqual(len(rendered), codex.ORIENTATION_MAX_CHARS)


class OpeningResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(session_state, "STATE_ROOT", Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)
        session_state.save_startup_cache("opening", payload())
        session_state.save("opening", {"startup_loaded": True})

    def greet(self):
        return codex._user_prompt({"session_id": "opening", "prompt": "good afternoon", "turn_id": "one"})

    def test_first_greeting_gets_an_explicit_response_contract_without_network(self):
        with mock.patch.object(boswell_client, "search") as search, mock.patch.object(boswell_client, "startup") as startup:
            response = self.greet()
        self.assertIsNotNone(response)
        self.assertIn("current work", response["hookSpecificOutput"]["additionalContext"])
        search.assert_not_called()
        startup.assert_not_called()

    def test_bare_greeting_gets_one_continuation_only(self):
        self.greet()
        event = {"session_id": "opening", "turn_id": "one", "last_assistant_message": "Good afternoon, Steve."}
        with mock.patch.object(codex.transcript_spool, "capture"):
            first = codex._stop_event(event)
            again = codex._stop_event({**event, "stop_hook_active": True})
        self.assertIsNotNone(first)
        self.assertEqual(first["decision"], "block")
        self.assertIsNone(again)

    def test_substantive_response_and_user_stop_are_not_forced_to_continue(self):
        self.greet()
        with mock.patch.object(codex.transcript_spool, "capture"):
            self.assertIsNone(codex._stop_event({"session_id": "opening", "last_assistant_message": "Good afternoon. Manifest protection is open; I will check its implementation status."}))
        self.assertIsNone(self.greet())

    def test_second_prompt_cancels_opening_gate(self):
        self.greet()
        codex._user_prompt({"session_id": "opening", "prompt": "stop", "turn_id": "two"})
        with mock.patch.object(codex.transcript_spool, "capture"):
            self.assertIsNone(codex._stop_event({"session_id": "opening", "turn_id": "two", "last_assistant_message": "Good afternoon, Steve."}))

    def test_direct_task_does_not_trigger_greeting_contract(self):
        with mock.patch.object(boswell_client, "search", return_value={"results": []}):
            self.assertIsNone(codex._user_prompt({"session_id": "opening", "prompt": "Inspect the build failure"}))
        self.assertIsNone(self.greet())

    def test_claude_uses_same_greeting_contract_and_gate(self):
        spec = importlib.util.spec_from_file_location("opening_claude", ROOT / "scripts" / "dispatcher.py")
        claude = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(claude)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch("prompt_retrieval.evaluate", return_value=None) as retrieval:
            claude._user_prompt({"session_id": "opening", "prompt": "good afternoon"})
        self.assertIn("current work", json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"])
        retrieval.assert_not_called()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch("done_gate.evaluate", return_value=None):
            claude._stop({"session_id": "opening", "last_assistant_message": "Good afternoon, Steve."})
        self.assertEqual(json.loads(output.getvalue())["decision"], "block")


if __name__ == "__main__":
    unittest.main()
