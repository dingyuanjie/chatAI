# 中文模块说明：验证连续科研硬上限、收敛、循环检测、人工停止和错误停止。
import unittest

from app.continuous_research.controller import ContinuousResearchController
from app.continuous_research.loop_detector import ResearchLoopDetector
from app.continuous_research.models import ContinuousResearchConfig
from app.continuous_research.progress_tracker import ProgressTracker
from app.continuous_research.stop_policy import StopPolicyEngine


class ContinuousResearchTests(unittest.TestCase):
    """验证连续科研停止策略、进展衡量、循环检测与逃逸探索结果。"""
    def setUp(self):
        """构造不调用模型的确定性配置，让停止行为可重复测试。"""
        self.config = ContinuousResearchConfig(judge_enabled=False)

    def decide(self, **overrides):
        """合并测试覆写值后调用统一停止策略，减少每个场景重复搭建上下文。"""
        values = dict(state="running", elapsed_minutes=1, cycles=1, consecutive_errors=0,
                      no_progress_cycles=0, convergence_cycles=0, recent_metrics=[])
        values.update(overrides)
        return StopPolicyEngine.evaluate(self.config, **values)

    def test_hard_cycle_limit_stops(self):
        """确认达到最大研究轮数会触发硬停止。"""
        decision = self.decide(cycles=self.config.max_cycles)
        self.assertTrue(decision.should_stop)
        self.assertEqual(decision.reason_code, "MAX_CYCLES")

    def test_low_gain_enters_convergence_then_sleeps(self):
        """确认连续低进展先进入收敛观察，再按确认轮数自动休眠。"""
        low = [{"information_gain_score": 0.0, "duplicate_rate": 0.0} for _ in range(4)]
        first = self.decide(no_progress_cycles=4, recent_metrics=low)
        self.assertEqual(first.next_state, "converging")
        second = self.decide(state="converging", convergence_cycles=2, recent_metrics=low)
        self.assertTrue(second.should_sleep)
        self.assertEqual(second.reason_code, "NO_EXECUTABLE_TASKS")

    def test_non_adjacent_repeated_cycle_is_detected(self):
        """确认循环检测能识别非相邻轮次重复，而不只比较上一轮。"""
        cycles = [{"signature": text} for text in ["alpha beta gamma delta", "different work entirely",
            "another unique science result", "alpha beta gamma delta"]]
        detector = ResearchLoopDetector(0.8, 8)
        self.assertGreaterEqual(detector.score(cycles), 0.8)

    def test_new_information_resets_no_progress(self):
        """确认信息增量达到阈值后停滞计数清零。"""
        self.assertEqual(ProgressTracker.advance_no_progress(3, 0.3, 0.1), 0)
        decision = self.decide(state="converging", recent_metrics=[{"information_gain_score": 0.3}])
        self.assertEqual(decision.reason_code, "NEW_PROGRESS")
        self.assertEqual(decision.next_state, "running")

    def test_blocked_tasks_contribute_to_soft_stop(self):
        """确认没有可执行任务且存在阻塞问题时，会增加软停止信号。"""
        low = [{"information_gain_score": 0.0, "duplicate_rate": 0.0, "loop_score": 0.0,
                "executable_tasks": 0, "blocked_questions": [{"reason": "NEEDS_EXPERIMENT"}],
                "new_open_questions": 0} for _ in range(4)]
        decision = self.decide(no_progress_cycles=4, recent_metrics=low)
        self.assertEqual(decision.next_state, "converging")
        self.assertGreaterEqual(decision.metrics["soft_stop_signals"], 1)

    def test_escape_cycle_can_resume_research(self):
        """确认最终逃逸探索发现真正新方向时可以恢复研究。"""
        self.assertEqual(ContinuousResearchController.escape_outcome(True), "running")
        self.assertEqual(ContinuousResearchController.escape_outcome(False), "sleeping")

    def test_manual_stop_wins_over_hard_limits(self):
        """确认人工停止具有最高优先级，不能被自动判断覆盖。"""
        decision = self.decide(manual_stop=True, emergency_stop=True,
                               elapsed_minutes=self.config.max_runtime_minutes,
                               cycles=self.config.max_cycles)
        self.assertTrue(decision.should_stop)
        self.assertEqual(decision.reason_code, "MANUAL_STOP")

    def test_consecutive_error_limit_stops(self):
        """确认连续失败达到阈值后停止无效重试。"""
        decision = self.decide(consecutive_errors=self.config.max_consecutive_errors)
        self.assertTrue(decision.should_stop)
        self.assertEqual(decision.reason_code, "TOO_MANY_ERRORS")


if __name__ == "__main__":
    unittest.main()
