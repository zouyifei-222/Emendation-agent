"""Team Prompt / SOP asset governance.

Read-only aggregation over the artifacts the Agent already writes, plus
authorized writes that go through the existing skill file helpers so version
snapshots and audit logs stay intact. Nothing here changes the agent loop.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import governance_assets, governance_eval, governance_rules, governance_tools
from .frontmatter import parse_frontmatter
from .skill_candidates import rollback_skill
from .skill_evolution import (
    HISTORY_DIR,
    archive_skill_file,
    evolve_skill_file,
    get_evolution_dir,
    load_skill_stats,
    restore_pruned_skill,
    _safe_skill_slug,
)
from .skills import create_skill, discover_skills, get_skill_by_name, reset_skill_cache

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff ]{1,120}$")
STALE_MIN_RETRIEVED_ENV = "BEAR_GOVERNANCE_STALE_MIN_RETRIEVED"
STALE_MAX_USED_ENV = "BEAR_GOVERNANCE_STALE_MAX_USED"
MAX_INSTRUCTIONS = 100000
ACTOR = "web-governance"


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.environ.get(name, "") or "").strip() or default))
    except ValueError:
        return default


def _validate_name(value: Any) -> str:
    name = str(value or "").strip()
    if not NAME_RE.fullmatch(name) or name.startswith((".", "-")):
        raise ValueError("规则名称只能是中英文、数字、空格、下划线、点或短横线，长度 1–120。")
    return name


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _champions() -> dict[str, Any]:
    registry = _read_json(get_evolution_dir() / "online-eval" / "champions.json", {})
    return registry.get("champions", {}) if isinstance(registry, dict) else {}


def _report() -> dict[str, Any] | None:
    report = _read_json(get_evolution_dir() / "online_eval_report.json", None)
    return report if isinstance(report, dict) else None


def _champion_for(name: str, champions: dict[str, Any]) -> dict[str, Any] | None:
    for lineage, champion in champions.items():
        if not isinstance(champion, dict):
            continue
        snapshot = champion.get("snapshot") or {}
        if isinstance(snapshot, dict) and str(snapshot.get("name") or "").strip() == name:
            return {"lineage": lineage, "scope": champion.get("scope", ""),
                    "runId": champion.get("run_id", ""), "summary": champion.get("summary") or {}}
    return None


def _report_for(name: str, report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    for row in report.get("skills") or []:
        if isinstance(row, dict) and str(row.get("skill") or row.get("name") or "").strip() == name:
            return row
    return None


def _usage(stats: dict[str, Any], name: str) -> dict[str, Any]:
    row = stats.get(name) if isinstance(stats.get(name), dict) else {}
    return {"retrieved": int(row.get("retrieved", 0) or 0),
            "relevant": int(row.get("relevant", 0) or 0),
            "used": int(row.get("used", 0) or 0),
            "pruned": bool(row.get("pruned")),
            "prunedTo": str(row.get("pruned_to") or ""),
            "lastRetrieved": str(row.get("last_retrieved") or ""),
            "lastUsed": str(row.get("last_used") or "")}


def _stale_suggested(usage: dict[str, Any]) -> bool:
    return (not usage["pruned"]
            and usage["retrieved"] >= _int_env(STALE_MIN_RETRIEVED_ENV, 12)
            and usage["used"] <= _int_env(STALE_MAX_USED_ENV, 0))


def _history_for(name: str, limit: int = 12) -> list[dict[str, Any]]:
    path = get_evolution_dir() / HISTORY_DIR / f"{_safe_skill_slug(name)}.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append({key: row.get(key, "") for key in
                         ("event", "time", "actor", "version", "lesson", "rationale")})
    except (OSError, ValueError):
        return rows
    return list(reversed(rows))[:limit]


def _read_body(path: Path) -> tuple[dict[str, Any], str, str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_frontmatter(raw)
    return dict(parsed.meta), str(parsed.body), raw


def list_assets() -> dict[str, Any]:
    """Return one row per published or archived asset, with health and evidence."""
    errors: list[str] = []
    try:
        skills = discover_skills()
    except Exception as exc:  # a broken skill file must not hide the whole board
        skills = []
        errors.append("规则读取失败: " + str(exc)[:300])
    stats = load_skill_stats()
    champions = _champions()
    report = _report()
    registry = governance_assets.load_registry()
    conflicts = governance_assets.conflicts()

    def registry_fields(name: str) -> dict[str, Any]:
        entry = registry.get(name) or governance_assets.default_asset(name)
        return {"status": str(entry.get("status") or "draft"),
                "owner": str(entry.get("owner") or ""),
                "team": str(entry.get("team") or ""),
                "scope": str(entry.get("scope") or "project")}

    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for skill in skills:
        usage = _usage(stats, skill.name)
        assets.append({
            "name": skill.name, "description": skill.description,
            "whenToUse": skill.when_to_use or "", "source": skill.source,
            "skillDir": skill.skill_dir,
            "pruned": False, "usage": usage,
            "staleSuggested": _stale_suggested(usage),
            "champion": _champion_for(skill.name, champions),
            "lastReport": _report_for(skill.name, report),
            "historyCount": len(_history_for(skill.name, limit=1000)),
            **registry_fields(skill.name),
        })
        seen.add(skill.name)
    for name, row in stats.items():
        if not isinstance(row, dict) or not row.get("pruned") or name in seen:
            continue
        usage = _usage(stats, name)
        assets.append({"name": name, "description": str(row.get("description") or ""),
                       "whenToUse": "", "source": str(row.get("source") or ""),
                       "skillDir": str(row.get("skill_dir") or ""),
                       "pruned": True, "usage": usage, "staleSuggested": False,
                       "champion": _champion_for(name, champions),
                       "lastReport": _report_for(name, report),
                       "historyCount": len(_history_for(name, limit=1000)),
                       **registry_fields(name)})
    return {"assets": sorted(assets, key=lambda row: (row["pruned"], row["name"])),
            "staleThreshold": {"minRetrieved": _int_env(STALE_MIN_RETRIEVED_ENV, 12),
                               "maxUsed": _int_env(STALE_MAX_USED_ENV, 0)},
            "reportGeneratedAt": (report or {}).get("generated_at", ""),
            "conflicts": conflicts, "conflictCount": len(conflicts),
            "assetsSummary": {"registered": len(registry), "file": str(governance_assets.registry_path())},
            "toolScopeNote": governance_tools.TEXT_SCOPE_NOTE,
            "errors": errors}


def asset_detail(name: str) -> dict[str, Any] | None:
    """Full text, versions, usage, champion and audit pointers for one asset."""
    name = _validate_name(name)
    skill = get_skill_by_name(name)
    stats = load_skill_stats()
    usage = _usage(stats, name)
    pruned = bool(skill is None and usage["pruned"])
    if skill is None and not pruned:
        return None
    path = Path(skill.skill_dir) / "SKILL.md" if skill else Path(usage["prunedTo"]) / "SKILL.md"
    meta: dict[str, Any] = {}
    body = ""
    raw = ""
    if path.is_file():
        try:
            meta, body, raw = _read_body(path)
        except OSError:
            pass
    return {"name": name, "description": str(meta.get("description") or (skill.description if skill else "")),
            "whenToUse": str(meta.get("when-to-use") or ""),
            "source": skill.source if skill else str((stats.get(name) or {}).get("source") or ""),
            "version": str(meta.get("version") or ""), "instructions": body, "raw": raw,
            "file": str(path), "pruned": pruned, "usage": usage,
            "staleSuggested": _stale_suggested(usage),
            "champion": _champion_for(name, _champions()),
            "lastReport": _report_for(name, _report()),
            "history": _history_for(name),
            "decisions": governance_eval.read_decisions(name),
            "registry": governance_assets.get_asset(name),
            "rules": {"pending": governance_rules.pending_rules(governance_eval.lineage(name)),
                      "active": list(governance_rules.load_rules(governance_eval.lineage(name)).values())},
            "conflicts": [row for row in governance_assets.conflicts()
                          if name in (row.get("assets") or [])],
            "lint": governance_eval.lint_skill(
                name=name, description=str(meta.get("description") or ""), instructions=body,
                lifecycle="archived" if pruned else "active",
                migration_path="治理页可恢复" if pruned else ""),
            "audit": {"history": str(get_evolution_dir() / HISTORY_DIR / f"{_safe_skill_slug(name)}.jsonl"),
                      "usage": str(get_evolution_dir() / "usage.jsonl"),
                      "provenance": str(get_evolution_dir() / "online_skill_provenance.json"),
                      "decisions": str(governance_eval.governance_root() / governance_eval.DECISIONS_FILE),
                      "holdout": str(governance_eval.dataset_dir(name))}}


def save_asset(payload: dict[str, Any]) -> dict[str, Any]:
    """Create or edit one asset. Edits go through the version-snapshot helper."""
    try:
        name = _validate_name(payload.get("name"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    description = str(payload.get("description") or "").strip()
    instructions = str(payload.get("instructions") or "").strip()
    when_to_use = str(payload.get("whenToUse") or "").strip()
    target = str(payload.get("target") or "project").strip().lower()
    rationale = str(payload.get("rationale") or "").strip()[:500]
    if not description:
        return {"ok": False, "error": "请填写规则描述。"}
    if not instructions:
        return {"ok": False, "error": "请填写规则正文。"}
    if len(instructions) > MAX_INSTRUCTIONS:
        return {"ok": False, "error": f"规则正文不能超过 {MAX_INSTRUCTIONS} 字符。"}
    if target not in {"project", "user"}:
        return {"ok": False, "error": "无效的保存位置。"}
    if payload.get("mode") == "edit":
        skill = get_skill_by_name(name)
        if skill is None:
            return {"ok": False, "error": "找不到要编辑的规则。"}
        result = evolve_skill_file(
            skill_name=name, lesson="网页治理：编辑规则正文",
            rationale=rationale or "由治理页面编辑", target="active",
            active_dir=skill.skill_dir, actor=ACTOR, description=description,
            when_to_use=when_to_use, preserve_existing=False,
            # Keep the edited body verbatim; _skill_body would prepend a heading on every save.
            _prepared_body=instructions.rstrip() + "\n",
        )
        if result.get("ok"):
            reset_skill_cache()
    else:
        if get_skill_by_name(name) is not None:
            return {"ok": False, "error": "同名规则已存在，请改用编辑。"}
        result = create_skill(name=name, description=description, instructions=instructions,
                              when_to_use=when_to_use, target=target, actor=ACTOR)
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "保存失败。")}
    file_path = str(result.get("file") or "")
    warnings = governance_eval.lint_skill(name=name, description=description,
                                          instructions=instructions, lifecycle="active")
    governance_eval.record_decision(
        name, "edit" if payload.get("mode") == "edit" else "create",
        evidence={"rationale": rationale, "file": file_path,
                  "lint": [finding["rule_id"] for finding in warnings]})
    return {"ok": True, "name": name, "file": file_path, "warnings": warnings}


def rollback_asset(name: str) -> dict[str, Any]:
    name = _validate_name(name)
    result = rollback_skill(name)
    if result.get("ok"):
        governance_eval.record_decision(name, "rollback")
    return result


def archive_asset(name: str, reason: str = "") -> dict[str, Any]:
    name = _validate_name(name)
    result = archive_skill_file(name, actor=ACTOR, reason=reason)
    if result.get("ok"):
        governance_eval.record_decision(name, "archive", evidence={"reason": reason})
    return result


def restore_asset(name: str) -> dict[str, Any]:
    name = _validate_name(name)
    result = restore_pruned_skill(name)
    if result.get("ok"):
        reset_skill_cache()
        governance_eval.record_decision(name, "restore")
    return result


def stage_rules(name: str, raw: str) -> dict[str, Any]:
    """Stage model-proposed acceptance criteria for human review."""
    name = _validate_name(name)
    result = governance_rules.stage_from_model_output(governance_eval.lineage(name), raw)
    if result.get("ok"):
        governance_eval.record_decision(name, "rules_staged",
                                        evidence={"staged": result.get("staged", 0)})
    return result


def confirm_rules(name: str, rule_ids: list[str], reviewer: str = ACTOR) -> dict[str, Any]:
    name = _validate_name(name)
    if not isinstance(rule_ids, list):
        return {"ok": False, "error": "ruleIds 必须是列表。"}
    result = governance_rules.confirm_rules(governance_eval.lineage(name),
                                            [str(value) for value in rule_ids], reviewer)
    if result.get("ok"):
        governance_eval.record_decision(name, "rules_confirmed",
                                        evidence={"rules": rule_ids, "reviewer": reviewer})
    return result


def asset_rules(name: str) -> dict[str, Any]:
    name = _validate_name(name)
    lineage = governance_eval.lineage(name)
    return {"pending": governance_rules.pending_rules(lineage),
            "active": list(governance_rules.load_rules(lineage).values())}


def set_asset_status(name: str, status: str, *, reason: str = "", owner: str = "",
                     team: str = "", scope: str = "", actor: str = ACTOR) -> dict[str, Any]:
    name = _validate_name(name)
    return governance_assets.set_status(name, str(status), actor=actor, reason=reason,
                                        owner=owner, team=team, scope=scope)


def review_asset(name: str, reviewer: str, decision: str, note: str = "") -> dict[str, Any]:
    name = _validate_name(name)
    return governance_assets.review_asset(name, reviewer, decision, note)


def list_conflicts() -> dict[str, Any]:
    return {"conflicts": governance_assets.conflicts(),
            "summary": governance_assets.summary()}
