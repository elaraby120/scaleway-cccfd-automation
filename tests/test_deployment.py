import importlib.util
import base64
import json
import unittest
from unittest.mock import patch
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

    def alert_deployment(self, contacts=None, enabled=True):
        deployment = self.module.Deployment.__new__(self.module.Deployment)
        deployment.config = {'region': 'fr-par'}
        deployment.project = 'project-example'
        contacts = list(contacts or [])
        mutations = []

        def api(method, path, payload=None):
            if method == 'GET' and '/alert-manager?' in path:
                return {'alert_manager_enabled': True}
            if method == 'GET' and '/alerts?' in path:
                return {'alerts': [{'name': 'JobRunFailed', 'rule_status': 'enabled' if enabled else 'disabled'}]}
            mutations.append((method, path, payload))
            if path.endswith('/contact-points'):
                contacts.append({'email': payload['email'], 'send_resolved_notifications': True})
            return {}

        deployment.api = api
        deployment.listing = lambda *args, **kwargs: contacts
        return deployment, mutations, contacts

    def test_alert_contact_creation_is_verified_and_repeat_is_idempotent(self):
        deployment, mutations, contacts = self.alert_deployment()
        with patch.dict('os.environ', {'ALERT_EMAIL': 'owner@example.com', 'SEND_ALERT_TEST': 'false'}):
            deployment.configure_alerts()
            deployment.configure_alerts()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0][0], 'POST')
        self.assertEqual(contacts, [{'email': {'to': 'owner@example.com'}, 'send_resolved_notifications': True}])

    def test_alert_test_cannot_notify_unrequested_contacts(self):
        deployment, mutations, _ = self.alert_deployment([
            {'email': {'to': 'someone@example.com'}, 'send_resolved_notifications': True}])
        with patch.dict('os.environ', {'ALERT_EMAIL': 'owner@example.com', 'SEND_ALERT_TEST': 'true'}):
            with self.assertRaisesRegex(RuntimeError, 'other recipients'):
                deployment.configure_alerts()
        self.assertFalse(any(path.endswith('/trigger-test-alert') for _, path, _ in mutations))

    def test_disabled_alert_is_not_activated_by_contact_configuration(self):
        deployment, mutations, _ = self.alert_deployment(enabled=False)
        with patch.dict('os.environ', {'ALERT_EMAIL': 'owner@example.com'}):
            with self.assertRaisesRegex(RuntimeError, 'already be enabled'):
                deployment.configure_alerts()
        self.assertEqual(mutations, [])

    def test_test_alert_is_requested_for_the_configured_recipient(self):
        deployment, mutations, _ = self.alert_deployment()
        with patch.dict('os.environ', {'ALERT_EMAIL': 'owner@example.com', 'SEND_ALERT_TEST': 'true'}):
            deployment.configure_alerts()
        self.assertTrue(mutations[-1][1].endswith('/trigger-test-alert'))
        self.assertEqual(mutations[-1][2], {'project_id': 'project-example'})


if __name__ == '__main__':
    unittest.main()
