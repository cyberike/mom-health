import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


def identifier(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Expected canonical lowercase UUID")
    return value


def device_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Expected opaque device identifier")
    if re.fullmatch(r"[0-9A-Fa-f]{12}", value):
        raise ValueError("Direct device addresses are not identifiers")
    return value


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sample_id: str
    device_id: str
    session_id: str
    model: Literal["V1", "V4", "V8"]
    source: Literal["live", "history"]
    received_at: str
    measured_at: str | None
    heart_rate_bpm: StrictInt | None = Field(ge=20, le=250)
    missing_reason: Literal["no_result", "unsupported", "not_requested", "unknown"] | None
    synthetic: StrictBool

    _ids = field_validator("sample_id", "session_id")(identifier)
    _device = field_validator("device_id")(device_identifier)

    @field_validator("received_at", "measured_at")
    @classmethod
    def timestamp(cls, value):
        if value is None:
            return None
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value)
        if match is None:
            raise ValueError("Expected timezone-aware ISO8601 timestamp")
        parsed = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        try:
            utc = parsed.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError("UTC timestamp outside supported year range") from exc
        # Java Instant supports nanoseconds; do not silently truncate to Python's
        # microseconds when deciding whether two sample contents are identical.
        fraction = (match[2] or "").rstrip("0")
        suffix = ("." + fraction) if fraction else ""
        return utc.isoformat(timespec="seconds").removesuffix("+00:00") + suffix + "Z"

    @model_validator(mode="after")
    def missing_value(self):
        if not self.synthetic and self.heart_rate_bpm is not None and (self.model not in ("V1", "V8") or self.source != "live"):
            raise ValueError("Populated HR unsupported for this model/source")
        if (self.heart_rate_bpm is None) != (self.missing_reason is not None):
            raise ValueError("Missing HR requires a reason; present HR requires null reason")
        return self


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt
    batch_id: str
    samples: list[Sample] = Field(min_length=1, max_length=100)
    _id = field_validator("batch_id")(identifier)

    @model_validator(mode="after")
    def validate_batch(self):
        if self.schema_version != 1:
            raise ValueError("Unsupported schema version")
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate sample IDs within batch")
        return self


class GatewayStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt = Field(ge=1,le=1)
    device_id: str
    model: Literal["V1","V8"]
    reported_at: str
    last_observation_at: str | None
    battery_percent: StrictInt | None = Field(ge=0,le=100)
    connection: Literal["connecting","connected","disconnected","error"]
    pending_samples: StrictInt = Field(ge=0,le=10000)
    uploaded_samples: StrictInt = Field(ge=0,le=10000)
    recording_enabled: StrictBool
    synthetic: StrictBool
    _device=field_validator("device_id")(device_identifier)

    @field_validator("reported_at","last_observation_at")
    @classmethod
    def timestamp(cls,value):
        return Sample.timestamp(value)
