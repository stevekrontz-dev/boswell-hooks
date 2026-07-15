"""Small crash-safe, machine-local state store keyed by Codex session id."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from codex_config import STATE_ROOT


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]{2,}", re.IGNORECASE)
GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|yo|sup)[!,.\s]*$",
    re.IGNORECASE,
)


def safe_session_id(value: str | None) -> str:
    raw = value or "unknown-session"
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:96]
    return clean or hashlib.sha256(raw.encode()).hexdigest()[:24]


def path_for(session_id: str | None) -> Path:
    return STATE_ROOT / f"{safe_session_id(session_id)}.json"


def load(session_id: str | None) -> dict:
    path = path_for(session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(session_id: str | None, state: dict) -> None:
    path = path_for(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def cache_path(session_id: str | None) -> Path:
    return STATE_ROOT / f"{safe_session_id(session_id)}.startup.json"


def save_startup_cache(session_id: str | None, payload: dict) -> None:
    path = cache_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_startup_cache(session_id: str | None) -> dict | None:
    try:
        value = json.loads(cache_path(session_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def tokens(value: object) -> set[str]:
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    return {t.lower() for t in TOKEN_RE.findall(value) if len(t) >= 3}


def substantive(prompt: str) -> bool:
    if not prompt or GREETING_RE.match(prompt):
        return False
    return len(tokens(prompt)) >= 2 or len(prompt.strip()) >= 24

