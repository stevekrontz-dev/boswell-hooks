from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_dispatcher as dispatcher
import session_state


class CodexHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=r"C:\tmp")
        self.state_root = Path(self.temp.name) / "sessions"
        self.state_patch = mock.patch.object(session_state, "STATE_ROOT", self.state_root)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def startup_payload():
        return {
            "local_time": "now",
            "sacred_manifest": {"identity": "Wren", "active_commitments": []},
            "recent_thread": [{"message": "recent"}],
            "open_tasks": [],
            "my_tasks": [],
            "wren_bootloader": [],
        }

    def test_hook_manifests_split_codex_and_claude_events(self):
        codex = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        claude = json.loads((ROOT / "hooks" / "claude-hooks.json").read_text(encoding="utf-8"))
        self.assertNotIn("SessionEnd", codex["hooks"])
        self.assertIn("PreCompact", codex["hooks"])
        self.assertIn("SessionEnd", claude["hooks"])

    def test_greeting_is_not_a_semantic_retrieval(self):
        self.assertFalse(session_state.substantive("good afternoon"))
        self.assertTrue(session_state.substantive("How should Codex load Boswell context?"))
        self.assertFalse(session_state.retrieval_eligible("design it"))
        self.assertTrue(session_state.retrieval_eligible(
            "How should Codex load Boswell context?"))

    @mock.patch.object(dispatcher.transcript_spool, "flush_pending", return_value=(0, 0))
    @mock.patch.object(dispatcher.boswell_client, "startup")
    def test_startup_runs_exactly_once_per_session(self, startup, _flush):
        startup.return_value = self.startup_payload()
        data = {"session_id": "s1", "source": "startup"}
        first = dispatcher._session_start(data)
        second = dispatcher._session_start({"session_id": "s1", "source": "resume"})
        self.assertEqual(startup.call_count, 1)
        self.assertEqual(session_state.load("s1")["startup_calls"], 1)
        self.assertIn("STRUCTURALLY LOADED", first["hookSpecificOutput"]["additionalContext"])
        self.assertIn("STRUCTURALLY LOADED", second["hookSpecificOutput"]["additionalContext"])

    @mock.patch.object(dispatcher.boswell_client, "search")
    def test_substantive_prompt_retrieves_and_records_evidence(self, search):
        session_state.save("s2", {"startup_loaded": True})
        search.return_value = {"results": [{
            "message": "Codex hook architecture",
            "content": "corrective memory read evidence",
            "blob_hash": "abc",
            "content_type": "methodology",
            "distance": 0.42,
        }]}
        result = dispatcher._user_prompt({
            "session_id": "s2", "prompt": "Build the Codex hook architecture",
        })
        self.assertIn("RELEVANT MEMORIES", result["hookSpecificOutput"]["additionalContext"])
        self.assertIn("corrective", session_state.load("s2")["boswell_read_tokens"])

    @mock.patch.object(dispatcher.boswell_client, "search")
    def test_short_followup_does_not_search(self, search):
        session_state.save("followup", {"startup_loaded": True})
        self.assertIsNone(dispatcher._user_prompt({
            "session_id": "followup", "prompt": "design it",
        }))
        search.assert_not_called()

    @mock.patch.object(dispatcher.boswell_client, "search")
    def test_automatic_retrieval_abstains_on_weak_results(self, search):
        session_state.save("noise", {"startup_loaded": True})
        search.return_value = {"results": [{
            "message": "Drafted touch for lead 349",
            "content": '{"biographical_weight":"low","user_participation":"minimal"}',
            "blob_hash": "noise",
            "content_type": "memory",
            "distance": 0.61,
        }]}
        self.assertIsNone(dispatcher._user_prompt({
            "session_id": "noise",
            "prompt": "Are these hook memories too noisy for this conversation?",
        }))
        self.assertEqual(session_state.load("noise")["last_retrieval"], [])

    @mock.patch.object(dispatcher.boswell_client, "search")
    def test_automatic_retrieval_caps_and_filters_results(self, search):
        session_state.save("filter", {"startup_loaded": True})
        search.return_value = {"results": [
            {"message": "raw transcript", "content": "x", "blob_hash": "t",
             "content_type": "transcript", "distance": 0.20},
            {"message": "one", "content": "first", "blob_hash": "1",
             "content_type": "methodology", "distance": 0.40},
            {"message": "two", "content": "second", "blob_hash": "2",
             "content_type": "memory", "distance": 0.45},
            {"message": "three", "content": "third", "blob_hash": "3",
             "content_type": "memory", "distance": 0.46},
        ]}
        result = dispatcher._user_prompt({
            "session_id": "filter",
            "prompt": "Explain the Boswell hook retrieval architecture now",
        })
        injected = result["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("raw transcript", injected)
        self.assertIn('"message":"one"', injected)
        self.assertIn('"message":"two"', injected)
        self.assertNotIn('"message":"three"', injected)

    def test_material_tool_is_denied_without_startup(self):
        result = dispatcher._pre_tool({
            "session_id": "blind", "tool_name": "apply_patch", "tool_input": {},
        })
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_corrective_commit_requires_overlapping_read(self):
        session_state.save("s3", {"startup_loaded": True, "boswell_read_tokens": []})
        data = {
            "session_id": "s3",
            "tool_name": "mcp__codex_apps__boswell_boswell_commit",
            "tool_input": {"message": "CORRECTION: atlas machine owner was wrong"},
        }
        denied = dispatcher._pre_tool(data)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        session_state.save("s3", {
            "startup_loaded": True,
            "boswell_read_tokens": ["atlas", "machine", "owner", "wrong"],
        })
        self.assertIsNone(dispatcher._pre_tool(data))

    @mock.patch.object(dispatcher.transcript_spool, "capture", return_value=None)
    def test_stop_gate_blocks_once_then_allows(self, _capture):
        session_state.save("s4", {
            "startup_loaded": True,
            "mutations": [{"tool": "apply_patch", "at": 10}],
            "verifications": [],
            "closed_mutation_seq": 0,
        })
        data = {"session_id": "s4", "turn_id": "t1"}
        first = dispatcher._stop_event(data)
        second = dispatcher._stop_event(data)
        self.assertFalse(first["continue"])
        self.assertIsNone(second)
        self.assertEqual(session_state.load("s4")["closed_mutation_seq"], 1)

    @mock.patch.object(dispatcher.transcript_spool, "capture", return_value=None)
    def test_verification_after_mutation_closes_without_block(self, _capture):
        session_state.save("s5", {
            "startup_loaded": True,
            "mutations": [{"tool": "apply_patch", "at": 10}],
            "verifications": [{"command": "pytest", "at": 11}],
            "closed_mutation_seq": 0,
        })
        self.assertIsNone(dispatcher._stop_event({"session_id": "s5", "turn_id": "t2"}))


if __name__ == "__main__":
    unittest.main()



