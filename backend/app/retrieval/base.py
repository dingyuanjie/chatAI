"""知识检索抽象边界。

科研编排只依赖 ``search(query, owner_id, top_k)`` 这一小组约定，不直接了解主程序
RAGStore 的参数形式或具体向量库实现。适配器同时传入 owner_id，确保召回内容遵循
用户隔离；将来替换检索后端时，研究流程和代理提示词不必跟着改写。
"""
# 中文模块说明：知识检索抽象模块，向科研流程提供统一的知识库查询协议和现有 RAG 存储适配器。

from typing import Any, List, Protocol


class KnowledgeRetriever(Protocol):
    """科研任务可依赖的检索器静态接口。"""

    def search(self, query: str, *, owner_id: str, top_k: int = 4) -> List[Any]: ...


class RAGStoreAdapter:
    """把应用现有 RAGStore 转换成科研编排使用的统一接口。"""

    def __init__(self, store: Any):
        """接收已初始化的知识库实例，不在适配器内创建数据库连接。"""
        self.store = store

    def search(self, query: str, *, owner_id: str, top_k: int = 4) -> List[Any]:
        """将统一参数映射到旧 RAGStore 的 ``k`` 参数并执行检索。"""
        return self.store.search(query, k=top_k, owner_id=owner_id)
