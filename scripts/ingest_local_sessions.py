"""Bulk-ingest local CC session JSONL files into Boswell (boswell-hooks plugin copy).
Outputs index cards as JSON to stdout for Claude to commit via MCP. Dormant
utility (not wired to any hook); reuses transcript_monitor as a sibling module.

Usage: python ingest_local_sessions.py [--batch N] [--offset N]
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcript_monitor import (  # noqa: E402
    parse_session,
    archive_session,
    build_index_card,
    CLAUDE_PROJECTS,
    STATE_FILE,
)

BATCH_SIZE = 50
PROGRESS_FILE = STATE_FILE.parent / "ingest_progress.json"


def get_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"ingested_files": [], "total_processed": 0}


def save_progress(progress):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress), encoding="utf-8")


def find_all_sessions():
    sessions = []
    if not CLAUDE_PROJECTS.exists():
        return sessions
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.rglob("*.jsonl"):
            sessions.append(f)
    return sorted(sessions, key=lambda f: f.stat().st_mtime)


def main():
    batch_size, offset = BATCH_SIZE, 0
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--batch" and i + 1 < len(args):
            batch_size = int(args[i + 1]); i += 2
        elif args[i] == "--offset" and i + 1 < len(args):
            offset = int(args[i + 1]); i += 2
        else:
            i += 1

    progress = get_progress()
    ingested = set(progress.get("ingested_files", []))
    remaining = [s for s in find_all_sessions() if str(s) not in ingested][offset:]
    batch = remaining[:batch_size]
    if not batch:
        print(json.dumps({"status": "done", "total_ingested": len(ingested)}))
        return

    results = []
    for session_file in batch:
        try:
            parsed = parse_session(session_file)
            sid = parsed["session_id"]
            archive_path = archive_session(session_file, sid)
            results.append({
                "session_id": sid,
                "index_card": build_index_card(parsed, archive_path),
                "first_prompt": parsed["first_prompt"][:80],
                "message_count": parsed["message_count"],
            })
            ingested.add(str(session_file))
        except Exception as e:
            print(json.dumps({"error": str(e), "file": str(session_file)}), file=sys.stderr)
            ingested.add(str(session_file))
            continue

    progress["ingested_files"] = list(ingested)
    progress["total_processed"] = len(ingested)
    save_progress(progress)
    print(json.dumps({
        "status": "batch", "count": len(results),
        "remaining": len(remaining) - len(batch),
        "total_ingested": len(ingested), "cards": results,
    }))


if __name__ == "__main__":
    main()
