# 中文模块说明：验证全局模型设置的持久化、地址校验、密钥隐藏和密钥清理。
import tempfile
import unittest
from pathlib import Path

from app.model_settings import GlobalModelSettings


class GlobalModelSettingsTests(unittest.TestCase):
    """验证模型设置的全局持久化、输入校验和密钥安全。"""
    def setUp(self):
        """用临时数据库构造全局设置存储，确保用例互不影响。"""
        self.temp = tempfile.TemporaryDirectory()
        self.settings = GlobalModelSettings(Path(self.temp.name) / "settings.sqlite")

    def tearDown(self):
        """恢复默认数据库路径并清理临时数据库。"""
        self.temp.cleanup()

    def test_settings_are_persistent_and_public_response_hides_secret(self):
        """确认设置写入后可重新读取，公开 API 只提供密钥是否配置而隐藏密钥内容。"""
        self.settings.update({
            "provider": "remote",
            "remote_base_url": "https://models.example/v1/",
            "remote_chat_model": "chat-model",
            "remote_api_key": "private-token",
        })

        reloaded = GlobalModelSettings(Path(self.temp.name) / "settings.sqlite")
        self.assertEqual(reloaded.get()["provider"], "remote")
        self.assertEqual(reloaded.get()["remote_base_url"], "https://models.example/v1")
        self.assertTrue(reloaded.public()["api_key_configured"])
        self.assertNotIn("remote_api_key", reloaded.public())

    def test_invalid_provider_and_url_are_rejected(self):
        """确认未知模型来源和无效服务 URL 不会写入设置。"""
        with self.assertRaises(ValueError):
            self.settings.update({"provider": "unknown"})
        with self.assertRaises(ValueError):
            self.settings.update({"remote_base_url": "file:///etc/passwd"})

    def test_api_key_can_be_cleared_explicitly(self):
        """确认只有明确提交清理标志时才会删除已保存的 API 密钥。"""
        self.settings.update({"remote_api_key": "private-token"})
        result = self.settings.update({"clear_api_key": True})
        self.assertFalse(result["remote_api_key"])


if __name__ == "__main__":
    unittest.main()
