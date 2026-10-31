"""Synthetic direct-candidate SQLite limits; not a production hard deadline.

An SQLite connection has one monotonic budget covering SQL statements until it
is closed. A short busy timeout bounds SQLite lock waits; the progress handler
interrupts long SQLite VM execution. Checks before SQL statements and commit
also catch time spent between statements. SQLite cannot interrupt arbitrary
filesystem or Python stalls, and an already-started commit can run past the
budget. The existing repository still acknowledges only after commit returns.
This wrapper is scoped to the current trusted Repository code, which calls
Connection.execute/executescript/commit. It is not a general sandbox for
arbitrary SQLite callers: short cursor.execute/executemany calls can bypass the
precheck, and executescript on an already-open transaction may implicitly
commit in SQLite legacy mode. Repository.initialize uses a fresh connection.
"""
import math
import os
from pathlib import Path
import sqlite3
import time

from mom_health_backend.repository import SQLiteRepository


class BudgetConnection(sqlite3.Connection):
    """Connection whose lifetime has a cooperative repository SQL budget."""

    def configure_budget(self, clock, budget_seconds, instruction_interval):
        self._budget_clock = clock
        now = clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError('Invalid SQLite clock')
        self._budget_last_time = now
        self._budget_deadline = now + budget_seconds
        self._budget_instruction_interval = instruction_interval
        self.set_progress_handler(self._interrupt_if_expired, instruction_interval)

    def _expired(self):
        now = self._budget_clock()
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now) or now < self._budget_last_time):
            return True
        self._budget_last_time = now
        return now >= self._budget_deadline

    def _interrupt_if_expired(self):
        return int(self._expired())

    def _check_budget(self):
        if self._expired():
            raise sqlite3.OperationalError('SQLite execution budget exceeded')

    def execute(self, sql, parameters=(), /):
        self._check_budget()
        return super().execute(sql, parameters)

    def executescript(self, sql_script, /):
        self._check_budget()
        return super().executescript(sql_script)

    def commit(self):
        # Once a commit starts, wait for its actual outcome; an externally
        # imposed timeout must not turn an unknown commit into an ACK.
        self._check_budget()
        self.set_progress_handler(None, 0)
        try:
            return super().commit()
        finally:
            self.set_progress_handler(self._interrupt_if_expired, self._budget_instruction_interval)

    def rollback(self):
        # Cleanup must not be interrupted just because the budget expired.
        self.set_progress_handler(None, 0)
        try:
            return super().rollback()
        finally:
            self.set_progress_handler(self._interrupt_if_expired, self._budget_instruction_interval)


class BudgetedSQLiteRepository(SQLiteRepository):
    """Explicit synthetic-only alternative for the direct-auth candidate."""

    def __init__(self, path, *, budget_seconds=2.0, busy_seconds=0.25,
                 instruction_interval=1000, clock=time.monotonic, require_existing=False):
        super().__init__(path)
        if not isinstance(require_existing, bool):
            raise ValueError('Invalid SQLite existing-only option')
        if require_existing:
            parsed = Path(self.path)
            if not parsed.is_absolute():
                raise ValueError('Existing-only SQLite path must be absolute')
            if os.name == 'nt' and parsed.drive.startswith('\\\\'):
                raise ValueError('Existing-only SQLite requires a drive-letter path')
        if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
                or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 30):
            raise ValueError('Invalid SQLite budget')
        if (isinstance(busy_seconds, bool) or not isinstance(busy_seconds, (int, float))
                or not math.isfinite(busy_seconds) or not 0 < busy_seconds <= min(5, budget_seconds)):
            raise ValueError('Invalid SQLite busy timeout')
        if type(instruction_interval) is not int or not 1 <= instruction_interval <= 100000:
            raise ValueError('Invalid SQLite progress interval')
        if not callable(clock):
            raise ValueError('Invalid SQLite clock')
        self.budget_seconds = budget_seconds
        self.busy_seconds = busy_seconds
        self.instruction_interval = instruction_interval
        self.clock = clock
        self.require_existing = require_existing

    def connect(self):
        # SQLite URI mode=rw refuses to create the DB if it disappeared after
        # metadata checks. as_uri percent-encodes path characters so they cannot
        # become URI query options. This does not prevent same-UID/admin swaps or
        # symlink races, and does not replace the separate storage validation.
        location = Path(self.path).as_uri() + '?mode=rw' if self.require_existing else self.path
        connection = sqlite3.connect(location, timeout=self.busy_seconds, factory=BudgetConnection,
                                     uri=self.require_existing)
        try:
            connection.configure_budget(self.clock, self.budget_seconds, self.instruction_interval)
            connection.execute('PRAGMA foreign_keys=ON')
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _synthetic_only(allow_real):
        if allow_real is not False:
            raise ValueError('Real data is disabled')

    def ingest(self, batch, allow_real=False):
        self._synthetic_only(allow_real)
        return super().ingest(batch, allow_real=False)

    def page(self, device, limit, cursor=None, allow_real=False, order='asc'):
        self._synthetic_only(allow_real)
        return super().page(device, limit, cursor, allow_real=False, order=order)

    def devices(self, limit=50, cursor=None, allow_real=False):
        self._synthetic_only(allow_real)
        return super().devices(limit, cursor, allow_real=False)

    def save_status(self, status, allow_real=False):
        self._synthetic_only(allow_real)
        return super().save_status(status, allow_real=False)

    def status(self, device, allow_real=False):
        self._synthetic_only(allow_real)
        return super().status(device, allow_real=False)
