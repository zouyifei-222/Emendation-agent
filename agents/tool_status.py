"""Tool outcome metadata with a compatibility fallback for older text results."""
from __future__ import annotations

import json
import re


class ToolResult(str):
    def __new__(cls, text: str, *, failed: bool = False):
        result = super().__new__(cls, text)
        result.failed = failed
        return result


def tool_failed(name: str, result: str) -> bool:
    if isinstance(getattr(result, "failed", None), bool):
        return result.failed
    text = result.lstrip()
    if text.startswith(("Error", "Action denied", "User denied", "Tool not available",
                        "Command failed (exit code", "Command timed out", "Unknown tool:", "Unknown skill:")):
        return True
    if name in {"agent", "skill"} and text.startswith(("Sub-agent error:", "Skill fork error:",
                                                         "Sub-agent skipped:", "Skill fork skipped:")):
        return True
    if name in {"write_file", "edit_file"} and text.startswith("Warning:"):
        return True
    # Legacy shell adapters may append their error after a long output body.
    # Current shell results carry an explicit outcome and return above.
    if name == "run_shell" and re.search(r"(?:^|\n)\s*Error:", text, re.IGNORECASE):
        return True
    if name == "compact_context" and text.startswith(("Compaction skipped:", "Context folding is disabled", "Context folding limit reached")):
        return True
    if name in {"skill_create", "skill_evolve"}:
        try:
            return json.loads(text).get("ok") is False
        except (ValueError, AttributeError):
            pass
    return False
