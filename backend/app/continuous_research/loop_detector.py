"""Detect repeated cycle themes, including non-adjacent A → B → C → A loops."""

from typing import Dict, List

from .novelty_detector import NoveltyDetector


class ResearchLoopDetector:
    def __init__(self, threshold: float = 0.80, window: int = 8):
        self.threshold = threshold
        self.window = window
        self.novelty = NoveltyDetector(threshold=threshold)

    def score(self, cycles: List[Dict]) -> float:
        recent = cycles[-self.window:]
        if len(recent) < 4:
            return 0.0
        current = recent[-1]
        text = str(current.get("signature", ""))
        past = recent[:-1]
        return max((self.novelty.lexical_similarity(text, str(item.get("signature", ""))) for item in past), default=0.0)

    def detected(self, cycles: List[Dict]) -> bool:
        return self.score(cycles) >= self.threshold

