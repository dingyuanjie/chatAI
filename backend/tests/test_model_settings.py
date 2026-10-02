import tempfile
import unittest
from pathlib import Path

from app.model_settings import GlobalModelSettings


class GlobalModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = GlobalModelSettings(Path(self.temp.name) / "settings.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_are_persistent_and_public_response_hides_secret(self):
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
        with self.assertRaises(ValueError):
            self.settings.update({"provider": "unknown"})
        with self.assertRaises(ValueError):
            self.settings.update({"remote_base_url": "file:///etc/passwd"})

    def test_api_key_can_be_cleared_explicitly(self):
        self.settings.update({"remote_api_key": "private-token"})
        result = self.settings.update({"clear_api_key": True})
        self.assertFalse(result["remote_api_key"])


if __name__ == "__main__":
    unittest.main()
