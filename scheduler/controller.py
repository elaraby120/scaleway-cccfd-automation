#!/usr/bin/env python3
"""Reconcile one Scaleway instance. Standard library only; never logs secrets."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


class ReconcileError(RuntimeError):
    pass


def desired_state(now):
    local = now.astimezone(ZoneInfo('Europe/Paris'))
    minutes = local.hour * 60 + local.minute
    return 'running' if local.weekday() < 6 and 530 <= minutes < 1260 else 'stopped'


class InstanceAPI:
    def __init__(self, instance_id, token, zone='fr-par-1'):
        self.url = f'https://api.scaleway.com/instance/v1/zones/{zone}/servers/{instance_id}'
        self.token = token

    def request(self, action=None):
        data = json.dumps({'action': action}).encode() if action else None
        request = Request(self.url + ('/action' if action else ''), data=data,
                          headers={'X-Auth-Token': self.token, 'Content-Type': 'application/json'})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=15) as response:
                    expected = 202 if action else 200
                    if response.status != expected:
                        raise ReconcileError(f'unexpected API HTTP {response.status}')
                    return json.load(response)
            except HTTPError as exc:
                # Retrying a GET is safe. An ambiguous POST is reconciled on the next run.
                if not action and (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ReconcileError(f'Scaleway API HTTP {exc.code}') from None
            except (URLError, TimeoutError, ValueError, OSError):
                if not action and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ReconcileError('Scaleway API request failed') from None

    def state(self):
        state = self.request().get('server', {}).get('state')
        if not state:
            raise ReconcileError('Scaleway returned no instance state')
        return state

    def action(self, action):
        self.request(action)


def application_healthy(url):
    try:
        # This request is intentionally separate: it never receives the Scaleway token.
        with urlopen(Request(url, headers={'User-Agent': 'moxe-scheduler/1'}), timeout=10) as response:
            if not 200 <= response.status < 300:
                return False
            if url.endswith('/healthz'):
                return json.load(response).get('status') == 'ok'
            return True
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return False


def reconcile(api, mode='reconcile', health_urls=(), now=None, sleep=time.sleep,
              monotonic=time.monotonic, health_check=application_healthy, wait_seconds=180):
    if mode not in ('status', 'reconcile'):
        raise ValueError('MODE must be status or reconcile')
    now = now or (lambda: datetime.now(timezone.utc))
    deadline = monotonic() + wait_seconds
    requested = None
    actions = []
    while True:
        state = api.state()
        target = desired_state(now())  # Re-evaluate after network delay and every poll.
        result = {'state': state, 'desired_state': target, 'mode': mode, 'actions': actions}
        if mode == 'status':
            if state == 'running':
                result['healthy'] = all(health_check(url) for url in health_urls)
                if not result['healthy']:
                    raise ReconcileError('application health check failed')
            return result
        if state not in ('running', 'stopped', 'starting', 'stopping'):
            raise ReconcileError(f'unsupported instance state: {state}')
        if state == target:
            if target == 'stopped':
                return result
            result['healthy'] = all(health_check(url) for url in health_urls)
            if result['healthy']:
                return result
            if monotonic() >= deadline:
                raise ReconcileError('application health check timeout; instance remains running')
        elif state in ('running', 'stopped') and requested != target:
            action = 'poweron' if target == 'running' else 'poweroff'
            api.action(action)
            requested = target
            actions.append(action)
            print(json.dumps({'event': 'power_request_accepted', 'action': action}), flush=True)
        if monotonic() >= deadline:
            raise ReconcileError(f'instance transition timeout: state={state}, desired={target}')
        sleep(5)


def main():
    try:
        instance = os.environ['INSTANCE_ID']
        token = os.environ['SCALEWAY_TOKEN']
        health_urls = [url for url in os.environ.get('HEALTH_URLS', '').split(',') if url]
        result = reconcile(InstanceAPI(instance, token), mode=os.environ.get('MODE', 'status'),
                           health_urls=health_urls)
        result['timestamp'] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result), flush=True)
        return 0
    except (KeyError, ValueError, ReconcileError) as exc:
        print(json.dumps({'event': 'scheduler_failed', 'error': str(exc)}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
