# 中文模块说明：验证 OpenAI-compatible 模型适配器、生成 Token 限制、错误处理、用量解析和费用估算。
import os
import unittest
from unittest.mock import Mock, patch

from app.research import ResearchEngine
from app.providers.openai_compatible import completion_token_limit
from app.providers.openai_compatible import OpenAICompatibleChatModel


class ResearchModelAdapterTests(unittest.TestCase):
    """验证聊天模型适配器的远端兼容性、Token 边界与用量记录。"""
    def test_deepseek_reasoning_models_get_a_reasonable_completion_budget(self):
        """确认 DeepSeek 推理模型有足够输出空间容纳推理 Token 和可见答案。"""
        self.assertEqual(completion_token_limit("remote", "deepseek-reasoner", 1200), 16384)
        self.assertEqual(completion_token_limit("remote", "deepseek-chat", 800), 8192)
        self.assertEqual(completion_token_limit("local", "deepseek-reasoner", 1200), 1200)

    @patch("app.providers.openai_compatible.httpx.post")
    def test_uses_configured_model_and_profile_generation_limits(self, post):
        """确认适配器使用设置中的模型，并保留专家档案规定的输出长度约束。"""
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
        """确认模型只返回 length 结束原因时给出“生成上限”诊断，而非误报输入超长。"""
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
        """确认远端自定义 API 地址、模型 ID 和认证信息会被正确用于请求。"""
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
        """确认服务端拒绝上下文长度时错误信息能区分输入窗口与生成 Token 上限。"""
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

    @patch("app.providers.openai_compatible.httpx.post")
    def test_remote_usage_is_normalized_and_priced_only_when_configured(self, post):
        """确认远端 Token 用量字段可归一化，且只有配置价格时才估算费用。"""
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
                      "prompt_tokens_details": {"cached_tokens": 500}}}
        post.return_value = response
        provider = OpenAICompatibleChatModel("https://api.deepseek.com", "deepseek-flash", "key", provider_type="REMOTE")
        result = provider.generate("system", "prompt")
        self.assertEqual(result.provider_type, "REMOTE")
        self.assertEqual(result.usage.input_tokens, 1000)
        self.assertEqual(result.usage.output_tokens, 200)
        self.assertEqual(result.usage.cached_input_tokens, 500)
        self.assertAlmostEqual(result.usage.estimated_cost, 0.000393)

    @patch("app.providers.openai_compatible.httpx.post")
    def test_unknown_remote_model_has_no_fabricated_cost(self, post):
        """确认未配置价格的模型费用保持未知，不会被误记为零。"""
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
        post.return_value = response
        provider = OpenAICompatibleChatModel("https://models.example/v1", "unpriced-model", "key", provider_type="REMOTE")
        self.assertIsNone(provider.generate("s", "p").usage.estimated_cost)

    def test_synthesis_prompt_is_bounded_for_small_local_context(self):
        """确认综合提示在小上下文模型下会限制问题和专家正文长度。"""
        outputs = [f"专家{i}：\n" + ("[finding] 支持该推论但仍需检验 [W1]。" * 100) for i in range(6)]
        prompt = ResearchEngine._synthesis_prompt("统一底层原理", "结构和涌现" * 100, 12, outputs)

        self.assertLessEqual(len(prompt), 2100)
        self.assertIn("第 12 轮", prompt)
        self.assertIn("专家1", prompt)


if __name__ == "__main__":
    unittest.main()
