import os
import unittest
from unittest.mock import Mock, patch

from app.research import ResearchEngine
from app.providers.openai_compatible import completion_token_limit


class ResearchModelAdapterTests(unittest.TestCase):
    def test_deepseek_reasoning_models_get_a_reasonable_completion_budget(self):
        self.assertEqual(completion_token_limit("remote", "deepseek-reasoner", 1200), 16384)
        self.assertEqual(completion_token_limit("remote", "deepseek-chat", 800), 8192)
        self.assertEqual(completion_token_limit("local", "deepseek-reasoner", 1200), 1200)

    @patch("app.providers.openai_compatible.httpx.post")
    def test_uses_configured_model_and_profile_generation_limits(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": "分析结果"}}]
        }
        post.return_value = response

        with patch("app.providers.openai_compatible.model_settings.get", return_value={
            "provider": "local", "ollama_url": "http://127.0.0.1:11434", "local_research_model": "research-test",
        }):
            answer = ResearchEngine._call_model("system", "question", temperature=0.2, max_tokens=777)

        self.assertEqual(answer, "分析结果")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "research-test")
        self.assertEqual(kwargs["json"]["temperature"], 0.2)
        self.assertEqual(kwargs["json"]["max_tokens"], 777)

    @patch("app.providers.openai_compatible.httpx.post")
    def test_explains_empty_output_after_generation_limit(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"finish_reason": "length", "message": {"content": ""}}]
        }
        post.return_value = response

        with patch("app.providers.openai_compatible.model_settings.get", return_value={
            "provider": "local", "ollama_url": "http://127.0.0.1:11434", "local_research_model": "research-test",
        }):
            with self.assertRaisesRegex(RuntimeError, "生成上限"):
                ResearchEngine._call_model("system", "question")

    @patch("app.providers.openai_compatible.httpx.post")
    def test_supports_configured_openai_compatible_endpoint(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        post.return_value = response

        with patch("app.providers.openai_compatible.model_settings.get", return_value={
            "provider": "remote", "remote_base_url": "http://model.local/v1", "remote_research_model": "custom",
            "remote_chat_model": "chat", "remote_api_key": "secret",
        }):
            self.assertEqual(ResearchEngine._call_model("s", "p"), "ok")
        self.assertEqual(post.call_args.args[0], "http://model.local/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    @patch("app.providers.openai_compatible.httpx.post")
    def test_explains_context_window_rejection(self, post):
        response = Mock()
        response.status_code = 400
        response.text = '{"error":{"message":"prompt exceeds the available context length"}}'
        response.json.return_value = {"error": {"message": "prompt exceeds the available context length"}}
        post.return_value = response

        with patch("app.providers.openai_compatible.model_settings.get", return_value={
            "provider": "local", "ollama_url": "http://127.0.0.1:11434", "local_research_model": "research-test",
        }):
            with self.assertRaisesRegex(RuntimeError, "上下文窗口限制"):
                ResearchEngine._call_model("system", "long prompt")

    def test_synthesis_prompt_is_bounded_for_small_local_context(self):
        outputs = [f"专家{i}：\n" + ("[finding] 支持该推论但仍需检验 [W1]。" * 100) for i in range(6)]
        prompt = ResearchEngine._synthesis_prompt("统一底层原理", "结构和涌现" * 100, 12, outputs)

        self.assertLessEqual(len(prompt), 2100)
        self.assertIn("第 12 轮", prompt)
        self.assertIn("专家1", prompt)


if __name__ == "__main__":
    unittest.main()
