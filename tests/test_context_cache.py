from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.agent import Agent, FOLD_LIMIT_GUIDANCE, STATIC_FOLD_GUIDANCE


def make_agent(**kwargs):
    options = dict(api_base="http://example.test/v1", api_key="fake",
                   enable_mcp=False, enable_memory_prefetch=False,
                   enable_online_evolution=False, enable_folding=True,
                   permission_mode="acceptEdits")
    options.update(kwargs)
    return Agent(**options)


class StableSystemPromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.origin)
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("BEAR_LEGACY_CONTEXT_REFRESH", None)
        self.addCleanup(self.env.stop)

    def test_runtime_counters_never_change_the_system_prompt(self):
        agent = make_agent()
        first = agent._system_prompt
        self.assertFalse(agent._legacy_context_refresh)
        self.assertIn("Runtime Fold Guidance", first)
        self.assertNotIn("Current context utilization", first)
        agent.last_input_token_count = 10000
        agent._tool_error_streak = 3
        agent._same_tool_repeat_count = 7
        agent._fold_last_time = time.time() - 300
        agent._refresh_runtime_system_prompt()
        self.assertEqual(agent._system_prompt, first)

    def test_tool_round_does_not_rebuild_the_system_prompt(self):
        agent = make_agent()
        with patch.object(agent, "_refresh_runtime_system_prompt") as refresh:
            agent._refresh_after_tool_round()
            refresh.assert_not_called()

    def test_legacy_switch_restores_dynamic_guidance_and_refresh(self):
        with patch.dict(os.environ, {"BEAR_LEGACY_CONTEXT_REFRESH": "1"}):
            agent = make_agent()
            self.assertTrue(agent._legacy_context_refresh)
            self.assertIn("Current context utilization", agent._system_prompt)
            before = agent._system_prompt
            agent.last_input_token_count = 99999
            agent._refresh_runtime_system_prompt()
            self.assertNotEqual(agent._system_prompt, before)
            with patch.object(agent, "_refresh_runtime_system_prompt") as refresh:
                agent._refresh_after_tool_round()
                refresh.assert_called_once()

    def test_fold_limit_guidance_is_static_too(self):
        agent = make_agent()
        with patch.object(agent, "_fold_limit_reached", return_value=True):
            self.assertEqual(agent._build_fold_guidance_section(), FOLD_LIMIT_GUIDANCE)
        self.assertNotIn("%", STATIC_FOLD_GUIDANCE)
        self.assertNotIn("utilization", STATIC_FOLD_GUIDANCE)

    def test_custom_system_prompt_still_has_no_guidance(self):
        agent = make_agent(custom_system_prompt="fixed")
        self.assertEqual(agent._system_prompt, "fixed")
        self.assertEqual(agent._build_fold_guidance_section(), "")


class CacheTokenAccountingTests(unittest.TestCase):
    def test_token_usage_reports_cache_and_side_fields(self):
        agent = make_agent()
        agent.total_input_tokens = 100
        agent.side_input_tokens = 20
        agent.cache_read_input_tokens = 500
        agent.side_cache_read_tokens = 30
        agent.cache_creation_input_tokens = 40
        usage = agent.get_token_usage()
        self.assertEqual(usage["input"], 100)
        self.assertEqual(usage["output"], 0)
        self.assertEqual(usage["cacheRead"], 530)
        self.assertEqual(usage["cacheWrite"], 40)
        self.assertEqual(usage["sideInput"], 20)

    def test_anthropic_cached_tokens_count_towards_context_length(self):
        agent = make_agent()
        agent._record_api_usage(SimpleNamespace(
            input_tokens=10, output_tokens=2,
            cache_read_input_tokens=100, cache_creation_input_tokens=5),
            openai_protocol=False)
        self.assertEqual(agent.total_input_tokens, 10)
        self.assertEqual(agent.cache_read_input_tokens, 100)
        self.assertEqual(agent.cache_creation_input_tokens, 5)
        self.assertEqual(agent.last_input_token_count, 115)

    def test_anthropic_usage_without_cache_fields_matches_old_behavior(self):
        agent = make_agent()
        agent._record_api_usage(SimpleNamespace(input_tokens=7, output_tokens=3),
                                openai_protocol=False)
        self.assertEqual(agent.total_input_tokens, 7)
        self.assertEqual(agent.total_output_tokens, 3)
        self.assertEqual(agent.last_input_token_count, 7)
        self.assertEqual(agent.cache_read_input_tokens, 0)

    def test_openai_usage_records_streamed_cached_tokens(self):
        agent = make_agent(api_protocol="openai")
        agent._record_api_usage({"prompt_tokens": 120, "completion_tokens": 8,
                                 "cached_tokens": 100}, openai_protocol=True)
        self.assertEqual(agent.total_input_tokens, 120)
        self.assertEqual(agent.cache_read_input_tokens, 100)
        self.assertEqual(agent.last_input_token_count, 120)

    def test_openai_prompt_tokens_details_is_also_supported(self):
        agent = make_agent(api_protocol="openai")
        agent._record_api_usage({"prompt_tokens": 50, "completion_tokens": 1,
                                 "prompt_tokens_details": {"cached_tokens": 30}},
                                openai_protocol=True)
        self.assertEqual(agent.cache_read_input_tokens, 30)

    def test_child_usage_accounts_for_cache_counters(self):
        parent, child = make_agent(), make_agent()
        child.cache_read_input_tokens = 77
        child.cache_creation_input_tokens = 4
        child.side_cache_read_tokens = 3
        parent._account_child_usage(child)
        self.assertEqual(parent.cache_read_input_tokens, 77)
        self.assertEqual(parent.cache_creation_input_tokens, 4)
        self.assertEqual(parent.side_cache_read_tokens, 3)


if __name__ == "__main__":
    unittest.main()
