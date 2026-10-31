import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from itertools import repeat
from contract import Sample, Model, Source, Evidence, HR_EVIDENCE, history_enabled
from memory_buffer import MemoryBuffer
from batch_contract import validate_batch

T = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
def sample(key='fixture1', **changes):
    values=dict(sample_id=key,device_id='synthetic-watch-A',session_id='synthetic-session',model=Model.V8,
                received_at=T,heart_rate_bpm=80,synthetic=True)
    values.update(changes)
    return Sample(**values)
def envelope(*samples): return dict(schema_version=1,samples=[s.wire() for s in samples])

class ContractTests(unittest.TestCase):
    def test_valid_candidate(self):
        s=sample();self.assertEqual(s.heart_rate_bpm,80);self.assertEqual(s.received_at,T)
    def test_null_requires_reason(self):
        with self.assertRaises(ValueError): sample(heart_rate_bpm=None)
        self.assertIsNone(sample(heart_rate_bpm=None,missing_reason='no_result').heart_rate_bpm)
    def test_null_is_not_zero(self):
        for value in [0,19,251,-1,True,False,80.0,float('nan'),float('inf'),'80']:
            with self.subTest(value=value),self.assertRaises(ValueError):sample(heart_rate_bpm=value)
    def test_contradiction(self):
        with self.assertRaises(ValueError):sample(missing_reason='no_result')
    def test_time_requires_zone(self):
        for t in [datetime(2026,1,1),None,'2026-01-01']:
            with self.assertRaises(ValueError):sample(received_at=t)
    def test_identity(self):
        for key in ['', 'a'*65,'../private','bad space',None]:
            with self.assertRaises(ValueError):sample(key)
    def test_synthetic_only(self):
        for value in [False,1,'true',None]:
            with self.assertRaises(ValueError):sample(synthetic=value)
    def test_model_evidence_not_accuracy(self):
        self.assertEqual(HR_EVIDENCE[Model.V8],Evidence.USER_REPORTED_TRANSPORT)
        self.assertEqual(HR_EVIDENCE[Model.V1],Evidence.SOURCE_ONLY)
        self.assertEqual(HR_EVIDENCE[Model.V4],Evidence.UNKNOWN)
        with self.assertRaises(ValueError):sample(model=Model.V4)
        self.assertIsNone(sample(model=Model.V4,heart_rate_bpm=None,missing_reason='unsupported').heart_rate_bpm)
    def test_history_closed(self):
        for model in Model:
            self.assertFalse(history_enabled(model))
            with self.assertRaises(ValueError):sample(model=model,source=Source.HISTORY)
    def test_immutable(self):
        with self.assertRaises(AttributeError):sample().heart_rate_bpm=90

class BufferTests(unittest.TestCase):
    def test_empty_no_collection(self):self.assertEqual(MemoryBuffer().batch(),())
    def test_order_and_limit(self):
        b=MemoryBuffer();b.enqueue(sample('b'));b.enqueue(sample('a'))
        self.assertEqual([s.sample_id for s in b.batch(1)],['b']);self.assertEqual(len(b),2)
    def test_idempotent_pending(self):
        b=MemoryBuffer();self.assertTrue(b.enqueue(sample()));self.assertFalse(b.enqueue(sample()));self.assertEqual(len(b),1)
    def test_conflict_preserves(self):
        b=MemoryBuffer();b.enqueue(sample())
        with self.assertRaises(ValueError):b.enqueue(sample(heart_rate_bpm=81))
        self.assertEqual(b.batch()[0].heart_rate_bpm,80)
    def test_full_never_evicts(self):
        b=MemoryBuffer(1);b.enqueue(sample('one'))
        with self.assertRaises(BufferError):b.enqueue(sample('two'))
        self.assertEqual(b.batch()[0].sample_id,'one')
    def test_partial_ack(self):
        b=MemoryBuffer();b.enqueue(sample('a'));b.enqueue(sample('b'));b.acknowledge(['a'])
        self.assertEqual([s.sample_id for s in b.batch()],['b'])
    def test_invalid_ack_atomic(self):
        b=MemoryBuffer();b.enqueue(sample('a'))
        for ids in [['a','missing'],['a','a']]:
            with self.assertRaises(ValueError):b.acknowledge(ids)
            self.assertEqual(len(b),1)
    def test_ack_string_cannot_split_into_other_ids(self):
        b=MemoryBuffer();b.enqueue(sample('a'));b.enqueue(sample('b'))
        for ids in ['ab', [['a']], [None]]:
            with self.assertRaises(ValueError):b.acknowledge(ids)
            self.assertEqual(len(b),2)
    def test_capacity_and_batch_bounds(self):
        for capacity in [0,1001,True,-1]:
            with self.assertRaises(ValueError):MemoryBuffer(capacity)
        for size in [0,101,False]:
            with self.assertRaises(ValueError):MemoryBuffer().batch(size)
    def test_fresh_instance_empty(self):
        b=MemoryBuffer();b.enqueue(sample());self.assertEqual(len(MemoryBuffer()),0)
    def test_retries_retain_without_ack(self):
        b=MemoryBuffer();b.enqueue(sample());first=b.batch()
        self.assertEqual(b.batch(),first);self.assertEqual(len(b),1)

class BatchTests(unittest.TestCase):
    def test_roundtrip(self):self.assertEqual(validate_batch(envelope(sample())),(sample(),))
    def test_null_roundtrip(self):
        s=sample(heart_rate_bpm=None,missing_reason='unknown');self.assertEqual(validate_batch(envelope(s)),(s,))
    def test_envelope_strict(self):
        for payload in [None,{},dict(schema_version=2,samples=[]),dict(schema_version=True,samples=[]),dict(schema_version=1,samples=[],token='x')]:
            with self.assertRaises(ValueError):validate_batch(payload)
    def test_bounds(self):
        for rows in [[],[sample(str(i)).wire() for i in range(101)],'not-list']:
            with self.assertRaises(ValueError):validate_batch(dict(schema_version=1,samples=rows))
    def test_unknown_fields_rejected(self):
        for field in ['glucose','blood_pressure','sleep_stage','token']:
            p=envelope(sample());p['samples'][0][field]=123
            with self.assertRaises(ValueError):validate_batch(p)
    def test_no_implicit_real_mode(self):
        p=envelope(sample());p['samples'][0]['synthetic']=False
        with self.assertRaises(ValueError):validate_batch(p)
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):validate_batch(envelope(sample(),sample()))
    def test_invalid_enum_or_date(self):
        for key,value in [('model','V9'),('source','history'),('received_at','yesterday'),('heart_rate_bpm',True)]:
            p=envelope(sample());p['samples'][0][key]=value
            with self.assertRaises(ValueError):validate_batch(p)
    def test_missing_field(self):
        p=envelope(sample());del p['samples'][0]['synthetic']
        with self.assertRaises(ValueError):validate_batch(p)
    def test_no_input_mutation(self):
        p=envelope(sample());before=repr(p);validate_batch(p);self.assertEqual(repr(p),before)

class ReviewTests(unittest.TestCase):
    def test_device_id_required(self):
        for value in ['',None,'a'*65]:
            with self.assertRaises(ValueError):sample(device_id=value)
    def test_utc_normalization(self):
        other=T.astimezone(timezone(timedelta(hours=5,minutes=30)))
        self.assertEqual(sample(received_at=other).wire(),sample().wire())
        self.assertEqual(sample(received_at=other).received_at.tzinfo,timezone.utc)
    def test_utc_boundary_overflow_rejected(self):
        for t in [datetime(1,1,1,tzinfo=timezone(timedelta(hours=1))),datetime(9999,12,31,23,59,tzinfo=timezone(timedelta(hours=-1)))]:
            with self.assertRaises(ValueError):sample(received_at=t)
            p=envelope(sample());p['samples'][0]['received_at']=t.isoformat()
            with self.assertRaises(ValueError):validate_batch(p)
    def test_receipt_time_not_device_order(self):
        b=MemoryBuffer();b.enqueue(sample('new'));b.enqueue(sample('older',received_at=T-timedelta(days=1)))
        self.assertEqual([s.sample_id for s in b.batch()],['new','older'])
    def test_malformed_null_reason(self):
        for reason in [[],{},123]:
            with self.assertRaises(ValueError):sample(heart_rate_bpm=None,missing_reason=reason)
    def test_evidence_cannot_be_promoted(self):
        with self.assertRaises(TypeError):HR_EVIDENCE[Model.V4]=Evidence.USER_REPORTED_TRANSPORT
    def test_device_session_model_scope(self):
        b=MemoryBuffer();b.enqueue(sample())
        for changes in [dict(device_id='other'),dict(session_id='other'),dict(model=Model.V1)]:
            with self.assertRaises(ValueError):b.enqueue(sample('second',**changes))
            self.assertEqual(len(b),1)
    def test_scope_survives_ack(self):
        b=MemoryBuffer();b.enqueue(sample());b.acknowledge(['fixture1'])
        with self.assertRaises(ValueError):b.enqueue(sample('other',session_id='next'))
        self.assertEqual(len(b),0)
    def test_capacity_cannot_be_widened(self):
        b=MemoryBuffer(1)
        with self.assertRaises(AttributeError):b.capacity=10000
    def test_ack_infinite_iterable_bounded(self):
        b=MemoryBuffer(2);b.enqueue(sample())
        with self.assertRaises(ValueError):b.acknowledge(repeat('fixture1'))
        self.assertEqual(len(b),1)
    def test_ack_invalid_iterable(self):
        with self.assertRaises(ValueError):MemoryBuffer().acknowledge(None)
    def test_equal_id_different_scope_not_duplicate(self):
        b=MemoryBuffer();b.enqueue(sample())
        with self.assertRaises(ValueError):b.enqueue(sample(device_id='B'))
    def test_mixed_batch_rejected(self):
        for change in [dict(device_id='B'),dict(session_id='B'),dict(model=Model.V1)]:
            with self.assertRaises(ValueError):validate_batch(envelope(sample(),sample('two',**change)))
    def test_timestamp_bound(self):
        for value in ['a'*100000,{},123,None,'2026-10-22']:
            p=envelope(sample());p['samples'][0]['received_at']=value
            with self.assertRaises(ValueError):validate_batch(p)
    def test_no_model_claim_fields(self):
        for field in ['clinically_validated','history_supported','accuracy']:
            p=envelope(sample());p['samples'][0][field]=True
            with self.assertRaises(ValueError):validate_batch(p)
    def test_partial_ack_retry_scope_preserved(self):
        b=MemoryBuffer();b.enqueue(sample('one'));b.enqueue(sample('two'));b.acknowledge(['one'])
        self.assertEqual(validate_batch(envelope(*b.batch()))[0].sample_id,'two')
        with self.assertRaises(ValueError):b.acknowledge(['one'])
        self.assertEqual(len(b),1)

class IntegrationTests(unittest.TestCase):
    def test_validation_is_not_commit(self):
        b=MemoryBuffer();b.enqueue(sample('a'));b.enqueue(sample('b'))
        accepted=validate_batch(envelope(*b.batch()))
        self.assertEqual(len(b),2) # Validation alone must not drop pending data.
        simulated_commit={accepted[0].sample_id:accepted[0]}
        b.acknowledge(simulated_commit.keys());self.assertEqual([s.sample_id for s in b.batch()],['b'])
    def test_invalid_batch_keeps_pending(self):
        b=MemoryBuffer();b.enqueue(sample());p=envelope(*b.batch());p['samples'][0]['heart_rate_bpm']=0
        with self.assertRaises(ValueError):validate_batch(p)
        self.assertEqual(len(b),1);self.assertEqual(b.batch()[0].heart_rate_bpm,80)
    def test_no_cross_model_history(self):
        with self.assertRaises(ValueError):sample(model=Model.V1,source=Source.HISTORY)
        self.assertEqual(len(MemoryBuffer()),0)

if __name__=='__main__':unittest.main()
