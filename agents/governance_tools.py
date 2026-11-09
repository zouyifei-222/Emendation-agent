"""Tool-using SOP evaluation, kept in a separate artifact tree and score scope.

Reuses the paired fold_on/fold_off accounting from agents/eval_folding.py
(READ_ONLY_TOOLS and summarize) so tool-using tasks are never mixed into the
text-only online Skill replay report.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import governance_eval
from .eval_folding import AVAILABLE_CASE_TOOLS, READ_ONLY_TOOLS, summarize
from .storage import atomic_write_text

TEXT_SCOPE_NOTE = ("当前在线评测仅覆盖纯文本任务；工具型 SOP 需单独运行并分目录分口径保存，"
                   "其分数不得与历史回放的回归分数混算。")

Runner = Callable[[dict[str, Any], str, list[str]], Awaitable[dict[str, Any]]]


def tool_runs_root() -> Path:
    return governance_eval.governance_root() / "tool-runs"


def prepare_cases(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert governance samples into paired cases without inventing gold answers."""
    cases = []
    for sample in samples:
        task_id = str(sample.get("sample_id") or sample.get("id") or "")
        if not task_id:
            continue
        cases.append({"task_id": task_id, "task_user": str(sample.get("task_user") or ""),
                      "context_messages": sample.get("context_messages") or [],
                      "answers": sample.get("answers") or [],
                      "mode": "tool_sop", "text_only": False,
                      "required_tools": sample.get("required_tools") or [],
                      "checks": sample.get("checks") or [],
                      "requirements": sample.get("requirements") or []})
    return cases


async def run_tool_sop_eval(name: str, cases: list[dict[str, Any]], runner: Runner,
                            *, write: bool = True) -> dict[str, Any]:
    """Run both conditions per case with read-only tools, then use the paired summary."""
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        task_id = str(case.get("task_id") or case.get("sample_id") or case.get("id") or f"case_{index}")
        prepared = {**case, "task_id": task_id}
        for condition in ("fold_on", "fold_off"):
            allowed = sorted(READ_ONLY_TOOLS if condition == "fold_on" else AVAILABLE_CASE_TOOLS)
            result = await runner(prepared, condition, allowed)
            if not isinstance(result, dict):
                return {"ok": False, "error": "runner 必须返回结构化结果"}
            rows.append({"task_id": task_id, "condition": condition, "allowed_tools": allowed,
                         "status": "completed", "correct": False, "fold_count": 0,
                         "capabilities_verified": True, "main_input_tokens": 0,
                         "main_output_tokens": 0, "side_input_tokens": 0,
                         "side_output_tokens": 0, **result})
    try:
        metrics = summarize(rows)
    except ValueError as exc:
        return {"ok": False, "error": f"配对统计失败: {exc}"}
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    directory = tool_runs_root() / governance_eval.lineage(name) / run_id
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(directory / "config.json", json.dumps(
            {"skill": name, "scope": "tool_sop_only", "conditions": ["fold_on", "fold_off"],
             "read_only_tools": sorted(READ_ONLY_TOOLS), "note": TEXT_SCOPE_NOTE,
             "separate_from": "online_skill_eval/replay_runs"}, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(directory / "results.jsonl", "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        atomic_write_text(directory / "summary.json", json.dumps(
            metrics, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "run_dir": str(directory) if write else "", "summary": metrics,
            "scope": "tool_sop_only; never merged with text replay scores"}
