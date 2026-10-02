"""Provider-neutral knowledge retrieval contract."""

from typing import Any, List, Protocol


class KnowledgeRetriever(Protocol):
    def search(self, query: str, *, owner_id: str, top_k: int = 4) -> List[Any]: ...


class RAGStoreAdapter:
    """Adapt the application's existing RAGStore without leaking its API upstream."""

    def __init__(self, store: Any):
        self.store = store

    def search(self, query: str, *, owner_id: str, top_k: int = 4) -> List[Any]:
        return self.store.search(query, k=top_k, owner_id=owner_id)

