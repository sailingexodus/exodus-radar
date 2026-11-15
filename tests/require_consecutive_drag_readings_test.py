import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

class EndSimulation(Exception): pass

class ConsecutiveDragTest(unittest.TestCase):
    def run_watch(self, distances):
        spec = importlib.util.spec_from_file_location('anchor_consecutive',
            Path(__file__).resolve().parents[1] / 'anchor-watch.py')
        watch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        statuses = []
        index = [0]
        def save(path, value): statuses.append(dict(value))
        def sleep(seconds):
            index[0] += 1
            if index[0] == len(distances): raise EndSimulation()
        with (patch.object(watch, 'load_cfg', return_value={'armed': True,
                'radius_ft': 100, 'anchor': {'lat': 50, 'lon': -1}}),
              patch.object(watch, 'position', return_value=(50, -1, 0)),
              patch.object(watch, 'dist_ft', side_effect=lambda *a: distances[index[0]]),
              patch.object(watch, 'save_json', side_effect=save),
              patch.object(watch, 'speak'), patch.object(watch, 'tg'),
              patch.object(watch.time, 'time', side_effect=lambda: 1000 + index[0]*5),
              patch.object(watch.time, 'sleep', side_effect=sleep),
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaises(EndSimulation): watch.main()
        return [s['state'] for s in statuses]

    def test_inside_reading_interrupts_pending_alarm(self):
        self.assertEqual(self.run_watch([110, 98, 110, 110]),
                         ['ok', 'ok', 'ok', 'alarm'])

    def test_established_alarm_retains_clearance_hysteresis(self):
        self.assertEqual(self.run_watch([110, 110, 98, 94]),
                         ['ok', 'alarm', 'alarm', 'ok'])
