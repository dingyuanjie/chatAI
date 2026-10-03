"""连续科研的循环检测。

每轮分析会生成一个代表本轮主张与综合结论的 ``signature``。检测器在最近窗口内
比较当前签名与之前所有签名，而不只比较相邻轮次，因此也能发现 A→B→C→A 这类
隔轮重复。相似度达到阈值表示存在循环风险，具体是否休眠由停止策略决定。
"""
# 中文模块说明：连续科研策略组件，提供进展度量、循环/新颖度检测、资源预算和自动停止决策；策略代码保持可独立测试，不直接调用模型服务。

from typing import Dict, List

from .novelty_detector import NoveltyDetector


class ResearchLoopDetector:
    """在有限历史窗口内检测重复研究主题。"""

    def __init__(self, threshold: float = 0.80, window: int = 8):
        """设置循环阈值和最多参与比较的近期轮数。"""
        self.threshold = threshold
        self.window = window
        self.novelty = NoveltyDetector(threshold=threshold)

    def score(self, cycles: List[Dict]) -> float:
        """返回最新轮次与窗口内历史签名的最高词面相似度。

        少于四轮时不做循环判定，避免极少量早期文本产生过强的误报；没有
        可比较的历史项时，最高相似度默认是 0。
        """
        recent = cycles[-self.window:]
        if len(recent) < 4:
            return 0.0
        current = recent[-1]
        text = str(current.get("signature", ""))
        past = recent[:-1]
        return max((self.novelty.lexical_similarity(text, str(item.get("signature", ""))) for item in past), default=0.0)

    def detected(self, cycles: List[Dict]) -> bool:
        """判断循环分数是否达到配置阈值。"""
        return self.score(cycles) >= self.threshold
