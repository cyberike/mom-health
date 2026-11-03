"""Authorization failures at the main app's Boundary, including across a restart
that rotates the access token. Fictional tokens and fresh SQLite fixtures only.
"""
from contextlib import closing
import socket
import sqlite3
import unittest

from fastapi.testclient import TestClient

from mom_health_backend.app import Settings, create_app

try:
    from synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample, status
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample, status

OTHER = 'synthetic-resilience-token-rotated-not-deployed-02'
PROTECTED = (('GET', '/v1/health', None), ('GET', '/v1/devices', None),
             ('GET', '/v1/samples?device_id=synthetic-watch', None),
             ('GET', '/v1/gateway-status?device_id=synthetic-watch', None),
             ('POST', '/v1/batches', 'batch'), ('POST', '/v1/gateway-status', 'status'))


class BoundaryAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.database = fixture_dir('auth-fixtures') / 'synthetic.sqlite3'

    def client(self, token=TOKEN):
        client = TestClient(create_app(Settings(sqlite_path=str(self.database), access_token=token)), client=('127.0.0.1', 50000))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def stored(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return [connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('samples', 'batches', 'gateway_status')]

    def send(self, client, method, path, body, headers):
        payload = {'batch': batch(sample()), 'status': status()}.get(body)
        return client.request(method, path, json=payload, headers=headers)

    def test_wrong_or_malformed_credentials_are_refused_everywhere_without_side_effects(self):
        client = self.client()
        # Surrounding whitespace is not listed: over real HTTP, h11 trims it
        # (see test_real_http_trims_surrounding_whitespace_but_not_inner_spacing).
        refused = {'missing': {}, 'wrong token': {'Authorization': 'Bearer ' + OTHER},
                   'lowercase scheme': {'Authorization': 'bearer ' + TOKEN},
                   'double space': {'Authorization': 'Bearer  ' + TOKEN},
                   'basic scheme': {'Authorization': 'Basic ' + TOKEN},
                   'token only': {'Authorization': TOKEN}}
        for label, headers in refused.items():
            for method, path, body in PROTECTED:
                with self.subTest(credential=label, route=method + ' ' + path):
                    response = self.send(client, method, path, body, headers)
                    self.assertEqual(401, response.status_code)
                    self.assertEqual({'detail': 'Authorization required'}, response.json())
                    self.assertNotIn(TOKEN, response.text)
        query = client.get('/v1/devices', params={'access_token': TOKEN, 'token': TOKEN})
        self.assertEqual(401, query.status_code)
        self.assertEqual([0, 0, 0], self.stored())

    def test_authorization_is_checked_before_the_body_is_read(self):
        client = self.client()
        oversized = b'{' + b' ' * 200000 + b'}'
        self.assertEqual(401, client.post('/v1/batches', content=oversized, headers={'Content-Type': 'application/json'}).status_code)
        self.assertEqual(413, client.post('/v1/batches', content=oversized, headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}).status_code)
        self.assertEqual(400, client.get('/v1/health', headers=[('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer ' + TOKEN)]).status_code)
        self.assertEqual([0, 0, 0], self.stored())

    def test_only_the_exact_dashboard_shell_is_exempt(self):
        client = self.client()
        for path in ('/dashboard/', '/dashboard/styles.css', '/dashboard/core.js', '/dashboard/app.js'):
            with self.subTest(path=path):
                self.assertEqual(200, client.get(path).status_code)
                # The Boundary exempts HEAD for the shell too; FastAPI GET routes
                # do not register HEAD, so it is answered 405, never with data.
                self.assertNotEqual(401, client.head(path).status_code)
        for method, path in (('GET', '/dashboard'), ('GET', '/dashboard/index.html'), ('GET', '/dashboard/../v1/devices'),
                             ('POST', '/dashboard/'), ('OPTIONS', '/v1/devices'), ('HEAD', '/v1/devices'),
                             ('HEAD', '/v1/samples?device_id=synthetic-watch'), ('GET', '/docs'), ('GET', '/openapi.json')):
            with self.subTest(method=method, path=path):
                self.assertEqual(401, client.request(method, path).status_code)

    def test_real_http_trims_surrounding_whitespace_but_not_inner_spacing(self):
        # The Boundary compares the header byte-exactly. Over a real listener,
        # h11 strips optional whitespace around a field value (RFC 9110), so a
        # trailing space or tab is accepted there, while spacing inside the
        # value and the scheme's case still must match exactly.
        app = create_app(Settings(sqlite_path=str(self.database), access_token=TOKEN))
        expected = ((b'Bearer ' + TOKEN.encode() + b' ', 200), (b'Bearer ' + TOKEN.encode() + b'\t', 200),
                    (b'Bearer  ' + TOKEN.encode(), 401), (b'bearer ' + TOKEN.encode(), 401), (b'Bearer ' + OTHER.encode(), 401))
        with LoopbackServer(app) as server:
            for value, status_code in expected:
                with self.subTest(value=value), closing(socket.create_connection(('127.0.0.1', server.port), timeout=5)) as stream:
                    stream.sendall(b'GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nAuthorization: ' + value + b'\r\n\r\n')
                    reply = b''
                    while chunk := stream.recv(4096):
                        reply += chunk
                    self.assertEqual(status_code, int(reply.split(b' ', 2)[1]))

    def test_restart_with_a_rotated_token_refuses_the_old_one_and_keeps_the_data(self):
        first = self.client(TOKEN)
        self.assertEqual(200, first.post('/v1/batches', json=batch(sample()), headers={'Authorization': 'Bearer ' + TOKEN}).status_code)
        first.__exit__(None, None, None)
        rotated = self.client(OTHER)
        self.assertEqual(401, rotated.get('/v1/devices', headers={'Authorization': 'Bearer ' + TOKEN}).status_code)
        self.assertEqual(401, rotated.post('/v1/batches', json=batch(sample()), headers={'Authorization': 'Bearer ' + TOKEN}).status_code)
        listing = rotated.get('/v1/devices', headers={'Authorization': 'Bearer ' + OTHER})
        self.assertEqual(200, listing.status_code)
        self.assertEqual(1, listing.json()['devices'][0]['sample_count'])
        self.assertEqual([1, 1, 0], self.stored())


if __name__ == '__main__':
    unittest.main()
