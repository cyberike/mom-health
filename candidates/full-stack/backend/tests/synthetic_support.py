"""Shared synthetic-only helpers for the resilience suites (not collected as tests).

Fixtures live in unique ``build/<suite>/<uuid4>`` directories and are retained
for inspection, matching the existing suites. Servers bind 127.0.0.1 only, are
owned by the test that starts them and must stop before the test returns.
Every value is fictional; nothing here contacts a phone, watch or external host.
"""
import http.client
import json
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import uvicorn

# Test constant only; never deployed or stored as an operational credential.
TOKEN = 'synthetic-resilience-token-not-for-deployment-01'


def fixture_dir(suite):
    path = Path(__file__).resolve().parents[2] / 'build' / suite / str(uuid4())
    path.mkdir(parents=True)
    return path


def sample(**changes):
    value = dict(sample_id=str(uuid4()), device_id='synthetic-watch', session_id=str(uuid4()), model='V8',
                 source='live', received_at='2026-10-22T00:00:00Z', measured_at=None, heart_rate_bpm=80,
                 missing_reason=None, synthetic=True)
    value.update(changes)
    return value


def batch(*samples, batch_id=None):
    return dict(schema_version=1, batch_id=batch_id or str(uuid4()), samples=list(samples))


def status(device_id='synthetic-watch', **changes):
    value = dict(schema_version=1, device_id=device_id, model='V8', reported_at='2026-10-22T00:00:05Z',
                 last_observation_at=None, battery_percent=55, connection='connected', pending_samples=1,
                 uploaded_samples=2, recording_enabled=True, synthetic=True)
    value.update(changes)
    return value


class LoopbackServer:
    """Run an ASGI app on 127.0.0.1:<ephemeral> for the duration of a with-block."""

    def __init__(self, app, **options):
        self.app, self.options = app, options

    def __enter__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        options = dict(log_level='critical', access_log=False, lifespan='on', proxy_headers=False,
                       timeout_graceful_shutdown=5)
        options.update(self.options)
        config = uvicorn.Config(self.app, **options)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.listener]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            self.__exit__(None, None, None)
            raise RuntimeError('Synthetic loopback server failed to start')
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(15)
        self.listener.close()
        if self.thread.is_alive():
            raise RuntimeError('Synthetic loopback server failed to stop')

    def request(self, method, path, body=None, token=TOKEN, timeout=20, headers=None):
        """Plain HTTP/1.1 over loopback; no proxy or environment configuration applies.

        ``headers`` may override defaults, e.g. a fictional Host for the hosted
        candidates (http.client then sends that Host instead of 127.0.0.1).
        """
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            extra, headers = headers or {}, {'Connection': 'close'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            headers.update(extra)
            payload = None
            if body is not None:
                payload = json.dumps(body).encode()
                headers['Content-Type'] = 'application/json'
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
