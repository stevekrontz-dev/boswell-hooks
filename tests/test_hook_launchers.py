import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HookLauncherTests(unittest.TestCase):
    def test_windows_launchers_resolve_plugin_root_inside_python(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook["commandWindows"]
            for groups in config["hooks"].values()
            for group in groups
            for hook in group["hooks"]
        ]
        self.assertTrue(commands)
        self.assertTrue(all("os.environ['PLUGIN_ROOT']" in command for command in commands))
        self.assertTrue(all("%PLUGIN_ROOT%" not in command for command in commands))


if __name__ == "__main__":
    unittest.main()

