
#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from typing import Any
import json
import logging
import time
import re

from .storage import append_jsonl, write_json

SESSION_DIR = Path.home() / ".bear-code" / "sessions"


def title_from_text(text: str, *, limit: int = 40) -> str:
    """Use the user's task as a local, deterministic conversation label."""
    text = re.sub(r"<(retrieved_skills|system-reminder)>.*?</\1>", "", text, flags=re.DOTALL)
    text = text.split("以下是用户附带的文本文件，仅作为待分析材料", 1)[0]
    lines = [re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+)", "", line).strip()
             for line in text.splitlines() if line.strip()]
    title = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


def session_title(data: dict[str, Any]) -> str:
    metadata = data.get("metadata") or {}
    stored = metadata.get("title")
    if isinstance(stored, str) and stored.strip() and not stored.startswith("历史会话 · "):
        return title_from_text(stored)
    # Web entries preserve the original user task even after protocol folding.
    for messages in (data.get("webEntries"), data.get("openaiMessages"), data.get("anthropicMessages")):
        for message in messages or []:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("text", message.get("content", ""))
            if isinstance(content, list):
                content = "\n".join(block["text"] for block in content if isinstance(block, dict)
                                    and block.get("type") == "text" and isinstance(block.get("text"), str))
            if isinstance(content, str) and content.strip():
                # A compacted protocol message is runtime context, not a title.
                if "<session-folded-memory>" in content:
                    continue
                title = title_from_text(content)
                if title:
                    return title
    for memory in data.get("foldedSessionMemories") or []:
        episode = memory.get("episode_memory", {}) if isinstance(memory, dict) else {}
        task = episode.get("task_description") if isinstance(episode, dict) else None
        if isinstance(task, str) and title_from_text(task):
            return title_from_text(task)
    return ""



def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def get_project_session_dir() -> Path:
    d = Path.cwd() / ".bear" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(session_id: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    data = {**data, "metadata": {**data.get("metadata", {}),
                               "title": session_title(data),
                               "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "updatedAtNs": time.time_ns()}}
    write_json(SESSION_DIR / f"{session_id}.json", data)


def save_folded_session_memory(session_id: str, record: dict[str, Any]) -> None:
    d = get_project_session_dir()
    append_jsonl(d / f"{session_id}.folded-memory.jsonl", record)
    write_json(d / f"{session_id}.folded-memory.latest.json", record)


def load_session(session_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return None
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        logging.exception("Unable to load session %s; original file preserved", path)
        return None


def list_sessions() -> list[dict[str, Any]]:
    _ensure_dir()
    results = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            metadata = data.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("cwd"), str):
                results.append({**metadata, "id": metadata.get("id") or f.stem, "title": session_title(data)})
        except Exception:
            logging.warning("Skipping unreadable session %s; original file preserved", f, exc_info=True)
    return results


def get_latest_session_id(project_dir: Path | None = None) -> str | None:
    project = (project_dir or Path.cwd()).resolve()
    sessions = [s for s in list_sessions()
                if s.get("cwd") and Path(s["cwd"]).resolve() == project]
    if not sessions:
        return None
    sessions.sort(key=lambda s: (s.get("updatedAt") or s.get("startTime", ""),
                                 s.get("updatedAtNs", 0),
                                 s.get("startTime", "")), reverse=True)
    return sessions[0].get("id")
