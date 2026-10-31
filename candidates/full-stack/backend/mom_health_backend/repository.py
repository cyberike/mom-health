"""Transactional adapters; acknowledgments are returned only after commit."""
import base64
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Batch

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class BatchConflict(Exception):
    pass


def encode_cursor(device, seq):
    return base64.urlsafe_b64encode(canonical([device, seq]).encode()).decode().rstrip("=")


def decode_cursor(device, cursor):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 256:
            raise ValueError()
        payload = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(payload, list) or len(payload) != 2 or payload[0] != device:
            raise ValueError()
        seq = payload[1]
        if type(seq) is not int or not 0 <= seq <= 9223372036854775807:
            raise ValueError()
        return seq
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc


class Repository:
    postgres = False

    def execute(self, connection, sql, args=()):
        return connection.execute(sql.replace("?", "%s") if self.postgres else sql, args)

    def initialize(self):
        connection = self.connect()
        try:
            suffix="postgresql" if self.postgres else "sqlite"
            migration = "\n".join(path.read_text() for path in sorted(MIGRATIONS.glob("*_"+suffix+".sql")))
            if self.postgres:
                # Serialize first-time migration among processes.
                connection.execute("SELECT pg_advisory_xact_lock(7240090001)")
                for statement in migration.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            else:
                connection.executescript("BEGIN IMMEDIATE;\n" + migration)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ingest(self, batch: Batch, allow_real=False):
        # In synthetic-only mode, a mixed or real-marked batch is rejected as a
        # whole before opening storage. This prevents a receipt from retaining
        # submitted batch/sample identifiers or a digest of rejected values.
        # Synthetic entries stay pending and may be retried in a new batch.
        if not allow_real and any(not sample.synthetic for sample in batch.samples):
            return dict(batch_id=batch.batch_id, accepted_ids=[], duplicate_ids=[],
                        rejected=[dict(sample_id=sample.sample_id,
                                       reason='batch_contains_real_data' if sample.synthetic else 'real_data_disabled')
                                  for sample in batch.samples])
        # Receipt stores only a digest, never the values of policy-rejected samples.
        batch_json = hashlib.sha256(canonical(batch.model_dump()).encode()).hexdigest()
        connection = self.connect()
        try:
            if self.postgres:
                # One transaction lock ensures deterministic duplicate/collision behavior,
                # including concurrent requests with intersecting IDs in differing order.
                connection.execute("SELECT pg_advisory_xact_lock(7240090002)")
            else:
                connection.execute("BEGIN IMMEDIATE")
            previous = self.execute(connection, "SELECT canonical, response FROM batches WHERE batch_id=?", (batch.batch_id,)).fetchone()
            if previous:
                if previous[0] != batch_json:
                    raise BatchConflict()
                result = json.loads(previous[1])
            else:
                result = dict(batch_id=batch.batch_id, accepted_ids=[], duplicate_ids=[], rejected=[])
                for sample in batch.samples:
                    if not sample.synthetic and not allow_real:
                        result["rejected"].append(dict(sample_id=sample.sample_id, reason="real_data_disabled"))
                        continue
                    content = canonical(sample.model_dump())
                    existing = self.execute(connection, "SELECT canonical FROM samples WHERE sample_id=?", (sample.sample_id,)).fetchone()
                    if existing:
                        if existing[0] == content:
                            result["duplicate_ids"].append(sample.sample_id)
                        else:
                            result["rejected"].append(dict(sample_id=sample.sample_id, reason="sample_id_collision"))
                    else:
                        self.execute(connection, "INSERT INTO samples(sample_id,device_id,synthetic,canonical) VALUES (?,?,?,?)", (sample.sample_id, sample.device_id, int(sample.synthetic), content))
                        result["accepted_ids"].append(sample.sample_id)
                self.execute(connection, "INSERT INTO batches(batch_id,canonical,response) VALUES (?,?,?)", (batch.batch_id, batch_json, canonical(result)))
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def page(self, device, limit, cursor=None, allow_real=False, order="asc"):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid page limit")
        if order not in ("asc", "desc"):
            raise ValueError("Invalid order")
        cursor_scope = device if order == "asc" else device + "|desc"
        seq = decode_cursor(cursor_scope, cursor) if cursor else (0 if order == "asc" else 9223372036854775807)
        connection = self.connect()
        try:
            policy = "" if allow_real else " AND synthetic=1"
            comparison, direction = (">", "ASC") if order == "asc" else ("<", "DESC")
            rows = self.execute(connection, "SELECT seq,canonical FROM samples WHERE device_id=? AND seq" + comparison + "?" + policy + " ORDER BY seq " + direction + " LIMIT ?", (device, seq, limit + 1)).fetchall()
            selected = rows[:limit]
            return dict(samples=[json.loads(row[1]) for row in selected], next_cursor=encode_cursor(cursor_scope, selected[-1][0]) if len(rows) > limit else None)
        finally:
            connection.close()

    def devices(self, limit=50, cursor=None, allow_real=False):
        from .models import device_identifier
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        if cursor is not None:
            device_identifier(cursor)
        connection = self.connect()
        try:
            policy = "" if allow_real else " AND synthetic=1"
            selection="" if allow_real else " WHERE synthetic=1"
            groups=self.execute(connection,"SELECT device_id FROM (SELECT device_id FROM samples"+selection+" UNION SELECT device_id FROM gateway_status"+selection+") AS known_devices WHERE device_id>? ORDER BY device_id LIMIT ?",(cursor or "",limit+1)).fetchall()
            result=[]
            for (device,) in groups[:limit]:
                count,seq=self.execute(connection,"SELECT COUNT(*),MAX(seq) FROM samples WHERE device_id=?"+policy,(device,)).fetchone()
                row=self.execute(connection,"SELECT canonical FROM samples WHERE seq=?",(seq,)).fetchone() if seq is not None else None
                result.append(dict(device_id=device,sample_count=count,latest=json.loads(row[0]) if row else None))
            return dict(devices=result,next_cursor=result[-1]["device_id"] if len(groups)>limit else None)
        finally:
            connection.close()

    def save_status(self,status,allow_real=False):
        if not status.synthetic and not allow_real:
            raise ValueError("real_data_disabled")
        now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        connection=self.connect()
        try:
            self.execute(connection,"INSERT INTO gateway_status(device_id,synthetic,canonical,server_received_at) VALUES (?,?,?,?) ON CONFLICT(device_id,synthetic) DO UPDATE SET canonical=excluded.canonical,server_received_at=excluded.server_received_at",(status.device_id,int(status.synthetic),canonical(status.model_dump()),now))
            connection.commit()
            return dict(accepted=True,server_received_at=now)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def status(self,device,allow_real=False):
        connection=self.connect()
        try:
            policy="" if allow_real else " AND synthetic=1"
            row=self.execute(connection,"SELECT canonical,server_received_at FROM gateway_status WHERE device_id=?"+policy+" ORDER BY server_received_at DESC LIMIT 1",(device,)).fetchone()
            return dict(status=json.loads(row[0]),server_received_at=row[1]) if row else dict(status=None,server_received_at=None)
        finally:
            connection.close()


class SQLiteRepository(Repository):
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


class PostgreSQLRepository(Repository):
    postgres = True

    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requires the separately approved psycopg dependency") from exc
        return psycopg.connect(self.dsn, connect_timeout=5)
