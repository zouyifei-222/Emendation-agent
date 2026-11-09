"""Preview-first cleanup of disposable artifacts, preserving current champions."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import shutil
import time

from .skill_evolution import get_evolution_dir


def _referenced_results(directory: Path) -> set[str] | None:
    """Scan all projects' sessions; an unreadable session makes deletion unsafe."""
    from .session import SESSION_DIR
    roots = {str(directory), str(directory.resolve())}
    pattern = re.compile("(?:" + "|".join(re.escape(root) for root in roots) + r")[/\\]([A-Za-z0-9_.-]+\.txt)")
    referenced = set()
    try:
        for path in SESSION_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Invalid session: {path}")
            pending = [data]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    pending.extend(item.keys())
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
                elif isinstance(item, str):
                    referenced.update(pattern.findall(item))
    except (OSError, ValueError):
        logging.warning("Skipping tool-result cleanup: session references could not be verified", exc_info=True)
        return None
    return referenced


def cleanup_candidates(days: int = 30) -> list[Path]:
    if days < 1:
        raise ValueError("retention days must be positive")
    cutoff = time.time() - days * 86400
    paths = []
    results = Path.home() / ".bear-code" / "tool-results"
    referenced = _referenced_results(results)
    for path in results.glob("*.txt"):
        if (referenced is not None and path.name not in referenced
                and not path.is_symlink() and path.stat().st_mtime < cutoff):
            paths.append(path)
    evaluation = get_evolution_dir() / "online-eval"
    registry = evaluation / "champions.json"
    champions = json.loads(registry.read_text()).get("champions", {}) if registry.exists() else {}
    protected = {record.get("run_id") for record in champions.values()}
    for path in (evaluation / "runs").glob("*/*"):
        if (path.is_dir() and not path.is_symlink() and evaluation.resolve() in path.resolve().parents
                and path.name not in protected and path.stat().st_mtime < cutoff):
            paths.append(path)
    return paths


def cleanup_state(days: int = 30, *, apply: bool = False, expected_paths: list[str] | None = None) -> dict:
    paths = cleanup_candidates(days)
    if apply:
        if expected_paths is not None and set(map(str, paths)) != set(expected_paths):
            raise ValueError("cleanup candidates changed during approval; preview again")
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    return {"applied": apply, "count": len(paths), "paths": [str(path) for path in paths]}
