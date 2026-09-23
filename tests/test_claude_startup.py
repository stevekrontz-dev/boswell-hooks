from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("claude_dispatcher", SCRIPTS / "dispatcher.py")
claude_dispatcher = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(claude_dispatcher)


class ClaudeStartupTests(unittest.TestCase):
    def test_session_start_delegates_to_structural_startup_and_emits_one_json_value(self):
        receipt = {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "startup receipt",
            },
        }
        output = io.StringIO()
        with (
            mock.patch("codex_dispatcher._session_start", return_value=receipt) as startup,
            mock.patch("transcript_monitor.check_pending", return_value=None),
            mock.patch("hook_health.report", return_value=None),
            contextlib.redirect_stdout(output),
        ):
            claude_dispatcher._session_start({"session_id": "claude-1", "source": "startup"})

        startup.assert_called_once_with({"session_id": "claude-1", "source": "startup"})
        self.assertEqual(json.loads(output.getvalue()), receipt)

    def test_cached_resume_is_silent(self):
        output = io.StringIO()
        with (
            mock.patch("codex_dispatcher._session_start", return_value=None) as startup,
            contextlib.redirect_stdout(output),
        ):
            claude_dispatcher._session_start({"session_id": "claude-1", "source": "resume"})

        startup.assert_called_once()
        self.assertEqual(output.getvalue(), "")

    def test_pretool_uses_shared_structural_startup_gate_first(self):
        denial = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "startup missing",
            }
        }
        output = io.StringIO()
        event = {"session_id": "claude-1", "tool_name": "Edit", "tool_input": {}}
        with (
            mock.patch("codex_dispatcher._pre_tool", return_value=denial) as gate,
            contextlib.redirect_stdout(output),
        ):
            claude_dispatcher._pre_tool(event)

        gate.assert_called_once_with(event)
        self.assertEqual(json.loads(output.getvalue()), denial)

    def test_prompt_is_stopped_when_structural_startup_receipt_is_missing(self):
        output = io.StringIO()
        import boswell_client
        with (
            mock.patch("session_state.load", return_value={}),
            mock.patch("session_state.load_startup_cache", return_value=None),
            mock.patch("session_state.save"),
            mock.patch(
                "boswell_client.startup",
                side_effect=boswell_client.BoswellUnavailable("Boswell transport failure: TimeoutError"),
            ) as recovery,
            mock.patch("prompt_retrieval.evaluate") as retrieval,
            contextlib.redirect_stdout(output),
        ):
            claude_dispatcher._user_prompt({
                "session_id": "claude-1",
                "prompt": "Audit the deployment architecture before editing",
            })

        result = json.loads(output.getvalue())
        self.assertFalse(result["continue"])
        self.assertIn("startup continuity is missing", result["stopReason"])
        recovery.assert_called_once()
        retrieval.assert_not_called()


if __name__ == "__main__":
    unittest.main()
