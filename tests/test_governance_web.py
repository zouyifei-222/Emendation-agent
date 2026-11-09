from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agents import session, skills
from agents.skill_evolution import SKILL_USAGE_STATS, get_evolution_dir
from agents.web import WebApp, WebError, WebServer


class GovernanceWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        self.session_patch = patch.object(session, "SESSION_DIR", Path(self.temp.name) / "sessions")
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.app = WebApp(options={"api_key": "fake-secret", "api_base": "http://example.test/v1",
                                   "api_protocol": "openai", "model": "test-model", "custom_system_prompt": "test",
                                   "enable_mcp": False, "enable_memory_prefetch": False,
                                   "enable_online_evolution": False})
        self.addCleanup(self.app.close)
        skills.reset_skill_cache()

    def dispatch(self, method, path, body=None):
        return self.app.call(self.app.dispatch(method, path, body))

    def create(self, name="论文格式", instructions="# Goal\n\n先给结论。"):
        return self.dispatch("POST", "/api/governance/save", {
            "mode": "new", "name": name, "description": "团队文档规则",
            "whenToUse": "写报告时", "instructions": instructions, "target": "project"})

    def test_board_lists_created_asset_and_exposes_full_text(self):
        board = self.dispatch("GET", "/api/governance")
        self.assertEqual(board["assets"], [])
        self.assertEqual(board["staleThreshold"]["minRetrieved"], 12)
        result = self.create()
        self.assertTrue(result["ok"])
        board = self.dispatch("GET", "/api/governance")
        self.assertEqual(len(board["assets"]), 1)
        asset = board["assets"][0]
        self.assertEqual(asset["name"], "论文格式")
        self.assertEqual(asset["source"], "project")
        self.assertFalse(asset["staleSuggested"])
        self.assertEqual(asset["usage"]["retrieved"], 0)
        detail = self.dispatch("GET", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F")
        self.assertIn("先给结论。", detail["instructions"])
        self.assertEqual(detail["whenToUse"], "写报告时")
        self.assertIn("history", detail["audit"])
        self.assertFalse(detail["pruned"])

    def test_edit_creates_snapshot_and_rollback_restores_previous_body(self):
        self.create()
        saved = self.dispatch("POST", "/api/governance/save", {
            "mode": "edit", "name": "论文格式", "description": "团队文档规则",
            "whenToUse": "写报告时", "instructions": "# Goal\n\n先给结论，再列三条依据。",
            "rationale": "补充依据要求"})
        self.assertTrue(saved["ok"])
        detail = self.dispatch("GET", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F")
        self.assertIn("再列三条依据", detail["instructions"])
        self.assertEqual(detail["history"][0]["actor"], "web-governance")
        rolled = self.dispatch("POST", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F/rollback", {})
        self.assertTrue(rolled["ok"])
        detail = self.dispatch("GET", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F")
        self.assertIn("先给结论。", detail["instructions"])
        self.assertNotIn("再列三条依据", detail["instructions"])
        self.assertNotIn("# Skill Instructions\n\n# Skill Instructions", detail["instructions"])

    def test_archive_and_restore_are_reversible(self):
        self.create()
        archived = self.dispatch("POST", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F/archive",
                                 {"reason": "长期未使用"})
        self.assertTrue(archived["ok"])
        board = self.dispatch("GET", "/api/governance")
        self.assertTrue(board["assets"][0]["pruned"])
        detail = self.dispatch("GET", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F")
        self.assertTrue(detail["pruned"])
        self.assertIn("先给结论。", detail["instructions"])
        restored = self.dispatch("POST", "/api/governance/%E8%AE%BA%E6%96%87%E6%A0%BC%E5%BC%8F/restore", {})
        self.assertTrue(restored["ok"])
        board = self.dispatch("GET", "/api/governance")
        self.assertEqual(len(board["assets"]), 1)
        self.assertFalse(board["assets"][0]["pruned"])

    def test_stale_is_only_a_suggestion_and_threshold_is_configurable(self):
        self.create()
        path = get_evolution_dir() / SKILL_USAGE_STATS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"论文格式": {"retrieved": 12, "relevant": 0, "used": 0,
                                                 "source": "project"}}), encoding="utf-8")
        board = self.dispatch("GET", "/api/governance")
        self.assertTrue(board["assets"][0]["staleSuggested"])
        self.assertFalse(board["assets"][0]["pruned"])
        with patch.dict(os.environ, {"BEAR_GOVERNANCE_STALE_MIN_RETRIEVED": "40"}):
            board = self.dispatch("GET", "/api/governance")
            self.assertFalse(board["assets"][0]["staleSuggested"])

    def test_invalid_names_and_missing_assets_are_rejected(self):
        with self.assertRaises(WebError):
            self.dispatch("POST", "/api/governance/save", {"mode": "new", "name": "../../evil",
                                                          "description": "x", "instructions": "x"})
        with self.assertRaises(WebError):
            self.dispatch("GET", "/api/governance/%E4%B8%8D%E5%AD%98%E5%9C%A8")
        with self.assertRaises(WebError):
            self.dispatch("POST", "/api/governance/save", {"mode": "new", "name": "新规则",
                                                          "description": "", "instructions": "x"})

    def test_http_mutation_requires_cookie_and_csrf(self):
        server = WebServer(("127.0.0.1", 0), self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        self.addCleanup(client.close)
        client.request("GET", "/")
        response = client.getresponse()
        cookie = response.getheader("Set-Cookie").split(";")[0]
        response.read()
        client.request("GET", "/governance-view.mjs")
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b"renderGovernancePage", response.read())
        client.request("GET", "/api/governance", headers={"Cookie": cookie})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        body = json.dumps({"mode": "new", "name": "网页规则", "description": "d", "instructions": "# Goal\n\nx"})
        for headers in ({"Cookie": cookie, "Content-Type": "application/json"},
                        {"Content-Type": "application/json", "X-Bear-CSRF": self.app.config["csrf"]}):
            client.request("POST", "/api/governance/save", body=body, headers=headers)
            response = client.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        client.request("POST", "/api/governance/save", body=body,
                       headers={"Cookie": cookie, "Content-Type": "application/json",
                                "X-Bear-CSRF": self.app.config["csrf"]})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["ok"])


if __name__ == "__main__":
    unittest.main()
