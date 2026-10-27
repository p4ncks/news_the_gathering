"""Long scheduling waits must recover promptly after a suspended host wakes."""
import unittest
from unittest.mock import patch

from news.collect import wait_for_next_run


class FakeClock:
    def __init__(self, jump=0):
        self.wall = 10000
        self.monotonic_value = 200
        self.jump = jump
        self.sleeps = []

    def time(self):
        return self.wall

    def monotonic(self):
        return self.monotonic_value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.wall += seconds + self.jump
        self.monotonic_value += seconds
        self.jump = 0


class ScheduleTests(unittest.TestCase):
    def test_suspension_catches_up_after_one_short_check(self):
        clock = FakeClock(jump=4 * 3600)
        with patch("news.collect.time", clock):
            wait_for_next_run(1800)
        self.assertEqual(clock.sleeps, [30])

    def test_backwards_clock_does_not_postpone_the_interval(self):
        clock = FakeClock(jump=-3600)
        with patch("news.collect.time", clock):
            wait_for_next_run(1800)
        self.assertEqual(sum(clock.sleeps), 1800)

    def test_ordinary_interval_is_preserved(self):
        clock = FakeClock()
        with patch("news.collect.time", clock):
            wait_for_next_run(90)
        self.assertEqual(sum(clock.sleeps), 90)
