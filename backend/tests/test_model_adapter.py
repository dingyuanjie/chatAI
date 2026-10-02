import os
import unittest
from unittest.mock import Mock, patch

from app.research import ResearchEngine


class ResearchModelAdapterTests(unittest.TestCase):
    @patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1", "RESEARCH_MODEL": "research-test"})
    @patch("app.providers.openai_compatible.httpx.post")
    def test_uses_configured_model_and_profile_generation_limits(self, post):
        response = Mock()
        response.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": "分析结果"}}]
        }
        post.return_value = response

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
        response.json.return_value = {
            "choices": [{"finish_reason": "length", "message": {"content": ""}}]
        }
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "耗尽了生成长度"):
            ResearchEngine._call_model("system", "question")

    @patch.dict(os.environ, {"RESEARCH_BASE_URL": "http://model.local/v1", "RESEARCH_MODEL": "custom", "RESEARCH_API_KEY": "secret"})
    @patch("app.providers.openai_compatible.httpx.post")
    def test_supports_configured_openai_compatible_endpoint(self, post):
        response = Mock()
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        post.return_value = response

        self.assertEqual(ResearchEngine._call_model("s", "p"), "ok")
        self.assertEqual(post.call_args.args[0], "http://model.local/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
