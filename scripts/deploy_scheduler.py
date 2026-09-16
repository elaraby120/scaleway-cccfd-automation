#!/usr/bin/env python3
"""Deploy via GitHub's existing Scaleway secret, without exporting credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def job_payload(config, project_id, instance_id):
    return {
        'name': config['name'], 'project_id': project_id,
        'cpu_limit': config['cpu_limit'], 'memory_limit': config['memory_limit'],
        'image_uri': config['image_uri'], 'job_timeout': config['job_timeout'],
        'startup_command': ['python3', '/controller.py'], 'args': [],
        'description': 'Moxe Mon-Sat 08:50-21:00 Europe/Paris; application health checks',
        'environment_variables': {'INSTANCE_ID': instance_id, 'MODE': 'status',
                                  'HEALTH_URLS': ','.join(config['health_urls'])},
        'retry_policy': {'max_retries': 1},
    }


class Deployment:
    def __init__(self):
        self.config = json.loads((ROOT / 'scheduler/config.json').read_text())
        self.token = os.environ['SCALEWAY_TOKEN']
        self.instance_id = os.environ['INSTANCE_ID']
        self.jobs = f"/serverless-jobs/v1alpha2/regions/{self.config['region']}"
        self.secrets = f"/secret-manager/v1beta1/regions/{self.config['region']}"

    def api(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request('https://api.scaleway.com' + path, data=body, method=method,
                          headers={'X-Auth-Token': self.token, 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode(errors='replace').replace(self.token, '[redacted]')
            raise RuntimeError(f'{method} {path}: HTTP {exc.code}: {detail[:800]}') from None

    def listing(self, path, key, **filters):
        items = []
        for page in range(1, 101):
            result = self.api('GET', path + '?' + urlencode(dict(filters, page=page, page_size=100)))
            batch = result[key]
            items.extend(batch)
            if len(items) >= result.get('total_count', len(items)) or not batch:
                return items
        raise RuntimeError('Pagination exceeded expected resource count')

    def inspect(self):
        server = self.api('GET', f"/instance/v1/zones/{self.config['zone']}/servers/{self.instance_id}")['server']
        if server['name'] != self.config['expected_instance_name']:
            raise RuntimeError('Instance identity does not match the reviewed configuration')
        self.project = server['project']
        jobs = self.listing(self.jobs + '/job-definitions', 'job_definitions', project_id=self.project)
        matching = [job for job in jobs if job['name'] == self.config['name']]
        if len(matching) > 1:
            raise RuntimeError('Duplicate scheduler definitions; refusing ambiguous update')
        self.job = matching[0] if matching else None
        # Read permissions are checked before any resource is created.
        self.listing(self.secrets + '/secrets', 'secrets', project_id=self.project, name=self.config['name'])
        print(json.dumps({'instance_name': server['name'], 'state': server['state'],
                          'project_id': self.project, 'job_id': self.job['id'] if self.job else None}), flush=True)

    def secret_version(self, suffix, content, version_description):
        name = self.config['name'] + '-' + suffix
        secrets = self.listing(self.secrets + '/secrets', 'secrets', project_id=self.project, name=name)
        matches = [s for s in secrets if s['name'] == name]
        if len(matches) > 1:
            raise RuntimeError('Duplicate scheduler secrets')
        secret = matches[0] if matches else self.api('POST', self.secrets + '/secrets', {
            'project_id': self.project, 'name': name, 'type': 'opaque',
            'tags': ['moxe-scheduler'], 'description': 'Managed by scaleway-cccfd-automation',
        })
        versions = self.listing(f"{self.secrets}/secrets/{secret['id']}/versions", 'versions')
        current = next((v for v in versions if v['status'] == 'enabled' and
                        v.get('description') == version_description), None)
        if current is None:
            current = self.api('POST', f"{self.secrets}/secrets/{secret['id']}/versions", {
                'data': base64.b64encode(content).decode(), 'description': version_description,
            })
        return secret['id'], str(current['revision'])

    def triggers(self):
        return self.listing(self.jobs + '/triggers', 'triggers', job_definition_id=self.job['id'])

    def stage(self):
        if self.job and self.triggers():
            raise RuntimeError('Scheduler is already active; deactivate before staging an update')
        code = (ROOT / 'scheduler/controller.py').read_bytes()
        script_id, script_version = self.secret_version('controller', code, hashlib.sha256(code).hexdigest())
        token_id, token_version = self.secret_version('api-token', self.token.encode(), 'existing-github-scaleway-token')
        payload = job_payload(self.config, self.project, self.instance_id)
        if self.job:
            payload.pop('project_id')
            self.job = self.api('PATCH', self.jobs + '/job-definitions/' + self.job['id'], payload)
        else:
            self.job = self.api('POST', self.jobs + '/job-definitions', payload)
        refs = self.listing(self.jobs + '/secrets', 'secrets', job_definition_id=self.job['id'])
        for secret_id, version, reference in [(script_id, script_version, {'path': '/controller.py'}),
                                               (token_id, token_version, {'env_var_name': 'SCALEWAY_TOKEN'})]:
            existing = next((r for r in refs if r['secret_manager_id'] == secret_id), None)
            if existing:
                self.api('PATCH', self.jobs + '/secrets/' + existing['secret_id'],
                         {'secret_manager_version': version, **reference})
            else:
                self.api('POST', self.jobs + '/secrets', {'job_definition_id': self.job['id'], 'secrets': [
                    {'secret_manager_id': secret_id, 'secret_manager_version': version, **reference}]})
        print(json.dumps({'event': 'staged', 'job_id': self.job['id'], 'schedule_active': False}), flush=True)
        self.run('status')

    def run(self, mode):
        if not self.job:
            raise RuntimeError('No scheduler deployed')
        response = self.api('POST', self.jobs + '/job-definitions/' + self.job['id'] + '/start',
                            {'environment_variables': {'MODE': mode}})
        run_id = response['job_runs'][0]['id']
        print(json.dumps({'event': 'run_started', 'run_id': run_id, 'mode': mode}), flush=True)
        for _ in range(90):
            run = self.api('GET', self.jobs + '/job-runs/' + run_id)
            state = run['state']
            if state in ('succeeded', 'failed', 'interrupted'):
                safe = {key: run.get(key) for key in ('id', 'state', 'exit_code', 'reason', 'error_message', 'run_duration', 'attempts')}
                print(json.dumps(safe), flush=True)
                if state != 'succeeded':
                    raise RuntimeError('Scaleway execution failed; inspect its Cockpit logs')
                return run_id
            time.sleep(10)
        raise RuntimeError('Job verification timed out; schedules were not enabled')

    def activate(self):
        self.run('status')
        # A live reconciliation is verified before installing any cron trigger.
        self.run('reconcile')
        env = dict(self.job['environment_variables'], MODE='reconcile')
        self.job = self.api('PATCH', self.jobs + '/job-definitions/' + self.job['id'],
                            {'environment_variables': env})
        existing = {trigger['name']: trigger for trigger in self.triggers()}
        for trigger in self.config['triggers']:
            cron = {'schedule': trigger['schedule'], 'timezone': trigger['timezone']}
            if trigger['name'] in existing:
                self.api('PATCH', self.jobs + '/triggers/' + existing[trigger['name']]['id'], {'cron_config': cron})
            else:
                self.api('POST', self.jobs + '/triggers', {'job_definition_id': self.job['id'],
                         'name': trigger['name'], 'cron_config': cron})
        print(json.dumps({'event': 'activated', 'triggers': [
            {'name': t['name'], 'cron_config': t['cron_config']} for t in self.triggers()]}), flush=True)

    def deactivate(self):
        # Remove only the triggers of this identified scheduler. The instance is untouched.
        for trigger in self.triggers():
            self.api('DELETE', self.jobs + '/triggers/' + trigger['id'])
        self.api('PATCH', self.jobs + '/job-definitions/' + self.job['id'],
                 {'environment_variables': dict(self.job['environment_variables'], MODE='status')})
        print(json.dumps({'event': 'deactivated', 'job_id': self.job['id']}), flush=True)

    def status(self):
        if not self.job:
            return
        runs = self.listing(self.jobs + '/job-runs', 'job_runs', job_definition_id=self.job['id'])
        print(json.dumps({'job_id': self.job['id'], 'mode': self.job['environment_variables']['MODE'],
                          'triggers': [{'name': t['name'], 'cron_config': t['cron_config']} for t in self.triggers()],
                          'recent_runs': [{k: r.get(k) for k in ('id', 'created_at', 'state', 'exit_code', 'run_duration')}
                                          for r in sorted(runs, key=lambda r: r['created_at'], reverse=True)[:8]]}), flush=True)


def main():
    operation = sys.argv[1] if len(sys.argv) > 1 else 'inspect'
    if operation not in ('inspect', 'stage', 'activate', 'deactivate', 'status'):
        raise ValueError('Unknown deployment operation')
    deployment = Deployment()
    deployment.inspect()
    if operation != 'inspect':
        getattr(deployment, operation)()
    if deployment.job:
        result = {'job_id': deployment.job['id'], 'project_id': deployment.project,
                  'region': deployment.config['region']}
        Path('deployment-result.json').write_text(json.dumps(result, indent=2) + '\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
                summary.write(f"Operation `{operation}` completed. Job `{deployment.job['id']}` in `{deployment.config['region']}`.\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Deployment failed: {exc}', file=sys.stderr)
        sys.exit(1)
