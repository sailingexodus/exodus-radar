"""Exercise the real watch loop without GPS, network, audio or file access."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


class EndSimulation(Exception):
    pass


class LocalAlarmOrderTest(unittest.TestCase):
    def run_watch(self, telegram_delay, distance=110):
        spec = importlib.util.spec_from_file_location(
            "anchor_alarm_order", Path(__file__).resolve().parents[1] / "anchor-watch.py"
        )
        watch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        clock = [1000.0]
        polls = [0]
        calls = []
        states = []

        def telegram(message):
            calls.append(("telegram", clock[0], message))
            clock[0] += telegram_delay
            return False  # Simulate a failed/timed-out request.

        def sleep(seconds):
            polls[0] += 1
            if polls[0] == 2:
                raise EndSimulation()
            clock[0] += seconds

        with (
            patch.object(watch, "load_cfg", return_value={
                "armed": True, "radius_ft": 100,
                "anchor": {"lat": 50, "lon": -1},
            }),
            patch.object(watch, "position", return_value=(50, -1, 0)),
            patch.object(watch, "dist_ft", return_value=distance),
            patch.object(watch, "save_json", side_effect=lambda path, value: states.append(value["state"])),
            patch.object(watch, "speak", side_effect=lambda message: calls.append(("sound", clock[0], message))),
            patch.object(watch, "tg", side_effect=telegram),
            patch.object(watch.time, "time", side_effect=lambda: clock[0]),
            patch.object(watch.time, "sleep", side_effect=sleep),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(EndSimulation):
                watch.main()
        return calls, states

    def test_stalled_telegram_does_not_delay_first_local_warning(self):
        calls, states = self.run_watch(25)
        self.assertEqual(states, ["ok", "alarm"])
        self.assertEqual([call[0] for call in calls], ["sound", "telegram"])
        self.assertEqual(calls[0][1], 1005)
        self.assertIn("Anchor alarm", calls[0][2])

    def test_immediate_telegram_still_sends_both_warnings(self):
        calls, states = self.run_watch(0)
        self.assertEqual(states, ["ok", "alarm"])
        self.assertEqual([call[0] for call in calls], ["sound", "telegram"])
        self.assertEqual([call[1] for call in calls], [1005, 1005])

    def test_inside_circle_does_not_raise_an_alarm(self):
        calls, states = self.run_watch(25, distance=90)
        self.assertEqual(states, ["ok", "ok"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
