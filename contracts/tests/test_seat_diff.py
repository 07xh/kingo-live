"""Cross-monitor regression cases for unknown versus zero seats."""
import unittest
from dataclasses import dataclass

from contracts.seat_diff import classify_seat_delta, remaining_count


@dataclass
class Offer:
    remain: int | None


class SeatDeltaTests(unittest.TestCase):
    def test_missing_and_invalid_counts_remain_unknown(self):
        for value in (None, -1, "", "N/A", True, False):
            with self.subTest(value=value):
                self.assertIsNone(remaining_count(Offer(value)))

    def test_known_zero_is_not_unknown(self):
        self.assertEqual(remaining_count(Offer(0)), 0)
        self.assertEqual(remaining_count(Offer("03")), 3)

    def test_release_events_are_strictly_observed(self):
        self.assertEqual(classify_seat_delta(Offer(2), first_observation=True), "new")
        self.assertIsNone(classify_seat_delta(Offer(0), first_observation=True))
        self.assertEqual(classify_seat_delta(Offer(2), Offer(0)), "reopened")
        self.assertEqual(classify_seat_delta(Offer(3), Offer(2)), "increased")
        self.assertEqual(classify_seat_delta(
            Offer(1), Offer(3), include_decrease=True), "decreased")
        self.assertIsNone(classify_seat_delta(Offer(1), Offer(3)))

    def test_unknown_never_creates_fictitious_release(self):
        self.assertIsNone(classify_seat_delta(Offer(2), Offer(None)))
        self.assertIsNone(classify_seat_delta(Offer(None), Offer(0)))
        self.assertIsNone(classify_seat_delta(Offer(0), Offer(None)))


if __name__ == "__main__":
    unittest.main()
