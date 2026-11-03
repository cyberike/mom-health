"""Authentication-failure pins for the inactive ingress candidates.

gateway.py and direct_candidate.py run in process with fictional credentials,
fake clocks and fresh fixtures (mount recognition simulated, as in the
existing suites). These pin documented behavior; they do not establish
Railway edge identity, TLS, a proxy or a real abuse defense.
"""
import base64
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from railway_candidate.direct_candidate import create_direct_candidate
from railway_candidate.gateway import create_gateway

try:
    from synthetic_support import batch, fixture_dir, sample
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import batch, fixture_dir, sample

HOST = 'synthetic.example.com'
USER, PASSWORD = 'fictional-caregiver', 'fictional-caregiver-password-for-ingress-1234'
OLD_USER, OLD_PASSWORD = 'fictional-previous-caregiver', 'fictional-previous-password-for-ingress-12'
PHONE = 'fictional-phone-token-for-ingress-tests-0123456789'


def basic(user, password):
    return 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()


class GatewayAuthFailureTests(unittest.TestCase):
    def setUp(self):
        folder = fixture_dir('ingress-auth-fixtures').resolve()
        if os.name == 'posix':
            folder.chmod(0o700)
        mount = patch('railway_candidate.gateway.os.path.ismount', return_value=True)
        mount.start()
        self.addCleanup(mount.stop)
        self.seconds = 1000.0
        app = create_gateway(HOST, USER, PASSWORD, PHONE, str(folder / 'mom-health-synthetic.sqlite3'), str(folder),
                             '127.0.0.0/8', clock=lambda: self.seconds)
        self.client = TestClient(app, client=('127.0.0.1', 1234))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def call(self, method, path, auth=None, **kwargs):
        headers = {'host': HOST, 'x-forwarded-proto': 'https'}
        if auth is not None:
            headers['authorization'] = auth
        return self.client.request(method, path, headers=headers, **kwargs)

    def test_each_invalid_caregiver_credential_is_challenged_and_costs_one_failure(self):
        invalid = (basic(USER, 'not-the-caregiver-password-0000000000'), basic('someone-else', PASSWORD),
                   'Basic !!!not-base64!!!', 'Basic ' + base64.b64encode(b'no-colon-in-this-value').decode(),
                   'basic ' + basic(USER, PASSWORD)[6:], 'Basic ', 'Bearer ' + PHONE,
                   basic(USER, 'another-wrong-caregiver-password-000'))
        for auth in invalid:
            with self.subTest(auth=auth[:24]):
                response = self.call('GET', '/v1/health', auth)
                self.assertEqual(401, response.status_code)
                self.assertTrue(response.headers.get('www-authenticate', '').startswith('Basic realm='))
        # Eight recorded failures: the documented role-wide lockout now applies
        # to the valid caregiver too, while the phone role is unaffected.
        self.assertEqual(429, self.call('GET', '/v1/health', basic(USER, PASSWORD)).status_code)
        self.assertEqual(200, self.call('POST', '/v1/batches', 'Bearer ' + PHONE, json=batch(sample())).status_code)

    def test_phone_refusals_are_unchallenged_and_charged_to_the_phone_role(self):
        for _ in range(8):
            response = self.call('POST', '/v1/batches', basic(USER, PASSWORD), json=batch(sample()))
            self.assertEqual(401, response.status_code)
            self.assertNotIn('www-authenticate', response.headers)
        self.assertEqual(429, self.call('POST', '/v1/batches', 'Bearer ' + PHONE, json=batch(sample())).status_code)
        self.assertEqual(200, self.call('GET', '/v1/health', basic(USER, PASSWORD)).status_code)

    def test_lockout_counts_down_and_attempts_during_it_do_not_extend_it(self):
        for _ in range(8):
            self.assertEqual(401, self.call('GET', '/v1/health').status_code)
        for now, remaining in ((1000.0, '60'), (1030.0, '30')):
            self.seconds = now
            response = self.call('GET', '/v1/health', basic(USER, PASSWORD))
            self.assertEqual((429, remaining), (response.status_code, response.headers['retry-after']))
        for _ in range(8):  # refused while full and therefore never recorded
            self.assertEqual(429, self.call('GET', '/v1/health', basic(USER, 'wrong-during-lockout-0000000000000')).status_code)
        self.seconds = 1059.5
        self.assertEqual('1', self.call('GET', '/v1/health', basic(USER, PASSWORD)).headers['retry-after'])
        self.seconds = 1060.0
        self.assertEqual(200, self.call('GET', '/v1/health', basic(USER, PASSWORD)).status_code)


class DirectCandidateAuthFailureTests(unittest.TestCase):
    def open(self, **options):
        database = fixture_dir('ingress-auth-fixtures') / 'mom-health-synthetic.sqlite3'
        self.wall = 1000.0
        with patch('railway_candidate.direct_candidate.validate_storage', return_value=str(database)):
            app = create_direct_candidate(HOST, USER, PASSWORD, PHONE, str(database), str(database.parent),
                                          wall_clock=lambda: self.wall, **options)
        client = TestClient(app, client=('127.0.0.1', 1234))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return lambda auth: client.get('/v1/health', headers={'host': HOST, 'authorization': auth})

    def test_previous_and_current_caregiver_credentials_cannot_be_mixed(self):
        health = self.open(caregiver_previous=(OLD_USER, OLD_PASSWORD, 1100.0))
        for user, password, code in ((USER, PASSWORD, 200), (OLD_USER, OLD_PASSWORD, 200),
                                     (OLD_USER, PASSWORD, 401), (USER, OLD_PASSWORD, 401)):
            with self.subTest(user=user, password=password[:20]):
                response = health(basic(user, password))
                self.assertEqual(code, response.status_code)
                if code == 401:
                    self.assertTrue(response.headers['www-authenticate'].startswith('Basic realm='))

    def test_a_wall_clock_rollback_is_refused_until_time_catches_up(self):
        # Documented: the process refuses a wall-clock rollback during its
        # lifetime, even with no previous credential configured.
        health = self.open()
        self.assertEqual(200, health(basic(USER, PASSWORD)).status_code)
        self.wall = 999.0
        self.assertEqual(503, health(basic(USER, PASSWORD)).status_code)
        self.wall = 1000.0
        self.assertEqual(200, health(basic(USER, PASSWORD)).status_code)


if __name__ == '__main__':
    unittest.main()
