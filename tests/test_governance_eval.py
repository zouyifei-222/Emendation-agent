from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from agents import governance, governance_eval, online_skill_eval, skills


def row(identifier: str, task: str) -> dict:
    return {"id": identifier, "task_user": task, "context_messages": [],
            "requirements": ["先给结论"], "checks": [{"mode": "contains", "text": "结论"}],
            "never_trained": True, "reviewed_by": "tester", "frozen_at": "2026-11-08"}


class FrozenBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        skills.reset_skill_cache()
        self.name = "论文格式"

    def write_split(self, split: str, rows: list[dict]) -> None:
        directory = governance_eval.dataset_dir(self.name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{split}.jsonl").write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in rows), encoding="utf-8")

    def freeze(self, *, training=None):
        self.write_split("probe", [row("p1", "写报告 A")])
        self.write_split("pr_val", [row("v1", "写报告 B")])
        self.write_split("test", [row("t1", "写报告 C")])
        return governance_eval.freeze_dataset(self.name, training_samples=training)

    def test_lineage_matches_online_eval(self):
        self.assertEqual(governance_eval.lineage(self.name),
                         online_skill_eval._lineage_id_for_skill(self.name))

    def test_freeze_and_load_produce_evaluator_compatible_samples(self):
        frozen = self.freeze()
        self.assertTrue(frozen["ok"], frozen)
        loaded = governance_eval.load_frozen_dataset(self.name)
        self.assertTrue(loaded["ok"], loaded["errors"])
        self.assertEqual(sorted(loaded["splits"]), ["pr_val", "probe", "test"])
        sample = loaded["splits"]["probe"][0]
        for key in ("sample_id", "split", "evaluation_mode", "task_user",
                    "context_messages", "requirements", "checks", "source_hash"):
            self.assertIn(key, sample)
        self.assertEqual(sample["split"], "frozen_probe")
        self.assertTrue(sample["never_trained"])

    def test_any_edit_after_freezing_invalidates_the_campaign(self):
        self.assertTrue(self.freeze()["ok"])
        self.write_split("probe", [row("p1", "写报告 A（被改过）")])
        loaded = governance_eval.load_frozen_dataset(self.name)
        self.assertFalse(loaded["ok"])
        self.assertTrue(any("被修改" in error for error in loaded["errors"]))
        self.assertTrue(governance_eval.freeze_dataset(self.name)["errors"])

    def test_leakage_between_splits_or_training_is_rejected(self):
        self.write_split("probe", [row("p1", "同一道题")])
        self.write_split("pr_val", [row("v1", "同一道题")])
        result = governance_eval.freeze_dataset(self.name)
        self.assertFalse(result["ok"])
        self.assertTrue(any("泄漏" in error for error in result["errors"]))

        self.assertTrue(self.freeze()["ok"])
        loaded = governance_eval.load_frozen_dataset(
            self.name, training_samples=[{"task_user": "写报告 A", "context_messages": []}])
        self.assertFalse(loaded["ok"])
        self.assertTrue(any("参与过规则学习" in error for error in loaded["errors"]))

    def test_unreviewed_or_trained_rows_are_refused(self):
        self.write_split("probe", [{"id": "p1", "task_user": "A", "requirements": ["x"],
                                    "checks": [], "never_trained": False, "reviewed_by": "tester"}])
        self.assertFalse(governance_eval.freeze_dataset(self.name)["ok"])
        (governance_eval.dataset_dir(self.name) / "probe.jsonl").write_text(
            json.dumps({"id": "p1", "task_user": "A", "requirements": ["x"], "checks": [],
                        "never_trained": True, "reviewed_by": ""}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        self.assertFalse(governance_eval.freeze_dataset(self.name)["ok"])

    def test_optimizer_view_never_contains_task_or_gold_text(self):
        view = governance_eval.optimizer_view([
            {"task_user": "机密任务文本", "failed_rules": [{"rule_id": "req:1"}, {"rule_id": "req:1"}]},
            {"task_user": "另一道题", "failed_rules": [{"rule_id": "check:0"}]}])
        self.assertEqual(view["failed_rules"], {"req:1": 2, "check:0": 1})
        self.assertNotIn("机密任务文本", json.dumps(view, ensure_ascii=False))
        self.assertIn("pr_val", view["excludes"])

    def test_layered_gates_require_probe_gain_and_no_pr_val_regression(self):
        base = {"average_score": 0.50}
        gain = {"average_score": 0.55}
        weak = {"average_score": 0.51}
        regressed = {"average_score": 0.40}
        kwargs = {"hard_failures": 0, "required_failures": 0, "usage_eligible": True, "changed": True}
        passing = governance_eval.layered_gates(baseline_probe=base, best_probe=gain,
                                                baseline_pr_val=base, best_pr_val=weak, **kwargs)
        self.assertTrue(passing["promoted"])
        self.assertFalse(passing["automerge"])
        self.assertTrue(passing["review_required"])
        self.assertFalse(governance_eval.layered_gates(
            baseline_probe=base, best_probe=weak, baseline_pr_val=base, best_pr_val=gain, **kwargs)["promoted"])
        self.assertFalse(governance_eval.layered_gates(
            baseline_probe=base, best_probe=gain, baseline_pr_val=base, best_pr_val=regressed, **kwargs)["promoted"])
        missing = governance_eval.layered_gates(baseline_probe=base, best_probe=gain, **kwargs)
        self.assertIsNone(missing["gates"]["pr_val_gate"])
        self.assertFalse(missing["promoted"])
        self.assertFalse(governance_eval.layered_gates(
            baseline_probe=base, best_probe=gain, baseline_pr_val=base, best_pr_val=gain,
            hard_failures=1, required_failures=0, usage_eligible=True, changed=True)["promoted"])

    def test_final_heldout_measurement_runs_once_per_frozen_campaign(self):
        frozen = self.freeze()
        self.assertTrue(frozen["ok"])
        manifest = frozen["manifest"]
        self.assertFalse(governance_eval.final_test_consumed(self.name, manifest))
        self.assertTrue(governance_eval.claim_final_test(self.name, manifest, "run-1")["ok"])
        second = governance_eval.claim_final_test(self.name, manifest, "run-2")
        self.assertFalse(second["ok"])
        self.assertIn("一次", second["error"])
        changed = dict(manifest, fingerprints={"probe": "new-campaign"})
        self.assertTrue(governance_eval.claim_final_test(self.name, changed, "run-3")["ok"])

    def test_recommendation_has_three_states_and_never_autopublishes(self):
        self.assertEqual(governance_eval.recommendation(frozen_loaded=False)["label"], "数据不足，先别动")
        self.assertEqual(governance_eval.recommendation(gates=None, review_required=2,
                                                       frozen_loaded=True)["label"], "数据不足，先别动")
        self.assertEqual(governance_eval.recommendation(
            gates={"promoted": True}, frozen_loaded=True)["label"], "建议采纳")
        kept = governance_eval.recommendation(gates={"promoted": False}, frozen_loaded=True)
        self.assertEqual(kept["label"], "建议保留当前版")
        self.assertFalse(kept["automerge"])

    def test_decision_ledger_and_lint_rules(self):
        governance_eval.record_decision(self.name, "edit", evidence={"rationale": "补充要求"})
        governance_eval.record_decision("另一条规则", "archive")
        rows = governance_eval.read_decisions(self.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "edit")
        findings = governance_eval.lint_skill(name="", description="", instructions="没有标题",
                                              lifecycle="archived")
        codes = {item["rule_id"] for item in findings}
        self.assertTrue({"SKL101", "SKL102", "SKL201", "SKL507"} <= codes)
        clean = governance_eval.lint_skill(name="论文格式", description="团队文档规则",
                                           instructions="# Goal\n\n先给结论。", lifecycle="active")
        self.assertFalse([item for item in clean if item["severity"] == "warning"])

    def test_default_evaluation_is_unchanged_and_skill_filter_needs_no_model(self):
        self.assertTrue(governance.save_asset({
            "mode": "new", "name": self.name, "description": "团队文档规则",
            "instructions": "# Goal\n\n先给结论。", "target": "project"})["ok"])
        skills.reset_skill_cache()
        report = asyncio.run(online_skill_eval.evaluate_online_skill_evolution_async(
            write_report=False, write_artifacts=False))
        self.assertFalse(report["frozen"]["enabled"])
        self.assertEqual(report["budget"]["model_calls"], 0)
        names = [item["skill"] for item in report["skills"]]
        self.assertIn(self.name, names)
        filtered = asyncio.run(online_skill_eval.evaluate_online_skill_evolution_async(
            write_report=False, write_artifacts=False, skill="不存在的规则", frozen=True))
        self.assertEqual(filtered["skills"], [])
        self.assertTrue(filtered["frozen"]["enabled"])
        decisions = governance_eval.read_decisions(self.name)
        self.assertTrue(any(item["event"] == "create" for item in decisions))

    def test_report_formatter_shows_recommendation_and_frozen_gates(self):
        report = {"budget": {"model_calls": 0, "max_calls": 80, "cache_hits": 0},
                  "skills": [{"skill": self.name, "status": "watch", "reasons": ["ok"],
                              "replay": {"count": 1, "mutate_dev": 1, "promotion_test": 0,
                                         "review_required": 0, "review_file": "/tmp/r.jsonl"},
                              "recommendation": {"label": "建议采纳", "reason": "门槛通过"},
                              "frozen": {"requested": True, "errors": ["数据集尚未冻结"],
                                         "scores": {"probe": {"baseline": 0.40, "selected": 0.78}},
                                         "gates": {"promoted": True, "gates": {"probe_gate": True,
                                                                               "pr_val_gate": True}}}}]}
        text = online_skill_eval.format_online_skill_eval(report)
        self.assertIn("建议采纳", text)
        self.assertIn("frozen_gates", text)
        self.assertIn("automerge=False", text)
        self.assertIn("数据集尚未冻结", text)
        self.assertIn("frozen_probe: baseline=0.4, selected=0.78", text)
        self.assertIn("历史回放=回归表现", text)


if __name__ == "__main__":
    unittest.main()
