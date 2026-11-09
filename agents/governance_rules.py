"""Stable rule ids, human-confirmed rule compilation and per-rule metrics.

Rule ids are derived from the *content of human-reviewed acceptance criteria*,
not from keywords in Skill text. That keeps them stable across runs without
migrating the existing replay_review.jsonl schema (review_schema stays 3).
Model output is only ever staged; nothing becomes an active rule until a
reviewer calls confirm_rules().
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import governance_eval
from .storage import atomic_write_text, state_lock

SCHEMA_VERSION = 1
KINDS = ("check", "requirement")


def rules_dir(lineage: str) -> Path:
    return governance_eval.governance_root() / "rules" / lineage


def _stable(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize(kind: str, spec: Any) -> dict[str, Any]:
    if kind == "requirement":
        text = spec if isinstance(spec, str) else str((spec or {}).get("text") or "")
        required = True if isinstance(spec, str) else bool((spec or {}).get("required", True))
        return {"text": text.strip(), "required": required}
    spec = spec if isinstance(spec, dict) else {}
    normalized = {"mode": str(spec.get("mode") or "")}
    for key in ("text", "max_paragraphs", "schema"):
        if key in spec:
            normalized[key] = spec[key]
    return normalized


def rule_id(kind: str, spec: Any) -> str:
    return f"{kind}:{_stable({'kind': kind, 'spec': normalize(kind, spec)})[:12]}"


def label_for(kind: str, spec: Any) -> str:
    value = normalize(kind, spec)
    if kind == "requirement":
        return value["text"] or "(空要求)"
    mode = value.get("mode", "")
    detail = value.get("text") or value.get("max_paragraphs") or ("schema" if "schema" in value else "")
    return f"{mode}: {detail}" if detail else mode


def _valid_check(spec: Any) -> bool:
    if not isinstance(spec, dict) or spec.get("mode") not in governance_eval.CHECK_MODES:
        return False
    mode = spec["mode"]
    if mode == "max_paragraphs":
        return type(spec.get("max_paragraphs")) is int and spec["max_paragraphs"] >= 1
    if mode in {"contains", "not_contains"}:
        return bool(str(spec.get("text") or "").strip())
    if mode == "json_schema":
        return isinstance(spec.get("schema"), dict)
    return True


def build_compile_prompt(instructions: str) -> str:
    return ("Extract explicit acceptance criteria from this Skill text. Never infer hard rules "
            "from keywords. Return strict JSON: {\"checks\":[...], \"requirements\":[...]}. "
            "checks use mode json_parseable|json_schema|max_paragraphs|contains|not_contains "
            "with the matching field; requirements are strings or {\"text\":..., \"required\":bool}. "
            "Only include criteria the text actually states.\n\n" + instructions)


def stage_from_model_output(lineage: str, raw: str, *, source: str = "model") -> dict[str, Any]:
    """Stage model-proposed criteria for human review. No keyword heuristics."""
    try:
        payload = json.loads(raw)
        checks = payload.get("checks") or []
        requirements = payload.get("requirements") or []
        if not isinstance(payload, dict) or not isinstance(checks, list) or not isinstance(requirements, list):
            raise ValueError("checks/requirements must be arrays")
    except (ValueError, TypeError, AttributeError) as exc:
        return {"ok": False, "error": f"模型输出不是合法候选规则: {exc}"}
    if len(checks) + len(requirements) > 24:
        return {"ok": False, "error": "候选规则过多（上限 24 条），请拆批审核。"}
    invalid = [item for item in checks if not _valid_check(item)]
    if invalid:
        return {"ok": False, "error": "候选硬检查含不支持的模式或缺少参数。"}
    for item in requirements:
        if not ((isinstance(item, str) and item.strip())
                or (isinstance(item, dict) and str(item.get("text") or "").strip()
                    and type(item.get("required", True)) is bool)):
            return {"ok": False, "error": "候选语义要求格式不合法。"}
    entries = []
    for kind, values in (("check", checks), ("requirement", requirements)):
        for spec in values:
            entries.append({"rule_id": rule_id(kind, spec), "kind": kind,
                            "spec": normalize(kind, spec), "label": label_for(kind, spec),
                            "hard": kind == "check", "required": normalize(kind, spec).get("required", True),
                            "source": source, "confirmed_by": "", "staged_at": governance_eval._utc_now()})
    directory = rules_dir(lineage)
    directory.mkdir(parents=True, exist_ok=True)
    queue_path = directory / "review_queue.jsonl"
    with state_lock(queue_path):
        existing = {}
        try:
            for line in queue_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    existing[row["rule_id"]] = row
        except (OSError, ValueError):
            existing = {}
        for entry in entries:
            existing.setdefault(entry["rule_id"], entry)
        atomic_write_text(queue_path, "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in existing.values()))
    active = load_rules(lineage)
    return {"ok": True, "staged": len(entries),
            "pending": [row for row in existing.values() if row["rule_id"] not in active]}


def _read_registry(lineage: str) -> dict[str, dict[str, Any]]:
    path = rules_dir(lineage) / "rules.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value.get("rules", {}) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def confirm_rules(lineage: str, rule_ids: list[str], reviewer: str) -> dict[str, Any]:
    """The only way a compiled rule becomes active."""
    reviewer = str(reviewer or "").strip()
    if not reviewer:
        return {"ok": False, "error": "确认人不能为空。"}
    directory = rules_dir(lineage)
    queue_path = directory / "review_queue.jsonl"
    registry_path = directory / "rules.json"
    with state_lock(registry_path):
        pending = {}
        try:
            for line in queue_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    pending[row["rule_id"]] = row
        except (OSError, ValueError):
            pending = {}
        missing = [value for value in rule_ids if value not in pending]
        if missing:
            return {"ok": False, "error": "待确认规则不存在: " + ", ".join(missing[:5])}
        rules = _read_registry(lineage)
        for value in rule_ids:
            entry = dict(pending.pop(value))
            entry.update(confirmed_by=reviewer, confirmed_at=governance_eval._utc_now())
            rules[value] = entry
        atomic_write_text(registry_path, json.dumps(
            {"schema_version": SCHEMA_VERSION, "lineage": lineage, "rules": rules},
            ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(queue_path, "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in pending.values()))
    return {"ok": True, "confirmed": len(rule_ids), "active": len(rules)}


def load_rules(lineage: str) -> dict[str, dict[str, Any]]:
    return _read_registry(lineage)


def pending_rules(lineage: str) -> list[dict[str, Any]]:
    path = rules_dir(lineage) / "review_queue.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    active = load_rules(lineage)
    return [row for row in rows if row["rule_id"] not in active]


def aggregate(judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-rule pass rate. Sorted worst-first for rule-level mutation."""
    metrics: dict[str, dict[str, Any]] = {}
    for row in judgments:
        identifier = str(row.get("rule_id") or "")
        if not identifier:
            continue
        item = metrics.setdefault(identifier, {
            "rule_id": identifier, "label": str(row.get("rule_label") or identifier),
            "hard": bool(row.get("hard")), "required": bool(row.get("required", True)),
            "total": 0, "passed": 0, "failed_samples": [], "variants": set(), "splits": set()})
        item["total"] += 1
        item["passed"] += 1 if row.get("passed") else 0
        if not row.get("passed"):
            item["failed_samples"].append(str(row.get("sample_id") or ""))
        if row.get("variant_id"):
            item["variants"].add(str(row["variant_id"]))
        if row.get("split"):
            item["splits"].add(str(row["split"]))
    for item in metrics.values():
        item["pass_rate"] = item["passed"] / item["total"] if item["total"] else 0.0
        item["variants"] = sorted(item["variants"])
        item["splits"] = sorted(item["splits"])
    return sorted(metrics.values(), key=lambda value: (value["pass_rate"], -value["total"], value["rule_id"]))


def write_metrics(run_dir: Path, judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = aggregate(judgments)
    atomic_write_text(run_dir / "rule_metrics.json", json.dumps(
        {"schema_version": SCHEMA_VERSION, "rules": metrics}, ensure_ascii=False, indent=2) + "\n")
    return metrics


def conflict_findings(rules_by_asset: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Cross-asset conflicts from confirmed rules, never from raw keyword matching."""
    findings: list[dict[str, Any]] = []
    positives: dict[str, list[str]] = {}
    negatives: dict[str, list[str]] = {}
    for asset, rules in rules_by_asset.items():
        for rule in rules.values():
            spec = rule.get("spec") or {}
            if rule.get("kind") != "check" or spec.get("mode") not in {"contains", "not_contains"}:
                continue
            text = str(spec.get("text") or "").strip()
            (positives if spec["mode"] == "contains" else negatives).setdefault(text, []).append(asset)
    for text, assets in positives.items():
        if text in negatives:
            findings.append({"type": "mutually_exclusive", "text": text,
                             "assets": sorted(set(assets + negatives[text])),
                             "message": f"不同规则对同一文本「{text[:40]}」同时要求出现与不出现"})
    for asset, rules in rules_by_asset.items():
        thresholds = {str(rule.get("spec", {}).get("max_paragraphs"))
                      for rule in rules.values()
                      if rule.get("kind") == "check" and rule.get("spec", {}).get("mode") == "max_paragraphs"}
        if len(thresholds) > 1:
            findings.append({"type": "inconsistent_threshold", "assets": [asset],
                             "text": ", ".join(sorted(thresholds)),
                             "message": "同一资产声明了多个段落数上限"})
    return findings
