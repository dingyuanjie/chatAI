# 中文模块说明：验证 Markdown 知识文件更新、重复内容、历史版本、差异比较和删除级联。
import tempfile
import unittest
import gc
from pathlib import Path
from unittest.mock import patch

from app.main import RAGStore


class KnowledgeVersionTests(unittest.TestCase):
    """验证 Markdown 知识库文件内容、版本历史和删除级联。"""
    def setUp(self):
        """使用临时 SQLite 文件创建独立知识库，避免测试修改真实用户资料。"""
        self.temp = tempfile.TemporaryDirectory()
        self.store = RAGStore(Path(self.temp.name) / "rag.sqlite")
        self.embed = patch.object(self.store, "_embed", side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
        self.embed.start()

    def tearDown(self):
        """恢复模块默认数据库路径并清理临时目录。"""
        self.embed.stop()
        del self.store
        gc.collect()
        self.temp.cleanup()

    def test_upload_update_duplicate_and_compare(self):
        """验证首次上传生成版本、相同内容不重复建版、更新创建新版本且差异可比较。"""
        first = self.store.upsert_file("theory.md", "# Theory\n\nFirst claim.", "alice")
        self.assertEqual(first["version"], 1)
        second = self.store.upsert_file("theory.md", "# Theory\n\nRevised claim.", "alice")
        self.assertEqual(second["version"], 2)
        duplicate = self.store.upsert_file("THEORY.md", "# Theory\n\nRevised claim.", "alice")
        self.assertTrue(duplicate["unchanged"])
        self.assertEqual(duplicate["version"], 2)
        versions = self.store.list_versions(first["id"], "alice")
        self.assertEqual([item["version"] for item in versions], [2, 1])
        diff = self.store.compare_versions(first["id"], 1, 2, "alice")
        self.assertIn("-First claim.", diff["diff"])
        self.assertIn("+Revised claim.", diff["diff"])
        self.assertIsNone(self.store.get_version(first["id"], 1, "bob"))
        self.assertIsNone(self.store.list_versions(first["id"], "bob"))

    def test_delete_removes_version_history(self):
        """验证删除文件会连同历史版本、向量块与索引一并清理。"""
        result = self.store.upsert_file("theory.md", "content", "alice")
        self.assertTrue(self.store.delete_file(result["id"], "alice"))
        self.assertIsNone(self.store.list_versions(result["id"], "alice"))


if __name__ == "__main__":
    unittest.main()
