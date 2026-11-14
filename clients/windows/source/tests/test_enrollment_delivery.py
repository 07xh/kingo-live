"""Offline delivery presentation: no network, no school credentials."""
import unittest

from core.enrollment import RunReport
from core.enrollment_delivery import display_delivery
from core.enrollment_state import EngineState, Transition


class DeliveryPresentationTests(unittest.TestCase):
    def test_three_distinct_delivery_states(self):
        report = RunReport(
            {"A": "学校选课窗口未开放",
             "B": "官方记录未确认，已停止防止重复提交",
             "C": "写入结果不确定，已停止防止重复提交"},
            True,
            delivery={"A": "not_sent", "B": "response_received", "C": "unknown"},
        )
        view = display_delivery(report)
        self.assertIn("本次未发送", view.target_labels["A"])
        self.assertIn("已收到提交回执", view.target_labels["B"])
        self.assertIn("非选中证明", view.target_labels["B"])
        self.assertIn("发送状态未知", view.target_labels["C"])
        self.assertIn("禁止自动重发", view.target_labels["C"])
        self.assertIn("1 项", view.headline)
        self.assertNotIn("选课成功", view.headline)

    def test_older_report_with_submitting_transition_is_not_called_unsent(self):
        report = RunReport(
            {"A": "此前提交结果待核验"}, True,
            transitions={"A": (
                Transition(EngineState.TRIGGERED, EngineState.SUBMITTING, "test"),
            )},
        )
        self.assertIn("发送状态未知", display_delivery(report).target_labels["A"])

    def test_old_report_without_write_transition_is_unsent_this_run(self):
        report = RunReport({"A": "存在未核实的历史提交"}, True)
        self.assertIn("本次未发送", display_delivery(report).target_labels["A"])
        self.assertIn("历史提交", display_delivery(report).target_labels["A"])

    def test_report_is_presentation_only(self):
        report = RunReport({"A": "待核验"}, True, delivery={"A": "unknown"})
        before = repr(report)
        display_delivery(report)
        self.assertEqual(repr(report), before)


if __name__ == "__main__":
    unittest.main()
