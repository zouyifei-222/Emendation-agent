"""Asset registry, lifecycle state machine, review records and conflict detection.

Lifecycle states follow the SkillOps (MIT) contract. Git remains the source of
truth for SKILL.md; this file only records ownership, review and status sidecar
data. No database, no server-side multi-user editing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import governance_eval, governance_rules
from .storage import atomic_write_text, state_lock

SCHEMA_VERSION = 1
TRANSITIONS = {
    "draft": {"review"},
    "review": {"draft", "active"},
    "active": {"review", "deprecated"},
    "deprecated": {"active", "archived"},
    "archived": {"draft"},
}


def registry_path() -> Path:
    return governance_eval.governance_root() / "assets.json"


def _utc_now() -> str:
    return governance_eval._utc_now()


def load_registry() -> dict[str, dict[str, Any]]:
    path = registry_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value.get("assets", {}) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def default_asset(name: str) -> dict[str, Any]:
    return {"name": name, "owner": "", "status": "draft", "scope": "project", "team": "",
            "migration_path": "", "last_review_at": "", "review_history": []}


def get_asset(name: str) -> dict[str, Any]:
    return dict(load_registry().get(name) or default_asset(name))


def _save(assets: dict[str, dict[str, Any]]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(
        {"schema_version": SCHEMA_VERSION, "assets": assets}, ensure_ascii=False, indent=2) + "\n")


def set_status(name: str, status: str, *, actor: str = "web-governance", reason: str = "",
               owner: str = "", team: str = "", scope: str = "") -> dict[str, Any]:
    """Only legal transitions are persisted; every change leaves a review record."""
    if status not in governance_eval.LIFECYCLE_STATES:
        return {"ok": False, "error": "非法的生命周期状态。"}
    path = registry_path()
    with state_lock(path):
        assets = load_registry()
        current = dict(assets.get(name) or default_asset(name))
        previous = current["status"]
        if status != previous and status not in TRANSITIONS.get(previous, set()):
            return {"ok": False, "error": f"不允许从 {previous} 直接切换到 {status}。"}
        if status in {"deprecated", "archived"} and not (reason.strip() or current.get("migration_path")):
            return {"ok": False, "error": "废弃或归档需写明迁移路径。"}
        if owner:
            current["owner"] = owner.strip()
        if team:
            current["team"] = team.strip()
        if scope:
            current["scope"] = scope.strip()
        current["status"] = status
        current["last_review_at"] = _utc_now()
        current["reason"] = reason.strip()[:500]
        current.setdefault("review_history", []).append({
            "time": current["last_review_at"], "actor": actor, "from": previous,
            "to": status, "reason": reason.strip()[:500]})
        assets[name] = current
        _save(assets)
    governance_eval.record_decision(name, "lifecycle",
                                    evidence={"from": previous, "to": status, "reason": reason[:200]})
    return {"ok": True, "asset": current}


def review_asset(name: str, reviewer: str, decision: str, note: str = "") -> dict[str, Any]:
    """Record a human review without implying the asset passed an evaluation."""
    if not reviewer.strip() or decision not in {"approve", "reject", "abstain"}:
        return {"ok": False, "error": "评审人和结论不合法。"}
    path = registry_path()
    with state_lock(path):
        assets = load_registry()
        asset = dict(assets.get(name) or default_asset(name))
        asset.setdefault("review_history", []).append({
            "time": _utc_now(), "actor": reviewer.strip(), "decision": decision,
            "note": note.strip()[:500], "kind": "human_review"})
        asset["last_review_at"] = _utc_now()
        assets[name] = asset
        _save(assets)
    governance_eval.record_decision(name, "human_review",
                                    evidence={"reviewer": reviewer, "decision": decision})
    return {"ok": True, "asset": asset}


def conflicts() -> list[dict[str, Any]]:
    """Cross-asset conflicts from confirmed rules plus obvious duplicate scope."""
    from .skills import discover_skills
    try:
        discovered = discover_skills()
    except Exception:  # a broken skill file must not hide the whole board
        discovered = []
    registry = load_registry()
    names = sorted(set(registry) | {skill.name for skill in discovered})
    rules_by_asset = {name: governance_rules.load_rules(governance_eval.lineage(name)) for name in names}
    findings = governance_rules.conflict_findings(rules_by_asset)
    scopes: dict[str, list[str]] = {}
    for skill in discovered:
        value = str(getattr(skill, "when_to_use", "") or "").strip()
        if value:
            scopes.setdefault(value, []).append(skill.name)
    for value, assets in scopes.items():
        if len(set(assets)) > 1:
            findings.append({"type": "duplicate_scope", "text": value[:120],
                             "assets": sorted(set(assets)),
                             "message": "多条规则声明了完全相同的适用场景，可能互相冲突"})
    return findings


def summary() -> dict[str, Any]:
    registry = load_registry()
    counts: dict[str, int] = {}
    for asset in registry.values():
        state = str(asset.get("status") or "draft")
        counts[state] = counts.get(state, 0) + 1
    return {"registered": len(registry), "by_status": counts,
            "conflicts": len(conflicts()), "file": str(registry_path())}
