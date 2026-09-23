"""Behavioral contract for the server -> shared Hooks work-state briefing."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_dispatcher as dispatcher

TENANT = "10000000-0000-4000-8000-000000000001"
WORKSPACE = "20000000-0000-4000-8000-000000000001"


def startup_payload(*, tenant=TENANT, warm=True, total=7):
    tasks = [{
        "id": f"30000000-0000-4000-8000-{index:012d}",
        "tenant_id": tenant, "workspace_id": WORKSPACE,
        "title": f"Synthetic {status} task", "branch": "example-project",
        "priority": 2, "assigned_to": "Example worker" if status == "claimed" else None,
        "status": status, "plan_blob_hash": "a" * 64,
        "plan_status": "paused", "plan_revision": 2,
        "can_start": False, "can_finish": True, "unfinished": True,
        "in_progress": status == "claimed", "start_blocked_reason": "PLAN_RESUME_REQUIRED",
    } for index, status in enumerate(("open", "claimed", "blocked"), 1)] if total else []
    payload = {
        "local_time": "synthetic time", "sacred_manifest": {"identity": "Example tenant"},
        "startup_integrity": {"contract": "warm-continuity-v1", "status": "ok",
                              "degraded_components": []},
        "work_state": {
            "summary": {"total_plans": 2, "total_ideas": 1, "total_branches": 1,
                        "backlog_count": 0, "backlog_returned": 0, "backlog_limit": 10,
                        "backlog_truncated": False, "unfinished_count": total,
                        "unfinished_returned": len(tasks), "my_task_count": 1 if total else 0},
            "projection": {"scope": {"tenant_id": tenant, "workspace_id": WORKSPACE},
                           "status": "ok", "errors": [],
                           "integrity_counts": {"contradiction_count": 0, "broken_link_count": 0,
                                                "unknown_task_count": 0},
                           "truncation": {"branches": True, "plans": True, "backlog": False,
                                          "my_tasks": bool(total), "unfinished_tasks": total > len(tasks)},
                           "limits": {"backlog_limit": 10, "plan_limit": 20, "branch_limit": 500},
                           "visibility": "all unresolved work; parked plans do not hide tasks"},
            "unfinished_tasks": tasks,
        },
    }
    if warm:
        payload["continuity"] = {"narrative_thread": {"recent": []},
                                 "key_decisions_and_tensions": {"active_work": [], "blocked_work": []}}
    else:
        payload.update(open_tasks=[], my_tasks=[], recent_thread=[])
    return payload


class WorkStateOrientationTests(unittest.TestCase):
    def project(self, payload):
        rendered = dispatcher._orientation(payload)
        self.assertLessEqual(len(rendered), dispatcher.ORIENTATION_MAX_CHARS)
        self.assertIn("STRUCTURALLY LOADED", rendered.split("\n", 1)[0])
        result = json.loads(rendered.split("\n", 1)[1])
        self.assertIn("work_state", result, "Hooks must carry the new work-state briefing")
        return result

    def test_warm_receipt_carries_counts_and_qualified_unfinished_tasks(self):
        payload = startup_payload()
        work = self.project(payload)["work_state"]
        self.assertEqual(work["summary"]["unfinished_count"], 7)
        self.assertEqual(work["summary"]["unfinished_returned"], 3)
        self.assertEqual(work["projection"]["scope"], payload["work_state"]["projection"]["scope"])
        for original, emitted in zip(payload["work_state"]["unfinished_tasks"], work["unfinished_tasks"]):
            for field in ("id", "tenant_id", "workspace_id", "status", "plan_status", "plan_blob_hash",
                          "can_start", "can_finish", "unfinished", "in_progress", "start_blocked_reason"):
                self.assertEqual(emitted[field], original[field], field)

    def test_legacy_receipt_also_carries_the_dedicated_work_state(self):
        self.assertEqual(self.project(startup_payload(warm=False))["work_state"]["summary"]["unfinished_count"], 7)

    def test_missing_state_is_unknown_not_no_work(self):
        payload = startup_payload()
        del payload["work_state"]
        work = self.project(payload)["work_state"]
        self.assertIsNone(work["summary"]["unfinished_count"])
        self.assertEqual(work["projection"]["status"], "unavailable")
        self.assertEqual(work["unfinished_tasks"], [])

    def test_explicit_empty_state_remains_distinct_from_unavailable(self):
        work = self.project(startup_payload(total=0))["work_state"]
        self.assertEqual(work["summary"]["unfinished_count"], 0)
        self.assertEqual(work["projection"]["status"], "ok")
        self.assertFalse(work["projection"]["truncation"]["unfinished_tasks"])

    def test_bad_count_is_not_normalized_to_zero(self):
        for count in (None, -1, True, "0", [], {}):
            with self.subTest(count=count):
                payload = startup_payload()
                payload["work_state"]["summary"]["unfinished_count"] = count
                work = self.project(payload)["work_state"]
                self.assertIsNone(work["summary"]["unfinished_count"])
                self.assertNotEqual(work["projection"]["status"], "ok")

    def test_attention_and_qualified_error_are_not_silenced(self):
        payload = startup_payload()
        projection = payload["work_state"]["projection"]
        projection["status"] = "attention_required"
        projection["integrity_counts"]["broken_link_count"] = 1
        projection["errors"] = [{"code": "PLAN_REFERENCE_UNRESOLVED", "task_id": "synthetic-task",
                                 "tenant_id": TENANT, "workspace_id": WORKSPACE}]
        work = self.project(payload)["work_state"]
        self.assertEqual(work["projection"]["status"], "attention_required")
        self.assertEqual(work["projection"]["errors"], projection["errors"])
        self.assertEqual(work["projection"]["integrity_counts"]["broken_link_count"], 1)

    def test_budget_pressure_keeps_totals_and_truthful_returned_counts(self):
        payload = startup_payload()
        payload["sacred_manifest"]["identity"] = "s" * 5700
        payload["continuity"]["narrative_thread"]["recent"] = [{"message": "n" * 4000}] * 5
        work = self.project(payload)["work_state"]
        self.assertEqual(work["summary"]["unfinished_count"], 7)
        self.assertEqual(work["summary"]["unfinished_returned"], len(work["unfinished_tasks"]))
        self.assertTrue(work["projection"]["truncation"]["unfinished_tasks"])
        self.assertEqual(work["projection"]["scope"]["tenant_id"], TENANT)
        self.assertLess(len(work["unfinished_tasks"]), 3)

    def test_projection_does_not_mutate_or_mix_two_tenant_inputs(self):
        first = startup_payload()
        second = startup_payload(tenant="10000000-0000-4000-8000-000000000002")
        originals = copy.deepcopy((first, second))
        emitted_first = self.project(first)
        emitted_second = self.project(second)
        self.assertEqual((first, second), originals)
        self.assertNotIn(TENANT, json.dumps(emitted_second))
        self.assertEqual(self.project(first), emitted_first)

    def test_malformed_link_never_becomes_an_orphan(self):
        for link in ("a" * 256, True, {}, []):
            with self.subTest(link=link):
                payload = startup_payload()
                payload["work_state"]["unfinished_tasks"][0]["plan_blob_hash"] = link
                work = self.project(payload)["work_state"]
                self.assertEqual(work["summary"]["backlog_returned"], 0)
                self.assertNotEqual(work["projection"]["status"], "ok")
                self.assertTrue(work["projection"]["truncation"]["unfinished_tasks"])

    def test_malformed_scope_is_not_an_unbound_workspace(self):
        for scope in (False, "w" * 129, {}, []):
            with self.subTest(scope=scope):
                payload = startup_payload()
                payload["work_state"]["projection"]["scope"]["workspace_id"] = scope
                work = self.project(payload)["work_state"]
                self.assertEqual(work["projection"]["status"], "unavailable")
                self.assertEqual(work["unfinished_tasks"], [])

    def test_genuinely_unbound_workspace_and_orphan_remain_valid(self):
        payload = startup_payload()
        source = payload["work_state"]
        source["projection"]["scope"]["workspace_id"] = None
        source["unfinished_tasks"][0]["plan_blob_hash"] = None
        source["summary"]["backlog_count"] = 1
        source["summary"]["backlog_returned"] = 1
        work = self.project(payload)["work_state"]
        self.assertEqual(work["projection"]["status"], "ok")
        self.assertIsNone(work["projection"]["scope"]["workspace_id"])
        self.assertEqual(work["summary"]["backlog_returned"], 1)

    def test_omitted_error_detail_is_marked_incomplete(self):
        payload = startup_payload()
        source = payload["work_state"]["projection"]
        source["status"] = "attention_required"
        source["errors"] = [{"code": "PLAN_STATE_CONTRADICTION", "tenant_id": TENANT,
                             "workspace_id": WORKSPACE, "blob_hash": "a" * 64,
                             "contradictions": ["UNFINISHED_TASKS_ON_COMPLETED_PLAN"]}]
        work = self.project(payload)["work_state"]
        self.assertEqual(work["projection"]["errors"][0]["code"], "PLAN_STATE_CONTRADICTION")
        self.assertTrue(work["projection"]["truncation"]["errors"])

    def test_sampling_is_reported_at_top_level_even_when_under_character_limit(self):
        payload = startup_payload()
        source = payload["work_state"]
        source["unfinished_tasks"].append(copy.deepcopy(source["unfinished_tasks"][0]))
        source["summary"]["unfinished_returned"] = 4
        result = self.project(payload)
        self.assertEqual(result["work_state"]["projection"]["hook_projection"]["status"], "trimmed")
        self.assertEqual(result["startup_integrity"]["hook_projection"]["status"], "trimmed")


if __name__ == "__main__":
    unittest.main()
