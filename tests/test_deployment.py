import importlib.util
import base64
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'scripts/deploy_scheduler.py'
        spec = importlib.util.spec_from_file_location('deployment', path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_payload_keeps_credentials_out_of_environment_and_schedules_disabled(self):
        config = json.loads((ROOT / 'scheduler/config.json').read_text())
        payload = self.module.job_payload(config, 'project-example', 'instance-example')
        self.assertEqual(payload['environment_variables']['MODE'], 'status')
        self.assertNotIn('SCALEWAY_TOKEN', payload['environment_variables'])
        self.assertNotIn('cron_schedule', payload)
        self.assertEqual(payload['startup_command'], ['python3', '/controller.py'])
        self.assertEqual(payload['cpu_limit'], 560)
        self.assertEqual(payload['memory_limit'], 1024)
        self.assertGreater(payload.get('local_storage_capacity', 0), 0)

    def test_rotated_token_creates_new_secret_version_and_unchanged_token_reuses_it(self):
        deployment = self.module.Deployment.__new__(self.module.Deployment)
        deployment.config = {'name': 'moxe-instance-scheduler'}
        deployment.project = 'project-example'
        deployment.secrets = '/secret-manager/v1beta1/regions/fr-par'
        versions = [{'revision': 1, 'status': 'enabled',
                     'description': 'existing-github-scaleway-token'}]
        created_payloads = []

        def listing(path, key, **filters):
            if key == 'secrets':
                return [{'id': 'secret-example', 'name': 'moxe-instance-scheduler-api-token'}]
            return versions

        def api(method, path, payload):
            self.assertEqual((method, path), ('POST', deployment.secrets + '/secrets/secret-example/versions'))
            created_payloads.append(payload)
            version = {'revision': len(versions) + 1, 'status': 'enabled', 'description': payload['description']}
            versions.append(version)
            return version

        deployment.listing = listing
        deployment.api = api
        first = deployment.secret_version('api-token', b'new-token')
        second = deployment.secret_version('api-token', b'new-token')
        self.assertEqual(first, ('secret-example', '2'))
        self.assertEqual(second, first)
        self.assertEqual(len(created_payloads), 1)
        self.assertEqual(base64.b64decode(created_payloads[0]['data']), b'new-token')


if __name__ == '__main__':
    unittest.main()
