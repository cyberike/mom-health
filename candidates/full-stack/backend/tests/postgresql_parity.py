"""Opt-in PostgreSQL parity checks for the main backend. Fictional data only.

Not collected by ``unittest discover`` (like integration_contract_android): it
needs a disposable PostgreSQL database. ``tests/postgresql_throwaway.py``
creates a brand-new local cluster reachable only through an owner-only Unix
socket, runs these checks against it and stops it again. From
candidates/full-stack/backend:

    python tests/postgresql_throwaway.py --run-as postgres -- python -m unittest tests.postgresql_parity -v

(--run-as only as root, which PostgreSQL refuses). MOM_HEALTH_TEST_POSTGRESQL_DSN
may instead name another disposable database whose name starts with
``mom_health_synthetic_``. Every store is a new schema that search_path points
at, dropped afterwards; nothing else in the database is read or changed.
Without psycopg or the variable the module fails with POSTGRESQL PARITY
CHECKS NOT RUN; it never reports a pass. The server crash check also needs
the launcher's cluster directory (MOM_HEALTH_TEST_POSTGRESQL_CLUSTER) and is
skipped without it.

Most classes inherit the SQLite suites' test methods unchanged and override
only how a store is created and inspected; methods that open the SQLite file
directly are replaced by PostgreSQL equivalents. A "peer" is a second
connection holding the same advisory lock or row lock the backend uses, as
another instance or a stuck transaction would.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
from threading import Barrier, Timer
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from mom_health_backend.app import Settings, create_app  # noqa: E402
from mom_health_backend.models import Batch, GatewayStatus  # noqa: E402
from mom_health_backend.repository import PostgreSQLRepository  # noqa: E402

try:
    import test_backend
    import test_concurrent_retries
    import test_device_isolation
    import test_restart_recovery
    from synthetic_support import LoopbackServer, TOKEN, batch, sample, status
except ImportError:  # imported as tests.<module>
    from tests import test_backend, test_concurrent_retries, test_device_isolation, test_restart_recovery
    from tests.synthetic_support import LoopbackServer, TOKEN, batch, sample, status

try:
    import psycopg
    from psycopg import conninfo, sql
except ImportError:
    psycopg = None

DSN = os.environ.get('MOM_HEALTH_TEST_POSTGRESQL_DSN')
CLUSTER = os.environ.get('MOM_HEALTH_TEST_POSTGRESQL_CLUSTER')
requires_cluster = unittest.skipUnless(CLUSTER, 'PostgreSQL crash checks not run: needs the throwaway launcher')
# The backend's transaction advisory locks (repository.py), held by peers here.
LOCKS = {'migrate': 7240090001, 'ingest': 7240090002, 'status': 7240090003}


def setUpModule():
    if not (DSN and psycopg):
        raise RuntimeError('POSTGRESQL PARITY CHECKS NOT RUN: needs psycopg and MOM_HEALTH_TEST_POSTGRESQL_DSN '
                           '(see tests/postgresql_throwaway.py)')
    with psycopg.Connection.connect(DSN, connect_timeout=5) as connection:
        name, version = connection.execute("SELECT current_database(), current_setting('server_version')").fetchone()
        database = connection.execute('SELECT to_jsonb(d) FROM pg_database d WHERE datname = current_database()').fetchone()[0]
    if not name.startswith('mom_health_synthetic_'):
        raise RuntimeError('Refusing database %r: these checks run only against a disposable mom_health_synthetic_* database' % name)
    print('PostgreSQL %s, database %s, collation provider %s, locale %s' % (
        version, name, database.get('datlocprovider'), database.get('daticulocale') or database.get('datcollate')), file=sys.stderr)


def admin(dsn=None):
    # Always the real class, so a patched psycopg.connect only affects the backend.
    return psycopg.Connection.connect(dsn or DSN, autocommit=True, connect_timeout=5)


def drop_schema(schema):
    with admin() as connection:
        connection.execute("SET lock_timeout = '15s'")
        connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def within(seconds, action):
    """('ok', result), ('error', exception) or ('pending', None) if action is still running after the deadline."""
    outcome = []

    def run():
        try:
            outcome.append(('ok', action()))
        except Exception as exc:  # reported by the caller
            outcome.append(('error', exc))
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    return outcome[0] if outcome else ('pending', None)


if psycopg is not None:
    class PausingConnection(psycopg.Connection):
        """Pauses one armed ingest inside its transaction after its Nth sample INSERT."""
        gate = None

        def execute(self, query, params=None, **kwargs):
            cursor = super().execute(query, params, **kwargs)
            gate = PausingConnection.gate
            if gate is not None and str(query).startswith('INSERT INTO samples'):
                gate['inserted'] += 1
                if gate['inserted'] == gate['after']:
                    PausingConnection.gate = None
                    gate['pid'] = self.info.backend_pid
                    gate['entered'].set()
                    if not gate['release'].wait(30):
                        raise TimeoutError('synthetic test gate was never released')
            return cursor

    class StallBeforeStatusLock(psycopg.Connection):
        """Holds one armed status write after connect() but before it asks for the status lock."""
        gate = None

        def execute(self, query, params=None, **kwargs):
            gate = StallBeforeStatusLock.gate
            if gate is not None and 'pg_advisory_xact_lock(%d)' % LOCKS['status'] in str(query):
                StallBeforeStatusLock.gate = None
                gate['entered'].set()
                if not gate['release'].wait(30):
                    raise TimeoutError('synthetic test gate was never released')
            return super().execute(query, params, **kwargs)

    class CommitThenFail(psycopg.Connection):
        """Commits durably, then raises once: the caller cannot know the outcome."""
        armed = False

        def commit(self):
            super().commit()
            if CommitThenFail.armed:
                CommitThenFail.armed = False
                raise psycopg.OperationalError('synthetic fault after a durable commit')


def new_gate(after):
    return dict(after=after, inserted=0, entered=threading.Event(), release=threading.Event())


class PostgreSQLFixture:
    """Each store is a fresh schema in the disposable database, dropped on cleanup."""

    def fresh_dsn(self, **parameters):
        schema = 'mom_health_synthetic_' + uuid4().hex[:12]
        with admin() as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        self.addCleanup(drop_schema, schema)
        options = ' '.join('-c %s=%s' % (name, str(value).replace(' ', '\\ '))
                           for name, value in dict(search_path=schema, **parameters).items())
        params = conninfo.conninfo_to_dict(DSN)
        params['options'] = (params.get('options', '') + ' ' + options).strip()
        self.dsn = conninfo.make_conninfo(**params)
        return self.dsn

    def rows(self, query, args=()):
        with admin(self.dsn) as connection:
            return connection.execute(query.replace('?', '%s'), args).fetchall()

    def count(self, query):
        return self.rows(query)[0][0]

    def hold(self, query, args=()):
        """A peer transaction keeping whatever lock `query` takes until release()."""
        holder = psycopg.Connection.connect(self.dsn, connect_timeout=5)
        holder.execute(query, args)
        guard = threading.Lock()

        def release():
            with guard:
                if not holder.closed:
                    holder.rollback()
                    holder.close()
        self.addCleanup(release)
        return release

    def release_later(self, release, seconds=0.5):
        timer = Timer(seconds, release)
        timer.start()
        self.addCleanup(timer.join)
        return timer


class PostgreSQLRepositoryTests(PostgreSQLFixture, test_backend.RepositoryTests):
    """test_backend.RepositoryTests on PostgreSQL; its SQLite-file checks are replaced below."""
    test_policy_rejects_real_or_mixed_batch_before_storage = None
    test_rejected_mixed_batch_does_not_change_pure_synthetic_idempotency = None
    test_failure_rolls_back_and_emits_no_receipt = None

    def setUp(self):
        self.repo = PostgreSQLRepository(self.fresh_dsn())
        self.repo.initialize()

    def tearDown(self):
        pass

    def stored(self):
        return [self.count('SELECT count(*) FROM ' + table) for table in ('samples', 'batches')]

    def test_policy_rejects_real_or_mixed_batch_without_opening_storage(self):
        rejected, synthetic = sample(synthetic=False, heart_rate_bpm=137), sample()
        mixed, whole = Batch.model_validate(batch(rejected, synthetic)), Batch.model_validate(batch(rejected))
        with patch.object(self.repo, 'connect', side_effect=AssertionError('storage must not be opened')):
            result = self.repo.ingest(mixed)
            self.assertEqual(result, self.repo.ingest(mixed))
            self.assertEqual('real_data_disabled', self.repo.ingest(whole)['rejected'][0]['reason'])
        self.assertEqual({'batch_id': mixed.batch_id, 'accepted_ids': [], 'duplicate_ids': [],
                          'rejected': [{'sample_id': rejected['sample_id'], 'reason': 'real_data_disabled'},
                                       {'sample_id': synthetic['sample_id'], 'reason': 'batch_contains_real_data'}]}, result)
        self.assertEqual([0, 0], self.stored())

    def test_rejected_mixed_batch_keeps_pure_synthetic_idempotency(self):
        synthetic = sample()
        pure = Batch.model_validate(batch(synthetic))
        first = self.repo.ingest(pure)
        self.repo.ingest(Batch.model_validate(batch(sample(synthetic=False), synthetic)))
        self.assertEqual(first, self.repo.ingest(pure))
        self.assertEqual([1, 1], self.stored())

    def test_database_fault_rolls_back_and_emits_no_receipt(self):
        with admin(self.dsn) as connection:
            connection.execute("CREATE FUNCTION synthetic_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic fault'; END $$")
            connection.execute('CREATE TRIGGER fail_receipt BEFORE INSERT ON batches FOR EACH ROW EXECUTE FUNCTION synthetic_fault()')
        submitted = Batch.model_validate(batch(sample(), sample()))
        with self.assertRaises(psycopg.errors.RaiseException):
            self.repo.ingest(submitted)
        self.assertEqual([0, 0], self.stored())
        with admin(self.dsn) as connection:
            connection.execute('DROP TRIGGER fail_receipt ON batches')
        receipt = self.repo.ingest(submitted)
        self.assertEqual([s.sample_id for s in submitted.samples], receipt['accepted_ids'])
        self.assertEqual(receipt, self.repo.ingest(submitted))
        self.assertEqual([2, 1], self.stored())


class PostgreSQLRepositoryRaceTests(PostgreSQLFixture, test_concurrent_retries.RepositoryRaceTests):
    """The SQLite race suite unchanged; PostgreSQL serializes writers with the ingest advisory lock."""

    def setUp(self):
        self.repo = PostgreSQLRepository(self.fresh_dsn())
        self.repo.initialize()


class PostgreSQLDeviceIsolationTests(PostgreSQLFixture, test_device_isolation.DeviceIsolationTests):
    """The SQLite device-isolation suite unchanged, through create_app with database_url."""

    def open(self, **settings):
        client = TestClient(create_app(Settings(database_url=self.fresh_dsn(), access_token=TOKEN, **settings)), client=('127.0.0.1', 50000))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.headers['Authorization'] = 'Bearer ' + TOKEN
        return client

    def test_device_listing_keeps_byte_order_under_a_linguistic_collation(self):
        # A database created with an en_US or ICU locale gives text columns a
        # linguistic collation (hosted servers are commonly initialized with
        # en_US.utf8). The listing and its cursor must still follow SQLite's
        # byte order.
        client = self.open()
        found = self.rows("SELECT collname FROM pg_collation WHERE collname IN ('en-US-x-icu', 'en_US.utf8') ORDER BY 1")
        if not found:
            self.skipTest('no linguistic collation is installed on this server')
        with admin(self.dsn) as connection:
            for table in ('samples', 'gateway_status'):
                connection.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN device_id TYPE text COLLATE {}').format(
                    sql.Identifier(table), sql.Identifier(found[0][0])))
        devices = test_device_isolation.SIMILAR + ('synthetic-dev-2',)
        self.ok(client.post('/v1/batches', json=batch(*[sample(device_id=device) for device in devices[:-1]])))
        self.ok(client.post('/v1/gateway-status', json=status(devices[-1])))
        seen, cursor = [], None
        for _ in range(20):
            page = self.ok(client.get('/v1/devices', params={'limit': 1} if cursor is None else {'limit': 1, 'cursor': cursor}))
            seen += [d['device_id'] for d in page['devices']]
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(sorted(devices), seen)
        self.assertEqual(sorted(devices), [d['device_id'] for d in self.ok(client.get('/v1/devices', params={'limit': 100}))['devices']])

    def test_real_report_is_served_whatever_order_the_store_returns_rows(self):
        # PostgreSQL version: with index scans disabled the planner reads the
        # table in storage order, where the synthetic report was stored first,
        # so only the ORDER BY makes the real report win.
        dsn = self.fresh_dsn(enable_indexscan='off', enable_bitmapscan='off', enable_indexonlyscan='off')
        client = TestClient(create_app(Settings(database_url=dsn, access_token=TOKEN, allow_real_data=True)), client=('127.0.0.1', 50000))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.headers['Authorization'] = 'Bearer ' + TOKEN
        for flag, battery in ((True, 11), (False, 99)):
            self.ok(client.post('/v1/gateway-status', json=status('synthetic-unordered', battery_percent=battery, synthetic=flag)))
        self.assertEqual([(1,), (0,)], self.rows("SELECT synthetic FROM gateway_status WHERE device_id = 'synthetic-unordered'"))
        current = self.ok(client.get('/v1/gateway-status', params={'device_id': 'synthetic-unordered'}))['status']
        self.assertEqual((99, False), (current['battery_percent'], current['synthetic']))


class PostgreSQLRestartTests(PostgreSQLFixture, test_restart_recovery.RestartRecoveryTests):
    """App restarts over one PostgreSQL store; the SQLite file and process checks are replaced below."""
    test_concurrent_first_start_initializes_one_schema = None
    test_startup_waits_for_a_brief_writer_and_fails_closed_on_a_persistent_one = None
    test_writer_killed_inside_an_ingest_leaves_nothing_and_the_retry_commits_once = None

    def setUp(self):
        self.settings = Settings(database_url=self.fresh_dsn(), access_token=TOKEN)

    def tables(self):
        return [row[0] for row in self.rows('SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() ORDER BY 1')]

    def interrupt(self, server, submitted, after):
        """Start an ingest that pauses inside its transaction; returns (gate, pending result, worker)."""
        gate, pending = new_gate(after), {}
        PausingConnection.gate = gate
        self.addCleanup(setattr, PausingConnection, 'gate', None)
        worker = threading.Thread(target=lambda: pending.update(result=server.request('POST', '/v1/batches', submitted)))
        worker.start()
        self.addCleanup(worker.join, 30)
        self.addCleanup(gate['release'].set)
        self.assertTrue(gate['entered'].wait(15), 'ingest never reached the gated INSERT')
        return gate, pending, worker

    def test_concurrent_first_start_creates_one_schema(self):
        gate = Barrier(8)

        def start(_):
            gate.wait(15)
            PostgreSQLRepository(self.settings.database_url).initialize()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(start, range(8)))
        self.assertEqual([(1,), (2,)], self.rows('SELECT version FROM schema_versions ORDER BY version'))
        self.assertEqual(['batches', 'gateway_status', 'samples', 'schema_versions'], self.tables())
        with self.serve() as server:
            self.assertEqual('ready', self.get(server, '/v1/health')['status'])

    def test_startup_waits_for_a_brief_peer_and_fails_closed_on_a_persistent_one(self):
        with self.serve() as server:
            self.assertEqual(200, server.request('POST', '/v1/batches', batch(sample()))[0])
        # Another instance migrating, or a peer holding an uncommitted write.
        peers = {'migration lock': ('SELECT pg_advisory_xact_lock(%s)', (LOCKS['migrate'],)),
                 'uncommitted sample': ('INSERT INTO samples(sample_id,device_id,synthetic,canonical) VALUES (%s,%s,1,%s)',
                                        (str(uuid4()), 'synthetic-peer', '{}'))}
        for label, (query, args) in peers.items():
            with self.subTest(peer=label):
                self.release_later(self.hold(query, args))
                with self.serve() as server:
                    self.assertEqual('ready', self.get(server, '/v1/health')['status'])
                release = self.hold(query, args)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'failed to start'):
                        with self.serve():
                            pass
                finally:
                    release()
        with self.serve() as server:
            self.assertEqual(1, self.get(server, '/v1/devices')['devices'][0]['sample_count'])

    def test_a_session_terminated_inside_an_ingest_leaves_nothing_and_the_retry_commits_once(self):
        seeded = batch(*[sample() for _ in range(3)])
        submitted = batch(*[sample(device_id='synthetic-killed') for _ in range(100)])
        with self.serve() as server, patch.object(psycopg, 'connect', PausingConnection.connect):
            self.assertEqual(200, server.request('POST', '/v1/batches', seeded)[0])
            committed = self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100)
            gate, pending, worker = self.interrupt(server, submitted, after=90)
            self.assertEqual([(True,)], self.rows('SELECT pg_terminate_backend(%s, 10000)', (gate['pid'],)))
            gate['release'].set()
            worker.join(15)
            self.assertEqual(503, pending['result'][0])
            self.assertEqual(committed, self.get(server, '/v1/samples', device_id='synthetic-watch', limit=100))
            self.assertEqual([], self.get(server, '/v1/samples', device_id='synthetic-killed', limit=100)['samples'])
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], json.loads(payload)['accepted_ids'])
            self.assertEqual(json.loads(payload), json.loads(server.request('POST', '/v1/batches', submitted)[1]))
        self.assertEqual(103, self.count('SELECT COUNT(*) FROM samples'))
        self.assertEqual(2, self.count('SELECT COUNT(*) FROM batches'))

    @requires_cluster
    def test_a_server_crash_keeps_acknowledged_batches_and_loses_only_the_open_transaction(self):
        acknowledged = batch(*[sample() for _ in range(5)])
        interrupted = batch(*[sample(device_id='synthetic-crashed') for _ in range(50)])
        later = batch(sample(device_id='synthetic-later'))
        with self.serve() as server, patch.object(psycopg, 'connect', PausingConnection.connect):
            code, payload = server.request('POST', '/v1/batches', acknowledged)
            self.assertEqual(200, code)
            receipt = json.loads(payload)
            gate, pending, worker = self.interrupt(server, interrupted, after=25)
            try:
                import postgresql_throwaway  # Linux-only launcher, needed here only
            except ImportError:  # imported as tests.<module>
                from tests import postgresql_throwaway
            # Immediate mode: the postmaster and every backend quit without a
            # checkpoint, so the next start must recover from the WAL.
            postgresql_throwaway.control(CLUSTER, 'stop', mode='immediate')
            try:
                gate['release'].set()
                worker.join(15)
                self.assertEqual(503, pending['result'][0])
                self.assertEqual(503, server.request('POST', '/v1/batches', later)[0])
                self.assertEqual(503, server.request('POST', '/v1/gateway-status', status())[0])
                self.assertEqual(503, server.request('GET', '/v1/devices')[0])
                # Documented limitation: /v1/health reports process readiness only.
                self.assertEqual(200, server.request('GET', '/v1/health')[0])
            finally:
                postgresql_throwaway.control(CLUSTER, 'start')
            code, payload = server.request('POST', '/v1/batches', acknowledged)
            self.assertEqual((200, receipt), (code, json.loads(payload)))
            listing = self.get(server, '/v1/devices')['devices']
            self.assertEqual([('synthetic-watch', 5)], [(d['device_id'], d['sample_count']) for d in listing])
            code, payload = server.request('POST', '/v1/batches', interrupted)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in interrupted['samples']], json.loads(payload)['accepted_ids'])
            self.assertEqual(200, server.request('POST', '/v1/batches', later)[0])
        self.assertEqual(56, self.count('SELECT COUNT(*) FROM samples'))
        self.assertEqual(3, self.count('SELECT COUNT(*) FROM batches'))


class PostgreSQLWriteLockTests(PostgreSQLFixture, unittest.TestCase):
    """Lock waits, status ordering, isolation level and ambiguous commits on PostgreSQL."""
    WRITES = (('/v1/batches', lambda: batch(sample()), 'ingest'), ('/v1/gateway-status', status, 'status'))

    def setUp(self):
        self.settings = Settings(database_url=self.fresh_dsn(), access_token=TOKEN)

    def client(self):
        client = TestClient(create_app(self.settings), client=('127.0.0.1', 50000))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        client.headers['Authorization'] = 'Bearer ' + TOKEN
        return client

    def test_every_connection_runs_read_committed_with_a_5_second_lock_timeout(self):
        # Whatever the server, database or role defaults are, the backend's own settings
        # win: a lock timeout of exactly 5 s and READ COMMITTED. It sets no statement
        # timeout of its own, so one that is already in force still applies (here the
        # connection's own 45 s, which also overrides any database or role default).
        repository = PostgreSQLRepository(self.fresh_dsn(default_transaction_isolation='serializable', lock_timeout='0',
                                                         statement_timeout='45s'))
        connection = repository.connect()
        try:
            self.assertEqual(('read committed', '5s', '45s'), connection.execute(
                "SELECT current_setting('transaction_isolation'), current_setting('lock_timeout'), current_setting('statement_timeout')").fetchone())
        finally:
            connection.close()
        # Nor one of its own when the connection brings none: the backend's connection then sees
        # what a plain connection with the same settings sees (the server, database or role default).
        dsn = self.fresh_dsn()
        with admin(dsn) as plain:
            expected = plain.execute("SELECT current_setting('statement_timeout')").fetchone()[0]
        connection = PostgreSQLRepository(dsn).connect()
        try:
            self.assertEqual(expected, connection.execute("SELECT current_setting('statement_timeout')").fetchone()[0])
        finally:
            connection.close()

    def test_writes_wait_for_a_brief_lock_holder(self):
        client = self.client()
        for path, body, lock in self.WRITES:
            with self.subTest(path=path):
                self.release_later(self.hold('SELECT pg_advisory_xact_lock(%s)', (LOCKS[lock],)))
                self.assertEqual(200, client.post(path, json=body()).status_code)

    def test_a_persistent_lock_holder_yields_503_instead_of_a_hung_request(self):
        # Like SQLite's 5 s busy timeout: storage trouble is answered 503 with
        # no acknowledgment, rather than tying up a worker indefinitely.
        client = self.client()
        for path, body, lock in self.WRITES:
            with self.subTest(path=path):
                submitted = body()
                release = self.hold('SELECT pg_advisory_xact_lock(%s)', (LOCKS[lock],))
                started = time.monotonic()
                try:
                    kind, response = within(20, lambda: client.post(path, json=submitted))
                finally:
                    release()
                self.assertEqual('ok', kind, 'still waiting for the lock after 20 s' if kind == 'pending' else response)
                self.assertEqual(503, response.status_code)
                self.assertGreater(time.monotonic() - started, 4, 'the request gave up before the 5 s lock timeout')
                self.assertEqual(200, client.post(path, json=submitted).status_code)
        self.assertEqual(1, self.count('SELECT count(*) FROM samples'))
        self.assertEqual(1, self.count('SELECT count(*) FROM gateway_status'))

    def test_reads_behind_a_persistent_table_lock_yield_503_instead_of_hanging(self):
        client = self.client()
        self.assertEqual(200, client.post('/v1/batches', json=batch(sample())).status_code)
        self.assertEqual(200, client.post('/v1/gateway-status', json=status()).status_code)
        release = self.hold('LOCK TABLE samples, gateway_status IN ACCESS EXCLUSIVE MODE')
        try:
            for path in ('/v1/devices', '/v1/samples?device_id=synthetic-watch', '/v1/gateway-status?device_id=synthetic-watch'):
                with self.subTest(path=path):
                    kind, response = within(20, lambda: client.get(path))
                    self.assertEqual('ok', kind, 'read still waiting for the lock after 20 s' if kind == 'pending' else response)
                    self.assertEqual(503, response.status_code)
        finally:
            release()
        self.assertEqual(1, client.get('/v1/devices').json()['devices'][0]['sample_count'])

    def test_a_report_held_before_the_status_lock_still_gets_the_later_receipt_time(self):
        # The receipt time must be read while holding the status lock. A report
        # held between connect() and the lock is stored after a prompt one, so it
        # must also carry the later receipt time and be the report served.
        repository = PostgreSQLRepository(self.settings.database_url)
        repository.initialize()
        gate, acknowledged = dict(entered=threading.Event(), release=threading.Event()), {}
        with patch.object(psycopg, 'connect', StallBeforeStatusLock.connect):
            StallBeforeStatusLock.gate = gate
            self.addCleanup(setattr, StallBeforeStatusLock, 'gate', None)
            self.addCleanup(gate['release'].set)
            held = threading.Thread(target=lambda: acknowledged.update(
                held=repository.save_status(GatewayStatus.model_validate(status(battery_percent=1)))))
            held.start()
            self.assertTrue(gate['entered'].wait(10), 'the status write never reached the lock')
            acknowledged['prompt'] = repository.save_status(GatewayStatus.model_validate(status(battery_percent=2)))
            gate['release'].set()
            held.join(15)
        self.assertLess(acknowledged['prompt']['server_received_at'], acknowledged['held']['server_received_at'])
        served = repository.status('synthetic-watch')
        self.assertEqual((acknowledged['held']['server_received_at'], 1), (served['server_received_at'], served['status']['battery_percent']))

    def test_a_delayed_status_report_never_overwrites_a_later_acknowledged_one(self):
        # PostgreSQL version of test_concurrent_retries.StatusReportRaceTests.
        repository = PostgreSQLRepository(self.settings.database_url)
        plain, armed, stalled, resume = repository.connect, threading.Event(), threading.Event(), threading.Event()

        def connect():
            if armed.is_set():
                armed.clear()
                stalled.set()
                if not resume.wait(10):
                    raise TimeoutError('synthetic test gate was never released')
            return plain()
        repository.connect = connect
        with TestClient(create_app(self.settings, repository=repository), client=('127.0.0.1', 50000)) as client:
            client.headers['Authorization'] = 'Bearer ' + TOKEN
            acknowledged = {}
            armed.set()
            delayed = threading.Thread(target=lambda: acknowledged.update(
                delayed=client.post('/v1/gateway-status', json=status(connection='disconnected', pending_samples=21)).json()))
            delayed.start()
            self.assertTrue(stalled.wait(10))
            acknowledged['prompt'] = client.post('/v1/gateway-status', json=status(connection='connected', pending_samples=0)).json()
            resume.set()
            delayed.join(10)
            served = client.get('/v1/gateway-status', params={'device_id': 'synthetic-watch'}).json()
        latest = max(acknowledged.values(), key=lambda ack: ack['server_received_at'])
        self.assertTrue(all(ack['accepted'] for ack in acknowledged.values()))
        self.assertEqual(latest['server_received_at'], served['server_received_at'])

    def test_concurrent_status_reports_serve_the_last_acknowledged_one(self):
        repository = PostgreSQLRepository(self.settings.database_url)
        repository.initialize()
        for round_number in range(3):
            reports = [GatewayStatus.model_validate(status(battery_percent=i)) for i in range(32)]
            results = test_concurrent_retries.race(32, lambda i: repository.save_status(reports[i]))
            self.assertEqual([], [value for kind, value in results if kind != 'ok'])
            acks = [value['server_received_at'] for _, value in results]
            served = repository.status('synthetic-watch')
            self.assertEqual(max(acks), served['server_received_at'], 'round %d' % round_number)
            self.assertEqual(acks.index(max(acks)), served['status']['battery_percent'])

    def test_lock_protocol_holds_under_a_stricter_default_isolation_level(self):
        # The advisory locks serialize writers only if the statements after
        # the lock see the previous holder's commit (READ COMMITTED). A server
        # or role default of REPEATABLE READ or SERIALIZABLE must not change that.
        for level in ('repeatable read', 'serializable'):
            with self.subTest(default_transaction_isolation=level):
                repository = PostgreSQLRepository(self.fresh_dsn(default_transaction_isolation=level))
                repository.initialize()
                self.assertEqual([(level,)], self.rows('SHOW default_transaction_isolation'))
                submitted = Batch.model_validate(batch(sample(), sample(), sample()))
                results = test_concurrent_retries.race(16, lambda _: repository.ingest(submitted))
                self.assertEqual([], [value for kind, value in results if kind != 'ok'])
                self.assertEqual({json.dumps(value, sort_keys=True) for _, value in results},
                                 {json.dumps(results[0][1], sort_keys=True)})
                self.assertEqual([s.sample_id for s in submitted.samples], results[0][1]['accepted_ids'])
                reports = [GatewayStatus.model_validate(status(battery_percent=i)) for i in range(16)]
                outcomes = test_concurrent_retries.race(16, lambda i: repository.save_status(reports[i]))
                self.assertEqual([], [value for kind, value in outcomes if kind != 'ok'])
                acks = [value['server_received_at'] for _, value in outcomes]
                self.assertEqual(max(acks), repository.status('synthetic-watch')['server_received_at'])

    def test_no_acknowledgment_after_a_durable_commit_then_the_retry_gets_the_receipt(self):
        submitted = batch(*[sample() for _ in range(4)])
        with LoopbackServer(create_app(self.settings)) as server, patch.object(psycopg, 'connect', CommitThenFail.connect):
            CommitThenFail.armed = True
            self.addCleanup(setattr, CommitThenFail, 'armed', False)
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(503, code)
            self.assertNotIn('accepted_ids', json.loads(payload))
            self.assertEqual(1, self.count('SELECT COUNT(*) FROM batches'))
            code, payload = server.request('POST', '/v1/batches', submitted)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], json.loads(payload)['accepted_ids'])
        self.assertEqual(4, self.count('SELECT COUNT(*) FROM samples'))

    def test_client_that_gives_up_mid_ingest_still_gets_one_commit_and_its_receipt_on_retry(self):
        # PostgreSQL version of test_cancellation_loopback: the identical retry
        # waits on the ingest lock held by the abandoned attempt.
        import socket
        submitted = batch(*[sample() for _ in range(5)])
        payload = json.dumps(submitted).encode()
        gate = new_gate(1)
        with LoopbackServer(create_app(self.settings), log_level='warning') as server, patch.object(psycopg, 'connect', PausingConnection.connect):
            PausingConnection.gate = gate
            self.addCleanup(setattr, PausingConnection, 'gate', None)
            self.addCleanup(gate['release'].set)
            stream = socket.create_connection(('127.0.0.1', server.port), timeout=5)
            stream.sendall(('POST /v1/batches HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer ' + TOKEN +
                            '\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)) + '\r\n\r\n').encode() + payload)
            self.assertTrue(gate['entered'].wait(10), 'ingest never reached the gated INSERT')
            stream.close()
            retry = {}
            worker = threading.Thread(target=lambda: retry.update(result=server.request('POST', '/v1/batches', submitted)))
            worker.start()
            worker.join(0.3)
            self.assertNotIn('result', retry)
            gate['release'].set()
            worker.join(15)
            code, body = retry['result']
            receipt = json.loads(body)
            self.assertEqual(200, code)
            self.assertEqual([s['sample_id'] for s in submitted['samples']], receipt['accepted_ids'])
            self.assertEqual(receipt, json.loads(server.request('POST', '/v1/batches', submitted)[1]))
        self.assertEqual(5, self.count('SELECT COUNT(*) FROM samples'))
        self.assertEqual(1, self.count('SELECT COUNT(*) FROM batches'))


if __name__ == '__main__':
    unittest.main()
