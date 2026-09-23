"""Bounded display of an already-authorized server work snapshot.

No I/O, credential resolution, access grants, task transitions or live refresh.
Missing/partial data must not look like an empty queue. Returned counts describe
this display, not the larger server response retained in the startup cache.
"""
from __future__ import annotations

TOTAL_FIELDS = ("total_plans", "total_ideas", "total_branches", "backlog_count",
                "unfinished_count", "my_task_count")
INTEGRITY_FIELDS = ("contradiction_count", "broken_link_count", "unknown_task_count")
COLLECTIONS = ("branches", "plans", "unfinished_tasks", "backlog", "my_tasks", "errors")
TASK_TEXT = ("id", "tenant_id", "workspace_id", "plan_blob_hash", "status", "plan_status",
             "start_blocked_reason", "title", "branch", "assigned_to")
TASK_FLAGS = ("can_start", "can_finish", "in_progress", "unfinished")
ERROR_TEXT = ("code", "task_id", "tenant_id", "workspace_id", "blob_hash")


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _text_fields(value, fields):
    # Do not stringify unknown objects or truncate identifiers into other IDs.
    return {key: value[key] for key in fields
            if isinstance(value.get(key), str) and len(value[key]) <= 255}


def project(value):
    """Copy only the work briefing surface; keep server truth and display limits separate."""
    source = value if isinstance(value, dict) else {}
    summary = source.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    projection = source.get("projection")
    projection = projection if isinstance(projection, dict) else {}
    totals = {key: _count(summary.get(key)) for key in TOTAL_FIELDS}
    scope = projection.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    scope_valid = (
        isinstance(scope.get("tenant_id"), str) and 0 < len(scope["tenant_id"]) <= 128
        and "workspace_id" in scope and (scope["workspace_id"] is None
        or isinstance(scope["workspace_id"], str) and 0 < len(scope["workspace_id"]) <= 128)
    )
    scope = {key: scope.get(key) if isinstance(scope.get(key), str) and len(scope[key]) <= 128 else None
             for key in ("tenant_id", "workspace_id")}
    status = projection.get("status")
    if status not in ("ok", "attention_required", "unavailable"):
        status = "unavailable"
    if any(value is None for value in totals.values()) or not scope_valid:
        status = "unavailable"
    raw_tasks = source.get("unfinished_tasks")
    raw_errors = projection.get("errors")
    malformed = not scope_valid or not isinstance(raw_tasks, list) or not isinstance(raw_errors, list)
    raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []
    raw_errors = raw_errors if isinstance(raw_errors, list) else []
    tasks = []
    task_fields_omitted = False
    for row in raw_tasks[:3]:
        if not isinstance(row, dict):
            malformed = True
            continue
        task = _text_fields(row, TASK_TEXT)
        if row.get("plan_blob_hash") is not None and "plan_blob_hash" not in task:
            # A malformed non-NULL link is unresolved, never an orphan.
            malformed = True
            continue
        if not all(task.get(key) for key in ("id", "tenant_id", "workspace_id")):
            malformed = True
            continue
        task.update({key: row[key] for key in TASK_FLAGS if type(row.get(key)) is bool})
        task.update({key: row[key] for key in ("priority", "plan_revision") if _count(row.get(key)) is not None})
        task_fields_omitted |= any(row[key] is not None and key not in task for key in row)
        tasks.append(task)
    errors = [_text_fields(row, ERROR_TEXT) for row in raw_errors[:3] if isinstance(row, dict)]
    error_fields_omitted = any(
        any(key not in _text_fields(row, ERROR_TEXT) for key in row)
        for row in raw_errors[:3] if isinstance(row, dict)
    )
    malformed |= any(not row.get("code") for row in errors) or len(errors) != min(3, len(raw_errors))
    integrity = projection.get("integrity_counts")
    integrity = integrity if isinstance(integrity, dict) else {}
    integrity = {key: _count(integrity.get(key)) for key in INTEGRITY_FIELDS}
    if any(value is None for value in integrity.values()):
        malformed = True
    if totals["unfinished_count"] is not None and totals["unfinished_count"] < len(raw_tasks):
        totals["unfinished_count"] = None
        status = "unavailable"
    if not scope_valid:
        # Invalid scope must not look like a deliberately unbound workspace.
        tasks, errors = [], []
        totals = {key: None for key in TOTAL_FIELDS}
    if status == "ok" and (malformed or raw_errors or any(integrity.values())):
        status = "attention_required"
    truncation = projection.get("truncation")
    truncation = truncation if isinstance(truncation, dict) else {}
    result = {
        "summary": totals,
        "projection": {
            "scope": scope, "status": status, "errors": errors,
            "integrity_counts": integrity,
            "truncation": {key: truncation.get(key, key != "errors") is not False for key in COLLECTIONS},
            "hook_projection": {"source_tasks": len(raw_tasks), "source_errors": len(raw_errors),
                                "malformed": malformed, "status": "within_limit",
                                "task_fields_omitted": task_fields_omitted,
                                "error_fields_omitted": error_fields_omitted},
            "freshness": "cached session-start snapshot; not a live authorization or task claim",
        },
        "unfinished_tasks": tasks,
    }
    if status == "unavailable":
        result["projection"]["reason"] = "work_state_missing_or_invalid; do not infer no work"
    refresh(result)
    return result


def refresh(work):
    """Recount after every display-budget reduction; never turn unknown totals into zero."""
    summary, projection, tasks = work["summary"], work["projection"], work["unfinished_tasks"]
    truncated, hook = projection["truncation"], projection["hook_projection"]
    summary["unfinished_returned"] = len(tasks)
    summary["backlog_returned"] = sum("plan_blob_hash" not in task for task in tasks)
    for collection, count, returned in (
        ("unfinished_tasks", summary["unfinished_count"], len(tasks)),
        ("backlog", summary["backlog_count"], summary["backlog_returned"]),
        ("branches", summary["total_branches"], 0),
        ("my_tasks", summary["my_task_count"], 0),
        ("plans", None if summary["total_plans"] is None or summary["total_ideas"] is None
         else summary["total_plans"] + summary["total_ideas"], 0),
        ("errors", hook["source_errors"], len(projection["errors"])),
    ):
        truncated[collection] |= count is None or returned < count
    summary["backlog_truncated"] = truncated["backlog"]
    truncated["errors"] |= hook["error_fields_omitted"]
    if (len(tasks) < hook["source_tasks"] or len(projection["errors"]) < hook["source_errors"]
            or hook["task_fields_omitted"] or hook["error_fields_omitted"]):
        hook["status"] = "trimmed"
    if hook["malformed"]:
        truncated["unfinished_tasks"] = True
