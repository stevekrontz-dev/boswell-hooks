"""Incremental, idempotent Codex transcript capture with a durable outbound queue."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boswell_client
from codex_config import ARCHIVE_ROOT, PLUGIN_DATA
from session_state import safe_session_id


# v1 was shared with Claude's incompatible list queue and carried no binding.
# Leave it untouched for explicit evidence review; never assign it today's key.
QUEUE_PATH = PLUGIN_DATA / "bound_transcripts.json"
MACHINE = socket.gethostname().lower()


def _read_queue() -> dict[str, dict]:
    try:
        value = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_queue(queue: dict[str, dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture(data: dict, event: str) -> dict | None:
    transcript = data.get("transcript_path")
    if not transcript:
        return None
    source = Path(transcript)
    if not source.is_file():
        return None
    import tenant_binding
    binding = tenant_binding.bind_session(data)
    session_id = data['session_id']
    safe_id = safe_session_id(session_id)
    client = "claude" if data.get("client") == "claude" else "codex"
    label = "Claude Code" if client == "claude" else "Codex"
    month = datetime.now().strftime("%Y-%m")
    archive_dir = ARCHIVE_ROOT / MACHINE / month
    archive_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".jsonl"
    archive = archive_dir / f"{client}-{safe_id}{suffix}"
    descriptor, temporary = tempfile.mkstemp(dir=archive_dir, prefix='.capture-')
    os.close(descriptor)
    snapshot = Path(temporary)
    try:
        shutil.copy2(source, snapshot)
        tenant_binding.validate_transcript(snapshot, session_id)
        os.replace(snapshot, archive)
    finally:
        snapshot.unlink(missing_ok=True)
    card = {
        "authored_by": label,
        "client": client,
        "session_id": session_id,
        "machine": MACHINE,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_event": event,
        "cwd": data.get("cwd"),
        "model": data.get("model"),
        "byte_count": archive.stat().st_size,
        "sha256": _sha256(archive),
        "archive_path": str(archive),
        "format_note": "Raw client transcript archived without parsing; transcript schema is unstable.",
    }
    queue = _read_queue()
    previous = queue.get(session_id, {}).get("index_card", {})
    if previous.get("sha256") != card["sha256"]:
        queue[session_id] = {"index_card": card, "binding": binding}
        _write_queue(queue)
    return card


def flush_pending(*, exclude_session_id: str | None = None) -> tuple[int, int]:
    queue = _read_queue()
    committed = 0
    survivors: dict[str, dict] = {}
    for session_id, entry in queue.items():
        from tenant_binding import current_binding
        binding = entry.get('binding') if isinstance(entry, dict) else None
        if not binding or binding != current_binding():
            survivors[session_id] = entry
            continue
        if session_id == exclude_session_id:
            survivors[session_id] = entry
            continue
        card = entry.get("index_card") if isinstance(entry, dict) else None
        if not isinstance(card, dict):
            continue
        try:
            client = "claude" if card.get("client") == "claude" else "codex"
            label = "Claude Code" if client == "claude" else "Codex"
            boswell_client.commit(
                branch="transcripts",
                content=card,
                content_type="transcript",
                message=(f"TRANSCRIPT: {label} {session_id[:8]} ({card.get('machine')}, "
                         f"{card.get('byte_count', '?')} bytes)"),
                tags=["transcript", f"{client}-session", str(card.get("machine", MACHINE))],
                expected_binding=binding,
            )
            committed += 1
        except boswell_client.BoswellUnavailable:
            survivors[session_id] = entry
    if queue != survivors:
        _write_queue(survivors)
    return committed, len(survivors)

