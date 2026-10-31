from collections import OrderedDict
from itertools import islice
from contract import Sample

class MemoryBuffer:
    """Explicit synthetic enqueue only. No disk, timers, collection or transport."""
    def __init__(self, capacity=100):
        if type(capacity) is not int or not 1 <= capacity <= 1000:
            raise ValueError('Capacity must be 1..1000')
        self._capacity = capacity
        self._scope = None
        self._pending = OrderedDict()

    @property
    def capacity(self): return self._capacity

    def enqueue(self, sample: Sample) -> bool:
        if not isinstance(sample, Sample): raise ValueError('Validated sample required')
        scope = (sample.device_id, sample.session_id, sample.model)
        if self._scope is not None and scope != self._scope:
            raise ValueError('Buffer is bound to one device/model/session; use a separate buffer')
        previous = self._pending.get(sample.sample_id)
        if previous is not None:
            if previous != sample: raise ValueError('Conflicting sample ID')
            return False
        if len(self._pending) >= self.capacity: raise BufferError('Full; no silent eviction')
        self._scope = scope
        self._pending[sample.sample_id] = sample
        return True

    def batch(self, limit=25) -> tuple[Sample, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Batch limit must be 1..100')
        return tuple(islice(self._pending.values(), limit))

    def acknowledge(self, sample_ids) -> None:
        # Caller must represent a confirmed commit, never merely serialization/validation.
        if isinstance(sample_ids, (str, bytes)): raise ValueError("Acknowledgment must be a collection of IDs")
        try: ids = tuple(islice(iter(sample_ids), self.capacity + 1))
        except TypeError as error: raise ValueError('Acknowledgment IDs must be iterable') from error
        if len(ids) > self.capacity: raise ValueError('Acknowledgment exceeds capacity')
        if any(type(x) is not str for x in ids): raise ValueError("Acknowledgment IDs must be strings")
        if len(ids) != len(set(ids)) or any(x not in self._pending for x in ids):
            raise ValueError('Unknown/duplicate acknowledgment; nothing removed')
        for sample_id in ids: del self._pending[sample_id]

    def __len__(self): return len(self._pending)
