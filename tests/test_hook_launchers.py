import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HookLauncherTests(unittest.TestCase):
    def _windows_commands(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        return {
            event: hook["commandWindows"]
            for event, groups in config["hooks"].items()
            for group in groups
            for hook in group["hooks"]
        }

    def test_windows_launchers_have_portable_cache_hydration_fallback(self):
        commands = list(self._windows_commands().values())
        self.assertTrue(commands)
        self.assertTrue(all("os.environ.get('PLUGIN_ROOT')" in command for command in commands))
        self.assertTrue(all("os.environ.get('CLAUDE_PLUGIN_ROOT')" in command for command in commands))
        self.assertTrue(all("'plugins','cache','*','boswell-hooks','*'" in command for command in commands))
        self.assertTrue(all("time.sleep(0.1)" in command for command in commands))
        self.assertTrue(all("dispatcher unavailable after 10s" in command for command in commands))
        self.assertTrue(all("os.environ['PLUGIN_ROOT']" not in command for command in commands))
        self.assertTrue(all("%PLUGIN_ROOT%" not in command for command in commands))

    @unittest.skipUnless(os.name == "nt", "Windows launcher integration test")
    def test_windows_launcher_finds_dispatcher_from_cache_without_plugin_root(self):
        command = self._windows_commands()["SessionStart"]
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            dispatcher = (
                codex_home / "plugins" / "cache" / "personal" / "boswell-hooks"
                / "9.9.9" / "scripts" / "codex_dispatcher.py"
            )
            dispatcher.parent.mkdir(parents=True)
            dispatcher.write_text(
                "import os, pathlib, sys\n"
                "pathlib.Path(os.environ['BOSWELL_HOOK_TEST_MARKER']).write_text(sys.argv[1])\n",
                encoding="utf-8",
            )
            marker = Path(temp) / "marker.txt"
            env = os.environ.copy()
            env.pop("PLUGIN_ROOT", None)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            env["CODEX_HOME"] = str(codex_home)
            env["BOSWELL_HOOK_TEST_MARKER"] = str(marker)

            result = subprocess.run(
                command,
                input="{}",
                text=True,
                shell=True,
                env=env,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "SessionStart")

    @unittest.skipUnless(os.name == "nt", "Windows launcher integration test")
    def test_windows_launcher_waits_for_delayed_cache_hydration(self):
        command = self._windows_commands()["SessionStart"]
        with tempfile.TemporaryDirectory() as temp:
            plugin_root = Path(temp) / "hydrating-plugin"
            dispatcher = plugin_root / "scripts" / "codex_dispatcher.py"
            marker = Path(temp) / "marker.txt"
            env = os.environ.copy()
            env["PLUGIN_ROOT"] = str(plugin_root)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            env["CODEX_HOME"] = str(Path(temp) / ".codex")
            env["BOSWELL_HOOK_TEST_MARKER"] = str(marker)

            def materialize_dispatcher():
                dispatcher.parent.mkdir(parents=True)
                dispatcher.write_text(
                    "import os, pathlib, sys\n"
                    "pathlib.Path(os.environ['BOSWELL_HOOK_TEST_MARKER']).write_text(sys.argv[1])\n",
                    encoding="utf-8",
                )

            timer = threading.Timer(0.3, materialize_dispatcher)
            timer.start()
            try:
                result = subprocess.run(
                    command,
                    input="{}",
                    text=True,
                    shell=True,
                    env=env,
                    capture_output=True,
                    timeout=15,
                )
            finally:
                timer.join()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "SessionStart")

    @unittest.skipUnless(os.name == "nt", "Windows launcher integration test")
    def test_windows_launcher_waits_for_delayed_cache_without_plugin_root(self):
        command = self._windows_commands()["SessionStart"]
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            dispatcher = (
                codex_home / "plugins" / "cache" / "personal" / "boswell-hooks"
                / "9.9.9" / "scripts" / "codex_dispatcher.py"
            )
            marker = Path(temp) / "marker.txt"
            env = os.environ.copy()
            env.pop("PLUGIN_ROOT", None)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            env["CODEX_HOME"] = str(codex_home)
            env["BOSWELL_HOOK_TEST_MARKER"] = str(marker)

            def materialize_dispatcher():
                dispatcher.parent.mkdir(parents=True)
                dispatcher.write_text(
                    "import os, pathlib, sys\n"
                    "pathlib.Path(os.environ['BOSWELL_HOOK_TEST_MARKER']).write_text(sys.argv[1])\n",
                    encoding="utf-8",
                )

            timer = threading.Timer(0.3, materialize_dispatcher)
            timer.start()
            try:
                result = subprocess.run(
                    command,
                    input="{}",
                    text=True,
                    shell=True,
                    env=env,
                    capture_output=True,
                    timeout=15,
                )
            finally:
                timer.join()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "SessionStart")


if __name__ == "__main__":
    unittest.main()

