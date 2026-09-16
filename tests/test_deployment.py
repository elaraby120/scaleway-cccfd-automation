import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class DeploymentTests(unittest.TestCase):
    def test_deployment_script_exists(self):
        self.assertTrue((ROOT / 'scripts/deploy_scheduler.py').exists(), 'Safe staged deployment is missing')

    def test_payload_keeps_credentials_out_of_environment_and_schedules_disabled(self):
        path = ROOT / 'scripts/deploy_scheduler.py'
        if not path.exists():
            self.skipTest('Deployment not implemented yet')
        spec = importlib.util.spec_from_file_location('deployment', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = json.loads((ROOT / 'scheduler/config.json').read_text())
        payload = module.job_payload(config, 'project-example', 'instance-example')
        self.assertEqual(payload['environment_variables']['MODE'], 'status')
        self.assertNotIn('SCALEWAY_TOKEN', payload['environment_variables'])
        self.assertNotIn('cron_schedule', payload)
        self.assertEqual(payload['startup_command'], ['python3', '/controller.py'])
        self.assertEqual(payload['cpu_limit'], 560)
        self.assertEqual(payload['memory_limit'], 1024)
        self.assertGreater(payload.get('local_storage_capacity', 0), 0)


if __name__ == '__main__':
    unittest.main()
