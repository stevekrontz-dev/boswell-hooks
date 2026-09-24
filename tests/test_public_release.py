from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_manifests_publish_one_version(self):
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads(
            (ROOT / "claude" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(codex["version"], "2.4.1")
        self.assertEqual(claude["version"], codex["version"])

    def test_public_runtime_and_docs_are_tenant_neutral(self):
        paths = [ROOT / "INSTALL.md", ROOT / "CODEX.md", ROOT / "build_release.sh"]
        paths.extend((ROOT / "scripts").glob("*"))
        forbidden = (
            "Steve",
            "Henry",
            "Wren",
            "TintAtlanta",
            "tintatlanta",
            "wren_bootloader",
            "X-Boswell-Internal",
            "BOSWELL_INTERNAL_SECRET",
            "00000000-0000-0000-0000-000000000001",
            "51ac2193-9dd2-4cf3-9232-38bf6b555640",
            "74092d71-cc85-41d4-b4ac-5635478622d8",
            "C:/Users/Steve",
            r"C:\Users\Steve",
        )
        failures = []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in forbidden:
                if marker.lower() in text.lower():
                    failures.append(f"{path.relative_to(ROOT)} contains {marker!r}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_private_handoff_and_obsolete_utilities_are_not_published(self):
        for relative in (
            "HANDOFF-FOR-CLAUDE.md",
            "scripts/ingest_local_sessions.py",
            "scripts/priority_check.py",
            "scripts/relevance.py",
            "scripts/sacred_commitments.py",
            "scripts/test_corrective_gate.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_release_builder_uses_an_explicit_runtime_inventory(self):
        source = (ROOT / "build_release.sh").read_text(encoding="utf-8")
        self.assertNotIn("for f in scripts/*.py", source)
        self.assertNotIn("HANDOFF-FOR-CLAUDE.md", source)
        self.assertIn("RUNTIME_FILES=", source)


if __name__ == "__main__":
    unittest.main()
