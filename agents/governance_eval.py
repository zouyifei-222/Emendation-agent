"""Frozen evaluation boundary and decision ledger for Prompt / SOP governance.

Reused data contracts (both projects are MIT licensed; no source code is copied):
  * Tencent/SkillHone, docs/benchmark-optimization.md -- probe / pr_val / test
    splits, an Eval side invisible to the optimizer, fingerprint pinning at
    registration, layered gates, review-by-default and one final held-out run.
  * Xerus-ai/skillops, README -- Draft/Review/Active/Deprecated/Archived
    lifecycle, SKL lint categories, and "deprecated needs a migration path".

The checks and storage below are BearCode's own Python implementations of those
contracts. SkillOps' 24 lint rules and SkillHone's TypeScript CLI are not
importable into this single-process Python runtime, so only the contracts are
ported. See docs/Prompt资产治理平台改造计划.md for attribution.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .skill_evolution import get_evolution_dir
from .storage import atomic_write_text, state_lock

HOLDOUT_SCHEMA_VERSION = 1
SPLITS = ("probe", "pr_val", "test")
LIFECYCLE_STATES = ("draft", "review", "active", "deprecated", "archived")
LINT_CATEGORIES = ("frontmatter", "structure", "links", "governance")
CHECK_MODES = {"json_parseable", "json_schema", "max_paragraphs", "contains", "not_contains"}
DEFAULT_MIN_IMPROVEMENT = 0.02
DEFAULT_MAX_PR_VAL_REGRESSION = 0.02
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff ]{1,120}$")
DECISIONS_FILE = "decisions.jsonl"


def governance_root() -> Path:
    return get_evolution_dir().parent / "governance"


def lineage(name: str) -> str:
    # Must match online_skill_eval._lineage_id_for_skill so both sides address
    # the same asset. Verified by tests/test_governance_eval.py.
    digest = hashlib.sha1(json.dumps({"name": name.strip()}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return "skill-" + digest[:16]


def dataset_dir(name: str) -> Path:
    return governance_root() / "holdout" / lineage(name)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("dataset rows must be JSON objects")
            rows.append(row)
    return rows


def task_key(row: dict[str, Any]) -> str:
    return _stable_hash([str(row.get("task_user") or ""), row.get("context_messages") or []])


def _validate_rows(split: str, rows: list[dict[str, Any]], errors: list[str]) -> list[dict[str, Any]]:
    required = ("id", "task_user")
    seen_ids: set[str] = set()
    seen_tasks: set[str] = set()
    samples = []
    for index, row in enumerate(rows):
        missing = [key for key in required if not str(row.get(key) or "").strip()]
        if missing:
            errors.append(f"{split}.jsonl 第 {index + 1} 行缺少 {', '.join(missing)}")
            continue
        if row.get("never_trained") is not True:
            errors.append(f"{split}.jsonl 第 {index + 1} 行必须标记 never_trained=true")
            continue
        if not str(row.get("reviewed_by") or "").strip():
            errors.append(f"{split}.jsonl 第 {index + 1} 行缺少人工审核者 reviewed_by")
            continue
        requirements = row.get("requirements") or []
        checks = row.get("checks") or []
        if not isinstance(requirements, list) or not isinstance(checks, list) or (not requirements and not checks):
            errors.append(f"{split}.jsonl 第 {index + 1} 行必须提供验收条件")
            continue
        invalid_check = False
        for check in checks:
            if not isinstance(check, dict) or check.get("mode") not in CHECK_MODES:
                invalid_check = True
                break
            mode = check["mode"]
            if (mode == "max_paragraphs"
                    and (type(check.get("max_paragraphs")) is not int or check["max_paragraphs"] < 1)):
                invalid_check = True
                break
            if mode in {"contains", "not_contains"} and not str(check.get("text") or "").strip():
                invalid_check = True
                break
            if mode == "json_schema" and not isinstance(check.get("schema"), dict):
                invalid_check = True
                break
        if invalid_check:
            errors.append(f"{split}.jsonl 第 {index + 1} 行含不支持或不完整的硬检查")
            continue
        identifier = str(row["id"])
        key = task_key(row)
        if identifier in seen_ids or key in seen_tasks:
            errors.append(f"{split}.jsonl 第 {index + 1} 行与同组题目重复")
            continue
        seen_ids.add(identifier)
        seen_tasks.add(key)
        samples.append({
            "sample_id": f"frozen:{split}:{identifier}", "group_id": f"frozen-{split}",
            "split": f"frozen_{split}", "evaluation_mode": "original_task",
            "task_user": str(row["task_user"]), "context_messages": row.get("context_messages") or [],
            "requirements": requirements, "checks": checks,
            "source_hash": _stable_hash(row), "never_trained": True,
            "reviewed_by": str(row["reviewed_by"]), "frozen_at": str(row.get("frozen_at") or ""),
            "_task_key": key,
        })
    return samples


def _cross_split_errors(splits: dict[str, list[dict[str, Any]]], errors: list[str]) -> None:
    seen: dict[str, str] = {}
    for split, samples in splits.items():
        for sample in samples:
            key = sample["_task_key"]
            if key in seen and seen[key] != split:
                errors.append(f"冻结题在 {seen[key]} 与 {split} 之间重复，存在数据泄漏")
            seen[key] = split


def _training_errors(splits: dict[str, list[dict[str, Any]]], training_samples: list[dict[str, Any]] | None,
                     errors: list[str]) -> None:
    if not training_samples:
        return
    trained = {task_key(row) for row in training_samples if isinstance(row, dict)}
    for split, samples in splits.items():
        for sample in samples:
            if sample["_task_key"] in trained:
                errors.append(f"{split} 中的冻结题参与过规则学习，本轮结果作废")


def freeze_dataset(name: str, *, training_samples: list[dict[str, Any]] | None = None,
                   actor: str = "web-governance") -> dict[str, Any]:
    """Pin a reviewed dataset once. Any later edit invalidates the campaign."""
    directory = dataset_dir(name)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        return {"ok": False, "errors": ["数据集已冻结；修改题目需新建目录并重新冻结。"]}
    raw: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for split in SPLITS:
        path = directory / f"{split}.jsonl"
        if not path.is_file():
            if split == "probe":
                errors.append("缺少必需的 probe.jsonl")
            continue
        try:
            raw[split] = _read_jsonl(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{split}.jsonl 读取失败: {exc}")
    if errors:
        return {"ok": False, "errors": errors}
    splits = {split: _validate_rows(split, rows, errors) for split, rows in raw.items()}
    _cross_split_errors(splits, errors)
    _training_errors(splits, training_samples, errors)
    if errors:
        return {"ok": False, "errors": errors}
    manifest = {"schema_version": HOLDOUT_SCHEMA_VERSION, "lineage": lineage(name),
                "skill": name, "frozen_at": _utc_now(), "frozen_by": actor,
                "fingerprints": {split: _stable_hash(rows) for split, rows in raw.items()},
                "counts": {split: len(rows) for split, rows in raw.items()},
                "splits": list(raw), "reviewers": sorted({
                    str(row.get("reviewed_by")) for rows in raw.values() for row in rows})}
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "manifest": manifest, "file": str(manifest_path)}


def load_frozen_dataset(name: str, *, training_samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Load pinned splits, refusing anything that drifted since registration."""
    directory = dataset_dir(name)
    manifest = _read_json(directory / "manifest.json", None)
    if not isinstance(manifest, dict):
        return {"ok": False, "errors": ["数据集尚未冻结，请先运行 freeze_dataset。"], "splits": {}}
    errors: list[str] = []
    if manifest.get("schema_version") != HOLDOUT_SCHEMA_VERSION:
        errors.append("冻结数据集版本不兼容")
    fingerprints = manifest.get("fingerprints") or {}
    raw: dict[str, list[dict[str, Any]]] = {}
    for split, expected in fingerprints.items():
        if split not in SPLITS:
            errors.append(f"未知的冻结分组: {split}")
            continue
        try:
            rows = _read_jsonl(directory / f"{split}.jsonl")
        except (OSError, ValueError) as exc:
            errors.append(f"{split}.jsonl 读取失败: {exc}")
            continue
        if _stable_hash(rows) != expected:
            errors.append(f"{split}.jsonl 在冻结后被修改，本轮结果作废")
            continue
        raw[split] = rows
    if errors:
        return {"ok": False, "errors": errors, "splits": {}}
    splits = {split: _validate_rows(split, rows, errors) for split, rows in raw.items()}
    _cross_split_errors(splits, errors)
    _training_errors(splits, training_samples, errors)
    if errors:
        return {"ok": False, "errors": errors, "splits": {}}
    return {"ok": True, "errors": [], "splits": splits, "manifest": manifest}


def campaign_id(manifest: dict[str, Any]) -> str:
    return _stable_hash(manifest.get("fingerprints") or {})


def optimizer_view(failures: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate, redacted feedback: no task text, gold answers or held-out data."""
    rules: Counter = Counter()
    for failure in failures:
        for rule in failure.get("failed_rules", []) or []:
            if isinstance(rule, dict):
                rules[str(rule.get("rule_id") or "unknown")] += 1
    return {"failed_count": len(failures), "failed_rules": dict(rules),
            "excludes": ["task_user", "gold", "verifier", "pr_val", "test"]}


def layered_gates(*, baseline_probe: dict[str, Any] | None, best_probe: dict[str, Any] | None,
                  baseline_pr_val: dict[str, Any] | None = None, best_pr_val: dict[str, Any] | None = None,
                  min_improvement: float = DEFAULT_MIN_IMPROVEMENT,
                  max_pr_val_regression: float = DEFAULT_MAX_PR_VAL_REGRESSION,
                  hard_failures: int = 0, required_failures: int = 0,
                  usage_eligible: bool = True, changed: bool = True) -> dict[str, Any]:
    """SkillHone-style gates: probe gain first, then PR validation, never automerge."""
    base = (baseline_probe or {}).get("average_score")
    best = (best_probe or {}).get("average_score")
    probe_gate = (base is not None and best is not None and changed
                  and best >= base + min_improvement)
    pr_base = (baseline_pr_val or {}).get("average_score")
    pr_best = (best_pr_val or {}).get("average_score")
    pr_val_gate: bool | None = None
    if pr_base is not None and pr_best is not None:
        pr_val_gate = pr_best >= pr_base - max_pr_val_regression
    gates = {"probe_gate": probe_gate, "pr_val_gate": pr_val_gate,
             "hard_gate": hard_failures == 0 and required_failures == 0,
             "usage_gate": bool(usage_eligible)}
    promoted = all(value is True for value in gates.values())
    return {"gates": gates, "promoted": promoted, "status": "gates_passed" if promoted else "gates_not_met",
            "automerge": False, "review_required": True,
            "thresholds": {"min_improvement": min_improvement,
                           "max_pr_val_regression": max_pr_val_regression},
            "reason": "冻结题门槛全部通过，等待人工发布" if promoted else
                      "未通过冻结题门槛；不得据此改写或发布"}


def final_test_consumed(name: str, manifest: dict[str, Any]) -> bool:
    state = _read_json(dataset_dir(name) / "test_consumed.json", {})
    return isinstance(state, dict) and state.get("campaign") == campaign_id(manifest)


def claim_final_test(name: str, manifest: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    """Allow exactly one held-out measurement per frozen campaign."""
    directory = dataset_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "test_consumed.json"
    with state_lock(path):
        if final_test_consumed(name, manifest):
            return {"ok": False, "error": "本轮冻结数据集的 test 已经跑过一次，不能据此再改规则。"}
        atomic_write_text(path, json.dumps({"campaign": campaign_id(manifest), "run_id": run_id,
                                            "time": _utc_now()}, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "campaign": campaign_id(manifest)}


def recommendation(*, gates: dict[str, Any] | None = None, review_required: int = 0,
                   incomplete: bool = False, frozen_loaded: bool = False) -> dict[str, Any]:
    if incomplete:
        label, reason = "数据不足，先别动", "本轮评测未完成或模型调用失败"
    elif review_required or gates is None or not frozen_loaded:
        label, reason = "数据不足，先别动", "缺少人工审核样本或冻结题集"
    elif gates.get("promoted"):
        label, reason = "建议采纳", "回归与冻结题门槛均通过，仍需人工发布才生效"
    else:
        label, reason = "建议保留当前版", "冻结题门槛未通过或提升不足"
    return {"label": label, "reason": reason, "automerge": False,
            "evidence": {"gates": (gates or {}).get("gates", {}), "review_required": review_required}}


def record_decision(name: str, event: str, *, actor: str = "web-governance",
                    evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """One readable record per decision: the local equivalent of an Issue/PR trail."""
    root = governance_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / DECISIONS_FILE
    row = {"time": _utc_now(), "skill": name, "event": event, "actor": actor,
           "evidence": evidence or {}}
    with state_lock(path):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_decisions(name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    path = governance_root() / DECISIONS_FILE
    try:
        rows = _read_jsonl(path)
    except (OSError, ValueError):
        return []
    rows = [row for row in rows if name is None or row.get("skill") == name]
    return list(reversed(rows))[:max(0, limit)]


LINT_RULES = {
    "SKL101": ("frontmatter", "规则名称"),
    "SKL102": ("frontmatter", "规则描述"),
    "SKL103": ("frontmatter", "规则正文"),
    "SKL201": ("structure", "标题结构"),
    "SKL301": ("links", "外部链接与绝对路径"),
    "SKL506": ("governance", "生命周期状态"),
    "SKL507": ("governance", "废弃/归档需迁移路径"),
}


def lint_skill(*, name: str, description: str, instructions: str,
               lifecycle: str = "active", migration_path: str = "") -> list[dict[str, Any]]:
    """BearCode checks following SkillOps' SKL categories and lifecycle rules."""
    findings: list[dict[str, Any]] = []

    def add(rule_id: str, message: str, severity: str = "warning") -> None:
        category, title = LINT_RULES[rule_id]
        findings.append({"rule_id": rule_id, "category": category, "title": title,
                         "severity": severity, "message": message})

    if not NAME_RE.fullmatch(str(name or "").strip()) or str(name or "").startswith((".", "-")):
        add("SKL101", "规则名称含非法字符或为空")
    if not str(description or "").strip():
        add("SKL102", "缺少规则描述，检索时无法判断适用场景")
    if not str(instructions or "").strip():
        add("SKL103", "规则正文为空")
    if instructions and not re.search(r"^\s{0,3}#{1,3}\s+\S", instructions, re.MULTILINE):
        add("SKL201", "正文缺少标题结构，建议分节书写")
    if re.search(r"https?://|[A-Za-z]:[\\/]|\b/etc/", instructions or ""):
        add("SKL301", "正文引用外部链接或绝对路径，可能不可复现", "info")
    if lifecycle not in LIFECYCLE_STATES:
        add("SKL506", f"生命周期状态必须是 {'/'.join(LIFECYCLE_STATES)} 之一")
    if lifecycle in {"deprecated", "archived"} and not str(migration_path or "").strip():
        add("SKL507", "废弃或归档规则必须写明迁移路径")
    return findings
