"""新颖度检测器：优先使用可选向量回调比较语义；向量不可用时使用字符 n-gram 词面相似度，保证检测过程可离线运行。"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Iterable, List, Optional


class NoveltyDetector:
    """比较候选研究内容与历史内容的相似度；优先采用向量语义比较，向量服务不可用时退回词面算法。"""
    def __init__(self, threshold: float = 0.92, embed: Optional[Callable[[List[str]], List[List[float]]]] = None):
        """设置重复判定阈值及可选的批量文本向量化回调。"""
        self.threshold = threshold
        self.embed = embed

    @staticmethod
    def normalize(text: str) -> str:
        """使用 Unicode NFKC 和小写归一化，并去掉标点、空格等非文字字符以便稳定比较。"""
        value = unicodedata.normalize("NFKC", text or "").casefold()
        return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)

    @staticmethod
    def _ngrams(text: str, n: int = 2) -> set[str]:
        """将文本切成指定长度的字符片段集合，用于中英文混合文本的词面重合度估计。"""
        return {text[index:index + n] for index in range(max(0, len(text) - n + 1))}

    @classmethod
    def lexical_similarity(cls, left: str, right: str) -> float:
        """综合 SequenceMatcher 序列相似度与字符 n-gram 的 Jaccard 重合度，取较高值作为词面相似度。"""
        a, b = cls.normalize(left), cls.normalize(right)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        grams_a, grams_b = cls._ngrams(a), cls._ngrams(b)
        overlap = len(grams_a & grams_b) / max(1, len(grams_a | grams_b))
        return max(SequenceMatcher(None, a, b).ratio(), overlap)

    @staticmethod
    def cosine(left: List[float], right: List[float]) -> float:
        """计算两个向量的余弦相似度；空向量或零范数向量安全地返回 0。"""
        import math
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        numerator = sum(left[index] * right[index] for index in range(size))
        denominator = math.sqrt(sum(value * value for value in left[:size]) * sum(value * value for value in right[:size]))
        return numerator / denominator if denominator else 0.0

    def max_similarity(self, candidate: str, existing: Iterable[str]) -> float:
        """综合 SequenceMatcher 序列相似度与字符 n-gram 的 Jaccard 重合度，取较高值作为词面相似度。"""
        choices = [text for text in existing if text]
        if not candidate or not choices:
            return 0.0
        lexical = max(self.lexical_similarity(candidate, text) for text in choices)
        if lexical >= self.threshold or not self.embed:
            return lexical
        try:
            vectors = self.embed([candidate, *choices])
            semantic = max(self.cosine(vectors[0], vector) for vector in vectors[1:])
            return max(lexical, semantic)
        except Exception:
            return lexical

    def is_duplicate(self, candidate: str, existing: Iterable[str]) -> bool:
        """根据当前对象的状态判断是否满足“isduplicate”条件，并返回布尔结果。"""
        return self.max_similarity(candidate, existing) >= self.threshold

