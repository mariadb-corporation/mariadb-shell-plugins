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

The registry of tasks is tested on its own first, without a server, then the
command line a utility is run with and the parsing of what the shell prints,
on lines recorded from a real shell. A fake shell then crashes, to show that a
load is resumed and nothing else is. The tools finally run end to end over
stdio against the shared sandbox: a dump, a load into another schema, an
export and an import, each followed with util.get_task, a cancelled dump, and
the refusals.
"""
# cSpell:ignore mysqlsh thds

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pytest

import helpers
from mcp_plugin.lib import general, tasks, util_functions, util_process

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

    def work(task):
        task.on_event({"type": "stageStarted", "stage": "Writing DDL"})
        task.on_event({"type": "progress", "stage": "Writing DDL", "current": 1, "total": 4, "totalKnown": True})
        task.on_event({"type": "message", "level": "status", "text": "Writing DDL..."})
        task.on_event({"type": "stageFinished", "stage": "Writing DDL", "seconds": 0.5})
        task.on_event({"type": "stageStarted", "stage": "Dumping data"})
        task.on_event({
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

    def work(task):
        raise RuntimeError("Dump directory not found\n")

    task.start(work)
    assert _finished(task) == tasks.FAILED
    snapshot = task.snapshot()
    assert snapshot["error"] == "Dump directory not found"
    assert snapshot["messages"][-1] == {**snapshot["messages"][-1], "level": "error", "text": "Dump directory not found"}


def test_cancel_reaches_the_running_work():
    task = tasks.create("dump_instance", "Dump", CLIENT)
    started = threading.Event()
    stop = threading.Event()

    def work(task):
        task.set_canceller(stop.set)
        task.on_event({"type": "stageStarted", "stage": "Dumping data"})
        started.set()
        assert stop.wait(5)
        raise RuntimeError("Interrupted by user")

    task.start(work)
    assert started.wait(5)
    task.cancel()
    assert _finished(task) == tasks.CANCELLED
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
        task.start(lambda task: None)
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


def test_a_cancel_before_the_work_could_take_it_reaches_it_later():
    task = tasks.create("load_dump", "Load", CLIENT)
    task.cancel()
    stopped = []
    task.set_canceller(lambda: stopped.append(True))
    assert stopped == [True]


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
    # a bucket option without a name makes no bucket: the URL is still local
    for name in ("s3BucketName", "osBucketName", "azureContainerName"):
        for value in (None, "", 0, False):
            assert util_functions._local_paths(["/etc/x"], {name: value}) == ["/etc/x"]
    # a pattern of files is checked where the files are
    assert util_functions._local_paths(["/data/in/part-*.tsv"], {}) == ["/data/in"]


def test_a_file_url_is_checked_as_the_path_it_names():
    # as the shell reads it: the scheme matched in any case, then stripped
    assert util_functions._local_paths(["file:///etc/passwd"], {}) == ["/etc/passwd"]
    assert util_functions._local_paths(["FILE:///etc/passwd"], {}) == ["/etc/passwd"]
    assert util_functions._local_paths(["File://~/dump"], {}) == ["~/dump"]
    assert util_functions._local_paths(["file:///data/in/*.tsv"], {}) == ["/data/in"]
    # every other scheme is remote
    assert util_functions._local_paths(["HTTPS://example.com/par/"], {}) == []


def test_the_file_options_are_checked_wherever_the_dump_is():
    options = {
        "progressFile": "file:///tmp/progress.json",
        "s3ConfigFile": "~/.aws/config",
        "s3CredentialsFile": "/home/u/.aws/credentials",
        "threads": 4,
    }
    assert util_functions._local_paths(["/data/dump"], options) == [
        "/data/dump",
        "/tmp/progress.json",
        "~/.aws/config",
        "/home/u/.aws/credentials",
    ]
    # in a bucket the URL is a prefix there, but the files are still local
    assert util_functions._local_paths(
        ["prefix/dump"], {"s3BucketName": "b", "progressFile": "/tmp/p.json"}
    ) == ["/tmp/p.json"]
    # a progress file of a PAR load can be remote too
    assert util_functions._local_paths(
        ["https://example.com/par/"], {"progressFile": "https://example.com/p"}
    ) == []
    # an empty one is not a path
    assert util_functions._local_paths([], {"progressFile": ""}) == []


# --- the command line --------------------------------------------------------


def test_the_command_line_of_each_utility():
    assert util_process.build_command("dump_instance", ["/d"], {"threads": 4}) == [
        "util", "dump-instance", "/d", "--threads=4", "--showProgress=true",
    ]
    # what follows a list is named, and the list is JSON
    assert util_process.build_command("dump_schemas", [["a", "b,c"], "/d"], {}) == [
        "util", "dump-schemas", '["a", "b,c"]', "--outputUrl:str=/d", "--showProgress=true",
    ]
    assert util_process.build_command("dump_tables", ["s", [], "/d"], {"all": True}) == [
        "util", "dump-tables", "s", "[]", "--outputUrl:str=/d", "--all=true",
        "--showProgress=true",
    ]
    assert util_process.build_command("import_table", ["/in/a.tsv"], {"schema": "s"})[2:4] == [
        '["/in/a.tsv"]', "--schema:str=s",
    ]
    # every value typed, so that the shell does not guess: "100" stays a string
    assert util_process.build_command(
        "export_table", ["s.t", "/o.tsv"],
        {"maxRate": "100", "where": {"s.t": "id > 1"}, "compatibility": ["x"],
         "bytesPerChunk": None, "fieldsTerminatedBy": "\t"},
    )[4:-1] == [
        "--maxRate:str=100", '--where:json={"s.t": "id > 1"}',
        '--compatibility:json=["x"]', "--fieldsTerminatedBy:str=\t",
    ]


def test_a_copy_names_its_target_by_its_attributes():
    target = {"scheme": "mariadb", "user": "tgt", "host": "h", "port": 3307,
              "schema": "db", "password": "secret"}
    command = util_process.build_command(
        "copy_schemas", [["a"]], {"schema": "renamed"}, target
    )
    assert command == [
        "util", "copy-schemas", '["a"]', "--scheme:str=mariadb", "--user:str=tgt",
        "--host:str=h", "--port=3307", "--connection-data-schema:str=db",
        "--options-schema:str=renamed", "--showProgress=true",
    ]
    # the password is no argument
    assert not any("secret" in argument for argument in command)


_TARGET = {"user": "tgt", "host": "h", "port": 3307, "password": "secret"}


@pytest.mark.parametrize("name", [
    "host", "port", "user", "password", "socket", "uri", "ssh", "ssh-host",
    "ssh-password", "auth-method", "oci-config-file", "connect-timeout",
    # every spelling the shell's command line reads as the same name
    "Host", "sslMode", "ssl_mode", "ssl-mode", "authMethod", "sshPassword",
])
@pytest.mark.parametrize("function,args", [
    ("copy_instance", []),
    ("copy_schemas", [["s"]]),
    ("copy_tables", ["s", ["t"]]),
])
def test_a_copy_takes_no_connection_attribute_in_its_options(function, args, name):
    # it would replace the target's: the copy, and the target's stored
    # password, would go to a server the client chose
    with pytest.raises(Exception, match="names a connection attribute"):
        util_process.build_command(function, args, {name: "203.0.113.10"}, _TARGET)


@pytest.mark.parametrize("name", [
    "outputUrl",
    # every spelling the shell's command line reads as the same name
    "output_url", "output-url", "OutputUrl",
])
def test_an_option_the_tool_forbids_is_refused_in_any_spelling(name):
    forbidden = {"outputUrl": "it is the tool's output_url argument."}
    with pytest.raises(Exception, match="cannot be given: it is the tool's output_url"):
        util_process.build_command(
            "dump_schemas", [["s"], "/allowed/dump"], {name: "/x"}, forbidden=forbidden
        )


def test_a_copy_keeps_its_own_options():
    command = util_process.build_command(
        "copy_schemas", [["s"]],
        {"threads": 4, "users": False, "ddlOnly": True, "schema": "renamed",
         "excludeTables": ["s.t"]},
        _TARGET,
    )
    assert command[-6:-1] == [
        "--threads=4", "--users=false", "--ddlOnly=true",
        "--options-schema:str=renamed", '--excludeTables:json=["s.t"]',
    ]


@pytest.mark.parametrize("name", ["host=evil", "host:json", "threads ", "-host", "", "1x"])
@pytest.mark.parametrize("function,args,target", [
    ("copy_instance", [], _TARGET),
    ("dump_schemas", [["s"], "/d"], None),
])
def test_an_option_name_that_would_write_another_option_is_refused(function, args, target, name):
    # '--host=evil:str=x' would set host, whatever the name was meant to be
    with pytest.raises(Exception, match="is not the name of an option"):
        util_process.build_command(function, args, {name: "x"}, target)


@pytest.mark.parametrize("value", ["2024", "true", "null", "[1]", "-x", ""])
def test_an_unnamed_value_the_shell_would_misread_is_refused(value):
    with pytest.raises(Exception, match="cannot be passed|non-empty"):
        util_process.build_command("load_dump", [value], {})


def test_a_string_the_shell_would_unescape_is_refused():
    with pytest.raises(Exception, match="cannot pass on unchanged"):
        util_process.build_command("export_table", ["s.t", "/o"], {"where": "v = 'a\\'b'"})
    # a number written differently is a string to the shell, and fine
    assert util_process.build_command("load_dump", ["1.50"], {})[2] == "1.50"


# --- what the shell prints ------------------------------------------------------


def _events(kind, lines):
    parser = util_process.OutputParser(kind)
    events = []
    for line in lines:
        events += parser.feed(json.dumps(line) + "\n")
    return parser, events


def test_a_dump_is_parsed_into_stages_progress_and_messages():
    parser, events = _events("dump_schemas", [
        {"prompt": "Please provide the password for 'root@h': ", "type": "password"},
        {"info": "Initializing"},
        {"info": "Acquiring global read lock\n"},
        {"info": "Initializing - done"},
        {"info": "Writing DDL - 0 / 3 ", "numericProgressUpdate": {
            "current": 0, "description": "Writing DDL", "total": 3, "totalKnown": True}},
        {"note": "Progress information uses estimated values and may not be accurate.\n"},
        {"info": "Writing DDL - done", "numericProgressUpdate": {
            "current": 3, "description": "Writing DDL", "total": 3, "totalKnown": True}},
        {"info": "2 thds dumping - 13% (80.00K rows / ~595.79K rows)",
         "dumpProgress": {}, "throughputProgressConfig": {
             "itemsFull": "rows", "totalIsApproximate": True}},
        {"info": "2 thds dumping - 13% (80.00K rows / ~595.79K rows)",
         "dumpProgress": {}, "throughputProgressUpdate": {
             "current": 80000, "etaSeconds": 7, "total": 595788,
             "throughput": {"items": 126000, "seconds": 2}}},
        {"status": "Dump duration: 00:00:09s\n"},
        {util_process.EXIT_MARKER: {"returncode": 0}},
    ])
    kinds = [(e["type"], e.get("stage") or e.get("text")) for e in events]
    assert kinds == [
        ("stageStarted", "Initializing"),
        ("message", "Acquiring global read lock"),
        ("stageFinished", "Initializing"),
        ("stageStarted", "Writing DDL"),
        ("progress", "Writing DDL"),
        ("message", "Progress information uses estimated values and may not be accurate."),
        ("progress", "Writing DDL"),
        ("stageFinished", "Writing DDL"),
        ("stageStarted", "Dumping data"),
        ("progress", "Dumping data"),
        ("message", "Dump duration: 00:00:09s"),
    ]
    assert events[5]["level"] == "note"
    assert events[-2] == {
        "type": "progress", "stage": "Dumping data", "current": 80000, "total": 595788,
        "throughput": 63000.0, "etaSeconds": 7, "items": "rows",
        "totalIsApproximate": True, "totalKnown": True,
    }
    assert parser.exit_code == 0
    # the data stage ends with the run
    assert [e["stage"] for e in parser.finish_all()] == ["Dumping data"]


def test_a_copy_has_the_stages_of_both_sides():
    _, events = _events("copy_schemas", [
        {"info": "SRC: Writing DDL - 0 / 3 ", "numericProgressUpdate": {
            "current": 0, "description": "Writing DDL", "total": 3, "totalKnown": True}},
        {"status": "TGT: Opening dump...\n"},
        {"status": "TGT: Opening dump - done\n"},
        {"info": "SRC: 100% (600.00K rows / ~595.79K rows)", "dumpProgress": {},
         "throughputProgressUpdate": {"current": 1, "total": 2}},
        {"info": "TGT: ?% (58.58 MB / ?)", "loadProgress": {},
         "throughputProgressUpdate": {"current": 5, "total": 0}},
    ])
    started = [e["stage"] for e in events if e["type"] == "stageStarted"]
    assert started == ["SRC: Writing DDL", "TGT: Opening dump", "SRC: Dumping data", "TGT: Loading data"]
    assert [e["stage"] for e in events if e["type"] == "stageFinished"] == ["TGT: Opening dump"]
    # an unknown total is no percentage
    assert tasks._progress_of(events[-1])["percent"] is None


def test_the_last_error_is_the_task_error_and_earlier_ones_are_messages():
    parser, events = _events("load_dump", [
        {"error": "[Worker001] Error loading t1\n"},
        {"error": "Exception: x\n", "exceptionInfo": {}},
        {"error": "Util.load_dump: Error loading dump\n"},
        {util_process.EXIT_MARKER: {"returncode": 1}},
    ])
    assert [e["text"] for e in events] == ["[Worker001] Error loading t1"]
    assert parser.error == "Util.load_dump: Error loading dump"
    assert parser.exit_code == 1


def test_other_output_is_kept_as_a_message():
    parser = util_process.OutputParser("load_dump")
    assert parser.feed("not json\n") == [{"type": "message", "level": "info", "text": "not json"}]
    assert parser.feed("\n") == []


# --- the shell binary ------------------------------------------------------------


def test_the_utility_runs_in_the_shell_the_server_runs_in():
    assert util_process.shell_executable() == util_process.mysqlsh.executable
    assert os.path.isfile(util_process.shell_executable())


def test_outside_a_shell_the_one_on_path(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("the fake shell is a POSIX script")

    executable = tmp_path / "mariadb-shell"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.delattr(util_process.mysqlsh, "executable", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert util_process.shell_executable() == str(executable)

    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    with pytest.raises(Exception, match="Put mariadb-shell on the PATH"):
        util_process.shell_executable()


# --- the process, with a fake shell ---------------------------------------------

_FAKE_SHELL = """
import json, os, signal, sys
state = os.environ["FAKE_SHELL_STATE"]
runs = json.load(open(state)) if os.path.exists(state) else []
runs.append({"argv": sys.argv[1:], "stdin": sys.stdin.read()})
def save():
    # whole or not at all: the test reads the file while this runs
    with open(state + ".tmp", "w") as file:
        json.dump(runs, file)
    os.replace(state + ".tmp", state)
save()
def out(line):
    print(json.dumps(line), flush=True)
mode = os.environ["FAKE_SHELL_MODE"]
if mode == "fail-at-startup":
    # what the shell does with a malformed --uri: no JSON yet, only stderr
    print("Invalid URI: Expected token at position 2 but no tokens left.", file=sys.stderr, flush=True)
    sys.exit(1)
if mode == "hang":
    # deaf to ^C: only a kill ends it
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    runs[-1]["pid"] = os.getpid()
    save()
    out({"info": "Dumping data", "dumpProgress": {}, "throughputProgressUpdate": {"current": 1, "total": 2}})
    import time
    time.sleep(60)
if mode == "crash-early" and len(runs) == 1:
    os.kill(os.getpid(), signal.SIGKILL)
out({"info": "Scanning metadata - done", "numericProgressUpdate": {"description": "Scanning metadata", "current": 1, "total": 1}})
out({"info": "Loading data", "loadProgress": {}, "throughputProgressUpdate": {"current": len(runs), "total": 3}})
if mode == "crash" or (mode == "crash-once" and len(runs) == 1):
    print("the shell's last words", file=sys.stderr, flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
if mode == "fail":
    out({"error": "Util.load_dump: Error loading dump\\n"})
    sys.exit(1)
out({"info": "1 chunks were loaded\\n"})
"""


@pytest.fixture
def fake_shell(tmp_path, monkeypatch):
    """A shell that records its runs, and crashes or fails when told to."""
    if os.name == "nt":
        pytest.skip("the fake shell is a POSIX script")

    script = tmp_path / "fake_shell.py"
    script.write_text(_FAKE_SHELL)
    executable = tmp_path / "mariadb-shell"
    executable.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I '{script}' \"$@\"\n")
    executable.chmod(0o755)
    state = tmp_path / "runs.json"

    monkeypatch.setattr(util_process, "shell_executable", lambda: str(executable))
    monkeypatch.setenv("FAKE_SHELL_STATE", str(state))

    def runs():
        return json.loads(state.read_text()) if state.exists() else []

    return runs


def _run(function, args, options, monkeypatch, mode, target=None):
    monkeypatch.setenv("FAKE_SHELL_MODE", mode)
    task = tasks.create(function, function, CLIENT)
    task.start(lambda task: util_process.run(
        task, function, args, options, ("mariadb://u@h:3306", "pw"), target
    ))
    _finished(task, timeout=30)
    return task.snapshot()


def test_the_utility_runs_in_a_process_with_the_passwords_on_stdin(fake_shell, monkeypatch):
    target = {"user": "t", "host": "h2", "port": 3307, "password": "pw2"}
    snapshot = _run("copy_schemas", [["s"]], {}, monkeypatch, "ok", target)

    assert snapshot["status"] == "completed", snapshot["error"]
    (run,) = fake_shell()
    assert run["stdin"] == "pw\npw2\n"
    assert run["argv"][:4] == ["--json=raw", "--passwords-from-stdin", "--uri=mariadb://u@h:3306", "--"]
    assert "pw" not in " ".join(run["argv"]).replace("--passwords", "")
    assert [(stage["name"], stage["status"]) for stage in snapshot["stages"]] == [
        ("Scanning metadata", "completed"), ("Loading data", "completed"),
    ]
    assert [m["text"] for m in snapshot["messages"]] == ["1 chunks were loaded"]


def test_a_crashed_load_is_resumed(fake_shell, monkeypatch):
    snapshot = _run("load_dump", ["/d"], {"resetProgress": True}, monkeypatch, "crash-once")

    assert snapshot["status"] == "completed", snapshot["error"]
    first, second = fake_shell()
    # the second run continues where the first stopped
    assert "--resetProgress=true" in first["argv"]
    assert "--resetProgress=true" not in second["argv"]
    texts = [m["text"] for m in snapshot["messages"]]
    assert "the shell's last words" in texts
    assert any("crashed: it was ended by SIGKILL. Resuming the load, attempt 2 of 4." in t for t in texts)
    assert [stage["status"] for stage in snapshot["stages"]] == [
        "completed", "failed", "completed", "completed",
    ]


def test_a_load_that_crashed_before_reading_its_progress_file_keeps_its_reset(fake_shell, monkeypatch):
    snapshot = _run("load_dump", ["/d"], {"resetProgress": True}, monkeypatch, "crash-early")

    assert snapshot["status"] == "completed", snapshot["error"]
    # the progress file might still be another load's: it is still reset
    assert all("--resetProgress=true" in run["argv"] for run in fake_shell())


def test_a_load_that_keeps_crashing_fails_in_the_end(fake_shell, monkeypatch):
    snapshot = _run("load_dump", ["/d"], {}, monkeypatch, "crash")

    assert snapshot["status"] == "failed"
    assert len(fake_shell()) == util_process.MAX_RESTARTS + 1
    assert "crashed: it was ended by SIGKILL. It was started 4 times." in snapshot["error"]


@pytest.mark.parametrize("function,args,options", [
    ("dump_schemas", [["s"], "/d"], {}),
    ("load_dump", ["https://example.com/par/"], {}),
    ("load_dump", ["/d"], {"progressFile": ""}),
])
def test_nothing_else_is_resumed(fake_shell, monkeypatch, function, args, options):
    snapshot = _run(function, args, options, monkeypatch, "crash")

    assert snapshot["status"] == "failed"
    assert len(fake_shell()) == 1
    assert "crashed" in snapshot["error"]


def test_a_shell_that_does_not_stop_on_cancel_is_killed(fake_shell, monkeypatch):
    monkeypatch.setattr(util_process, "CANCEL_GRACE_SECONDS", 0.5)
    monkeypatch.setenv("FAKE_SHELL_MODE", "hang")
    task = tasks.create("dump_schemas", "Dump", CLIENT)
    task.start(lambda task: util_process.run(
        task, "dump_schemas", [["s"], "/d"], {}, ("mariadb://u@h", "pw")
    ))
    deadline = time.monotonic() + 10
    while not (fake_shell() and "pid" in fake_shell()[0]) and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = fake_shell()[0]["pid"]

    task.cancel()
    assert _finished(task, timeout=10) == tasks.CANCELLED
    # the shell itself is gone, not only the watchdog above it
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        raise AssertionError("the shell is still running")


def test_a_failure_is_no_crash(fake_shell, monkeypatch):
    snapshot = _run("load_dump", ["/d"], {}, monkeypatch, "fail")

    assert snapshot["status"] == "failed"
    assert len(fake_shell()) == 1
    assert snapshot["error"] == "Util.load_dump: Error loading dump"


def test_a_failure_reported_only_on_stderr_is_no_crash(fake_shell, monkeypatch):
    # started again, it would fail the same way
    snapshot = _run("load_dump", ["/d"], {}, monkeypatch, "fail-at-startup")

    assert snapshot["status"] == "failed"
    assert len(fake_shell()) == 1
    assert snapshot["error"] == (
        "The MariaDB Shell process running the utility failed with exit code 1: "
        "Invalid URI: Expected token at position 2 but no tokens left."
    )
    assert not any("crashed" in m["text"] for m in snapshot["messages"])


@pytest.mark.parametrize("returncode,crashed", [
    (None, True), (-9, True), (1, False), (10, False), (0xC0000005, False),
])
def test_what_is_a_crash(monkeypatch, returncode, crashed):
    monkeypatch.setattr(util_process.os, "name", "posix")
    assert util_process._crashed(returncode) is crashed


@pytest.mark.parametrize("returncode,crashed", [
    (1, False), (10, False), (0xC0000005, True), (0xC0000409, True),
])
def test_what_is_a_crash_on_windows(monkeypatch, returncode, crashed):
    # a crash there is an exception code, which is positive
    monkeypatch.setattr(util_process.os, "name", "nt")
    assert util_process._crashed(returncode) is crashed


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


def _kill_shell_running(argument: str, timeout: float = 10) -> bool:
    """Kills the shell process whose command line has an argument, at once.

    The shell, not the watchdog above it: a crash of the utility.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True
        ).stdout
        for line in listing.splitlines():
            pid, _, command = line.strip().partition(" ")
            if argument in command and "--json=raw" in command and "util_watchdog" not in command:
                os.kill(int(pid), signal.SIGKILL)
                return True
        time.sleep(0.02)

    return False


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
                assert any(m["text"].startswith("Dump duration") for m in messages)
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

                # a load whose shell process is killed is resumed
                if os.name != "nt":
                    await sql("DROP SCHEMA IF EXISTS mcp_util_resumed")
                    started = payload(await call("util.load_dump", {
                        "connection_id": connection_id, "url": dump_dir,
                        "options": {"schema": "mcp_util_resumed", "resetProgress": True},
                    }))
                    assert await asyncio.to_thread(_kill_shell_running, "mcp_util_resumed")
                    state, messages, _ = await follow(started["task_id"])
                    assert state["status"] == "completed", state["error"]
                    assert any("Resuming the load, attempt 2 of 4" in m["text"] for m in messages)
                    rows = helpers.result_set(await sql("SELECT COUNT(*) FROM mcp_util_resumed.t"))["rows"]
                    assert 20000 in (rows[0] if isinstance(rows[0], list) else list(rows[0].values()))
                    await sql("DROP SCHEMA IF EXISTS mcp_util_resumed")

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
                assert [task["kind"] for task in listed] == (
                    ["dump_schemas", "load_dump"]
                    + (["load_dump"] if os.name != "nt" else [])
                    + ["export_table", "import_table", "dump_schemas", "copy_schemas"]
                )
                assert all("messages" not in task for task in listed)

                # refusals
                result = await call("util.copy_instance", {
                    "connection_id": connection_id, "target_connection_id": connection_id,
                    "options": {"host": "203.0.113.10", "port": 3306},
                })
                assert result.is_error
                assert "names a connection attribute" in str(helpers.tool_payload(result))
                result = await call("util.dump_schemas", {
                    "connection_id": connection_id, "schemas": ["mcp_util"],
                    "output_url": dump_dir, "options": {"session": 1},
                })
                assert result.is_error and "set by the tool" in str(helpers.tool_payload(result))
                # an outputUrl option would replace output_url, which alone was checked
                for name, arguments in (
                    ("util.dump_schemas", {"schemas": ["mcp_util"]}),
                    ("util.dump_tables", {"schema": "mcp_util", "tables": ["t"]}),
                ):
                    result = await call(name, {
                        "connection_id": connection_id, **arguments,
                        "output_url": os.path.join(allowed_temp_dir, "redirected"),
                        "options": {"output_url": "/tmp/elsewhere"},
                    })
                    assert result.is_error and "output_url argument" in str(helpers.tool_payload(result))
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
                await sql("DROP SCHEMA IF EXISTS mcp_util_resumed")
                await call("db.close", {"connection_id": connection_id})

    asyncio.run(_run())
