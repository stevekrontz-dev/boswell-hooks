"""Atlas is the default Boswell authority for every hook entry point."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ATLAS = "https://v3.askboswell.com"
RAILWAY = "delightful-imagination-production-f6a1.up.railway.app"


def test_python_hook_default_uses_atlas() -> None:
    environment = os.environ.copy()
    environment.pop("BOSWELL_API_BASE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import codex_config; print(codex_config.API_BASE)"
            ),
        ],
        cwd=ROOT / "scripts",
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [ATLAS]


def test_tenant_switcher_default_uses_atlas() -> None:
    source = (ROOT / "scripts" / "tenant_switcher.ps1").read_text(encoding="utf-8")

    assert ATLAS in source
    assert RAILWAY not in source


if __name__ == "__main__":
    # These are pytest-style functions, but pytest is not installed on every
    # fleet machine — and `python -m unittest` finds no TestCase here. Either way
    # the file reported "ran 0 tests" and looked like a pass. Measured on the M5
    # 2026-09-11: the ONE test guarding the Atlas default had never executed.
    # Run the same way as the rest of the suite so a Railway regression fails.
    tests = [test_python_hook_default_uses_atlas, test_tenant_switcher_default_uses_atlas]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — report every failure, then exit
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc!r}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
