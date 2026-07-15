"""Claude plugin-root shim for the repository's shared dispatcher."""

from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
runpy.run_path(str(ROOT / "scripts" / "dispatcher.py"), run_name="__main__")
