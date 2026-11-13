"""Regression coverage for mandatory server cooldown semantics (offline only)."""
import tempfile
import unittest
from pathlib import Path

from live_client import Engine, RequestBudget, Strategy


class CooldownSafetyTests(unittest.TestCase):
    def test_legacy_switch_cannot_disable_retry_after(self):
        with self.assertRaises(ValueError):
            Strategy.from_dict({"respect_retry_after": False})

    def test_server_cooldown_blocks_physical_http_request(self):
        with tempfile.TemporaryDirectory() as td:
            budget = RequestBudget(str(Path(td) / "budget.sqlite"), 100, 10, 1000)
            budget.cooldown(120_000)
            engine = Engine.__new__(Engine)
            engine.budget = budget
            engine.st = Strategy()

            class ForbiddenTransport:
                def request(self, *args, **kwargs):
                    raise AssertionError("cooldown attempted a physical HTTP request")

            response = engine._budgeted(ForbiddenTransport(), "GET", "/course-list")
            self.assertEqual(response.status, 429)
            self.assertEqual(response.error, "budget:retry_after")


if __name__ == "__main__":
    unittest.main()
