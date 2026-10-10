# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for the util function group and the background tasks it runs.

The registry of tasks is tested on its own first, without a server. The tools
then run end to end over stdio against the shared sandbox: a dump, a load into
another schema, an export and an import, each followed with util.get_task, a
cancelled dump, and the refusals.
"""
# cSpell:ignore mysqlsh

import asyncio
import os
import tempfile
import threading
import time

import pytest

import helpers
from mcp_plugin.lib import general, tasks, util_functions

CLIENT = general.ClientIdentity(None, None)
OTHER = general.ClientIdentity("10.0.0.2", "session-2")


@pytest.fixture(autouse=True)
def no_tasks():
    """Every test starts and ends with no tasks."""
    tasks.clear()
    yield
    tasks.clear()


# --- the registry, without a server --------------------------------------


def _finished(task, timeout=5):
    deadline = time.monotonic() + timeout
    while task.status not in tasks.FINISHED_STATES and time.monotonic() < deadline:
        task.wait_for_change(task.version, 0.1)
    return task.status


def test_a_task_records_its_events():
    task = tasks.create("dump_schemas", "Dump shop", CLIENT, "conn-1")

    def work(callback):
        callback({"type": "stageStarted", "stage": "Writing DDL"})
        callback({"type": "progress", "stage": "Writing DDL", "current": 1, "total": 4, "totalKnown": True})
        callback({"type": "message", "level": "status", "text": "Writing DDL..."})
        callback({"type": "stageFinished", "stage": "Writing DDL", "seconds": 0.5})
        callback({"type": "stageStarted", "stage": "Dumping data"})
        callback({
            "type": "progress", "stage": "Dumping data", "current": 50, "total": 200,
            "throughput": 1000, "etaSeconds": 3, "items": "rows", "totalIsApproximate": True,
        })
        return {"output_url": "/tmp/x"}

    task.start(work)
    assert _finished(task) == tasks.COMPLETED

    snapshot = task.snapshot()
    assert snapshot["kind"] == "dump_schemas"
    assert snapshot["connection_id"] == "conn-1"
    assert snapshot["result"] == {"output_url": "/tmp/x"}
    assert snapshot["error"] is None
    assert snapshot["stage"] == "Dumping data"
    assert snapshot["progress"] == {
        "current": 50, "total": 200, "throughput": 1000, "eta_seconds": 3,
        "items": "rows", "total_is_approximate": True, "percent": 25.0,
    }
    # the stage still running when the work returned ends with the task
    assert snapshot["stages"] == [
        {"name": "Writing DDL", "status": "completed", "seconds": 0.5},
        {"name": "Dumping data", "status": "completed", "seconds": None},
    ]
    assert [m["text"] for m in snapshot["messages"]] == ["Writing DDL..."]
    assert snapshot["next_since"] == 1
    assert snapshot["started_at"] and snapshot["finished_at"]


def test_messages_since_and_dropped(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_MESSAGES", 3)
    task = tasks.create("load_dump", "Load", CLIENT)
    # the deque was made with the default size: make one of the patched size
    task.messages = tasks.collections.deque(maxlen=3)
    for index in range(5):
        task.on_event({"type": "message", "level": "info", "text": f"line {index}"})

    snapshot = task.snapshot(1)
    assert [m["text"] for m in snapshot["messages"]] == ["line 2", "line 3", "line 4"]
    # line 1 was dropped before the caller could read it
    assert snapshot["messages_dropped"] == 1
    assert task.snapshot(4)["messages"][0]["seq"] == 5
    assert task.snapshot(5)["messages"] == []


def test_a_failed_task_reports_its_error():
    task = tasks.create("load_dump", "Load", CLIENT)

    def work(callback):
        raise RuntimeError("Dump directory not found\n")

    task.start(work)
    assert _finished(task) == tasks.FAILED
    snapshot = task.snapshot()
    assert snapshot["error"] == "Dump directory not found"
    assert snapshot["messages"][-1] == {**snapshot["messages"][-1], "level": "error", "text": "Dump directory not found"}


def test_cancel_reaches_the_utility_at_its_next_event():
    task = tasks.create("dump_instance", "Dump", CLIENT)
    started = threading.Event()
    answers = []

    def work(callback):
        answers.append(callback({"type": "stageStarted", "stage": "Dumping data"}))
        started.set()
        while True:
            answer = callback({"type": "progress", "stage": "Dumping data", "current": 1, "total": 2})
            if answer == "cancel":
                answers.append(answer)
                raise RuntimeError("Interrupted by user")
            time.sleep(0.01)

    task.start(work)
    assert started.wait(5)
    task.cancel()
    assert _finished(task) == tasks.CANCELLED
    assert answers == [None, "cancel"]
    snapshot = task.snapshot()
    assert snapshot["cancel_requested"] is True
    assert snapshot["stages"][0]["status"] == "cancelled"
    assert snapshot["messages"][-1]["text"] == "The task was cancelled."
    # cancelling a finished task changes nothing
    task.cancel()
    assert task.status == tasks.CANCELLED


def test_a_task_belongs_to_its_client():
    task = tasks.create("dump_schemas", "Dump", CLIENT)
    assert tasks.get(task.id, CLIENT) is task
    with pytest.raises(Exception, match="There is no task with the id"):
        tasks.get(task.id, OTHER)
    with pytest.raises(Exception, match="There is no task with the id"):
        tasks.get("nope", CLIENT)
    assert tasks.list_for(OTHER) == []
    assert tasks.list_for(CLIENT) == [task]


def test_running_tasks_are_limited_per_client(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_RUNNING_TASKS_PER_CLIENT", 2)
    tasks.create("a", "A", CLIENT)
    tasks.create("b", "B", CLIENT)
    with pytest.raises(Exception, match="Already 2 tasks are running"):
        tasks.create("c", "C", CLIENT)
    # another client has its own allowance
    tasks.create("d", "D", OTHER)


def test_finished_tasks_are_pruned(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_FINISHED_TASKS", 2)
    done = []
    for index in range(3):
        task = tasks.create("t", f"T{index}", CLIENT)
        task.start(lambda callback: None)
        _finished(task)
        done.append(task)
    # the oldest of the three finished ones is gone
    assert [task.title for task in tasks.list_for(CLIENT)] == ["T1", "T2"]

    monkeypatch.setattr(tasks, "FINISHED_TASK_LIFETIME", -1)
    assert tasks.list_for(CLIENT) == []


def test_a_waiting_caller_wakes_on_a_change():
    task = tasks.create("t", "T", CLIENT)
    version = task.version

    def later():
        time.sleep(0.2)
        task.on_event({"type": "message", "level": "info", "text": "hello"})

    threading.Thread(target=later).start()
    started = time.monotonic()
    task.wait_for_change(version, 5)
    assert time.monotonic() - started < 2
    assert task.snapshot()["messages"][0]["text"] == "hello"


def test_a_percentage_only_where_the_total_is_known():
    assert tasks._progress_of({"current": 5, "total": 10, "totalKnown": False})["percent"] is None
    assert tasks._progress_of({"current": 5, "total": 0})["percent"] is None
    assert tasks._progress_of({"current": 15, "total": 10})["percent"] == 100.0


def test_options_set_by_the_tools_are_refused():
    assert util_functions._options(None) == {}
    assert util_functions._options({"threads": 4}) == {"threads": 4}
    with pytest.raises(Exception, match="progressCallback, session"):
        util_functions._options({"session": 1, "progressCallback": 2})


def test_only_local_paths_are_checked():
    assert util_functions._local_paths(["/data/dump"], {}) == ["/data/dump"]
    assert util_functions._local_paths(["https://example.com/par/"], {}) == []
    assert util_functions._local_paths(["prefix/dump"], {"s3BucketName": "b"}) == []
    # a pattern of files is checked where the files are
    assert util_functions._local_paths(["/data/in/part-*.tsv"], {}) == ["/data/in"]


# --- the tools, end to end ----------------------------------------------------

TOOLS = {
    "util.dump_instance", "util.dump_schemas", "util.dump_tables",
    "util.export_table", "util.load_dump", "util.import_table",
    "util.copy_instance", "util.copy_schemas", "util.copy_tables",
    "util.get_task", "util.list_tasks", "util.cancel_task",
}


def test_util_tools_need_the_db_group():
    pytest.importorskip("mcp")

    assert not TOOLS & set(helpers.list_tool_names(["util"]))
    assert TOOLS <= set(helpers.list_tool_names(["util", "db"]))


def test_stdio_dump_load_export_import(allowed_temp_dir, sandbox):
    """Runs the utilities as tasks against the sandbox and follows them."""
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        async with helpers.mcp_session(function_groups=["util", "db"]) as call:
            def payload(result):
                assert result.is_error is False, helpers.tool_payload(result)
                return helpers.tool_payload(result)

            async def follow(task_id):
                """Follows a task to its end, collecting its messages."""
                since, messages, seen_progress = 0, [], False
                for _ in range(600):
                    state = payload(await call("util.get_task", {"task_id": task_id, "since": since, "wait_ms": 2000}))
                    messages += state["messages"]
                    since = state["next_since"]
                    seen_progress = seen_progress or state["progress"] is not None
                    if state["status"] in ("completed", "failed", "cancelled"):
                        return state, messages, seen_progress
                raise AssertionError("the task did not end")

            async def sql(statement):
                return payload(await call("db.execute_sql", {"connection_id": connection_id, "sql": statement}))

            connection_id = payload(await call("db.connect", {"uri": sandbox.uri}))
            try:
                await sql("SET GLOBAL local_infile = 1")
                await sql("DROP SCHEMA IF EXISTS mcp_util")
                await sql("DROP SCHEMA IF EXISTS mcp_util_loaded")
                await sql("CREATE SCHEMA mcp_util")
                await sql("CREATE TABLE mcp_util.t (id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(100))")
                await sql("INSERT INTO mcp_util.t (v) SELECT REPEAT('x', 100) FROM mcp_util.seq_1_to_20000")

                # a dump of the schema
                dump_dir = os.path.join(allowed_temp_dir, "dump")
                started = payload(await call("util.dump_schemas", {
                    "connection_id": connection_id, "schemas": ["mcp_util"],
                    "output_url": dump_dir, "options": {"threads": 2},
                }))
                assert started["status"] == "running"
                state, messages, seen_progress = await follow(started["task_id"])
                assert state["status"] == "completed", state["error"]
                assert state["result"] == {"output_url": dump_dir}
                assert os.path.isfile(os.path.join(dump_dir, "@.done.json"))
                assert "Dumping data" in [stage["name"] for stage in state["stages"]]
                assert all(stage["status"] == "completed" for stage in state["stages"])
                assert any(m["text"] == "Dumping data..." for m in messages)
                assert seen_progress

                # the connection's own session was never taken: it answers
                # at once, also while nothing runs
                assert payload(await call("db.list_schemas", {"connection_id": connection_id}))

                # loaded into another schema
                started = payload(await call("util.load_dump", {
                    "connection_id": connection_id, "url": dump_dir,
                    "options": {"schema": "mcp_util_loaded"},
                }))
                state, _, _ = await follow(started["task_id"])
                assert state["status"] == "completed", state["error"]
                rows = helpers.result_set(await sql("SELECT COUNT(*) AS n FROM mcp_util_loaded.t"))["rows"]
                assert rows[0][0] == 20000 if isinstance(rows[0], list) else rows[0]["n"] == 20000

                # exported and imported again
                tsv = os.path.join(allowed_temp_dir, "t.tsv")
                started = payload(await call("util.export_table", {
                    "connection_id": connection_id, "table": "mcp_util.t", "output_url": tsv,
                }))
                state, _, _ = await follow(started["task_id"])
                assert state["status"] == "completed", state["error"]
                assert os.path.isfile(tsv)
                await sql("TRUNCATE mcp_util_loaded.t")
                started = payload(await call("util.import_table", {
                    "connection_id": connection_id, "urls": tsv,
                    "options": {"schema": "mcp_util_loaded", "table": "t"},
                }))
                state, _, _ = await follow(started["task_id"])
                assert state["status"] == "completed", state["error"]
                rows = helpers.result_set(await sql("SELECT COUNT(*) FROM mcp_util_loaded.t"))["rows"]
                assert 20000 in (rows[0] if isinstance(rows[0], list) else list(rows[0].values()))

                # a slow dump, cancelled
                started = payload(await call("util.dump_schemas", {
                    "connection_id": connection_id, "schemas": ["mcp_util"],
                    "output_url": os.path.join(allowed_temp_dir, "cancelled"),
                    "options": {"threads": 1, "maxRate": "50k"},
                }))
                await asyncio.sleep(1)
                cancelled = payload(await call("util.cancel_task", {"task_id": started["task_id"]}))
                assert cancelled["cancel_requested"] is True
                state, _, _ = await follow(started["task_id"])
                assert state["status"] == "cancelled"
                assert "Interrupted by user" in state["error"]

                # a copy onto the same server fails as a task, with the reason
                started = payload(await call("util.copy_schemas", {
                    "connection_id": connection_id, "schemas": ["mcp_util"],
                    "target_connection_id": connection_id,
                }))
                state, _, _ = await follow(started["task_id"])
                assert state["status"] == "failed"
                assert "same as the source" in state["error"]

                # all of them are listed, without their messages
                listed = payload(await call("util.list_tasks", {}))
                listed = listed if isinstance(listed, list) else [listed]
                assert [task["kind"] for task in listed] == [
                    "dump_schemas", "load_dump", "export_table", "import_table",
                    "dump_schemas", "copy_schemas",
                ]
                assert all("messages" not in task for task in listed)

                # refusals
                result = await call("util.dump_schemas", {
                    "connection_id": connection_id, "schemas": ["mcp_util"],
                    "output_url": dump_dir, "options": {"session": 1},
                })
                assert result.is_error and "set by the tool" in str(helpers.tool_payload(result))
                result = await call("util.dump_schemas", {
                    "connection_id": "no-such-connection", "schemas": ["mcp_util"],
                    "output_url": os.path.join(allowed_temp_dir, "x"),
                })
                assert result.is_error
                result = await call("util.get_task", {"task_id": "no-such-task"})
                assert result.is_error and "no task with the id" in str(helpers.tool_payload(result))
            finally:
                await sql("DROP SCHEMA IF EXISTS mcp_util")
                await sql("DROP SCHEMA IF EXISTS mcp_util_loaded")
                await call("db.close", {"connection_id": connection_id})

    asyncio.run(_run())
