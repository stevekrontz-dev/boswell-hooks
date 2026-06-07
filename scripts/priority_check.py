"""Priority check timer (boswell-hooks plugin copy).
Runs the expiring-priorities marker only if 10+ minutes have passed since the
last check. State is machine-local; the marker is emitted in-process via the
sibling sacred_commitments module (no subprocess spawn)."""
import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from config import STATE_ROOT
except ImportError:
    STATE_ROOT = Path.home() / ".claude" / "hooks" / "state"

STATE_FILE = STATE_ROOT / "priority_check.json"
CHECK_INTERVAL_MINUTES = 10


def get_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_check": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def should_check():
    last = get_state().get("last_check")
    if not last:
        return True
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
        return elapsed >= CHECK_INTERVAL_MINUTES
    except Exception:
        return True


def run_check():
    if not should_check():
        return  # silent pass — not time yet
    save_state({"last_check": datetime.now().isoformat()})
    import sacred_commitments
    sacred_commitments.check_priorities()


if __name__ == "__main__":
    run_check()
