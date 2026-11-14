"""Safe, offline display of the *current run* enrollment delivery evidence.

Never infer that a server accepted an enrollment from an HTTP receipt, or
that the lack of a local SUBMITTING transition proves a historical write never
happened. All labels apply only to THIS run; official selected-course
confirmation is displayed separately from transport outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.enrollment import RunReport
from core.enrollment_state import EngineState


_LABELS = {
    "not_sent": "本次未发送",
    "response_received": "已收到提交回执（非选中证明）",
    "unknown": "发送状态未知（禁止自动重发）",
}


@dataclass(frozen=True)
class DeliveryView:
    headline: str
    target_labels: dict[str, str]


def display_delivery(report: RunReport) -> DeliveryView:
    """Return a GUI-safe per-target status and aggregate, with no network I/O."""
    if not isinstance(report, RunReport):
        raise TypeError("expected RunReport")
    counts = {"not_sent": 0, "response_received": 0, "unknown": 0}
    rows: dict[str, str] = {}
    for key, state in report.states.items():
        delivery = report.delivery.get(key)
        if delivery not in _LABELS:
            # Older RunReport instances did not expose delivery. If we can
            # see a SUBMITTING transition, never mislabel it as unsent.
            entered_submit = any(
                move.after == EngineState.SUBMITTING
                for move in report.transitions.get(key, ())
            )
            delivery = "unknown" if entered_submit else "not_sent"
        counts[delivery] += 1
        rows[key] = f"{_LABELS[delivery]}｜{state}"
    headline = (
        f"本次未发送 {counts['not_sent']} 项；"
        f"收到提交回执 {counts['response_received']} 项；"
        f"发送状态未知 {counts['unknown']} 项"
    )
    return DeliveryView(headline, rows)
