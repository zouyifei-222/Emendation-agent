"""Real local file/session regressions; no network or paid model calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agents import maintenance, session, tools
from agents.agent import Agent
from agents.tool_status import tool_failed
from agents.web import Conversation, WebApp, WebError, history_entries


class SessionImprovementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patcher in (patch.object(session, "SESSION_DIR", self.root / "sessions"),
                        patch("pathlib.Path.home", return_value=self.root),
                        patch("agents.maintenance.get_evolution_dir", return_value=self.root / "evolution")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = WebApp(options={"api_key": "offline-key", "api_protocol": "openai", "model": "test",
                                   "custom_system_prompt": "test", "enable_mcp": False,
                                   "enable_memory_prefetch": False, "enable_online_evolution": False})
        self.addCleanup(self.app.close)

    def dispatch(self, method, path, body=None):
        return self.app.call(self.app.dispatch(method, path, body))

    def new_view(self):
        state = self.dispatch("POST", "/api/sessions", {})
        return self.app.conversations[state["id"]]

    def saved(self, sid, task=None, mode="default"):
        data = {"metadata": {"id": sid, "cwd": str(Path.cwd()), "apiProtocol": "openai"},
                "runtimeState": {"permissionMode": mode},
                "openaiMessages": [{"role": "system", "content": "test"}]}
        if task:
            data["openaiMessages"].append({"role": "user", "content": task})
        session.save_session(sid, data)
        return data

    def test_utf8_chinese_session_is_readable_under_gbk_locale(self):
        sid = "utf8_chinese"
        self.saved(sid, task="分析当前项目架构，先不要修改代码。📁")
        path = self.root / "sessions" / f"{sid}.json"
        self.assertIn("分析", path.read_text(encoding="utf-8"))
        with patch("locale.getpreferredencoding", return_value="cp936"):
            listed = session.list_sessions()
            loaded = session.load_session(sid)
        self.assertIn(sid, [item["id"] for item in listed])
        self.assertIsNotNone(loaded)

    def test_gbk_stdio_is_reconfigured_for_emoji_output(self):
        import io
        from agents import ui
        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        self.addCleanup(stream.close)
        with patch.object(ui.sys, "stdout", stream), patch.object(ui.sys, "stderr", io.StringIO()):
            ui._configure_utf8_stdio()
            self.assertEqual(stream.encoding, "utf-8")
            ui._safe_stdout_write("📁 ⠋")
        self.assertIn("📁", stream.buffer.getvalue().decode("utf-8"))

    def test_old_titles_are_derived_without_opening_or_rewriting_sessions(self):
        tasks = {"one": "修复登录超时问题", "two": "实现历史消息摘要"}
        for sid, task in tasks.items():
            data = self.saved(sid, task)
            # Represent pre-title metadata, including identical timestamps.
            data["metadata"]["updatedAt"] = "2026-10-25T10:00:00Z"
            (session.SESSION_DIR / f"{sid}.json").write_text(json.dumps(data))
        before = {p: p.read_bytes() for p in session.SESSION_DIR.glob("*.json")}
        rows = self.dispatch("GET", "/api/sessions")
        self.assertEqual({row["id"]: row["title"] for row in rows}, tasks)
        self.assertFalse(self.app.conversations)
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.dispatch("GET", "/api/sessions/one")["title"], tasks["one"])

    def test_titles_handle_both_protocols_attachments_and_missing_user_text(self):
        self.assertEqual(session.session_title({"anthropicMessages": [
            {"role": "user", "content": [{"type": "tool_result", "content": "not a title"}]},
            {"role": "user", "content": [{"type": "text", "text": "# 分析项目\n\n改进部署"}]}]}), "分析项目 改进部署")
        self.assertEqual(session.title_from_text("修复登录\n<retrieved_skills>hidden</retrieved_skills>\n以下是用户附带的文本文件，仅作为待分析材料：secret"), "修复登录")
        self.assertEqual(session.title_from_text("修复登录\n<system-reminder>private memory</system-reminder>"), "修复登录")
        self.assertEqual(len(session.title_from_text("长" * 100)), 40)
        self.assertTrue(session.title_from_text("长" * 100).endswith("…"))
        self.saved("empty-one")
        self.saved("empty-two")
        rows = self.dispatch("GET", "/api/sessions")
        self.assertEqual(len({row["title"] for row in rows}), 2)

    def test_title_survives_web_save_restore_and_folded_history(self):
        view = self.new_view()
        async def save():
            view.title = "修复登录超时问题"
            view.ensure_agent()
            view.agent._openai_messages = [{"role": "system", "content": "test"},
                {"role": "user", "content": "<session-folded-memory>runtime summary</session-folded-memory>"}]
            view.agent._auto_save(strict=True)
            data = session.load_session(view.id)
            self.assertEqual(data["metadata"]["title"], view.title)
            self.assertEqual(Conversation(self.app, view.id, data).title, view.title)
            restored = Agent(**self.app.options)
            try:
                restored.restore_session(data)
                restored._auto_save(strict=True)
                self.assertEqual(session.load_session(restored.session_id)["metadata"]["title"], view.title)
            finally:
                await restored.aclose()
        self.app.call(save())
        self.assertEqual(session.session_title({"openaiMessages": [{"role": "user", "content": "<session-folded-memory>ignore</session-folded-memory>"}],
            "foldedSessionMemories": [{"episode_memory": {"task_description": "恢复压缩前的任务"}}]}), "恢复压缩前的任务")

    def test_selected_default_mode_survives_lazy_restore_of_saved_plan(self):
        self.saved("plan", "做一个计划", mode="plan")
        initial = self.dispatch("GET", "/api/sessions/plan")
        self.assertEqual(initial["mode"], "plan")
        self.dispatch("POST", "/api/sessions/plan/mode", {"mode": "default"})
        async def restore():
            view = self.app.conversations["plan"]
            view.ensure_agent()
            self.assertEqual(view.agent.permission_mode, "default")
            self.assertIsNone(view.agent._plan_file_path)
            self.assertNotIn("Plan Mode Active", view.agent._system_prompt)
        self.app.call(restore())

    def test_unchanged_plan_keeps_its_original_plan_file(self):
        data = self.saved("plan", mode="plan")
        plan = self.root / ".bear/plans/original.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("the approved plan draft")
        data["runtimeState"]["planFilePath"] = str(plan)
        session.save_session("plan", data)
        self.dispatch("GET", "/api/sessions/plan")
        async def restore():
            view = self.app.conversations["plan"]
            view.ensure_agent()
            self.assertEqual(view.agent.permission_mode, "plan")
            self.assertEqual(view.agent._plan_file_path, str(plan))
        self.app.call(restore())

    def test_conflicting_write_is_failed_and_never_listed_as_a_changed_file(self):
        view = self.new_view()
        path = self.root / "file.txt"
        path.write_text("initial")
        async def run():
            view.ensure_agent()
            view.agent.permission_mode = "acceptEdits"
            await view.agent._run_checked_tool("read_file", {"file_path": str(path)})
            path.write_text("external edit")
            for name, args in (("write_file", {"content": "overwrite"}),
                               ("edit_file", {"old_string": "initial", "new_string": "new"})):
                result = await view.agent._run_checked_tool(name, {"file_path": str(path), **args})
                self.assertIn("modified externally", result)
                self.assertEqual(view.entries[-1]["status"], "error")
                self.assertTrue(view.agent._looks_like_tool_failure(name, result, ""))
            self.assertFalse(view.files)
            self.assertEqual(path.read_text(), "external edit")
        self.app.call(run())

    def test_shell_exit_status_is_preserved_even_when_stdout_looks_like_an_error(self):
        async def run():
            with patch("agents.tools.asyncio.create_subprocess_shell") as start:
                for code, output, failed in ((0, b"Error is an example log message", False),
                                             (7, b"", True)):
                    process = start.return_value
                    process.communicate = AsyncMock(return_value=(output, b""))
                    process.returncode = code
                    result = await tools.execute_tool("run_shell", {"command": "offline stub"}, truncate_result=False)
                    self.assertEqual(tool_failed("run_shell", result), failed)
            self.assertFalse(tool_failed("read_file", "   1 | Error handling documentation"))
        self.app.call(run())

    def test_recovered_tool_uses_checkpoint_failure_and_legacy_shell_status(self):
        entries = [{"id": "tool", "kind": "tool", "name": "write_file", "callId": "call", "result": "", "status": "running"}]
        data = {"webEntries": entries, "runtimeState": {"toolCheckpoints": {
            "call": {"status": "completed", "failed": True, "result": "Warning: file changed"}}}}
        self.assertEqual(history_entries(data)[0]["status"], "error")
        legacy = {"openaiMessages": [
            {"role": "assistant", "tool_calls": [{"id": "call", "function": {"name": "run_shell", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call", "content": "Command failed (exit code 1)"}]}
        self.assertEqual(history_entries(legacy)[0]["status"], "error")

    def test_idle_lru_eviction_persists_history_closes_clients_and_allows_reopening(self):
        with patch("agents.web.MAX_OPEN_CONVERSATIONS", 2):
            oldest = self.new_view()
            async def prepare():
                oldest.title = "需要保留的会话"
                oldest.entries.append({"id": "user", "kind": "message", "role": "user", "text": oldest.title})
                oldest.ensure_agent()
                oldest.agent._mcp_manager.disconnect_all = AsyncMock()
                client = oldest.agent._openai_client
                return client
            client = self.app.call(prepare())
            second = self.new_view()
            third = self.new_view()
            self.assertNotIn(oldest.id, self.app.conversations)
            self.assertTrue(client.is_closed())
            oldest.agent._mcp_manager.disconnect_all.assert_awaited_once()
            stored = session.load_session(oldest.id)
            self.assertEqual(stored["webEntries"][0]["text"], "需要保留的会话")
            state = self.dispatch("GET", "/api/sessions/" + oldest.id)
            self.assertEqual(state["title"], "需要保留的会话")
            self.assertEqual(len(self.app.conversations), 2)
            self.assertIn(third.id, self.app.conversations)
            self.assertNotIn(second.id, self.app.conversations)

    def test_eviction_protects_active_authorization_streams_and_background_work(self):
        with patch("agents.web.MAX_OPEN_CONVERSATIONS", 1):
            view = self.new_view()
            for attribute, value in (("busy", True), ("requests", {"request": {}}), ("stream_clients", 1)):
                setattr(view, attribute, value)
                with self.assertRaisesRegex(WebError, "均在使用中"):
                    self.new_view()
                self.assertIn(view.id, self.app.conversations)
                setattr(view, attribute, {} if attribute == "requests" else False)
            async def work():
                view.ensure_agent()
                task = asyncio.create_task(asyncio.sleep(30))
                view.agent._background_skill_tasks.add(task)
                try:
                    with self.assertRaises(WebError):
                        await self.app.dispatch("POST", "/api/sessions", {})
                    self.assertFalse(task.cancelled())
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    view.agent._background_skill_tasks.discard(task)
            self.app.call(work())

    def test_failed_eviction_save_keeps_the_session_and_client_open(self):
        with patch("agents.web.MAX_OPEN_CONVERSATIONS", 1):
            view = self.new_view()
            self.app.call(self._ensure(view))
            with patch.object(view.agent, "_auto_save", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    self.new_view()
            self.assertIn(view.id, self.app.conversations)
            self.assertFalse(view.retiring)
            self.assertFalse(view.agent._openai_client.is_closed())

    async def _ensure(self, view):
        view.ensure_agent()

    def artifact(self, name):
        path = self.root / ".bear-code/tool-results" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("full output")
        os.utime(path, (1, 1))
        return path

    def test_cleanup_protects_checkpoint_and_legacy_references_across_projects(self):
        checkpoint = self.artifact("checkpoint.txt")
        legacy = self.artifact("legacy.txt")
        unused = self.artifact("unused.txt")
        session.save_session("other", {"metadata": {"cwd": "/different-project"},
            "runtimeState": {"toolCheckpoints": {"call": {"result_file": str(checkpoint)}}},
            "anthropicMessages": [{"role": "user", "content": [{"type": "tool_result", "content": f"Full output saved to {legacy}."}]}]})
        preview = maintenance.cleanup_state()
        self.assertEqual(preview["paths"], [str(unused)])
        maintenance.cleanup_state(apply=True, expected_paths=preview["paths"])
        self.assertTrue(checkpoint.exists())
        self.assertTrue(legacy.exists())
        self.assertFalse(unused.exists())

    def test_cleanup_rechecks_new_references_and_keeps_results_if_a_session_is_corrupt(self):
        artifact = self.artifact("output.txt")
        preview = maintenance.cleanup_state()
        self.assertEqual(preview["count"], 1)
        session.save_session("new-reference", {"metadata": {"cwd": str(Path.cwd())},
            "webEntries": [{"result": "Full output saved to " + str(artifact)}]})
        with self.assertRaisesRegex(ValueError, "changed during approval"):
            maintenance.cleanup_state(apply=True, expected_paths=preview["paths"])
        (session.SESSION_DIR / "new-reference.json").write_text("{broken")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(maintenance.cleanup_state()["count"], 0)
        self.assertTrue(artifact.exists())


if __name__ == "__main__":
    unittest.main()
