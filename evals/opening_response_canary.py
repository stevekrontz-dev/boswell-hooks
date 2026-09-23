"""Run fresh CLI models against exact shared hook output with synthetic work.

This measures model use of the emitted context. Adapter invocation, startup-once,
and Stop continuation are separately covered by lifecycle tests. No live Boswell
data, MCP servers, or write tools are supplied. No model override is selected:
Codex inherits the user's configured model; Claude uses its default selection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_dispatcher
import opening_briefing


def context():
    tasks = {"count": 3, "tasks": [
        {"id": "older", "title": "Old parity soak", "status": "open",
         "assigned_to": "Other worker", "updated_at": "2026-04-01"},
        {"id": "current", "title": "Harbor manifest safeguard: review the write guard tests",
         "status": "open", "assigned_to": "Current worker", "updated_at": "2026-09-23"},
        {"id": "claimed", "title": "Lumen canary review", "status": "claimed",
         "assigned_to": "Another worker", "updated_at": "2026-09-22"}]}
    return codex_dispatcher._orientation({
        "sacred_manifest": {"identity": "Assistant collaborating with the operator",
                            "active_commitments": []},
        "continuity": {}, "work_briefing": opening_briefing.task_snapshot(tasks),
        "startup_integrity": {"status": "ok", "degraded_components": []},
    }) + "\n" + opening_briefing.OPENING_CONTRACT


def run(client, root, prompt_context):
    work = root / client
    work.mkdir()
    env = dict(os.environ)
    env["BOSWELL_HOOK_STATE"] = str(work / "state")
    env["BOSWELL_TRANSCRIPTS_ARCHIVE"] = str(work / "archive")
    env["CLAUDE_PROJECTS_DIR"] = str(work / "projects")
    env.pop("PLUGIN_DATA", None)
    # Context-only model canary: disable automatic persistence/retrieval hooks
    # for this subprocess so it cannot contact production or upload fixtures.
    if client == "codex":
        executable = shutil.which("codex.cmd") or shutil.which("codex")
        cmd = [executable, "exec", "--ignore-user-config", "--ignore-rules",
               "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
               "--json", "-c", "features.hooks=false", "-c", "features.shell_tool=false",
               "-c", "project_doc_max_bytes=0", "-c", "web_search=\"disabled\"",
               "-c", "developer_instructions=" + json.dumps(prompt_context),
               "-o", str(work / "answer.txt")]
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.exists():
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            for key in ("model", "model_reasoning_effort"):
                if isinstance(config.get(key), str):
                    cmd.extend(["-c", key + "=" + json.dumps(config[key])])
        cmd.append("good afternoon")
    else:
        mcp = work / "mcp.json"
        mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
        cmd = [shutil.which("claude"), "-p", "good afternoon", "--tools", "",
               "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(mcp),
               "--no-session-persistence", "--output-format", "json",
               "--system-prompt", "You are an assistant collaborating with the operator.\n" + prompt_context]
    proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    (work / "stdout.jsonl").write_text(proc.stdout, encoding="utf-8")
    (work / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if client == "codex":
        answer_path = work / "answer.txt"
        answer = answer_path.read_text(encoding="utf-8") if answer_path.exists() else ""
    else:
        try:
            answer = json.loads(proc.stdout).get("result", "")
        except ValueError:
            answer = ""
    low = answer.lower()
    return {"client": client, "exit": proc.returncode, "answer": answer,
            "pass": proc.returncode == 0 and "harbor" in low
                    and any(word in low for word in ("review", "test", "check", "inspect", "verify"))
                    and not opening_briefing.BARE_GREETING.fullmatch(answer.strip())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    prompt_context = context()
    (args.output / "emitted-context.txt").write_text(prompt_context, encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: run(name, args.output, prompt_context), ("codex", "claude")))
    (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if all(item["pass"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
