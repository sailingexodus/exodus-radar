"""Run the anchor-watch loop with a simulated clock, GPS and alarm outputs."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


class EndSimulation(Exception):
    pass


class AnchorStartupGpsTest(unittest.TestCase):
    def run_watch(self, duration, position=None, disarm_at=None):
        path = Path(__file__).resolve().parents[1] / 'anchor-watch.py'
        spec = importlib.util.spec_from_file_location('anchor_watch_test', path)
        watch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        clock = [1000.0]
        state = {'anchor': None}
        statuses = []
        speak, telegram = Mock(), Mock()

        def load_config():
            return {'armed': disarm_at is None or clock[0] < 1000 + disarm_at,
                    'radius_ft': 150.0, 'anchor': state['anchor']}

        def save_json(path, value):
            if path == watch.CFG:
                state['anchor'] = value['anchor']
            else:
                statuses.append((clock[0] - 1000, dict(value)))

        def sleep(seconds):
            clock[0] += seconds
            if clock[0] > 1000 + duration:
                raise EndSimulation()

        def get_position():
            return position(clock[0] - 1000) if callable(position) else position

        with (
            patch.object(watch, 'load_cfg', side_effect=load_config),
            patch.object(watch, 'save_json', side_effect=save_json),
            patch.object(watch, 'position', side_effect=get_position),
            patch.object(watch, 'speak', speak),
            patch.object(watch, 'tg', telegram),
            patch.object(watch.time, 'time', side_effect=lambda: clock[0]),
            patch.object(watch.time, 'sleep', side_effect=sleep),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(EndSimulation):
                watch.main()
        return speak, telegram, statuses, state

    def test_missing_or_stale_gps_warns_after_timeout_without_anchor(self):
        for position in (None, (50.0, -1.0, 1000)):
            with self.subTest(position=position):
                speak, telegram, statuses, state = self.run_watch(90, position)
                speak.assert_called_once_with('Anchor watch has lost the GPS')
                self.assertTrue(any('CANNOT SEE GPS' in call.args[0] for call in telegram.call_args_list))
                self.assertEqual(statuses[-1][1]['state'], 'nogps_alarm')
                self.assertIsNone(state['anchor'])

    def test_does_not_warn_before_timeout(self):
        speak, _, statuses, _ = self.run_watch(85)
        speak.assert_not_called()
        self.assertEqual(statuses[-1][1]['state'], 'nogps')

    def test_disarming_cancels_pending_warning(self):
        speak, _, statuses, _ = self.run_watch(120, disarm_at=30)
        speak.assert_not_called()
        self.assertEqual(statuses[-1][1]['state'], 'off')

    def test_fresh_gps_before_timeout_sets_anchor_without_warning(self):
        speak, _, statuses, state = self.run_watch(120, lambda elapsed: None if elapsed < 30 else (50.0, -1.0, 0))
        speak.assert_not_called()
        self.assertEqual(statuses[-1][1]['state'], 'ok')
        self.assertEqual(state['anchor']['lat'], 50.0)

    def test_gps_return_clears_warning_and_sets_anchor(self):
        speak, _, statuses, state = self.run_watch(120, lambda elapsed: None if elapsed < 100 else (50.0, -1.0, 0))
        speak.assert_called_once_with('Anchor watch has lost the GPS')
        self.assertEqual(statuses[-1][1]['state'], 'ok')
        self.assertIsNotNone(state['anchor'])

    def test_normal_drag_alarm_still_works(self):
        speak, _, statuses, _ = self.run_watch(10, lambda elapsed: (50.0 if elapsed == 0 else 50.001, -1.0, 0))
        self.assertEqual(statuses[-1][1]['state'], 'alarm')
        self.assertTrue(any(call.args[0].startswith('Anchor alarm.') for call in speak.call_args_list))


if __name__ == '__main__':
    unittest.main()
