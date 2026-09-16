import importlib.util
import unittest
from datetime import datetime
from pathlib import Path


MODULE = Path(__file__).parents[1] / 'scheduler' / 'controller.py'


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('controller', MODULE)
        cls.c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.c)

    def controller(self):
        return self.c

    def test_paris_boundaries_saturday_sunday_and_daylight_saving(self):
        c = self.controller()
        cases = {
            '2026-09-16T06:49:59+00:00': 'stopped',
            '2026-09-16T06:50:00+00:00': 'running',
            '2026-09-16T18:59:59+00:00': 'running',
            '2026-09-16T19:00:00+00:00': 'stopped',
            '2026-09-19T10:00:00+00:00': 'running',
            '2026-09-20T10:00:00+00:00': 'stopped',
            '2026-12-14T07:49:59+00:00': 'stopped',
            '2026-12-14T07:50:00+00:00': 'running',
            '2026-12-14T20:00:00+00:00': 'stopped',
        }
        for date, expected in cases.items():
            with self.subTest(date=date):
                self.assertEqual(c.desired_state(datetime.fromisoformat(date)), expected)

    def run_controller(self, states, mode='reconcile', healthy=True, date='2026-09-16T10:00:00+00:00'):
        c = self.controller()
        api = FakeInstance(states)
        clock = FakeClock()
        health_calls = []

        def health(url):
            health_calls.append(url)
            return healthy

        result = c.reconcile(api, mode=mode, health_urls=['https://app.example/health'],
                             now=lambda: datetime.fromisoformat(date),
                             sleep=clock.sleep, monotonic=clock.monotonic,
                             health_check=health, wait_seconds=10)
        return result, api, health_calls

    def test_start_is_confirmed_by_state_and_application_health(self):
        result, api, calls = self.run_controller(['stopped', 'starting', 'running'])
        self.assertEqual(api.actions, ['poweron'])
        self.assertEqual(result['state'], 'running')
        self.assertTrue(result['healthy'])
        self.assertTrue(calls)

    def test_running_instance_is_not_restarted(self):
        result, api, _ = self.run_controller(['running'])
        self.assertEqual(api.actions, [])
        self.assertTrue(result['healthy'])

    def test_starting_instance_is_not_started_twice(self):
        _, api, _ = self.run_controller(['starting', 'running'])
        self.assertEqual(api.actions, [])

    def test_sunday_stops_instance_and_verifies_stopped(self):
        result, api, calls = self.run_controller(['running', 'stopping', 'stopped'], date='2026-09-20T10:00:00+00:00')
        self.assertEqual(api.actions, ['poweroff'])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(calls, [])

    def test_status_mode_never_changes_power(self):
        result, api, _ = self.run_controller(['stopped'], mode='status')
        self.assertEqual(api.actions, [])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['desired_state'], 'running')

    def test_accepted_poweron_that_never_starts_is_a_failure(self):
        c = self.controller()
        with self.assertRaisesRegex(c.ReconcileError, 'timeout'):
            self.run_controller(['stopped', 'starting'])

    def test_unhealthy_application_is_a_failure_without_rebooting(self):
        c = self.controller()
        with self.assertRaisesRegex(c.ReconcileError, 'health'):
            self.run_controller(['running'], healthy=False)

    def test_unknown_state_does_not_trigger_power_action(self):
        c = self.controller()
        api = FakeInstance(['locked'])
        with self.assertRaisesRegex(c.ReconcileError, 'unsupported'):
            c.reconcile(api)
        self.assertEqual(api.actions, [])

    def test_invalid_mode_does_not_access_instance(self):
        c = self.controller()
        api = FakeInstance(['running'])
        with self.assertRaises(ValueError):
            c.reconcile(api, mode='typo')
        self.assertEqual(api.reads, 0)


class FakeClock:
    def __init__(self):
        self.elapsed = 0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class FakeInstance:
    def __init__(self, states):
        self.states = list(states)
        self.actions = []
        self.reads = 0

    def state(self):
        self.reads += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def action(self, action):
        self.actions.append(action)


if __name__ == '__main__':
    unittest.main()
