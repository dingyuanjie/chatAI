"""Extract heuristic, inspectable progress metrics from the saved research graph."""

import re
from typing import Any, Dict, List

from .novelty_detector import NoveltyDetector


METRIC_KEYS = (
    "new_claims", "new_evidence", "new_counter_evidence", "new_hypotheses", "new_counterexamples",
    "new_predictions", "new_experiments", "new_mathematical_models", "new_open_questions",
    "resolved_questions", "new_document_connections", "new_contradictions", "resolved_contradictions",
)


class ProgressTracker:
    def __init__(self, weights: Dict[str, float] | None = None, duplicate_threshold: float = 0.92,
                 embed=None):
        self.weights = weights or {}
        self.novelty = NoveltyDetector(duplicate_threshold, embed)

    @staticmethod
    def _contains(text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def measure(self, claims: List[Dict[str, Any]], evidence: List[Dict[str, Any]],
                prior_claims: List[Dict[str, Any]], prior_urls: set[str],
                document_connections: int = 0, resolved_questions: int = 0,
                resolved_contradictions: int = 0) -> Dict[str, Any]:
        existing = [str(item.get("claim_text", item.get("text", ""))) for item in prior_claims]
        novelty_scores = []
        novel_claims = []
        novel_indexes = []
        claim_texts = [str(claim.get("claim_text", "")) for claim in claims]
        vectors = None
        if self.novelty.embed and claim_texts:
            try:
                vectors = self.novelty.embed([*existing, *claim_texts])
            except Exception:
                vectors = None
        for index, claim in enumerate(claims):
            text = claim_texts[index]
            prior_texts = existing + [str(item.get("claim_text", "")) for item in novel_claims]
            if vectors is not None:
                current_index = len(existing) + index
                compare_indices = list(range(len(existing))) + [len(existing) + j for j in novel_indexes]
                similarity = max((self.novelty.cosine(vectors[current_index], vectors[j]) for j in compare_indices), default=0.0)
            else:
                similarity = self.novelty.max_similarity(text, prior_texts)
            novelty_scores.append(similarity)
            if similarity < self.novelty.threshold:
                novel_claims.append(claim)
                novel_indexes.append(index)
        counts = {key: 0 for key in METRIC_KEYS}
        counts["new_claims"] = len(novel_claims)
        counts["new_evidence"] = len({str(item.get("url") or item.get("title")) for item in evidence
                                      if str(item.get("url") or item.get("title")) not in prior_urls})
        counts["new_document_connections"] = max(0, document_connections)
        counts["resolved_questions"] = max(0, resolved_questions)
        counts["resolved_contradictions"] = max(0, resolved_contradictions)
        for item in novel_claims:
            text = str(item.get("claim_text", ""))
            kind = str(item.get("claim_type", ""))
            if kind in ("counterevidence", "counterexample"):
                counts["new_counter_evidence"] += 1
                counts["new_counterexamples"] += 1
            if kind == "hypothesis":
                counts["new_hypotheses"] += 1
            if kind == "prediction":
                counts["new_predictions"] += 1
            if self._contains(text, ("实验方案", "实验设计", "实验验证", "可测量实验", "experiment")):
                counts["new_experiments"] += 1
            if self._contains(text, ("方程", "数学模型", "形式化模型", "动力学模型", "微分方程", "mathematical model")):
                counts["new_mathematical_models"] += 1
            if self._contains(text, ("矛盾", "冲突", "不一致", "contradict")):
                counts["new_contradictions"] += 1
            if self._contains(text, ("尚待回答", "开放问题", "仍需研究", "待检验", "open question")):
                counts["new_open_questions"] += 1
        weights = {**self.weights}
        raw = (
            counts["new_evidence"] * weights.get("new_evidence", 0.20)
            + counts["new_counterexamples"] * weights.get("new_counterexamples", 0.20)
            + counts["new_predictions"] * weights.get("new_predictions", 0.20)
            + counts["new_mathematical_models"] * weights.get("new_mathematical_models", 0.15)
            + counts["new_experiments"] * weights.get("new_experiments", 0.15)
            + counts["resolved_contradictions"] * weights.get("resolved_contradictions", 0.15)
            + counts["new_hypotheses"] * weights.get("new_hypotheses", 0.10)
            + counts["new_claims"] * weights.get("new_claims", 0.05)
        )
        return {**counts, "information_gain_score": round(min(1.0, raw), 3),
                "duplicate_rate": round(sum(score >= self.novelty.threshold for score in novelty_scores) / max(1, len(novelty_scores)), 3),
                "novelty_score": round(1 - (sum(novelty_scores) / max(1, len(novelty_scores))), 3),
                "agents_used": [], "tasks_completed": 0, "tasks_created": 0, "cycle_id": 0, "timestamp": "",
                "blocked_questions": [], "executable_tasks": 0, "loop_score": 0.0}

    @staticmethod
    def advance_no_progress(previous_count: int, information_gain_score: float, minimum: float) -> int:
        return 0 if information_gain_score >= minimum else previous_count + 1

    @staticmethod
    def blocked_reason(question: str) -> str:
        text = question.casefold()
        patterns = (
            ("NEEDS_EXPERIMENT", ("实验", "测量数据", "观测数据")),
            ("NEEDS_EXTERNAL_DATA", ("数据集", "外部数据", "样本数据")),
            ("NEEDS_WEB_ACCESS", ("联网", "网页访问", "在线检索")),
            ("NEEDS_HUMAN_DECISION", ("需要用户", "需要人工", "价值判断")),
            ("NEEDS_MORE_COMPUTE", ("算力", "计算资源", "gpu")),
            ("NEEDS_BETTER_MODEL", ("更强模型", "更大模型", "更高精度模型")),
            ("INSUFFICIENT_EVIDENCE", ("证据不足", "缺少证据", "未知")),
        )
        for reason, terms in patterns:
            if any(term in text for term in terms):
                return reason
        return ""

    @staticmethod
    def signature(claims: List[Dict[str, Any]], summary: str) -> str:
        parts = [str(item.get("claim_text", "")) for item in claims]
        if parts:
            return " ".join(parts[:12])
        return re.sub(r"\s+", " ", summary or "")[:1200]
