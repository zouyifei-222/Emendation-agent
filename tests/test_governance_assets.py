from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agents import governance_assets, governance_eval, governance_rules, governance_tools, session, skills
from agents.web import WebApp, WebError


class AssetRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        skills.reset_skill_cache()

    def test_lifecycle_transitions_are_enforced(self):
        self.assertFalse(governance_assets.set_status("论文格式", "active")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "review")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "active")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "deprecated", reason="迁移到新版格式规则")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "archived", reason="迁移到新版格式规则")["ok"])
        asset = governance_assets.get_asset("论文格式")
        self.assertEqual(asset["status"], "archived")
        self.assertEqual(len(asset["review_history"]), 4)
        self.assertFalse(governance_assets.set_status("论文格式", "active")["ok"])

    def test_deprecated_and_archived_require_a_migration_path(self):
        self.assertTrue(governance_assets.set_status("论文格式", "review")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "active")["ok"])
        self.assertFalse(governance_assets.set_status("论文格式", "deprecated")["ok"])
        self.assertTrue(governance_assets.set_status("论文格式", "deprecated", reason="改用新版")["ok"])
        self.assertFalse(governance_assets.set_status("论文格式", "archived")["ok"])

    def test_human_review_is_recorded_without_claiming_an_evaluation(self):
        result = governance_assets.review_asset("论文格式", "alice", "approve", "人工核对过正文")
        self.assertTrue(result["ok"])
        history = governance_assets.get_asset("论文格式")["review_history"]
        self.assertEqual(history[0]["kind"], "human_review")
        self.assertEqual(history[0]["decision"], "approve")
        self.assertFalse(governance_assets.review_asset("论文格式", "", "approve")["ok"])
        self.assertFalse(governance_assets.review_asset("论文格式", "alice", "pass")["ok"])
        decisions = governance_eval.read_decisions("论文格式")
        self.assertTrue(any(row["event"] == "human_review" for row in decisions))

    def test_conflicts_are_detected_from_confirmed_rules(self):
        for name, mode in (("规则A", "contains"), ("规则B", "not_contains")):
            governance_assets.set_status(name, "review")
            lineage = governance_eval.lineage(name)
            spec = {"mode": mode, "text": "结论"}
            governance_rules.stage_from_model_output(lineage, json.dumps({"checks": [spec]}))
            pending = governance_rules.pending_rules(lineage)
            self.assertTrue(governance_rules.confirm_rules(
                lineage, [pending[0]["rule_id"]], "tester")["ok"])
        findings = governance_assets.conflicts()
        self.assertTrue(any(row["type"] == "mutually_exclusive" for row in findings), findings)
        self.assertIn("规则A", findings[0]["assets"])
        self.assertIn("规则B", findings[0]["assets"])


class GovernanceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        self.session_patch = patch.object(session, "SESSION_DIR", Path(self.temp.name) / "sessions")
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.app = WebApp(options={"api_key": "fake-secret", "api_base": "http://example.test/v1",
                                   "api_protocol": "openai", "model": "test-model",
                                   "custom_system_prompt": "test", "enable_mcp": False,
                                   "enable_memory_prefetch": False, "enable_online_evolution": False})
        self.addCleanup(self.app.close)
        skills.reset_skill_cache()

    def dispatch(self, method, path, body=None):
        return self.app.call(self.app.dispatch(method, path, body))

    def create(self):
        return self.dispatch("POST", "/api/governance/save", {
            "mode": "new", "name": "论文格式", "description": "团队文档规则",
            "instructions": "# Goal\n\n先给结论。", "target": "project"})

    def test_rules_stage_confirm_and_lifecycle_endpoints(self):
        self.assertTrue(self.create()["ok"])
        path = "/api/governance/" + "论文格式"
        raw = json.dumps({"checks": [{"mode": "contains", "text": "结论"}],
                          "requirements": ["先给结论"]}, ensure_ascii=False)
        self.assertTrue(self.dispatch("POST", path + "/rules", {"raw": raw})["ok"])
        rules = self.dispatch("GET", path + "/rules")
        self.assertEqual(len(rules["pending"]), 2)
        self.assertEqual(rules["active"], [])
        confirmed = self.dispatch("POST", path + "/rules/confirm",
                                  {"ruleIds": [row["rule_id"] for row in rules["pending"]],
                                   "reviewer": "alice"})
        self.assertTrue(confirmed["ok"])
        detail = self.dispatch("GET", path)
        self.assertEqual(len(detail["rules"]["active"]), 2)
        self.assertEqual(len(detail["decisions"]), 3)
        with self.assertRaises(WebError):
            self.dispatch("POST", path + "/status", {"status": "active"})
        self.assertTrue(self.dispatch("POST", path + "/status", {"status": "review"})["ok"])
        self.assertTrue(self.dispatch("POST", path + "/status", {"status": "active"})["ok"])
        self.assertTrue(self.dispatch("POST", path + "/review",
                                      {"reviewer": "alice", "decision": "approve"})["ok"])
        conflicts = self.dispatch("GET", "/api/governance/conflicts")
        self.assertIn("conflicts", conflicts)
        board = self.dispatch("GET", "/api/governance")
        self.assertIn("toolScopeNote", board)
        self.assertIn("conflicts", board)


class ToolSopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)

    def test_tool_sop_eval_writes_separate_paired_artifacts(self):
        cases = governance_tools.prepare_cases([
            {"sample_id": "t1", "task_user": "读文件后总结", "checks": [], "requirements": []}])

        async def runner(case, condition, allowed):
            return {"status": "completed", "correct": condition == "fold_on",
                    "fold_count": 1 if condition == "fold_on" else 0,
                    "capabilities_verified": True, "allowed_tools": allowed}

        result = asyncio.run(governance_tools.run_tool_sop_eval("论文格式", cases, runner))
        self.assertTrue(result["ok"], result)
        self.assertIn("tool_sop_only", result["scope"])
        directory = Path(result["run_dir"])
        self.assertIn("tool-runs", str(directory))
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["scope"], "tool_sop_only")
        self.assertIn("compact_context", config["read_only_tools"])
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["cases_with_fold"], 1)
        self.assertNotIn("technique", directory.parts)

    def test_incomplete_pairing_is_not_reported_as_a_score(self):
        async def runner(case, condition, allowed):
            return {"status": "failed", "correct": False, "fold_count": 0,
                    "capabilities_verified": True}

        cases = governance_tools.prepare_cases([{"sample_id": "t1", "task_user": "x"}])
        result = asyncio.run(governance_tools.run_tool_sop_eval(
            "论文格式", cases, runner, write=False))
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["cases_with_fold"], 0)
        self.assertEqual(result["run_dir"], "")


if __name__ == "__main__":
    unittest.main()
