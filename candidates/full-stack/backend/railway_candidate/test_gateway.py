"""In-process, synthetic-only edge checks. No listener, phone, TLS or live secrets."""
import base64
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from railway_candidate.gateway import create_gateway, application, FailureLimiter, trusted_networks, validate_storage

HOST='synthetic.example.com'
USER='synthetic-caregiver'
PASSWORD='fictional-caregiver-password-not-for-use-12345'
PHONE='fictional-phone-upload-token-not-for-use-67890'
BASE={'host':HOST,'x-forwarded-proto':'https'}
BASIC='Basic '+base64.b64encode((USER+':'+PASSWORD).encode()).decode()
BEARER='Bearer '+PHONE

class ApplicationConfigurationTest(unittest.TestCase):
    def test_private_child_requires_explicit_strict_opt_in(self):
        for setting, expected in ((None, False), ('false', False), ('true', True)):
            with self.subTest(setting=setting):
                env = {} if setting is None else {'MOM_HEALTH_PRIVATE_CHILD': setting}
                with patch.dict(os.environ, env, clear=True), patch('railway_candidate.gateway.create_gateway') as create:
                    self.assertIs(application(), create.return_value)
                    self.assertIs(create.call_args.kwargs['private_child'], expected)
        for setting in ('', 'TRUE', '1', 'yes', ' false '):
            with self.subTest(setting=setting), patch.dict(os.environ, {'MOM_HEALTH_PRIVATE_CHILD': setting}, clear=True), patch('railway_candidate.gateway.create_gateway') as create:
                with self.assertRaisesRegex(ValueError, 'Invalid private-child'):
                    application()
                create.assert_not_called()

    def test_private_child_never_enables_real_data(self):
        with patch.dict(os.environ, {'MOM_HEALTH_PRIVATE_CHILD': 'true', 'MOM_HEALTH_ALLOW_REAL_DATA': 'true'}, clear=True), patch('railway_candidate.gateway.create_gateway') as create:
            with self.assertRaisesRegex(ValueError, 'synthetic-only'):
                application()
            create.assert_not_called()

class GatewayTest(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).resolve().parents[2]/'build'/'railway-fixtures'/str(uuid4())
        folder.mkdir(parents=True)
        if os.name == 'posix': folder.chmod(0o700)
        self.mount_patch=patch('railway_candidate.gateway.os.path.ismount', return_value=True)
        self.mount_patch.start()
        self.addCleanup(self.mount_patch.stop)
        self.folder=folder.resolve()
        self.db=self.folder/'mom-health-synthetic.sqlite3'
        self.seconds=100.0
        self.client=TestClient(create_gateway(HOST,USER,PASSWORD,PHONE,str(self.db),str(self.folder),'127.0.0.0/8',clock=lambda:self.seconds),client=('127.0.0.1',1234))
        self.client.__enter__()
    def tearDown(self): self.client.__exit__(None,None,None)
    def call(self, method,path,auth=None,**kwargs):
        return self.client.request(method,path,headers={**BASE,**({'authorization':auth} if auth else {})},**kwargs)
    def test_private_shell_and_assets(self):
        for path in ('/dashboard/','/dashboard/app.js','/dashboard/core.js','/dashboard/styles.css'):
            self.assertEqual(self.call('GET',path).status_code,401)
            self.assertEqual(self.call('GET',path,BEARER).status_code,401)
            r=self.call('GET',path,BASIC); self.assertEqual(r.status_code,200);self.assertEqual(r.headers['cache-control'],'no-store')
            self.seconds+=61
        self.assertIn('credentials: "same-origin"',self.call('GET','/dashboard/app.js',BASIC).text)
        self.assertIn('type="hidden"',self.call('GET','/dashboard/',BASIC).text)
    def test_roles_are_separate(self):
        self.assertEqual(self.call('GET','/v1/health',BASIC).status_code,200)
        self.assertEqual(self.call('GET','/v1/health',BEARER).status_code,401)
        self.assertEqual(self.call('POST','/v1/batches',BASIC,json={}).status_code,401)
        self.assertEqual(self.call('POST','/v1/batches',BEARER,json={}).status_code,422)
        self.assertEqual(self.call('GET','/not-an-allowed-path',BASIC).status_code,404)
    def test_origin_checks_and_ambiguous_headers(self):
        for headers in ({'host':'other.example.com','x-forwarded-proto':'https','authorization':BASIC},
                        {'host':HOST,'x-forwarded-proto':'http','authorization':BASIC},
                        {'host':HOST,'authorization':BASIC}):
            self.assertEqual(self.client.get('/v1/health',headers=headers).status_code,403)
        h=[('host',HOST),('x-forwarded-proto','https'),('authorization',BASIC),('authorization',BASIC)]
        self.assertEqual(self.client.get('/v1/health',headers=h).status_code,400)
    def test_synthetic_only_and_visibility(self):
        sample=dict(sample_id=str(uuid4()),device_id=str(uuid4()),session_id=str(uuid4()),model='V8',source='live',
                    received_at='2026-10-28T10:00:00Z',measured_at=None,heart_rate_bpm=70,missing_reason=None,synthetic=True)
        batch={'schema_version':1,'batch_id':str(uuid4()),'samples':[sample]}
        r=self.call('POST','/v1/batches',BEARER,json=batch);self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['accepted_ids'],[sample['sample_id']])
        self.assertEqual(self.call('GET','/v1/samples?device_id='+sample['device_id'],BASIC).json()['samples'][0]['heart_rate_bpm'],70)
        sample.update(sample_id=str(uuid4()),synthetic=False)
        r=self.call('POST','/v1/batches',BEARER,json={'schema_version':1,'batch_id':str(uuid4()),'samples':[sample]})
        self.assertEqual(r.json()['rejected'][0]['reason'],'real_data_disabled')
    def test_fail_closed_configuration(self):
        with self.assertRaises(ValueError): create_gateway(HOST,USER,PASSWORD,PASSWORD,str(self.db),str(self.folder),'127.0.0.0/8')
        with self.assertRaises(ValueError): create_gateway(HOST,USER,'short',PHONE,str(self.db),str(self.folder),'127.0.0.0/8')
        with self.assertRaises(ValueError): create_gateway('localhost',USER,PASSWORD,PHONE,str(self.db),str(self.folder),'127.0.0.0/8')
        with self.assertRaises(ValueError): create_gateway(HOST,USER,PASSWORD,PHONE,'relative.db',str(self.folder),'127.0.0.0/8')
        with patch.dict(os.environ,{'MOM_HEALTH_ALLOW_REAL_DATA':'true'}):
            with self.assertRaises(ValueError): application()

    def test_throttle_authentication_by_role_and_expiration(self):
        for _ in range(8): self.assertEqual(self.call('GET','/v1/health').status_code,401)
        blocked=self.call('GET','/v1/health',BASIC)
        self.assertEqual(blocked.status_code,429)
        self.assertEqual(blocked.headers['retry-after'],'60')
        self.assertEqual(self.call('POST','/v1/batches',BEARER,json={}).status_code,422)
        self.seconds+=60
        self.assertEqual(self.call('GET','/v1/health',BASIC).status_code,200)

    def test_spoofed_forwarded_client_ip_never_changes_budget(self):
        for i in range(8):
            r=self.client.get('/v1/health',headers={**BASE,'x-forwarded-for':'198.51.100.'+str(i+1)})
            self.assertEqual(r.status_code,401)
        self.assertEqual(self.call('GET','/v1/health',BASIC).status_code,429)

    def test_transport_peer_allowlist_and_bad_cidrs(self):
        with TestClient(create_gateway(HOST,USER,PASSWORD,PHONE,str(self.db),str(self.folder),'10.2.0.0/16'),client=('127.0.0.1',1)) as other:
            self.assertEqual(other.get('/v1/health',headers={**BASE,'authorization':BASIC,'x-forwarded-for':'10.2.0.1'}).status_code,403)
        for cidr in ('', '0.0.0.0/0', '8.8.8.8/32', '169.254.0.0/16', '192.0.2.0/24', 'bad', '10.0.0.1/8'):
            with self.subTest(cidr=cidr),self.assertRaises(ValueError): trusted_networks(cidr)

    def test_storage_validation_rejects_unmounted_and_unsafe_paths(self):
        with self.assertRaises(ValueError): validate_storage(str(self.folder),str(self.folder/'different.db'))
        with self.assertRaises(ValueError): validate_storage(str(self.folder),str(self.folder.parent/'mom-health-synthetic.sqlite3'))
        with patch('railway_candidate.gateway.os.path.ismount',return_value=False):
            with self.assertRaises(ValueError): validate_storage(str(self.folder),str(self.db))
        if os.name == 'posix':
            self.folder.chmod(0o755)
            try:
                with self.assertRaises(ValueError): validate_storage(str(self.folder),str(self.db))
            finally: self.folder.chmod(0o700)

    def test_limiter_uses_constant_memory_and_concurrent_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        limiter=FailureLimiter(clock=lambda:100)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _: limiter.check_and_record('phone',False), range(100)))
        self.assertEqual(sum(allowed for allowed,_ in results),8)
        self.assertEqual(len(limiter.failures['phone']),8)

if __name__=='__main__': unittest.main()
