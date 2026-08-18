"""Atlas is the default Boswell authority for every hook entry point."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ATLAS = "https://v3.askboswell.com"
RAILWAY = "delightful-imagination-production-f6a1.up.railway.app"


def test_python_hook_defaults_use_atlas() -> None:
    environment = os.environ.copy()
    environment.pop("BOSWELL_API_BASE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import config, codex_config; "
                "print(config.BOSWELL_API_BASE); print(codex_config.API_BASE)"
            ),
        ],
        cwd=ROOT / "scripts",
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [ATLAS, ATLAS]


def test_tenant_switcher_default_uses_atlas() -> None:
    source = (ROOT / "scripts" / "tenant_switcher.ps1").read_text(encoding="utf-8")

    assert ATLAS in source
    assert RAILWAY not in source
