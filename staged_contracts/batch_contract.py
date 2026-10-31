from datetime import datetime
from contract import Model, Source, Sample

FIELDS = {'sample_id','device_id','session_id','model','received_at','heart_rate_bpm',
          'missing_reason','source','synthetic'}

def validate_batch(payload) -> tuple[Sample, ...]:
    """Pure future-boundary validation; no endpoint, storage, acknowledgment or upload."""
    if type(payload) is not dict or set(payload) != {'schema_version', 'samples'}:
        raise ValueError('Unexpected envelope fields')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
        raise ValueError('Unknown schema version')
    rows = payload['samples']
    if type(rows) is not list or not 1 <= len(rows) <= 100:
        raise ValueError('Expected 1..100 synthetic samples')
    result=[]; seen=set(); scope=None
    for row in rows:
        if type(row) is not dict or set(row) != FIELDS: raise ValueError('Unexpected sample fields')
        if type(row['received_at']) is not str or not 1 <= len(row['received_at']) <= 40:
            raise ValueError('Timestamp must be bounded ISO text')
        try:
            sample = Sample(sample_id=row['sample_id'], device_id=row['device_id'], session_id=row['session_id'],
                model=Model(row['model']), received_at=datetime.fromisoformat(row['received_at']),
                heart_rate_bpm=row['heart_rate_bpm'], missing_reason=row['missing_reason'],
                source=Source(row['source']), synthetic=row['synthetic'])
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid sample') from error
        current_scope=(sample.device_id,sample.session_id,sample.model)
        if scope is not None and current_scope != scope: raise ValueError('Mixed device/model/session batch')
        scope=current_scope
        if sample.sample_id in seen: raise ValueError('Duplicate ID in batch')
        seen.add(sample.sample_id); result.append(sample)
    return tuple(result)
