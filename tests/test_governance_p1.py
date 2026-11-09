from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from agents import governance_eval, governance_rules, online_skill_eval


class P1RemainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        self.lineage = governance_eval.lineage("论文格式")

    def test_stable_rule_ids_are_content_derived(self):
        check = {"mode": "contains", "text": "结论"}
        first = governance_rules.rule_id("check", check)
        self.assertEqual(first, governance_rules.rule_id("check", dict(check)))
        self.assertTrue(first.startswith("check:"))
        self.assertEqual(len(first), len("check:") + 12)
        self.assertNotEqual(first, governance_rules.rule_id("check", {"mode": "contains", "text": "依据"}))
        self.assertNotEqual(governance_rules.rule_id("requirement", "先给结论"),
                            governance_rules.rule_id("requirement", {"text": "先给结论", "required": False}))

    def test_staged_rules_are_inactive_until_a_human_confirms(self):
        raw = json.dumps({"checks": [{"mode": "contains", "text": "结论"}],
                          "requirements": ["先给结论"]}, ensure_ascii=False)
        staged = governance_rules.stage_from_model_output(self.lineage, raw)
        self.assertTrue(staged["ok"], staged)
        self.assertEqual(staged["staged"], 2)
        self.assertEqual(governance_rules.load_rules(self.lineage), {})
        pending = governance_rules.pending_rules(self.lineage)
        self.assertEqual(len(pending), 2)
        self.assertFalse(governance_rules.confirm_rules(self.lineage, ["check:missing"], "tester")["ok"])
        self.assertFalse(governance_rules.confirm_rules(self.lineage, [pending[0]["rule_id"]], "")["ok"])
        result = governance_rules.confirm_rules(
            self.lineage, [row["rule_id"] for row in pending], "tester")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(governance_rules.load_rules(self.lineage)), 2)
        self.assertEqual(governance_rules.pending_rules(self.lineage), [])
        self.assertEqual(governance_rules.load_rules(self.lineage)[pending[0]["rule_id"]]["confirmed_by"], "tester")

    def test_no_keyword_heuristics_and_strict_candidate_validation(self):
        self.assertFalse(governance_rules.stage_from_model_output(self.lineage, "先给结论，不要废话")["ok"])
        self.assertFalse(governance_rules.stage_from_model_output(
            self.lineage, json.dumps({"checks": [{"mode": "regex", "text": ".*"}], "requirements": []}))["ok"])
        self.assertFalse(governance_rules.stage_from_model_output(
            self.lineage, json.dumps({"checks": [{"mode": "contains", "text": ""}], "requirements": []}))["ok"])
        self.assertFalse(governance_rules.stage_from_model_output(
            self.lineage, json.dumps({"checks": [], "requirements": [{"text": "x", "required": "yes"}]}))["ok"])
        self.assertIn("Never infer hard rules", governance_rules.build_compile_prompt("正文"))

    def test_per_rule_metrics_are_worst_first_and_persisted(self):
        judgments = []
        for rid, label, hard, passed in (("req:a", "先给结论", False, False),
                                         ("req:a", "先给结论", False, False),
                                         ("check:b", "contains: 依据", True, True),
                                         ("check:b", "contains: 依据", True, True)):
            judgments.append({"rule_id": rid, "rule_label": label, "hard": hard, "required": True,
                              "passed": passed, "sample_id": "s1" if passed else "s2",
                              "variant_id": "baseline", "split": "mutate_dev"})
        metrics = governance_rules.aggregate(judgments)
        self.assertEqual(metrics[0]["rule_id"], "req:a")
        self.assertEqual(metrics[0]["pass_rate"], 0.0)
        self.assertEqual(metrics[1]["pass_rate"], 1.0)
        self.assertIn("s2", metrics[0]["failed_samples"])
        directory = Path(self.temp.name) / "run"
        directory.mkdir()
        self.assertEqual(len(governance_rules.write_metrics(directory, judgments)), 2)
        payload = json.loads((directory / "rule_metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["rules"][0]["passed"], 0)

    def test_cross_asset_conflicts_from_confirmed_rules(self):
        includes = governance_rules.rule_id("check", {"mode": "contains", "text": "结论"})
        excludes = governance_rules.rule_id("check", {"mode": "not_contains", "text": "结论"})
        rules = {"A": {includes: {"kind": "check", "spec": {"mode": "contains", "text": "结论"}}},
                 "B": {excludes: {"kind": "check", "spec": {"mode": "not_contains", "text": "结论"}}}}
        findings = governance_rules.conflict_findings(rules)
        self.assertTrue(any(row["type"] == "mutually_exclusive" for row in findings))
        thresholds = {"A": {"x": {"kind": "check", "spec": {"mode": "max_paragraphs", "max_paragraphs": 3}},
                            "y": {"kind": "check", "spec": {"mode": "max_paragraphs", "max_paragraphs": 5}}}}
        self.assertTrue(any(row["type"] == "inconsistent_threshold"
                            for row in governance_rules.conflict_findings(thresholds)))

    def test_historic_scan_uses_saved_replies_only(self):
        source_rows = [{"sample_id": "s1", "original_response": "结论：A\n依据：B"},
                       {"sample_id": "s2", "original_response": "缺少要点"}]
        selected = [{"sample_id": "s1", "task_user": "写报告", "requirements": ["先给结论"],
                     "checks": [{"mode": "contains", "text": "结论"}]},
                    {"sample_id": "s2", "task_user": "写报告", "requirements": ["先给结论"],
                     "checks": [{"mode": "contains", "text": "结论"}]}]
        result = asyncio.run(online_skill_eval._historic_scan(source_rows, selected))
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["compliant"], 1)
        self.assertEqual(result["semantic_skipped"], 2)
        self.assertEqual(result["rows"][0]["response_source"], "saved_historical_reply")
        self.assertIn("never used for version promotion", result["scope"])

    def test_rule_level_mutation_changes_only_one_substring(self):
        snapshot = {"instructions": "# Goal\n\n- 先给结论\n- 保持简洁\n"}
        metrics = [{"rule_id": "requirement:abc", "label": "先给结论", "pass_rate": 0.1,
                    "total": 10, "passed": 1, "failed_samples": [], "variants": [], "splits": []}]

        async def query(system, user, **kwargs):
            return json.dumps({"target_rule_id": "requirement:abc", "old_text": "先给结论",
                               "new_text": "先给结论，再列三条依据"}, ensure_ascii=False)

        result = asyncio.run(online_skill_eval._rule_level_mutations(snapshot, query, metrics))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["instructions"], "# Goal\n\n- 先给结论，再列三条依据\n- 保持简洁\n")
        self.assertEqual(result[0]["mutation_mode"], "rule_level")

    def test_invalid_targeted_mutation_is_rejected(self):
        metrics = [{"rule_id": "requirement:abc", "label": "先给结论", "pass_rate": 0.0,
                    "total": 2, "passed": 0, "failed_samples": [], "variants": [], "splits": []}]

        async def query(system, user, **kwargs):
            return json.dumps({"target_rule_id": "requirement:abc", "old_text": "原文里没有",
                               "new_text": "改写"}, ensure_ascii=False)

        with self.assertRaises(online_skill_eval.EvaluationError):
            asyncio.run(online_skill_eval._rule_level_mutations(
                {"instructions": "# Goal\n\n先给结论\n"}, query, metrics))

    def test_formatter_shows_worst_rule_and_historic_scan(self):
        report = {"budget": {"model_calls": 0, "max_calls": 80, "cache_hits": 0},
                  "skills": [{"skill": "论文格式", "status": "watch", "reasons": ["ok"],
                              "replay": {"count": 1, "mutate_dev": 1, "promotion_test": 0,
                                         "review_required": 0, "review_file": "/tmp/r.jsonl"},
                              "ruleMetrics": [{"label": "先给结论", "pass_rate": 0.25,
                                               "passed": 1, "total": 4}],
                              "historicScan": {"scanned": 2, "compliant": 1, "semantic_skipped": 2}}]}
        text = online_skill_eval.format_online_skill_eval(report)
        self.assertIn("worst_rule", text)
        self.assertIn("25% (1/4)", text)
        self.assertIn("historic_scan", text)
        self.assertIn("不计入胜负", text)

    def test_mutation_mode_is_validated(self):
        with self.assertRaises(ValueError):
            asyncio.run(online_skill_eval.evaluate_online_skill_evolution_async(
                write_report=False, write_artifacts=False, mutation_mode="anything"))


if __name__ == "__main__":
    unittest.main()
