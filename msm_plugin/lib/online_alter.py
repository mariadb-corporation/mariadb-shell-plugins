# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Online ALTER TABLE: one ALTER TABLE, run the least disruptive way the
server accepts.

The server is never asked to guess. Each step sends the statement with
explicit ALGORITHM and LOCK clauses, which make MariaDB either run it in
exactly that way or refuse it at once (1845, 1846), before any data is
touched; a refusal moves on to the next, more expensive step:

1. ``ALGORITHM=INSTANT``: metadata only;
2. ``ALGORITHM=NOCOPY, LOCK=NONE``: in place, reads and writes go on;
3. ``ALGORITHM=COPY, LOCK=NONE``: the server's online copy (MariaDB 11.2
   and later; older servers refuse it with 1846);
4. Galera only, where ``nbo_lock`` is set: a Non-Blocking Operation
   (MariaDB Enterprise Server), which blocks writes to the table;
5. ``LOCK=SHARED``, then 6. ``LOCK=EXCLUSIVE``, no ALGORITHM, so the server
   takes the cheapest one it can run under that lock: for what no step
   without a lock can do (a column default from a sequence). Writes wait,
   with EXCLUSIVE reads too; this is what a plain ALTER TABLE would do.

What no step applies is ``NEEDS_EXTERNAL``: nothing changed, and the change
is for an external tool (gh-ost, pt-online-schema-change).

The table's size and Galera decide in advance which steps may run: COPY not
above ``max_copy_bytes``, the locked steps not above ``max_locked_bytes``
(no limit by default, so nothing a plain ALTER TABLE runs is refused); on
Galera, every step but INSTANT and NBO runs under Total Order Isolation,
which pauses the whole cluster, so only up to ``galera_max_bytes``.

No shell imports: the session is passed in, and an error's number is read
from its ``code``, as ``mysqlsh.DBError`` has it.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from msm_plugin.lib import statements

INSTANT = "INSTANT"
NOCOPY = "NOCOPY"
COPY = "COPY"
NBO = "NBO"
SHARED_LOCK = "SHARED_LOCK"
EXCLUSIVE_LOCK = "EXCLUSIVE_LOCK"
NEEDS_EXTERNAL = "NEEDS_EXTERNAL"

# The methods that apply the change, in the order they are tried
METHODS = (INSTANT, NOCOPY, COPY, NBO, SHARED_LOCK, EXCLUSIVE_LOCK)
# The steps that block writes to the table
LOCKED = (SHARED_LOCK, EXCLUSIVE_LOCK)

_CLAUSES = {
    INSTANT: "ALGORITHM=INSTANT",
    NOCOPY: "ALGORITHM=NOCOPY, LOCK=NONE",
    COPY: "ALGORITHM=COPY, LOCK=NONE",
    SHARED_LOCK: "LOCK=SHARED",
    EXCLUSIVE_LOCK: "LOCK=EXCLUSIVE",
}

MIB = 1024 * 1024
GIB = 1024 * MIB

# Server errors the steps handle themselves
ER_NOT_SUPPORTED_YET = 1235  # also: NBO does not support this statement
ER_ALTER_OPERATION_NOT_SUPPORTED = 1845
ER_ALTER_OPERATION_NOT_SUPPORTED_REASON = 1846
ER_LOCK_WAIT_TIMEOUT = 1205
ER_WRONG_VALUE_FOR_VAR = 1231  # wsrep_OSU_method = 'NBO' on Community Server
ER_QUERY_INTERRUPTED = 1317  # KILL QUERY

_REFUSED = (
    ER_ALTER_OPERATION_NOT_SUPPORTED,
    ER_ALTER_OPERATION_NOT_SUPPORTED_REASON,
    ER_NOT_SUPPORTED_YET,
)

# The longest wait between two attempts after a lock wait timeout
MAX_BACKOFF = 30


class OnlineAlterError(Exception):
    """The change could not be run: the table is missing, the session is
    not fit for it, or the metadata lock was never granted."""


class OnlineAlterCancelled(OnlineAlterError):
    """The caller cancelled the change; the step that ran was rolled back by
    the server (an ALTER TABLE is atomic)."""


@dataclass
class Options:
    """How far an online ALTER TABLE may go.

    Attributes:
        max_copy_bytes: The largest table (data + indexes) the server's
            online COPY may run on; None = no limit, 0 = never.
        galera_max_bytes: On Galera, the largest table NOCOPY or COPY may run
            on under Total Order Isolation, which pauses the cluster; None
            or 0 = only INSTANT under TOI.
        nbo_lock: On Galera, SHARED (reads go on) or EXCLUSIVE to allow a
            Non-Blocking Operation; None = never.
        max_locked_bytes: The largest table the steps that block writes
            (LOCK=SHARED, then EXCLUSIVE) may run on; None = no limit, 0 =
            never.
        lock_wait: Seconds to wait for the table's metadata lock per attempt
            (the session's lock_wait_timeout during it).
        max_retries: Attempts after a lock wait timeout, per step, waiting
            2, 4, 8 ... up to MAX_BACKOFF seconds in between.
    """

    max_copy_bytes: Optional[int] = 10 * GIB
    galera_max_bytes: Optional[int] = 256 * MIB
    nbo_lock: Optional[str] = None
    max_locked_bytes: Optional[int] = None
    lock_wait: int = 5
    max_retries: int = 10

    # The keys of from_dict, as msm.project.json and the tools name them
    KEYS = {
        "maxCopyBytes": "max_copy_bytes",
        "galeraMaxBytes": "galera_max_bytes",
        "nboLock": "nbo_lock",
        "maxLockedBytes": "max_locked_bytes",
        "lockWait": "lock_wait",
        "maxRetries": "max_retries",
    }

    @classmethod
    def from_dict(cls, values: Optional[dict], base: "Options" = None) -> "Options":
        """Options from a dict with camelCase keys (KEYS); a key left out
        keeps base's value (the defaults where base is None).

        Raises:
            ValueError: For an unknown key or a value out of range.
        """
        options = Options(**vars(base)) if base is not None else Options()
        for key, value in (values or {}).items():
            if key not in cls.KEYS:
                raise ValueError(
                    f"Unknown online ALTER TABLE option `{key}`; the options are "
                    f"{', '.join(cls.KEYS)}."
                )
            setattr(options, cls.KEYS[key], value)
        options.validate()
        return options

    def to_dict(self) -> dict:
        """The options with the camelCase keys of from_dict."""
        return {key: getattr(self, name) for key, name in self.KEYS.items()}

    def validate(self) -> None:
        """Raises ValueError for a value out of range; makes nbo_lock upper case."""
        for name in ("max_copy_bytes", "galera_max_bytes", "max_locked_bytes"):
            value = getattr(self, name)
            if value is not None and (not _is_int(value) or value < 0):
                raise ValueError(f"`{name}` must be a number of bytes (0 or more) or None.")
        if self.nbo_lock is not None:
            if not isinstance(self.nbo_lock, str) or self.nbo_lock.upper() not in (
                "SHARED",
                "EXCLUSIVE",
            ):
                raise ValueError("`nbo_lock` must be SHARED, EXCLUSIVE or None.")
            self.nbo_lock = self.nbo_lock.upper()
        if not _is_int(self.lock_wait) or self.lock_wait < 1:
            raise ValueError("`lock_wait` must be a number of seconds (1 or more).")
        if not _is_int(self.max_retries) or self.max_retries < 0:
            raise ValueError("`max_retries` must be 0 or more.")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# -----------------------------------------------------------------------------
# The setting: msm.project.json's `onlineAlter` and a deployment's own
#
#   "onlineAlter": {
#       "enabled": true,                        # optional, default true
#       "maxCopyBytes": 1073741824, ...         # any Options.KEYS
#       "tables": {"orders": {"maxCopyBytes": 0}, "audit": {"enabled": false}}
#   }
#
# `true` / `false` stand for {"enabled": ...}. A missing setting is enabled
# with the defaults.

SETTING = "onlineAlter"


def with_tables(setting, change: Callable[[dict], None]):
    """An `onlineAlter` setting as msm.project.json has it (None, a bool or
    a dict) with its tables changed by change(tables), which edits the dict
    in place; None where that changes nothing. An empty tables entry is
    left out; a bool or missing setting becomes {enabled}."""
    if isinstance(setting, dict):
        new = dict(setting)
    else:
        new = {"enabled": True if setting is None else setting}
    tables = dict(new.get("tables") or {})
    change(tables)
    if tables:
        new["tables"] = tables
    else:
        new.pop("tables", None)
    return None if new == setting else new


def read_setting(value, where: str) -> dict:
    """An onlineAlter setting checked and normalized: {enabled?, <option
    keys>?, tables: {table: {enabled?, <option keys>?}}}.

    Args:
        value: The setting: None, a bool or a dict.
        where: Names the setting in an error, e.g. "msm.project.json".

    Raises:
        ValueError: For an unknown key or a value out of range.
    """
    if value is None:
        return {"tables": {}}
    if isinstance(value, bool):
        return {"enabled": value, "tables": {}}
    if not isinstance(value, dict):
        raise ValueError(f"The {SETTING} setting of {where} must be true, false or an object.")
    setting = _read_level(value, f"{where}, {SETTING}", tables_allowed=True)
    tables = value.get("tables", {})
    if not isinstance(tables, dict):
        raise ValueError(f"`tables` of the {SETTING} setting of {where} must be an object.")
    setting["tables"] = {}
    for table, table_value in tables.items():
        if not isinstance(table_value, dict):
            raise ValueError(
                f"The {SETTING} setting of {where} for table `{table}` must be an object."
            )
        setting["tables"][table] = _read_level(
            table_value, f"{where}, {SETTING} of table `{table}`", tables_allowed=False
        )
    return setting


def _read_level(value: dict, where: str, tables_allowed: bool) -> dict:
    level = {}
    for key, item in value.items():
        if key == "enabled":
            if not isinstance(item, bool):
                raise ValueError(f"`enabled` must be true or false ({where}).")
            level[key] = item
        elif key == "tables" and tables_allowed:
            continue
        elif key in Options.KEYS:
            level[key] = item
        else:
            known = ["enabled"] + list(Options.KEYS) + (["tables"] if tables_allowed else [])
            raise ValueError(f"Unknown key `{key}` ({where}); the keys are {', '.join(known)}.")
    # The values on their own, so a bad one is reported where it is written
    try:
        Options.from_dict({key: item for key, item in level.items() if key in Options.KEYS})
    except ValueError as error:
        raise ValueError(f"{error} ({where})") from None
    return level


class DeploymentSettings:
    """The onlineAlter settings of one deployment: the project's and the
    call's. For a table, the most specific value wins, and at the same
    level the call's: the project's general values, the call's, the
    project's for the table, the call's for the table."""

    def __init__(self, project: dict, call: dict = None):
        self.project = project
        self.call = call if call is not None else read_setting(None, "the deployment")

    def any_enabled(self) -> bool:
        """Whether online ALTER TABLE is on for any table."""
        general = self.call.get("enabled", self.project.get("enabled", True))
        tables = list(self.project["tables"].values()) + list(self.call["tables"].values())
        return general or any(level.get("enabled") for level in tables)

    def options_for(self, table: str) -> Optional[Options]:
        """The Options for a table's ALTER TABLE; None where online ALTER
        TABLE is off for it. Table names match exactly."""
        enabled, values = True, {}
        for level in (
            self.project,
            self.call,
            self.project["tables"].get(table),
            self.call["tables"].get(table),
        ):
            if not level:
                continue
            enabled = level.get("enabled", enabled)
            values.update((key, item) for key, item in level.items() if key in Options.KEYS)
        return Options.from_dict(values) if enabled else None


def deployment_settings(project_settings: dict, call=None) -> DeploymentSettings:
    """The onlineAlter settings of a deployment, checked.

    Args:
        project_settings: The project's msm.project.json.
        call: The deployment's own setting (None, a bool or a dict, as in
            msm.project.json).

    Raises:
        ValueError: For a setting read_setting refuses.
    """
    return DeploymentSettings(
        read_setting(project_settings.get(SETTING), "msm.project.json"),
        read_setting(call, "the deployment"),
    )


@dataclass
class Result:
    """What an online ALTER TABLE did.

    Attributes:
        method: INSTANT, NOCOPY, COPY or NBO (the change is applied), or
            NEEDS_EXTERNAL (nothing changed).
        reason: Why NEEDS_EXTERNAL, or what NBO blocked; None otherwise.
        table_size: The table's data + index bytes before the change (an
            estimate for InnoDB).
        galera: Whether the session is a Galera node's.
        steps: Each step, in order: {method, outcome, seconds, message},
            outcome one of applied, refused, skipped.
    """

    method: str
    reason: Optional[str]
    table_size: int
    galera: bool
    steps: list = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return self.method != NEEDS_EXTERNAL


def check_spec(spec: str) -> str:
    """The clauses of an ALTER TABLE (the text after its table name) fit for
    the steps: stripped of a trailing ``;``, a line break after a trailing
    line comment.

    Raises:
        ValueError: For no clauses, or an ALGORITHM or LOCK clause, which the
            steps set themselves.
    """
    spec = (spec or "").strip()
    while spec.endswith(";"):
        spec = spec[:-1].rstrip()
    if not spec:
        raise ValueError("The ALTER TABLE has no clauses.")
    clause = statements.locking_clause(spec)
    if clause:
        raise ValueError(
            f"The ALTER TABLE sets `{clause}` itself; an online ALTER TABLE sets "
            "ALGORITHM and LOCK for each step. Run it as written instead."
        )
    # The clauses the steps append go on a line of their own
    return spec + "\n" if statements.ends_in_line_comment(spec) else spec


def plan(table_size: int, galera: bool, options: Options) -> tuple:
    """The steps an online ALTER TABLE may try, and the steps skipped.

    Returns:
        ([methods], [(method, why skipped)]), both in METHODS order.
    """
    def over(limit):
        return limit is not None and (limit == 0 or table_size > limit)

    def why_skipped(method):
        if galera and (not options.galera_max_bytes or table_size > options.galera_max_bytes):
            return ("Galera: non-instant DDL under TOI pauses the cluster; table size "
                    f"{table_size} > galera_max_bytes {options.galera_max_bytes or 0}")
        if method == COPY and over(options.max_copy_bytes):
            return (f"native COPY skipped: table size {table_size} > max_copy_bytes "
                    f"{options.max_copy_bytes}")
        if method in LOCKED and over(options.max_locked_bytes):
            return (f"blocking writes skipped: table size {table_size} > max_locked_bytes "
                    f"{options.max_locked_bytes}")
        return None

    allowed, skipped = [INSTANT], []
    for method in METHODS[1:]:
        if method == NBO:
            # Not capped: NBO is the way past TOI's pause
            if galera and options.nbo_lock:
                allowed.append(NBO)
            continue
        why = why_skipped(method)
        if why:
            skipped.append((method, why))
        else:
            allowed.append(method)
    return allowed, skipped


def step_sql(target: str, spec: str, method: str, nbo_lock: str = None) -> str:
    """The statement a step sends; target is the quoted table."""
    if method == NBO:
        # NBO wants the LOCK clause first and no ALGORITHM
        return f"ALTER TABLE {target} LOCK={nbo_lock}, {spec}"
    return f"ALTER TABLE {target} {spec}, {_CLAUSES[method]}"


def alter_table(
    session,
    schema: str,
    table: str,
    spec: str,
    options: Options = None,
    *,
    monitor=None,
    on_event: Callable[[dict], None] = None,
    cancel: threading.Event = None,
    poll_interval: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Result:
    """Runs one ALTER TABLE online, as far as options allow.

    Run it in its own session, outside a transaction (DDL commits). The
    session's lock_wait_timeout and, on Galera, wsrep_OSU_method are put
    back afterwards, also after an error.

    Args:
        session: The session to alter the table in.
        schema: The table's schema, as is (quoted here).
        table: The table, as is.
        spec: Everything after ``ALTER TABLE <table>``, without ALGORITHM or
            LOCK (check_spec).
        options: The limits; the defaults where None.
        monitor: A second session on the same server. With it, the step's
            progress is read from information_schema.PROCESSLIST while it
            runs, and a cancel stops the running statement (KILL QUERY);
            without it a cancel waits for the step to end.
        on_event: Called with {event: step | refused | skipped | retry |
            progress, method, ...} as the change goes; it must not raise.
        cancel: Set to cancel; OnlineAlterCancelled is raised.
        poll_interval: Seconds between two reads of the progress.
        sleep: Waits between attempts where there is no cancel event.

    Returns:
        The Result; NEEDS_EXTERNAL when no step applied the change.

    Raises:
        ValueError: For a spec check_spec refuses, or bad options.
        OnlineAlterError: For a missing table, a Galera session on RSU, or a
            metadata lock not granted after max_retries.
        OnlineAlterCancelled: When cancelled.
        Any other server error, unchanged (a syntax error, a duplicate key).
    """
    options = options if options is not None else Options()
    options.validate()
    spec = check_spec(spec)
    emit = on_event or (lambda event: None)
    target = f"{statements.quote_identifier(schema)}.{statements.quote_identifier(table)}"

    table_size = _table_size(session, schema, table)
    galera, old_osu = _galera(session)
    if galera and old_osu not in ("TOI", "NBO"):
        raise OnlineAlterError(
            f"The Galera session uses wsrep_OSU_method {old_osu}, which applies "
            "the change on this node only. Use TOI or NBO."
        )
    old_lock_wait = _scalar(session, "SELECT @@SESSION.lock_wait_timeout")
    connection_id = _scalar(session, "SELECT CONNECTION_ID()") if monitor is not None else None

    allowed, skipped = plan(table_size, galera, options)
    result = Result(NEEDS_EXTERNAL, None, table_size, galera)

    def record(method, outcome, seconds=0, message=None):
        result.steps.append(
            {"method": method, "outcome": outcome, "seconds": seconds, "message": message}
        )
        if outcome != "applied":
            emit({"event": outcome, "method": method, "message": message})

    for method, why in skipped:
        record(method, "skipped", message=why)
    reason = skipped[-1][1] if skipped else None

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise OnlineAlterCancelled("The online ALTER TABLE was cancelled.")

    def pause(seconds):
        if cancel is not None:
            if cancel.wait(seconds):
                check_cancel()
        else:
            sleep(seconds)

    try:
        session.run_sql(f"SET SESSION lock_wait_timeout = {int(options.lock_wait)}")
        for method in allowed:
            check_cancel()
            if galera:
                try:
                    session.run_sql(
                        "SET SESSION wsrep_OSU_method = " + ("'NBO'" if method == NBO else "'TOI'")
                    )
                except Exception as error:
                    if method == NBO and getattr(error, "code", None) == ER_WRONG_VALUE_FOR_VAR:
                        reason = "NBO not available on this server (MariaDB Enterprise Server only)"
                        record(method, "skipped", message=reason)
                        continue
                    raise
            sql = step_sql(target, spec, method, options.nbo_lock)
            emit({"event": "step", "method": method, "sql": sql})
            started = time.monotonic()
            refusal = _attempt(
                session, sql, method, options, emit, pause, check_cancel,
                _Watch(monitor, connection_id, method, emit, cancel, poll_interval),
            )
            seconds = round(time.monotonic() - started, 3)
            if refusal is None:
                result.method = method
                if method == NBO:
                    result.reason = (f"NBO with LOCK={options.nbo_lock}: writes to the table "
                                     "were blocked during the ALTER")
                elif method in LOCKED:
                    result.reason = (f"{_CLAUSES[method]}: no step without a lock could run "
                                     "it; writes to the table were blocked during the ALTER")
                record(method, "applied", seconds)
                break
            record(method, "refused", seconds, refusal)
            if method == NBO:
                reason = "NBO refused this statement"
        else:
            result.reason = reason or (
                f"the server refused every allowed step up to {allowed[-1]}"
            )
    except BaseException:
        _restore(session, old_lock_wait, old_osu if galera else None, failed=True)
        raise
    _restore(session, old_lock_wait, old_osu if galera else None, failed=False)
    return result


def _attempt(session, sql, method, options, emit, pause, check_cancel, watch) -> Optional[str]:
    """Runs one step until it applies or is refused, retrying after lock
    wait timeouts, watched by watch (a _Watch). Returns None when applied,
    else the server's refusal."""
    tries = 0
    while True:
        try:
            with watch:
                session.run_sql(sql)
            return None
        except Exception as error:
            code = getattr(error, "code", None)
            try:
                check_cancel()
            except OnlineAlterCancelled as cancelled:
                raise cancelled from error
            if code in _REFUSED:
                return str(error)
            if code != ER_LOCK_WAIT_TIMEOUT:
                raise
        tries += 1
        if tries > options.max_retries:
            raise OnlineAlterError(
                "Could not get the table's metadata lock in "
                f"{options.max_retries + 1} attempts of {options.lock_wait} s; "
                "a long transaction holds the table. Find it, then try again."
            )
        wait = min(MAX_BACKOFF, 2 ** tries)
        emit({"event": "retry", "method": method, "attempt": tries, "wait": wait})
        pause(wait)
        check_cancel()


class _Watch:
    """While a step runs: reads its progress through the monitor session
    and, once cancel is set, stops it with KILL QUERY. Nothing without a
    monitor. A failing read ends the watch, never the step."""

    def __init__(self, monitor, connection_id, method, emit, cancel, poll_interval):
        self._monitor = monitor
        self._connection_id = connection_id
        self._method = method
        self._emit = emit
        self._cancel = cancel
        self._interval = poll_interval
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._running = True
        self._thread = None

    def __enter__(self):
        self._done.clear()
        self._running = True
        if self._monitor is not None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        # No KILL once the statement has returned
        with self._lock:
            self._running = False
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        return False

    def _run(self):
        last = None
        try:
            while not self._done.wait(self._interval):
                if self._cancel is not None and self._cancel.is_set():
                    with self._lock:
                        if self._running:
                            self._monitor.run_sql(f"KILL QUERY {int(self._connection_id)}")
                    return
                row = self._monitor.run_sql(
                    "SELECT STAGE, MAX_STAGE, PROGRESS, STATE "
                    "FROM information_schema.PROCESSLIST WHERE ID = ?",
                    [self._connection_id],
                ).fetch_one()
                if row is None:
                    continue
                now = (int(row[0] or 0), int(row[1] or 0), float(row[2] or 0), row[3])
                if now != last:
                    last = now
                    self._emit({"event": "progress", "method": self._method, "stage": now[0],
                                "max_stage": now[1], "progress": now[2], "state": now[3]})
        except Exception:
            return


def _scalar(session, sql: str, params: list = None):
    row = session.run_sql(sql, params or []).fetch_one()
    return None if row is None else row[0]


def _table_size(session, schema: str, table: str) -> int:
    size = _scalar(
        session,
        "SELECT COALESCE(DATA_LENGTH, 0) + COALESCE(INDEX_LENGTH, 0) "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
        "AND TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')",
        [schema, table],
    )
    if size is None:
        raise OnlineAlterError(
            f"The table `{schema}`.`{table}` does not exist, is not a base table, "
            "or the session may not see it."
        )
    return int(size)


def _galera(session) -> tuple:
    """(whether the session is a Galera node's, its wsrep_OSU_method). Read
    through information_schema: a server without wsrep has no such rows."""
    wsrep_on = _scalar(
        session,
        "SELECT VARIABLE_VALUE FROM information_schema.SESSION_VARIABLES "
        "WHERE VARIABLE_NAME = 'WSREP_ON'",
    )
    provider = _scalar(
        session,
        "SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_VARIABLES "
        "WHERE VARIABLE_NAME = 'WSREP_PROVIDER'",
    )
    if str(wsrep_on or "").upper() != "ON" or str(provider or "none").lower() in ("none", ""):
        return False, None
    osu = _scalar(
        session,
        "SELECT VARIABLE_VALUE FROM information_schema.SESSION_VARIABLES "
        "WHERE VARIABLE_NAME = 'WSREP_OSU_METHOD'",
    )
    return True, str(osu or "").upper()


def _restore(session, lock_wait, osu, failed: bool) -> None:
    """Puts the session's settings back; an error doing so is raised unless
    another one is on its way already."""
    try:
        if lock_wait is not None:
            session.run_sql(f"SET SESSION lock_wait_timeout = {int(lock_wait)}")
        if osu is not None:
            session.run_sql(f"SET SESSION wsrep_OSU_method = '{osu}'")
    except Exception:
        if not failed:
            raise


# -----------------------------------------------------------------------------
# As a background task


def result_dict(result: Result) -> dict:
    """A Result as tools return it."""
    return {
        "method": result.method,
        "applied": result.applied,
        "reason": result.reason,
        "table_size": result.table_size,
        "galera": result.galera,
        "steps": result.steps,
    }


def run_in_task(task, session, monitor, schema: str, table: str, spec: str,
                options: Options = None, poll_interval: float = 1.0) -> dict:
    """alter_table as the work of a task (``mysqlsh.task_manager.Task``):
    each step a stage, the server's progress the stage's progress, refusals,
    skips and lock retries messages; a cancel of the task stops the running
    statement (KILL QUERY through monitor).

    NEEDS_EXTERNAL completes the task, with a warning: nothing failed and
    nothing changed; the result says so (``applied`` False).

    Returns:
        result_dict of the Result.
    """
    cancel = threading.Event()
    task.set_canceller(cancel.set)
    stage, state, percent_done = None, None, 0.0

    def finish_stage():
        nonlocal stage
        if stage is not None:
            task.stage_finished(stage)
            stage = None

    def on_event(event):
        nonlocal stage, state, percent_done
        kind, method = event["event"], event.get("method")
        if kind == "step":
            finish_stage()
            stage, state, percent_done = method, None, 0.0
            task.stage_started(method)
        elif kind == "refused":
            task.message(f"{method} refused: {event['message']}")
        elif kind == "skipped":
            task.message(f"{method} skipped: {event['message']}")
        elif kind == "retry":
            task.message(
                f"{method}: the table's metadata lock was not granted (a transaction holds "
                f"the table); attempt {event['attempt'] + 1} in {event['wait']} s.", "warning")
        elif kind == "progress":
            # PROGRESS goes back to 0 with each of the server's stages
            if event["max_stage"] > 0:
                percent = 100.0 * (max(event["stage"] - 1, 0) + event["progress"] / 100.0) \
                    / event["max_stage"]
            else:
                percent = event["progress"]
            percent_done = max(percent_done, min(percent, 100.0))
            task.progress(round(percent_done, 1), 100, stage=method)
            if event["state"] and event["state"] != state:
                state = event["state"]
                task.message(f"{method}: {event['state']}")

    result = alter_table(session, schema, table, spec, options, monitor=monitor,
                         on_event=on_event, cancel=cancel, poll_interval=poll_interval)
    finish_stage()
    if result.applied:
        task.message(f"Applied with {result.method}"
                     + (f": {result.reason}." if result.reason else "."))
    else:
        task.message(f"No online step applied the change, so nothing was changed: "
                     f"{result.reason}. Run it with an external tool, or allow more "
                     "(maxCopyBytes, maxLockedBytes).", "warning")
    return result_dict(result)
