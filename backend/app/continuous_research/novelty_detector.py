"""Novelty checks with an optional reuse of the app's local embedding service."""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Iterable, List, Optional


class NoveltyDetector:
    def __init__(self, threshold: float = 0.92, embed: Optional[Callable[[List[str]], List[List[float]]]] = None):
        self.threshold = threshold
        self.embed = embed

    @staticmethod
    def normalize(text: str) -> str:
        value = unicodedata.normalize("NFKC", text or "").casefold()
        return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)

    @staticmethod
    def _ngrams(text: str, n: int = 2) -> set[str]:
        return {text[index:index + n] for index in range(max(0, len(text) - n + 1))}

    @classmethod
    def lexical_similarity(cls, left: str, right: str) -> float:
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
        import math
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        numerator = sum(left[index] * right[index] for index in range(size))
        denominator = math.sqrt(sum(value * value for value in left[:size]) * sum(value * value for value in right[:size]))
        return numerator / denominator if denominator else 0.0

    def max_similarity(self, candidate: str, existing: Iterable[str]) -> float:
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
        return self.max_similarity(candidate, existing) >= self.threshold

