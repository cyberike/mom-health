"""Device isolation: samples, cursors, summaries and status reports never
surface under another device ID, including IDs that differ only by a suffix,
separator or letter case. Fictional data and fresh SQLite fixtures only.
"""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from mom_health_backend import repository
from mom_health_backend.app import Settings, create_app
from mom_health_backend.repository import encode_cursor

try:
    from synthetic_support import TOKEN, batch, fixture_dir, sample, status
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import TOKEN, batch, fixture_dir, sample, status

SIMILAR = ('synthetic-dev', 'synthetic-dev-1', 'synthetic-dev_1', 'SYNTHETIC-DEV', 'synthetic-dev-10')


class FixedClock:
    """Replaces repository.datetime so save_status sees chosen wall-clock times."""

    def __init__(self, *times):
        self.times = list(times)

    def now(self, tz=None):
        return self.times.pop(0)


class DeviceIsolationTests(unittest.TestCase):
    def open(self, **settings):
        database = fixture_dir('isolation-fixtures') / 'synthetic.sqlite3'
        client = TestClient(create_app(Settings(sqlite_path=str(database), access_token=TOKEN, **settings)), client=('127.0.0.1', 50000))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.headers['Authorization'] = 'Bearer ' + TOKEN
        return client

    def ok(self, response):
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_similar_device_ids_never_share_samples_summaries_or_status(self):
        client = self.open()
        expected = {device: [] for device in SIMILAR}
        interleaved = []
        for round_number in range(len(SIMILAR)):
            for index, device in enumerate(SIMILAR):
                if round_number <= index:
                    row = sample(device_id=device, heart_rate_bpm=60 + 5 * index + round_number)
                    expected[device].append(row['sample_id'])
                    interleaved.append(row)
        self.ok(client.post('/v1/batches', json=batch(*interleaved)))
        for index, device in enumerate(SIMILAR):
            self.ok(client.post('/v1/gateway-status', json=status(device, battery_percent=10 * index)))
        summaries = {d['device_id']: d for d in self.ok(client.get('/v1/devices', params={'limit': 100}))['devices']}
        self.assertEqual(set(SIMILAR), set(summaries))
        for index, device in enumerate(SIMILAR):
            with self.subTest(device=device):
                for order in ('asc', 'desc'):
                    page = self.ok(client.get('/v1/samples', params={'device_id': device, 'limit': 100, 'order': order}))
                    ids = [row['sample_id'] for row in page['samples']]
                    self.assertEqual(expected[device] if order == 'asc' else expected[device][::-1], ids)
                    self.assertEqual({device}, {row['device_id'] for row in page['samples']})
                self.assertEqual(index + 1, summaries[device]['sample_count'])
                self.assertEqual(device, summaries[device]['latest']['device_id'])
                self.assertEqual(expected[device][-1], summaries[device]['latest']['sample_id'])
                report = self.ok(client.get('/v1/gateway-status', params={'device_id': device}))['status']
                self.assertEqual((device, 10 * index), (report['device_id'], report['battery_percent']))

    def test_cursors_are_bound_to_one_device_and_one_direction(self):
        client = self.open()
        rows = {device: [sample(device_id=device) for _ in range(5)] for device in SIMILAR[:2]}
        self.ok(client.post('/v1/batches', json=batch(*rows[SIMILAR[0]], *rows[SIMILAR[1]])))
        a, b = SIMILAR[0], SIMILAR[1]
        up = self.ok(client.get('/v1/samples', params={'device_id': a, 'limit': 2}))['next_cursor']
        down = self.ok(client.get('/v1/samples', params={'device_id': a, 'limit': 2, 'order': 'desc'}))['next_cursor']
        forged = (encode_cursor(a, 1), encode_cursor(a + '|desc', 9), encode_cursor(b + '|desc', 9))
        refused = [(b, 'asc', up), (a, 'desc', up), (b, 'desc', down), (a, 'asc', down),
                   (b, 'asc', forged[0]), (a, 'asc', forged[1]), (b, 'asc', forged[2]), (a, 'desc', forged[0])]
        for device, order, cursor in refused:
            with self.subTest(device=device, order=order, cursor=cursor):
                response = client.get('/v1/samples', params={'device_id': device, 'limit': 2, 'order': order, 'cursor': cursor})
                self.assertEqual(422, response.status_code)
        for order, cursor in (('asc', up), ('desc', down)):
            collected, next_cursor = [], cursor
            while next_cursor:
                page = self.ok(client.get('/v1/samples', params={'device_id': a, 'limit': 2, 'order': order, 'cursor': next_cursor}))
                collected += page['samples']
                next_cursor = page['next_cursor']
            ids = [s['sample_id'] for s in rows[a]]
            self.assertEqual(ids[2:] if order == 'asc' else ids[2::-1], [s['sample_id'] for s in collected])

    def test_device_listing_pages_similar_ids_without_skips_or_repeats(self):
        client = self.open()
        self.ok(client.post('/v1/batches', json=batch(*[sample(device_id=device) for device in SIMILAR])))
        self.ok(client.post('/v1/gateway-status', json=status('synthetic-dev-2')))
        seen, cursor = [], None
        for _ in range(20):
            params = {'limit': 1} if cursor is None else {'limit': 1, 'cursor': cursor}
            page = self.ok(client.get('/v1/devices', params=params))
            seen += [d['device_id'] for d in page['devices']]
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(sorted(SIMILAR + ('synthetic-dev-2',)), seen)

    def test_direct_device_addresses_are_rejected_on_every_endpoint(self):
        # CONTRACT.md: device_id is an opaque identifier, not a direct BLE
        # address. Separator-formatted addresses were previously accepted.
        client = self.open()
        addresses = ('aabbccddeeff', 'AABBCCDDEEFF', '0123456789aB', 'AA-BB-CC-DD-EE-FF', 'aa_bb_cc_dd_ee_ff',
                     'AA-BB_CC-DD-EE-FF', 'AABB-CCDD-EEFF', 'abc-def-012-345', 'a_b_c_d_e_f_0_1_2_3_4_5')
        for address in addresses:
            with self.subTest(address=address):
                self.assertEqual(422, client.post('/v1/batches', json=batch(sample(device_id=address))).status_code)
                self.assertEqual(422, client.post('/v1/gateway-status', json=status(address)).status_code)
                self.assertEqual(422, client.get('/v1/samples', params={'device_id': address}).status_code)
                self.assertEqual(422, client.get('/v1/gateway-status', params={'device_id': address}).status_code)
                self.assertEqual(422, client.get('/v1/devices', params={'cursor': address}).status_code)
        self.assertEqual([], self.ok(client.get('/v1/devices'))['devices'])
        for opaque in ('synthetic-watch', 'AA-BB-CC-DD-EE-FF-00', 'deadbeefcafe1', '3f1c2b9e-1d4c-4a8e-9b7f-2c6d5e4f3a21'):
            with self.subTest(opaque=opaque):
                self.ok(client.post('/v1/batches', json=batch(sample(device_id=opaque))))

    def test_real_data_mode_serves_the_real_gateway_report_over_a_synthetic_one(self):
        # Real-data mode keeps one synthetic and one real report per device. A
        # synthetic test report, even one received later, must not hide the
        # real gateway's report. Receipt times stay fixed-width, so a report
        # saved at an exact second still sorts before a later one (it once
        # serialized without a fraction and sorted as "newer").
        whole = datetime(2026, 11, 3, 12, 0, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 11, 3, 12, 0, 0, 500000, tzinfo=timezone.utc)
        for first, second in ((True, False), (False, True)):
            with self.subTest(first_synthetic=first):
                client = self.open(allow_real_data=True)
                device = 'synthetic-order-' + ('a' if first else 'b')
                with patch.object(repository, 'datetime', FixedClock(whole, later)):
                    saved = [self.ok(client.post('/v1/gateway-status', json=status(device, battery_percent=battery, synthetic=flag)))
                             for flag, battery in ((first, 11), (second, 99))]
                self.assertLess(saved[0]['server_received_at'], saved[1]['server_received_at'])
                real = 1 if first else 0
                current = self.ok(client.get('/v1/gateway-status', params={'device_id': device}))
                self.assertEqual(((11, 99)[real], False), (current['status']['battery_percent'], current['status']['synthetic']))
                self.assertEqual(saved[real]['server_received_at'], current['server_received_at'])
        # Without a real report, the synthetic one is served and marked synthetic.
        client = self.open(allow_real_data=True)
        self.ok(client.post('/v1/gateway-status', json=status('synthetic-test-only', battery_percent=5)))
        current = self.ok(client.get('/v1/gateway-status', params={'device_id': 'synthetic-test-only'}))['status']
        self.assertEqual((5, True), (current['battery_percent'], current['synthetic']))

    def test_real_report_is_served_whatever_order_the_store_returns_rows(self):
        # SQLite's primary-key index happens to return the real report first.
        # Reversing the order of unordered results shows that only the ORDER BY,
        # not storage order, makes the real report win.
        plain = repository.SQLiteRepository.connect

        def reversed_connect(store):
            connection = plain(store)
            connection.execute('PRAGMA reverse_unordered_selects=ON')
            return connection
        with patch.object(repository.SQLiteRepository, 'connect', reversed_connect):
            client = self.open(allow_real_data=True)
            for order in ((True, False), (False, True)):
                device = 'synthetic-unordered-' + ('a' if order[0] else 'b')
                for flag, battery in zip(order, (11, 99)):
                    self.ok(client.post('/v1/gateway-status', json=status(device, battery_percent=battery, synthetic=flag)))
                current = self.ok(client.get('/v1/gateway-status', params={'device_id': device}))['status']
                real_battery = 99 if order[0] else 11
                self.assertEqual((real_battery, False), (current['battery_percent'], current['synthetic']))

if __name__ == '__main__':
    unittest.main()
