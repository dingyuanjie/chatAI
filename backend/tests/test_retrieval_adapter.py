# 中文模块说明：验证科研检索适配器正确转发查询、用户范围和返回条数。
import unittest
from unittest.mock import Mock

from app.retrieval.base import RAGStoreAdapter


class RetrievalAdapterTests(unittest.TestCase):
    """验证 RAGStore 适配器与科研检索协议之间的参数转换。"""
    def test_forwards_owner_and_top_k_to_existing_rag_store(self):
        """确认检索查询、用户隔离 ID 和 top_k 数量都正确转发给现有知识库。"""
        store = Mock()
        store.search.return_value = [{"content": "owned result"}]
        adapter = RAGStoreAdapter(store)

        result = adapter.search("question", owner_id="user-7", top_k=3)

        self.assertEqual(result, [{"content": "owned result"}])
        store.search.assert_called_once_with("question", k=3, owner_id="user-7")


if __name__ == "__main__":
    unittest.main()
