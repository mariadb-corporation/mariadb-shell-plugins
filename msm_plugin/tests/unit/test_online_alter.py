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

import os
import re
import threading
import time

import pytest

from msm_plugin.lib import online_alter, statements
from msm_plugin.lib.online_alter import (
    COPY, EXCLUSIVE_LOCK, GIB, INSTANT, MIB, NBO, NEEDS_EXTERNAL, NOCOPY, SHARED_LOCK,
    OnlineAlterCancelled, OnlineAlterError, Options,
)


# -----------------------------------------------------------------------------
# A session that answers by rules, for the decisions


class FakeError(Exception):
    def __init__(self, code: int, message: str = ""):
        super().__init__(f"MySQL Error ({code}): {message}")
        self.code = code


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetch_one(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Answers each statement by the first rule whose pattern it matches:
    a list of rows, or an exception to raise. A rule given a list of
    answers takes them in turn and keeps the last."""

    def __init__(self, size=1000, wsrep_on=None, provider=None, osu="TOI", alters=None):
        self.statements = []
        self.rules = [
            (r"FROM information_schema\.TABLES", [[[size]]] if size is not None else [[]]),
            (r"VARIABLE_NAME = 'WSREP_ON'", [[[wsrep_on]] if wsrep_on else []]),
            (r"VARIABLE_NAME = 'WSREP_PROVIDER'", [[[provider]] if provider else []]),
            (r"VARIABLE_NAME = 'WSREP_OSU_METHOD'", [[[osu]]]),
            (r"@@SESSION\.lock_wait_timeout", [[[50]]]),
            (r"CONNECTION_ID", [[[42]]]),
        ]
        for pattern, answers in (alters or {}).items():
            self.rules.append((pattern, list(answers)))
        self.rules.append((r"^SET ", [[]]))

    def run_sql(self, sql, params=None):
        self.statements.append(sql)
        for pattern, answers in self.rules:
            if re.search(pattern, sql):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, Exception):
                    raise answer
                return FakeResult(answer)
        raise AssertionError(f"No rule for: {sql}")

    def alters(self):
        return [sql for sql in self.statements if sql.startswith("ALTER TABLE")]


REFUSED = FakeError(1846, "ALGORITHM=INSTANT is not supported")
LOCK_TIMEOUT = FakeError(1205, "Lock wait timeout exceeded")
GALERA = {"wsrep_on": "ON", "provider": "/usr/lib/galera/libgalera_smm.so"}


def test_the_first_step_the_server_accepts_applies_the_change():
    session = FakeSession(alters={r"ALGORITHM=INSTANT": [REFUSED], r"ALGORITHM=NOCOPY": [[]]})
    events = []
    result = online_alter.alter_table(session, "shop", "orders", "ADD INDEX `ix` (`a`);",
                                      on_event=events.append)
    assert (result.method, result.reason, result.applied) == (NOCOPY, None, True)
    assert session.alters() == [
        "ALTER TABLE `shop`.`orders` ADD INDEX `ix` (`a`), ALGORITHM=INSTANT",
        "ALTER TABLE `shop`.`orders` ADD INDEX `ix` (`a`), ALGORITHM=NOCOPY, LOCK=NONE",
    ]
    assert [step["outcome"] for step in result.steps] == ["refused", "applied"]
    assert [event["event"] for event in events] == ["step", "refused", "step"]
    # The session's lock_wait_timeout is put back
    assert session.statements[-1] == "SET SESSION lock_wait_timeout = 50"


def test_no_step_accepted_needs_an_external_tool():
    session = FakeSession(alters={r"ALTER TABLE": [REFUSED]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT")
    assert result.method == NEEDS_EXTERNAL and not result.applied
    assert result.reason == "the server refused every allowed step up to EXCLUSIVE_LOCK"
    assert [sql.rsplit(", ", 1)[-1] for sql in session.alters()] == [
        "ALGORITHM=INSTANT", "LOCK=NONE", "LOCK=NONE", "LOCK=SHARED", "LOCK=EXCLUSIVE"]


def test_a_table_over_max_copy_bytes_gets_no_copy():
    session = FakeSession(size=20 * GIB, alters={r"ALTER TABLE": [REFUSED]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT")
    assert result.method == NEEDS_EXTERNAL
    assert result.reason.startswith("native COPY skipped: table size 21474836480 > max_copy_bytes")
    assert not any("ALGORITHM=COPY" in sql for sql in session.alters())
    assert result.steps[0] == {"method": COPY, "outcome": "skipped", "seconds": 0,
                               "message": result.reason}


@pytest.mark.parametrize("max_copy_bytes, copies", [(0, False), (None, True), (1000, True)])
def test_max_copy_bytes_zero_never_copies_and_none_always_may(max_copy_bytes, copies):
    allowed, _ = online_alter.plan(1000, False, Options(max_copy_bytes=max_copy_bytes))
    assert (COPY in allowed) == copies


def test_what_no_step_without_a_lock_runs_runs_with_one():
    session = FakeSession(alters={r"INSTANT|LOCK=NONE": [REFUSED], r"LOCK=SHARED": [[]]})
    result = online_alter.alter_table(session, "shop", "orders", "ADD `n` INT DEFAULT (NEXT VALUE FOR s)")
    assert result.method == SHARED_LOCK
    assert result.reason.startswith("LOCK=SHARED: no step without a lock could run it")
    assert session.alters()[-1] == ("ALTER TABLE `shop`.`orders` ADD `n` INT DEFAULT "
                                    "(NEXT VALUE FOR s), LOCK=SHARED")


@pytest.mark.parametrize("max_locked_bytes, locked", [(None, True), (0, False), (999, False),
                                                       (1000, True)])
def test_max_locked_bytes_caps_the_locked_steps(max_locked_bytes, locked):
    allowed, skipped = online_alter.plan(1000, False, Options(max_locked_bytes=max_locked_bytes))
    assert (SHARED_LOCK in allowed and EXCLUSIVE_LOCK in allowed) == locked
    if not locked:
        assert skipped[-1] == (EXCLUSIVE_LOCK, "blocking writes skipped: table size 1000 > "
                                               f"max_locked_bytes {max_locked_bytes}")


def test_a_lock_wait_timeout_is_retried_with_backoff():
    session = FakeSession(alters={r"ALGORITHM=INSTANT": [LOCK_TIMEOUT, LOCK_TIMEOUT, [[]]]})
    waits = []
    result = online_alter.alter_table(session, "shop", "orders", "ADD COLUMN `n` INT",
                                      Options(lock_wait=3), sleep=waits.append)
    assert result.method == INSTANT
    assert waits == [2, 4]
    assert session.statements.count("SET SESSION lock_wait_timeout = 3") == 1


def test_the_backoff_stops_at_max_backoff_and_gives_up_after_max_retries():
    session = FakeSession(alters={r"ALTER TABLE": [LOCK_TIMEOUT]})
    waits = []
    with pytest.raises(OnlineAlterError, match="metadata lock in 7 attempts of 5 s"):
        online_alter.alter_table(session, "shop", "orders", "ADD COLUMN `n` INT",
                                 Options(max_retries=6), sleep=waits.append)
    assert waits == [2, 4, 8, 16, 30, 30]
    assert session.statements[-1] == "SET SESSION lock_wait_timeout = 50"


def test_other_server_errors_are_passed_on_and_the_session_is_put_back():
    duplicate = FakeError(1062, "Duplicate entry")
    session = FakeSession(alters={r"ALTER TABLE": [duplicate]})
    with pytest.raises(FakeError) as raised:
        online_alter.alter_table(session, "shop", "orders", "ADD UNIQUE (`a`)")
    assert raised.value is duplicate
    assert session.statements[-1] == "SET SESSION lock_wait_timeout = 50"


def test_a_missing_table_is_an_error():
    with pytest.raises(OnlineAlterError, match="does not exist"):
        online_alter.alter_table(FakeSession(size=None), "shop", "orders", "ADD COLUMN `n` INT")


@pytest.mark.parametrize("spec", [
    "ADD COLUMN `n` INT, ALGORITHM=INPLACE",
    "ADD COLUMN `n` INT, LOCK = NONE",
    "algorithm=instant, ADD COLUMN `n` INT",
])
def test_a_spec_with_its_own_algorithm_or_lock_is_refused(spec):
    with pytest.raises(ValueError, match="Run it as written"):
        online_alter.check_spec(spec)


@pytest.mark.parametrize("spec", [
    "ADD COLUMN `algorithm` INT",
    "ADD COLUMN algorithm INT",
    "COMMENT = 'LOCK=NONE, ALGORITHM=COPY'",
    "ADD COLUMN `lock` INT /* , LOCK=NONE */",
])
def test_algorithm_or_lock_in_names_strings_and_comments_is_no_clause(spec):
    assert statements.locking_clause(spec) is None
    assert online_alter.check_spec(spec) == spec


def test_options_from_msm_project_json_keys():
    options = Options.from_dict({"maxCopyBytes": None, "nboLock": "shared", "lockWait": 2})
    assert (options.max_copy_bytes, options.nbo_lock, options.lock_wait) == (None, "SHARED", 2)
    assert options.galera_max_bytes == 256 * MIB and options.max_retries == 10
    assert options.max_locked_bytes is None
    assert Options.from_dict({"maxLockedBytes": 0}).max_locked_bytes == 0
    kept = Options.from_dict({"maxRetries": 0}, base=options)
    assert (kept.nbo_lock, kept.max_retries) == ("SHARED", 0)
    with pytest.raises(ValueError, match="Unknown online ALTER TABLE option `maxCopy`"):
        Options.from_dict({"maxCopy": 1})
    for bad in ({"nboLock": "NONE"}, {"lockWait": 0}, {"maxRetries": -1},
                {"maxCopyBytes": "1G"}, {"lockWait": True}):
        with pytest.raises(ValueError):
            Options.from_dict(bad)


# Galera


def test_galera_runs_instant_under_toi_and_big_tables_go_no_further():
    session = FakeSession(size=1 * GIB, **GALERA, alters={r"ALTER TABLE": [REFUSED]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT")
    assert result.galera and result.method == NEEDS_EXTERNAL
    assert len(session.alters()) == 1
    assert result.reason.startswith("Galera: non-instant DDL under TOI pauses the cluster")
    assert "SET SESSION wsrep_OSU_method = 'TOI'" in session.statements
    # wsrep_OSU_method is put back after the lock_wait_timeout
    assert session.statements[-1] == "SET SESSION wsrep_OSU_method = 'TOI'"


def test_galera_small_tables_may_use_nocopy_and_copy_under_toi():
    session = FakeSession(size=10 * MIB, **GALERA,
                          alters={r"INSTANT|NOCOPY": [REFUSED], r"ALGORITHM=COPY": [[]]})
    assert online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT").method == COPY


def test_galera_nbo_when_allowed():
    session = FakeSession(size=1 * GIB, **GALERA, osu="NBO",
                          alters={r"ALGORITHM=INSTANT": [REFUSED], r"LOCK=SHARED": [[]]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT",
                                      Options(nbo_lock="SHARED"))
    assert result.method == NBO
    assert result.reason.startswith("NBO with LOCK=SHARED: writes to the table were blocked")
    assert session.alters()[-1] == "ALTER TABLE `shop`.`orders` LOCK=SHARED, MODIFY `a` TEXT"
    assert session.statements[-1] == "SET SESSION wsrep_OSU_method = 'NBO'"


def test_galera_without_nbo_skips_it():
    session = FakeSession(size=1 * GIB, **GALERA, alters={
        r"ALGORITHM=INSTANT": [REFUSED],
        r"wsrep_OSU_method = 'NBO'": [FakeError(1231, "can't be set to the value of 'NBO'")]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT",
                                      Options(nbo_lock="EXCLUSIVE"))
    assert result.method == NEEDS_EXTERNAL
    assert result.reason == "NBO not available on this server (MariaDB Enterprise Server only)"


def test_galera_nbo_refusing_the_statement():
    session = FakeSession(size=1 * GIB, **GALERA, alters={
        r"ALGORITHM=INSTANT": [REFUSED], r"LOCK=SHARED": [FakeError(1235, "not supported")]})
    result = online_alter.alter_table(session, "shop", "orders", "MODIFY `a` TEXT",
                                      Options(nbo_lock="SHARED"))
    assert (result.method, result.reason) == (NEEDS_EXTERNAL, "NBO refused this statement")


def test_galera_on_rsu_is_refused():
    with pytest.raises(OnlineAlterError, match="wsrep_OSU_method RSU"):
        online_alter.alter_table(FakeSession(**GALERA, osu="RSU"), "shop", "orders",
                                 "ADD COLUMN `n` INT")


def test_wsrep_off_or_no_provider_is_no_galera():
    for kwargs in ({"wsrep_on": "OFF", "provider": "/lib.so"}, {"wsrep_on": "ON", "provider": "none"}):
        session = FakeSession(**kwargs, alters={r"ALTER TABLE": [[]]})
        result = online_alter.alter_table(session, "shop", "orders", "ADD COLUMN `n` INT")
        assert not result.galera
        assert not any("wsrep_OSU_method" in sql for sql in session.statements)


def test_a_cancel_before_a_step_stops_it():
    cancel = threading.Event()
    cancel.set()
    session = FakeSession(alters={r"ALTER TABLE": [[]]})
    with pytest.raises(OnlineAlterCancelled):
        online_alter.alter_table(session, "shop", "orders", "ADD COLUMN `n` INT", cancel=cancel)
    assert session.alters() == []


# -----------------------------------------------------------------------------
# On the sandbox

SCHEMA = "msm_online_alter_test"


@pytest.fixture
def server(sandbox_session):
    """A table `t` of 50000 rows in a scratch schema; yields
    (session, open_session) where open_session() gives another session."""
    import mysqlsh
    from msm_plugin.tests.unit import helpers

    data = helpers.get_connection_data()
    opened = []

    def open_session():
        session = mysqlsh.globals.shell.open_session(
            f"{data['user']}:{data['password']}@{data['host']}:{data['port']}")
        opened.append(session)
        return session

    session = open_session()
    session.run_sql(f"DROP SCHEMA IF EXISTS `{SCHEMA}`")
    session.run_sql(f"CREATE SCHEMA `{SCHEMA}`")
    session.run_sql(f"CREATE TABLE `{SCHEMA}`.`t` (id INT PRIMARY KEY, v INT)")
    session.run_sql(f"INSERT INTO `{SCHEMA}`.`t` SELECT seq, seq FROM `{SCHEMA}`.seq_1_to_50000")
    try:
        yield session, open_session
    finally:
        for other in opened:
            try:
                other.run_sql("ROLLBACK")
            except Exception:
                pass
        session.run_sql(f"DROP SCHEMA IF EXISTS `{SCHEMA}`")
        for other in opened:
            other.close()


def column_type(session, column) -> str:
    return session.run_sql(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = 't' AND COLUMN_NAME = ?",
        [SCHEMA, column]).fetch_one()[0]


def test_the_server_picks_instant_nocopy_or_copy(server):
    session, _ = server
    run = lambda spec, **kwargs: online_alter.alter_table(session, SCHEMA, "t", spec, **kwargs)

    assert run("ADD COLUMN `n` INT").method == INSTANT
    result = run("ADD INDEX `ix_v` (`v`)")
    assert result.method == NOCOPY
    assert "Reason: ADD INDEX" in result.steps[0]["message"]
    assert run("MODIFY `v` VARCHAR(20)").method == COPY
    assert column_type(session, "v") == "varchar(20)"


def test_needs_external_leaves_the_table_unchanged(server):
    session, _ = server
    result = online_alter.alter_table(session, SCHEMA, "t", "MODIFY `v` BIGINT",
                                      Options(max_copy_bytes=0, max_locked_bytes=0))
    assert result.method == NEEDS_EXTERNAL
    assert result.reason.startswith("blocking writes skipped")
    assert [(step["method"], step["outcome"]) for step in result.steps] == [
        (COPY, "skipped"), (SHARED_LOCK, "skipped"), (EXCLUSIVE_LOCK, "skipped"),
        (INSTANT, "refused"), (NOCOPY, "refused")]
    assert column_type(session, "v") == "int(11)"


def test_a_change_only_a_lock_allows_runs_with_a_shared_lock(server):
    session, _ = server
    session.run_sql(f"CREATE SEQUENCE `{SCHEMA}`.`s`")
    result = online_alter.alter_table(session, SCHEMA, "t",
                                      f"ADD COLUMN `n` INT DEFAULT (NEXT VALUE FOR `{SCHEMA}`.`s`)")
    assert result.method == SHARED_LOCK
    assert "Try LOCK=SHARED" in result.steps[-2]["message"]
    assert column_type(session, "n") == "int(11)"


def test_a_held_metadata_lock_times_out_and_gives_up(server):
    session, open_session = server
    holder = open_session()
    holder.run_sql("BEGIN")
    holder.run_sql(f"SELECT * FROM `{SCHEMA}`.`t` LIMIT 1")
    session.run_sql("SET SESSION lock_wait_timeout = 77")
    waits = []
    with pytest.raises(OnlineAlterError, match="metadata lock in 2 attempts"):
        online_alter.alter_table(session, SCHEMA, "t", "ADD COLUMN `n` INT",
                                 Options(lock_wait=1, max_retries=1), sleep=waits.append)
    assert waits == [2]
    assert session.run_sql("SELECT @@SESSION.lock_wait_timeout").fetch_one()[0] == 77


def test_a_cancel_stops_the_running_statement(server):
    session, open_session = server
    holder = open_session()
    holder.run_sql("BEGIN")
    holder.run_sql(f"SELECT * FROM `{SCHEMA}`.`t` LIMIT 1")
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(OnlineAlterCancelled):
        online_alter.alter_table(session, SCHEMA, "t", "ADD COLUMN `n` INT",
                                 Options(lock_wait=30), monitor=open_session(),
                                 cancel=cancel, poll_interval=0.05)
    # Stopped by KILL QUERY, not by the 30 s lock wait
    assert time.monotonic() - started < 10
    holder.run_sql("ROLLBACK")
    assert online_alter.alter_table(session, SCHEMA, "t", "ADD COLUMN `n` INT").method == INSTANT


def test_progress_is_read_while_a_copy_runs(server):
    session, open_session = server
    session.run_sql(f"INSERT INTO `{SCHEMA}`.`t` SELECT seq, seq FROM `{SCHEMA}`.seq_50001_to_400000")
    events = []
    result = online_alter.alter_table(session, SCHEMA, "t", "MODIFY `v` VARCHAR(20)",
                                      monitor=open_session(), on_event=events.append,
                                      poll_interval=0.01)
    assert result.method == COPY
    # INSTANT and NOCOPY are refused at once, so what is read comes from COPY
    progress = [event for event in events
                if event["event"] == "progress" and event["method"] == COPY]
    assert any(event["max_stage"] > 0 for event in progress)


# -----------------------------------------------------------------------------
# The onlineAlter setting


def settings(project=None, call=None):
    return online_alter.deployment_settings(
        {} if project is None else {"onlineAlter": project}, call)


def test_no_setting_is_enabled_with_the_defaults():
    assert settings().options_for("orders") == Options()
    assert settings(project=True, call=None).options_for("orders") == Options()


def test_false_turns_it_off():
    assert settings(project=False).options_for("orders") is None
    assert settings(call=False).options_for("orders") is None
    assert settings(project={"enabled": False}).options_for("orders") is None


def test_the_most_specific_value_wins_and_the_call_at_the_same_level():
    project = {"maxCopyBytes": 1 * GIB, "lockWait": 2,
               "tables": {"orders": {"maxCopyBytes": 0}, "audit": {"enabled": False}}}
    call = {"maxCopyBytes": 2 * GIB, "nboLock": "shared", "tables": {"items": {"lockWait": 9}}}
    resolved = settings(project, call)
    # The call's general value over the project's
    assert resolved.options_for("customer") == Options(max_copy_bytes=2 * GIB, lock_wait=2,
                                                       nbo_lock="SHARED")
    # The project's table value over the call's general one
    assert resolved.options_for("orders").max_copy_bytes == 0
    assert resolved.options_for("items").lock_wait == 9
    assert resolved.options_for("audit") is None
    # Table names match exactly
    assert resolved.options_for("Orders").max_copy_bytes == 2 * GIB
    # The call's table value over the project's
    call["tables"]["orders"] = {"maxCopyBytes": 5}
    assert settings(project, call).options_for("orders").max_copy_bytes == 5


def test_enabled_follows_the_same_order():
    resolved = settings({"enabled": False, "tables": {"orders": {"enabled": True}}})
    assert resolved.options_for("customer") is None
    assert resolved.options_for("orders") == Options()
    # The call turning it off generally does not reach a table the project turns on
    resolved = settings({"tables": {"orders": {"enabled": True}}}, call=False)
    assert resolved.options_for("customer") is None
    assert resolved.options_for("orders") == Options()


def test_none_is_a_value_of_its_own():
    resolved = settings({"maxCopyBytes": 0, "tables": {"big": {"maxCopyBytes": None}}})
    assert resolved.options_for("big").max_copy_bytes is None
    assert resolved.options_for("small").max_copy_bytes == 0


@pytest.mark.parametrize("project, message", [
    ("yes", "must be true, false or an object"),
    ({"maxCopy": 1}, r"Unknown key `maxCopy` \(msm.project.json, onlineAlter\)"),
    ({"enabled": "yes"}, "`enabled` must be true or false"),
    ({"lockWait": 0}, r"`lock_wait` must be .* \(msm.project.json, onlineAlter\)"),
    ({"tables": []}, "`tables` of the onlineAlter setting"),
    ({"tables": {"orders": 0}}, "for table `orders` must be an object"),
    ({"tables": {"orders": {"tables": {}}}}, r"Unknown key `tables` \(.*table `orders`\)"),
    ({"tables": {"orders": {"nboLock": "x"}}}, r"`nbo_lock` must be .*table `orders`"),
])
def test_a_bad_setting_says_where_it_is(project, message):
    with pytest.raises(ValueError, match=message):
        settings(project)


def new_project(tmp_path) -> str:
    from msm_plugin.management import create_new_project_folder

    return create_new_project_folder(schema_name="online_alter_setting",
                                     target_path=str(tmp_path), copyright_holder="Example Ltd.")


def test_a_new_project_has_the_setting(tmp_path):
    import json

    with open(os.path.join(new_project(tmp_path), "msm.project.json")) as f:
        assert json.load(f)["onlineAlter"] == {"enabled": True}


def test_a_bad_setting_stops_a_deployment_before_it_starts(tmp_path):
    from msm_plugin.lib import management
    from msm_plugin.tests.unit import helpers

    path = new_project(tmp_path)
    # No session and no release: the setting is checked before either is needed
    with pytest.raises(ValueError, match="Unknown key `maxCopy` \\(the deployment"):
        management.deploy_schema(session=None, schema_project_path=path,
                                 online_alter={"maxCopy": 1})
    helpers.set_project_setting(path, "onlineAlter", {"lockWait": "5"})
    with pytest.raises(ValueError, match="`lock_wait` must be .*msm.project.json"):
        management.deploy_schema(session=None, schema_project_path=path)


# -----------------------------------------------------------------------------
# As a background task


class FakeTask:
    """Records what the work reports, as mysqlsh.task_manager.Task takes it."""

    def __init__(self):
        self.events = []
        self.canceller = None

    def set_canceller(self, canceller):
        self.canceller = canceller

    def stage_started(self, stage):
        self.events.append(("started", stage))

    def stage_finished(self, stage, seconds=None):
        self.events.append(("finished", stage))

    def message(self, text, level="info"):
        self.events.append(("message", level, text))

    def progress(self, current, total, stage=None, **measures):
        self.events.append(("progress", stage, current, total))


def test_a_task_reports_each_step_as_a_stage():
    session = FakeSession(alters={r"ALGORITHM=INSTANT": [REFUSED], r"ALGORITHM=NOCOPY": [[]]})
    task = FakeTask()
    result = online_alter.run_in_task(task, session, None, "shop", "orders", "ADD INDEX (`a`)")
    assert result["method"] == NOCOPY and result["applied"] is True
    assert task.events == [
        ("started", INSTANT),
        ("message", "info", f"INSTANT refused: {REFUSED}"),
        ("finished", INSTANT),
        ("started", NOCOPY),
        ("finished", NOCOPY),
        ("message", "info", "Applied with NOCOPY."),
    ]
    assert task.canceller is not None


def test_a_task_that_needs_an_external_tool_completes_with_a_warning():
    task = FakeTask()
    result = online_alter.run_in_task(task, FakeSession(alters={r"ALTER TABLE": [REFUSED]}), None,
                                      "shop", "orders", "MODIFY `a` TEXT")
    assert result["method"] == NEEDS_EXTERNAL and result["applied"] is False
    level, text = task.events[-1][1:]
    assert level == "warning"
    assert text.startswith("No online step applied the change, so nothing was changed")


def test_a_cancel_of_the_task_cancels_the_alter():
    task = FakeTask()
    session = FakeSession(alters={r"ALTER TABLE": [[]]})
    # The task manager calls the canceller on task.cancel
    original = session.run_sql

    def cancel_on_alter(sql, params=None):
        if sql.startswith("ALTER TABLE"):
            task.canceller()
            raise FakeError(1317, "Query execution was interrupted")
        return original(sql, params)

    session.run_sql = cancel_on_alter
    with pytest.raises(OnlineAlterCancelled):
        online_alter.run_in_task(task, session, None, "shop", "orders", "ADD COLUMN `n` INT")


def wait_for_end(task, seconds=30):
    from mysqlsh.task_manager import FINISHED_STATES

    ends = time.monotonic() + seconds
    while task.status not in FINISHED_STATES:
        assert time.monotonic() < ends, "the task did not end"
        time.sleep(0.05)


def start_task(work):
    from mysqlsh import task_manager

    task = task_manager.create("msm", "online_alter_table", "test", None, 4, {})
    task.start(work)
    return task


def test_a_task_on_the_sandbox_reports_the_copys_progress(server):
    session, open_session = server
    session.run_sql(f"INSERT INTO `{SCHEMA}`.`t` SELECT seq, seq FROM `{SCHEMA}`.seq_50001_to_400000")
    monitor = open_session()
    task = start_task(lambda task: online_alter.run_in_task(
        task, session, monitor, SCHEMA, "t", "MODIFY `v` VARCHAR(20)", poll_interval=0.01))
    wait_for_end(task)
    assert task.status == "completed", task.error
    assert task.result["method"] == COPY
    assert [stage["name"] for stage in task.stages] == [INSTANT, NOCOPY, COPY]
    assert all(stage["status"] == "completed" for stage in task.stages)
    assert any(message["text"].startswith("COPY: ") for message in task.messages)


def test_a_cancelled_task_stops_a_blocked_alter(server):
    session, open_session = server
    holder = open_session()
    holder.run_sql("BEGIN")
    holder.run_sql(f"SELECT * FROM `{SCHEMA}`.`t` LIMIT 1")
    monitor = open_session()
    task = start_task(lambda task: online_alter.run_in_task(
        task, session, monitor, SCHEMA, "t", "ADD COLUMN `n` INT", Options(lock_wait=30),
        poll_interval=0.05))
    time.sleep(0.5)
    started = time.monotonic()
    task.cancel()
    wait_for_end(task)
    assert task.status == "cancelled"
    assert time.monotonic() - started < 10


def test_the_shell_function_alters_online(server):
    from msm_plugin.management import online_alter_table

    session, _ = server
    result = online_alter_table(SCHEMA, "t", "ADD COLUMN `n` INT", session=session,
                                options={"lockWait": 2})
    assert (result["method"], result["applied"]) == (INSTANT, True)
    with pytest.raises(ValueError, match="Unknown online ALTER TABLE option"):
        online_alter_table(SCHEMA, "t", "ADD COLUMN `m` INT", session=session,
                           options={"lockwait": 2})


# -----------------------------------------------------------------------------
# A project's setting, as the Table Editor reads and writes it


def test_the_defaults_without_a_project():
    from msm_plugin.management import get_online_alter_defaults

    assert get_online_alter_defaults() == {
        "maxCopyBytes": 10 * GIB, "galeraMaxBytes": 256 * MIB, "nboLock": None,
        "maxLockedBytes": None, "lockWait": 5, "maxRetries": 10}


def test_a_tables_settings_are_written_and_read_back(tmp_path):
    import json
    from msm_plugin.lib import management

    path = new_project(tmp_path)
    settings_file = os.path.join(path, "msm.project.json")
    read = management.get_online_alter_settings(path, "orders")
    assert read["defaults"] == Options().to_dict()
    assert read["setting"] == {"enabled": True, "tables": {}}
    assert (read["enabled"], read["options"]) == (True, Options().to_dict())

    assert management.set_online_alter_table(path, "orders", {"maxCopyBytes": 0, "lockWait": 2}) \
        == [settings_file]
    with open(settings_file) as f:
        written = json.load(f)
    assert written["onlineAlter"] == {"enabled": True,
                                      "tables": {"orders": {"maxCopyBytes": 0, "lockWait": 2}}}
    # Nothing else of the file changes
    assert written["schemaName"] == "online_alter_setting" and "copyrights" in written
    read = management.get_online_alter_settings(path, "orders")
    assert read["options"]["maxCopyBytes"] == 0 and read["options"]["lockWait"] == 2
    assert management.get_online_alter_settings(path, "items")["options"]["maxCopyBytes"] == 10 * GIB

    # The same again writes nothing; off; removed
    assert management.set_online_alter_table(path, "orders", {"maxCopyBytes": 0, "lockWait": 2}) == []
    management.set_online_alter_table(path, "orders", {"enabled": False})
    assert management.get_online_alter_settings(path, "orders")["enabled"] is False
    assert management.set_online_alter_table(path, "orders", None) == [settings_file]
    with open(settings_file) as f:
        assert json.load(f)["onlineAlter"] == {"enabled": True}
    assert management.set_online_alter_table(path, "orders", {}) == []


@pytest.mark.parametrize("table, settings, message", [
    ("", {}, "A table name is needed"),
    ("orders", [], "must be an object"),
    ("orders", {"maxCopy": 1}, "Unknown key `maxCopy`"),
    ("orders", {"tables": {}}, "Unknown key `tables`"),
    ("orders", {"lockWait": 0}, "`lock_wait` must be"),
])
def test_bad_table_settings_are_refused_and_nothing_is_written(tmp_path, table, settings, message):
    from msm_plugin.lib import management

    path = new_project(tmp_path)
    with open(os.path.join(path, "msm.project.json")) as f:
        before = f.read()
    with pytest.raises(ValueError, match=message):
        management.set_online_alter_table(path, table, settings)
    with open(os.path.join(path, "msm.project.json")) as f:
        assert f.read() == before


def test_a_project_without_the_setting_gets_one_with_a_tables_entry(tmp_path):
    import json
    from msm_plugin.lib import management

    path = new_project(tmp_path)
    with open(os.path.join(path, "msm.project.json")) as f:
        raw = json.load(f)
    raw.pop("onlineAlter")
    with open(os.path.join(path, "msm.project.json"), "w") as f:
        json.dump(raw, f)
    assert management.set_online_alter_table(path, "orders", None) == []
    management.set_online_alter_table(path, "orders", {"enabled": False})
    with open(os.path.join(path, "msm.project.json")) as f:
        assert json.load(f)["onlineAlter"] == {"enabled": True,
                                               "tables": {"orders": {"enabled": False}}}
