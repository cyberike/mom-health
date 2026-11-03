"""Client cancellation against the unmodified main app over real loopback TCP.

A real Uvicorn server on 127.0.0.1 serves create_app() with a real SQLite
repository whose connection can pause inside the ingest transaction on a test
gate, so a client can disconnect at a known point. Fictional data only.
"""
from contextlib import closing
import json
import socket
import sqlite3
import threading
import time
import unittest

from mom_health_backend.app import Settings, create_app
from mom_health_backend.repository import SQLiteRepository

try:
    from synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample
except ImportError:  # imported as tests.<module>
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, fixture_dir, sample


class PausingConnection(sqlite3.Connection):
    """Pauses after the first sample INSERT of a gated ingest, inside its transaction."""
    gate = None

    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        gate = PausingConnection.gate
        if gate is not None and sql.startswith('INSERT INTO samples'):
            PausingConnection.gate = None
            gate['entered'].set()
            if not gate['release'].wait(15):
                raise TimeoutError('synthetic test gate was never released')
        return result


class CancellationLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.database = fixture_dir('cancellation-fixtures') / 'synthetic.sqlite3'
        repository = SQLiteRepository(self.database)
        repository.connect = lambda: sqlite3.connect(repository.path, timeout=5, factory=PausingConnection)
        self.app = create_app(Settings(sqlite_path=str(self.database), access_token=TOKEN), repository=repository)
        self.addCleanup(setattr, PausingConnection, 'gate', None)

    def raw(self, server, payload, *, send_bytes=None):
        stream = socket.create_connection(('127.0.0.1', server.port), timeout=5)
        head = ('POST /v1/batches HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer ' + TOKEN +
                '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)) + '\r\n\r\n').encode()
        stream.sendall(head + payload[:send_bytes if send_bytes is not None else len(payload)])
        return stream

    def stored(self, sql):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(sql).fetchone()[0]

    def test_disconnect_during_body_upload_stores_nothing_and_server_stays_healthy(self):
        submitted = batch(*[sample() for _ in range(5)])
        payload = json.dumps(submitted).encode()
        with LoopbackServer(self.app) as server:
            stream = self.raw(server, payload, send_bytes=len(payload) // 2)
            time.sleep(0.2)
            stream.close()
            time.sleep(0.3)
            self.assertEqual(200, server.request('GET', '/v1/health')[0])
            self.assertEqual([], json.loads(server.request('GET', '/v1/devices')[1])['devices'])
            code, body = server.request('POST', '/v1/batches', submitted)
            self.assertEqual((200, 5), (code, len(json.loads(body)['accepted_ids'])))
        self.assertEqual(5, self.stored('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.stored('SELECT COUNT(*) FROM batches'))

    def test_client_that_gives_up_mid_ingest_still_gets_one_commit_and_its_receipt_on_retry(self):
        submitted = batch(*[sample() for _ in range(5)])
        gate = {'entered': threading.Event(), 'release': threading.Event()}
        PausingConnection.gate = gate
        with LoopbackServer(self.app) as server:
            stream = self.raw(server, json.dumps(submitted).encode())
            self.assertTrue(gate['entered'].wait(10), 'ingest never reached the gated INSERT')
            stream.close()  # the phone gives up while the server is inside the transaction
            # An identical retry arrives while the abandoned attempt still holds
            # the write lock; it must wait and then receive that attempt's receipt.
            retry = {}
            worker = threading.Thread(target=lambda: retry.update(result=server.request('POST', '/v1/batches', submitted)))
            worker.start()
            time.sleep(0.3)
            self.assertNotIn('result', retry)
            gate['release'].set()
            worker.join(15)
            code, body = retry['result']
            receipt = json.loads(body)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], receipt['accepted_ids'])
            self.assertEqual([], receipt['duplicate_ids'] + receipt['rejected'])
            self.assertEqual(receipt, json.loads(server.request('POST', '/v1/batches', submitted)[1]))
        self.assertEqual(5, self.stored('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.stored('SELECT COUNT(*) FROM batches'))


if __name__ == '__main__':
    unittest.main()
