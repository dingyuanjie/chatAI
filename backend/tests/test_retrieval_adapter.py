import unittest
from unittest.mock import Mock

from app.retrieval.base import RAGStoreAdapter


class RetrievalAdapterTests(unittest.TestCase):
    def test_forwards_owner_and_top_k_to_existing_rag_store(self):
        store = Mock()
        store.search.return_value = [{"content": "owned result"}]
        adapter = RAGStoreAdapter(store)

        result = adapter.search("question", owner_id="user-7", top_k=3)

        self.assertEqual(result, [{"content": "owned result"}])
        store.search.assert_called_once_with("question", k=3, owner_id="user-7")


if __name__ == "__main__":
    unittest.main()
