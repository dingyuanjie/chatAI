# 中文模块说明：验证本地与远端资源预算、错误类别、软性循环停止以及人工停止优先级。
import unittest

from app.continuous_research.controller import ContinuousResearchController
from app.continuous_research.models import ContinuousResearchConfig
from app.continuous_research.stop_policy import StopPolicyEngine


class ProviderResourcePolicyTests(unittest.TestCase):
    """验证本地与远端模型使用不同资源预算，同时共享停止与收敛规则。"""
    def decide(self, provider_type, *, config=None, **overrides):
        """按指定模型来源和测试覆写值创建配置并执行停止策略。"""
        config = config or ContinuousResearchConfig.from_dict({"judge_enabled": False}, provider_type)
        args = dict(state="running", elapsed_minutes=1, cycles=1, consecutive_errors=0,
                    no_progress_cycles=0, convergence_cycles=0, recent_metrics=[],
                    provider_type=provider_type, resource_usage={})
        args.update(overrides)
        return StopPolicyEngine.evaluate(config, **args)

    def test_local_runtime_hard_limit(self):
        """确认本地研究达到运行时长硬上限后停止。"""
        config = ContinuousResearchConfig.from_dict({"max_runtime_minutes": 2}, "LOCAL")
        decision = self.decide("LOCAL", config=config, elapsed_minutes=2)
        self.assertEqual(decision.reason_code, "MAX_RUNTIME")

    def test_local_convergence_uses_common_policy(self):
        """确认本地任务仍使用公共的低进展与收敛规则。"""
        config = ContinuousResearchConfig.from_dict({"judge_enabled": False}, "LOCAL")
        metrics = [{"information_gain_score": 0.0, "duplicate_rate": 0.0, "executable_tasks": 1} for _ in range(4)]
        decision = self.decide("LOCAL", config=config, no_progress_cycles=4, recent_metrics=metrics)
        self.assertEqual(decision.next_state, "converging")

    def test_remote_token_budget(self):
        """确认远端累计 Token 达到预算后休眠。"""
        config = ContinuousResearchConfig.from_dict({"max_session_tokens": 100}, "REMOTE")
        decision = self.decide("REMOTE", config=config, resource_usage={"total_tokens": 100})
        self.assertEqual(decision.reason_code, "TOKEN_BUDGET_EXHAUSTED")
        self.assertTrue(decision.should_sleep)

    def test_remote_cost_budget(self):
        """确认远端已知费用达到预算后休眠。"""
        config = ContinuousResearchConfig.from_dict({"max_session_cost": 1.5, "cost_currency": "USD"}, "REMOTE")
        decision = self.decide("REMOTE", config=config, resource_usage={"estimated_cost": 1.5, "currency": "USD"})
        self.assertEqual(decision.reason_code, "COST_BUDGET_EXHAUSTED")

    def test_unknown_cost_does_not_stop_or_raise(self):
        """确认未知费用不会触发费用停止，也不会被解释为零费用。"""
        config = ContinuousResearchConfig.from_dict({"max_session_cost": 0.01}, "REMOTE")
        decision = self.decide("REMOTE", config=config, resource_usage={"estimated_cost": None, "total_tokens": 20})
        self.assertFalse(decision.should_sleep)
        self.assertFalse(decision.should_stop)

    def test_local_provider_never_stops_for_cost(self):
        """确认本地模型策略不因远端费用预算而停止。"""
        config = ContinuousResearchConfig.from_dict({"max_session_cost": 0.01}, "LOCAL")
        decision = self.decide("LOCAL", config=config, resource_usage={"estimated_cost": 10.0, "currency": "USD"})
        self.assertFalse(decision.should_sleep)
        self.assertNotEqual(decision.reason_code, "COST_BUDGET_EXHAUSTED")

    def test_remote_quota_exhaustion(self):
        """确认远端配额错误转换为明确的配额停止原因。"""
        decision = self.decide("REMOTE", latest_error={"error_code": "API_QUOTA_EXHAUSTED"})
        self.assertEqual(decision.reason_code, "API_QUOTA_EXHAUSTED")
        self.assertTrue(decision.should_sleep)

    def test_loop_detection_is_provider_independent(self):
        """确认本地和远端模型都适用相同循环风险规则。"""
        config = ContinuousResearchConfig.from_dict({"judge_enabled": False}, "LOCAL")
        metrics = [{"information_gain_score": 0, "duplicate_rate": 1, "loop_score": 0.95, "executable_tasks": 1} for _ in range(4)]
        for provider in ("LOCAL", "REMOTE"):
            decision = self.decide(provider, config=config, no_progress_cycles=4, recent_metrics=metrics)
            self.assertEqual(decision.next_state, "converging")

    def test_manual_stop_wins_for_every_provider(self):
        """确认无论模型来源如何，人工停止都优先于预算策略。"""
        for provider in ("LOCAL", "REMOTE", "HYBRID"):
            decision = self.decide(provider, manual_stop=True, latest_error={"error_code": "API_QUOTA_EXHAUSTED"})
            self.assertEqual(decision.reason_code, "MANUAL_STOP")

    def test_new_evidence_clears_no_progress(self):
        """确认新的证据进展会重置连续无进展判断。"""
        self.assertEqual(ContinuousResearchController.escape_outcome(True), "running")
        self.assertEqual(ContinuousResearchController.escape_outcome(False), "sleeping")

    def test_remote_call_budget(self):
        """确认远端模型调用次数达到上限后自动休眠。"""
        config = ContinuousResearchConfig.from_dict({"max_model_calls": 3}, "REMOTE")
        decision = self.decide("REMOTE", config=config, resource_usage={"model_calls": 3})
        self.assertEqual(decision.reason_code, "CALL_BUDGET_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
