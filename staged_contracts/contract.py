from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from enum import Enum
import re

class Model(str, Enum):
    V1 = 'V1'
    V4 = 'V4'
    V8 = 'V8'

class Source(str, Enum):
    LIVE = 'live'
    HISTORY = 'history'

class Evidence(str, Enum):
    SOURCE_ONLY = 'source_only'
    USER_REPORTED_TRANSPORT = 'user_reported_transport'
    UNKNOWN = 'unknown'

HR_EVIDENCE = MappingProxyType({Model.V1: Evidence.SOURCE_ONLY, Model.V4: Evidence.UNKNOWN,
               Model.V8: Evidence.USER_REPORTED_TRANSPORT})
# No history retrieval has been implemented or hardware-verified in this project.
def history_enabled(model: Model) -> bool:
    if not isinstance(model, Model): raise ValueError('Unknown model')
    return False

def identifier(value: str) -> None:
    if type(value) is not str or not 1 <= len(value) <= 64 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('Invalid synthetic identifier')

@dataclass(frozen=True)
class Sample:
    sample_id: str
    device_id: str
    session_id: str
    model: Model
    received_at: datetime
    heart_rate_bpm: int | None
    synthetic: bool
    missing_reason: str | None = None
    source: Source = Source.LIVE

    def __post_init__(self):
        identifier(self.sample_id); identifier(self.device_id); identifier(self.session_id)
        if not isinstance(self.model, Model) or not isinstance(self.source, Source):
            raise ValueError('Unknown model or source')
        if self.synthetic is not True:
            raise ValueError('Inactive scaffolding accepts synthetic fixtures only')
        if not isinstance(self.received_at, datetime) or self.received_at.utcoffset() is None:
            raise ValueError('Timezone-aware phone receive time required')
        try: normalized = self.received_at.astimezone(timezone.utc)
        except (ValueError, OverflowError) as error: raise ValueError('Timestamp outside UTC range') from error
        object.__setattr__(self, 'received_at', normalized)
        if self.source is Source.HISTORY:
            raise ValueError('History retrieval is not enabled for any model')
        if self.heart_rate_bpm is None:
            if type(self.missing_reason) is not str or self.missing_reason not in {'no_result', 'not_requested', 'unsupported', 'unknown'}:
                raise ValueError('Null requires a reason')
        elif (type(self.heart_rate_bpm) is not int or not 20 <= self.heart_rate_bpm <= 250
              or self.missing_reason is not None):
            raise ValueError('Invalid HR candidate or contradictory reason')
        if self.model is Model.V4 and self.heart_rate_bpm is not None:
            raise ValueError('V4 HR adapter is not implemented')

    def wire(self) -> dict:
        return dict(sample_id=self.sample_id, device_id=self.device_id, session_id=self.session_id, model=self.model.value,
                    received_at=self.received_at.isoformat(), heart_rate_bpm=self.heart_rate_bpm,
                    missing_reason=self.missing_reason, source=self.source.value, synthetic=True)
