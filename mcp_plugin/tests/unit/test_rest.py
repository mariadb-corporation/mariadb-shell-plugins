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

"""Tests for the rest group: REST Daemon deployments and their tools.

A fake mariadb-restd stands in for the real one: its --setup writes a
configuration as the real one does and records what it was given, and run
with -c it listens on the configured port and writes the pid file the real
one writes. The tools are driven in-process against stand-ins for the
connection and its sessions.
"""

# cSpell:ignore mysqlsh MariaDB restd

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from mcp_plugin.lib import general, rest_functions, restd, restd_install, sandbox_functions
import mcp_plugin.tests.unit.helpers as helpers

_FAKE_RESTD = r'''
import json, os, signal, socket, sys, time
state = os.environ["FAKE_RESTD_STATE"]
mode = os.environ.get("FAKE_RESTD_MODE", "")
argv = sys.argv[1:]
def record(entry):
    runs = json.load(open(state)) if os.path.exists(state) else []
    runs.append(entry)
    with open(state + ".tmp", "w") as file:
        json.dump(runs, file)
    os.replace(state + ".tmp", state)
if argv and argv[0] == "--setup":
    print("Please enter MariaDB password for root: ", end="", flush=True)
    # As the real one: the deployment's folder, not the folders above it.
    if not os.path.isdir(os.path.dirname(argv[argv.index("--directory") + 1])):
        print("Error: Could not create directory", file=sys.stderr)
        sys.exit(1)
    record({"argv": argv, "stdin": sys.stdin.readline()})
    if mode == "setup-fails":
        print("Error: Access denied for user 'root'", file=sys.stderr)
        sys.exit(1)
    directory = argv[argv.index("--directory") + 1]
    port = [a.split("=", 2)[2] for a in argv if a.startswith("--conf-set-option=http_server.port")][0]
    for folder in ("log", "run", "data"):
        os.makedirs(os.path.join(directory, folder))
    with open(os.path.join(directory, "mariadb-restd.conf"), "w") as file:
        file.write(
            f"[DEFAULT]\nlogging_folder={directory}/log\n"
            f"[http_server]\nport={port}\nssl=1\n"
            "[mysql_rest_service]\nmysql_user=mariadb_restd_abc123\n"
            "mysql_read_write_destinations=127.0.0.1:3311\n"
            "rest_daemon_id=0199a1b2-6c3e-7d41-9a0f-2b8c4d5e6f70\n"
        )
    print("# Setting up a REST Daemon")
    sys.exit(0)
record({"argv": argv})
if mode == "start-fails":
    print("Error: plugin 'http_server' could not be loaded", file=sys.stderr, flush=True)
    sys.exit(1)
conf = argv[argv.index("-c") + 1]
port = int([line.split("=")[1] for line in open(conf) if line.startswith("port=")][0])
pid_file = os.environ["ROUTER_PID"]
if os.path.exists(pid_file):
    print(f"PID file {pid_file} found. Already running?", file=sys.stderr, flush=True)
    sys.exit(1)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", port))
listener.listen()
with open(pid_file, "w") as file:
    file.write(str(os.getpid()))
def bye(*_):
    os.remove(pid_file)
    sys.exit(0)
signal.signal(signal.SIGTERM, bye)
while True:
    time.sleep(0.1)
'''


@pytest.fixture
def fake_restd(tmp_path, monkeypatch):
    """A mariadb-restd that records its runs; yields (executable, runs)."""
    if os.name == "nt":
        pytest.skip("the fake mariadb-restd is a POSIX script")

    script = tmp_path / "fake_restd.py"
    script.write_text(_FAKE_RESTD)
    executable = tmp_path / "bin" / "mariadb-restd"
    executable.parent.mkdir()
    executable.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I '{script}' \"$@\"\n")
    executable.chmod(0o755)
    state = tmp_path / "runs.json"
    monkeypatch.setenv("FAKE_RESTD_STATE", str(state))

    def runs():
        return json.loads(state.read_text()) if state.exists() else []

    yield str(executable), runs


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of its own, so no real deployment or install is seen."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("APPDATA", str(home_dir / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home_dir / "AppData" / "Local"))
    monkeypatch.delenv("MARIADB_RESTD_PREFIX", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return home_dir


def _set_up(executable, directory, port=None, password="pw"):
    port = port or helpers.find_free_port()
    restd.setup(
        executable,
        directory,
        {"user": "root", "host": "127.0.0.1", "port": 3311},
        password,
        port,
        record={"host": "127.0.0.1", "server_port": 3311, "sandbox_port": 3311},
    )
    return port


# --- where things are ----------------------------------------------------------


def test_the_deployment_folders(home):
    """A sandbox's is in its folder, any other server's in ~/.mariadb-restd."""
    assert restd.sandbox_deployment_dir("/s", 3311) == os.path.join(
        "/s", "3311", "mariadb-restd"
    )
    if os.name != "nt":
        assert restd.deployments_root() == str(home / ".mariadb-restd")
    assert restd.connection_deployment_dir("db1.example.com", 3306).endswith(
        "db1.example.com_3306"
    )
    # An IPv6 address's colons cannot be part of a file name everywhere.
    assert restd.connection_deployment_dir("::1", 3306).endswith("--1_3306")


def test_mariadb_restd_is_looked_for_on_the_path_then_in_local_bin_then_installed(
    home, tmp_path, monkeypatch
):
    """And of the installed releases, the newest one."""
    if os.name == "nt":
        pytest.skip("POSIX layout")

    assert restd.find_restd() is None

    def executable(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return str(path)

    prefix = home / ".local" / "share" / "mariadb-restd"
    executable(prefix / "1.9.0" / "bin" / "mariadb-restd")
    newest = executable(prefix / "1.10.0" / "bin" / "mariadb-restd")
    assert restd.find_restd() == newest

    linked = executable(home / ".local" / "bin" / "mariadb-restd")
    assert restd.find_restd() == linked

    on_path = executable(tmp_path / "path" / "mariadb-restd")
    monkeypatch.setenv("PATH", f"{tmp_path / 'path'}:/usr/bin:/bin")
    assert restd.find_restd() == on_path

    # An explicit one is not searched past, and must be there.
    assert restd.find_restd(str(prefix / "1.9.0")) == str(
        prefix / "1.9.0" / "bin" / "mariadb-restd"
    )
    with pytest.raises(Exception, match="no mariadb-restd executable"):
        restd.find_restd(str(tmp_path / "nowhere"))


def test_a_sandbox_daemon_listens_on_ten_times_its_port(monkeypatch):
    """When that is free and a port at all; else the first free from 8443."""
    used = set()
    monkeypatch.setattr(restd, "is_listening", lambda port: port in used)
    monkeypatch.setattr(restd, "_can_bind", lambda port: port not in used)

    assert restd.choose_port(3311) == 33110
    used.add(33110)
    assert restd.choose_port(3311) == 8443
    # 70000 is no port.
    assert restd.choose_port(7000) == 8443
    assert restd.choose_port() == 8443
    used.add(8443)
    assert restd.choose_port() == 8444
    # A stopped deployment's port counts as taken.
    assert restd.choose_port(taken={8444}) == 8445
    assert restd.choose_port(3312, taken={33120}) == 8444


# --- setup, start, stop --------------------------------------------------------


def test_the_setup_gets_the_password_on_stdin(fake_restd, tmp_path):
    """Never on its command line, and always a JWT secret, so it asks nothing else."""
    executable, runs = fake_restd
    directory = str(tmp_path / "deployment")
    port = _set_up(executable, directory, password="s3cret!")

    (run,) = runs()
    assert run["stdin"] == "s3cret!\n"
    assert not any("s3cret!" in argument for argument in run["argv"])
    assert run["argv"][:2] == ["--setup", "root@127.0.0.1:3311"]
    assert any(argument.startswith("--jwt-secret=") for argument in run["argv"])
    assert f"--conf-set-option=http_server.port={port}" in run["argv"]
    # The default metadata schema is not named.
    assert "--metadata-schema" not in run["argv"]

    deployment = restd.read_deployment(directory)
    assert deployment["port"] == port
    assert deployment["url"] == f"https://localhost:{port}"
    assert deployment["sandbox_port"] == 3311
    assert deployment["host"] == "127.0.0.1"
    assert restd.generated_accounts(deployment) == ["mariadb_restd_abc123"]


def test_a_failed_setup_says_why(fake_restd, tmp_path, monkeypatch):
    executable, _ = fake_restd
    monkeypatch.setenv("FAKE_RESTD_MODE", "setup-fails")

    with pytest.raises(Exception, match="Access denied") as raised:
        _set_up(executable, str(tmp_path / "deployment"))
    assert "exit code 1" in str(raised.value)


def test_a_daemon_is_started_and_stopped(fake_restd, tmp_path):
    executable, runs = fake_restd
    directory = str(tmp_path / "deployment")
    port = _set_up(executable, directory)

    try:
        started = restd.start(executable, directory)
        assert started["status"] == "running"
        assert restd.is_listening(port)
        assert started["pid"] == restd.running_pid(directory)

        # Running already: nothing is started a second time.
        assert restd.start(executable, directory)["pid"] == started["pid"]
        assert len([run for run in runs() if "-c" in run["argv"]]) == 1
    finally:
        stopped = restd.stop(directory)

    assert stopped["status"] == "stopped"
    assert stopped["pid"] is None
    assert not restd.is_listening(port)
    # And a stopped one is stopped again without complaint.
    assert restd.stop(directory)["status"] == "stopped"


def test_a_pid_file_left_by_a_killed_daemon_does_not_stop_a_start(fake_restd, tmp_path):
    """mariadb-restd itself would refuse: 'PID file ... found. Already running?'."""
    executable, _ = fake_restd
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory)
    with open(os.path.join(directory, restd.PID_FILE), "w") as file:
        file.write("999999")

    try:
        assert restd.start(executable, directory)["status"] == "running"
    finally:
        restd.stop(directory)


def test_a_daemon_that_does_not_start_says_why(fake_restd, tmp_path, monkeypatch):
    executable, _ = fake_restd
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory)
    monkeypatch.setenv("FAKE_RESTD_MODE", "start-fails")

    with pytest.raises(Exception, match="could not be loaded") as raised:
        restd.start(executable, directory)
    assert "exited with code 1" in str(raised.value)
    assert restd.status(directory)["status"] == "stopped"


def test_a_deployment_is_deleted_with_its_daemon_stopped(fake_restd, tmp_path):
    executable, _ = fake_restd
    directory = str(tmp_path / "deployment")
    port = _set_up(executable, directory)
    restd.start(executable, directory)

    restd.delete_directory(directory)

    assert not os.path.exists(directory)
    assert not restd.is_listening(port)


def test_the_deployments_are_listed_with_the_sandboxes(fake_restd, home, tmp_path):
    executable, _ = fake_restd
    sandboxes = tmp_path / "sandboxes"
    in_sandbox = restd.sandbox_deployment_dir(str(sandboxes), 3311)
    for_server = restd.connection_deployment_dir("db1", 3306)
    first = _set_up(executable, in_sandbox)
    second = _set_up(executable, for_server)

    assert restd.deployment_dirs(str(sandboxes)) == sorted([in_sandbox, for_server])
    assert restd.deployment_dirs() == [for_server]
    assert restd.taken_ports(str(sandboxes)) == {first, second}


# --- the privileges the setup needs ---------------------------------------------


def test_root_has_every_privilege_the_setup_needs():
    grants = ["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"]
    assert rest_functions._missing_privileges(grants, "mariadb_rest_service") == []


def test_an_account_granted_exactly_what_the_setup_needs_passes():
    grants = [
        "GRANT CREATE USER ON *.* TO `admin`@`%` WITH GRANT OPTION",
        "GRANT SELECT, INSERT, UPDATE ON `mariadb_rest_service`.* TO `admin`@`%`",
        "GRANT `mariadb_rest_service_meta_provider` TO `admin`@`%` WITH ADMIN OPTION",
        "GRANT `mariadb_rest_service_data_provider` TO `admin`@`%` WITH ADMIN OPTION",
    ]
    assert rest_functions._missing_privileges(grants, "mariadb_rest_service") == []


def test_what_an_account_lacks_is_named():
    grants = [
        "GRANT USAGE ON *.* TO `dev`@`%`",
        "GRANT SELECT ON `mariadb_rest_service`.* TO `dev`@`%`",
        "GRANT `mariadb_rest_service_meta_provider` TO `dev`@`%`",
    ]
    assert rest_functions._missing_privileges(grants, "mariadb_rest_service") == [
        "CREATE USER on *.*",
        "the GRANT OPTION on *.*",
        "INSERT on mariadb_rest_service.*",
        "UPDATE on mariadb_rest_service.*",
        "the role mariadb_rest_service_meta_provider WITH ADMIN OPTION",
        "the role mariadb_rest_service_data_provider WITH ADMIN OPTION",
    ]


def test_the_roles_carry_the_metadata_schemas_prefix_and_postfix():
    assert rest_functions._role_names("acme_mariadb_rest_service_eu") == [
        "acme_mariadb_rest_service_meta_provider_eu",
        "acme_mariadb_rest_service_data_provider_eu",
    ]


# --- the tools -----------------------------------------------------------------


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _tools(function_groups=("db", "rest")):
    recorder = _ToolRecorder()
    rest_functions.register_rest_tools(recorder, list(function_groups))
    return recorder.tools


def test_setting_up_needs_the_db_group():
    """Starting, stopping, listing and deleting do not."""
    always = {
        "rest.get_restd_status",
        "rest.start_restd",
        "rest.stop_restd",
        "rest.delete_restd_config",
    }
    assert set(_tools(["rest"])) == always
    assert set(_tools()) == always | {"rest.configure_restd"}


def test_rest_is_a_default_group_but_not_a_multi_tenant_one():
    """It runs a daemon on this machine."""
    assert "rest" in general.DEFAULT_FUNCTION_GROUPS
    assert "rest" not in general.MULTI_TENANT_FUNCTION_GROUPS


def test_the_tools_are_served():
    names = set(helpers.list_tool_names(["db", "rest"]))
    assert "rest.configure_restd" in names
    assert "rest.get_restd_status" in names


class _Session:
    """A session answering what configure_restd asks, recording the rest."""

    def __init__(self, schemas, grants, statements):
        self.schemas, self.grants, self.statements = schemas, grants, statements

    def run_sql(self, sql, params=None):
        self.statements.append(sql)
        if sql.startswith("SHOW REST METADATA SCHEMAS"):
            rows = [[json.dumps([{"schema_name": s, "version": "5.0.0", "current": False}
                                 for s in self.schemas])]]
        elif sql == "SHOW GRANTS":
            rows = [[grant] for grant in self.grants]
        else:
            rows = []
        return SimpleNamespace(
            fetch_one=lambda: rows[0] if rows else None, fetch_all=lambda: rows
        )

    def close(self):
        pass


_ROOT_GRANTS = ["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"]


@pytest.fixture
def configure(fake_restd, home, tmp_path, monkeypatch, gui_mode):
    """rest.configure_restd against a fake connection and mariadb-restd.

    Yields a function taking the tool's arguments and the server's metadata
    schemas and grants, returning (result, statements, passwords).
    """
    executable, _ = fake_restd
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    started = []

    def run(schemas=(), grants=_ROOT_GRANTS, **arguments):
        statements, passwords = [], []
        monkeypatch.setattr(
            rest_functions.db_functions,
            "connection_credentials",
            lambda connection_id, client: ("mariadb://root@127.0.0.1:3311", "stored"),
        )
        monkeypatch.setattr(
            rest_functions,
            "_parse_uri",
            lambda uri: {"scheme": "mariadb", "user": "root", "host": "127.0.0.1", "port": 3311},
        )

        def open_session(connection, password):
            passwords.append(password)
            return _Session(list(schemas), list(grants), statements)

        monkeypatch.setattr(rest_functions, "_open_session", open_session)
        arguments.setdefault("sandbox_dir", str(tmp_path / "sandboxes"))
        result = asyncio.run(_tools()["rest.configure_restd"](None, "c1", **arguments))
        started.append(result["directory"])
        return result, statements, passwords

    yield run

    for directory in started:
        restd.stop(directory)


def test_a_sandbox_gets_a_daemon(configure, tmp_path, monkeypatch):
    """Its metadata is created, and the daemon is in the sandbox's folder."""
    monkeypatch.setattr(restd, "is_port_free", lambda port, taken=(): port != 33110)
    port = helpers.find_free_port()
    monkeypatch.setattr(restd, "choose_port", lambda sandbox_port, taken: port)

    result, statements, passwords = configure(sandbox_port=3311, password="given")

    assert result["directory"] == restd.sandbox_deployment_dir(
        str(tmp_path / "sandboxes"), 3311
    )
    assert result["status"] == "running"
    assert result["port"] == port
    assert result["installed"] is False
    assert "CONFIGURE REST METADATA ENABLED" in statements
    # The password given is the one tried, and the one the setup gets.
    assert set(passwords) == {"given"}


def test_the_stored_password_is_used_when_none_is_given(configure, monkeypatch):
    port = helpers.find_free_port()
    result, statements, passwords = configure(
        schemas=["mariadb_rest_service"], port=port
    )

    assert set(passwords) == {"stored"}
    # It has its metadata already.
    assert not any(s.startswith("CONFIGURE") for s in statements)
    assert result["directory"] == restd.connection_deployment_dir("127.0.0.1", 3311)


def test_of_several_metadata_schemas_one_must_be_named(configure):
    with pytest.raises(Exception, match="several REST metadata schemas") as raised:
        configure(schemas=["mariadb_rest_service", "acme_mariadb_rest_service"])
    assert "acme_mariadb_rest_service" in str(raised.value)


def test_an_account_without_the_privileges_is_refused(configure):
    with pytest.raises(Exception, match="cannot set up a REST Daemon"):
        configure(
            schemas=["mariadb_rest_service"],
            grants=["GRANT USAGE ON *.* TO `dev`@`%`"],
        )


def test_a_missing_daemon_is_installed_in_gui_mode(configure, home, monkeypatch, tmp_path):
    """Without asking: the user asked the extension for a REST Daemon."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    fake = str(tmp_path / "bin" / "mariadb-restd")
    installs = []

    def install(tag=None):
        installs.append(tag)
        monkeypatch.setenv("PATH", f"{os.path.dirname(fake)}:/usr/bin:/bin")
        return "installed"

    monkeypatch.setattr(rest_functions.restd_install, "install", install)
    result, _, _ = configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())

    assert installs == [None]
    assert result["installed"] is True


def test_an_agent_is_asked_before_anything_is_installed(configure, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(general, "is_gui_mode", lambda: False)
    monkeypatch.setattr(
        rest_functions.restd_install,
        "install",
        lambda tag=None: pytest.fail("installed without asking"),
    )

    # With no client to ask, the answer is no. No sandbox_dir: an agent would
    # be asked to trust that path first.
    with pytest.raises(Exception, match="was not installed"):
        configure(schemas=["mariadb_rest_service"], sandbox_dir=None)


def test_a_deployment_is_deleted_with_its_account(fake_restd, home, tmp_path, monkeypatch, gui_mode):
    executable, _ = fake_restd
    directory = restd.connection_deployment_dir("127.0.0.1", 3311)
    _set_up(executable, directory)
    restd.start(executable, directory)
    statements = []

    class _Use:
        def __init__(self, connection_id, client):
            pass

        def __enter__(self):
            return _Session([], [], statements)

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(rest_functions.db_functions, "use_session", _Use)
    tools = _tools()

    # Not without a connection to drop the account with.
    with pytest.raises(Exception, match="connection_id"):
        asyncio.run(tools["rest.delete_restd_config"](None, directory))
    assert os.path.isdir(directory)

    asyncio.run(tools["rest.delete_restd_config"](None, directory, connection_id="c1"))

    assert not os.path.exists(directory)
    assert "DROP USER IF EXISTS 'mariadb_restd_abc123'@'%'" in statements
    assert any(s.startswith("DELETE FROM `mariadb_rest_service`.rest_daemon") for s in statements)


def test_the_status_lists_running_and_stopped_daemons(fake_restd, home, tmp_path, monkeypatch, gui_mode):
    executable, _ = fake_restd
    running = restd.connection_deployment_dir("db1", 3306)
    stopped = restd.connection_deployment_dir("db2", 3306)
    _set_up(executable, running)
    _set_up(executable, stopped)
    restd.start(executable, running)
    try:
        report = {
            entry["directory"]: entry
            for entry in _tools()["rest.get_restd_status"]()
        }
    finally:
        restd.stop(running)

    assert report[running]["status"] == "running"
    assert report[stopped]["status"] == "stopped"


# --- the sandbox follows ---------------------------------------------------------


def _sandbox_tools(monkeypatch, calls):
    import mysqlsh.globals

    def recorder(name):
        return lambda port, options=None: calls.append((name, port))

    monkeypatch.setattr(
        mysqlsh.globals,
        "sandbox",
        SimpleNamespace(
            start=recorder("start"),
            stop=recorder("stop"),
            kill=recorder("kill"),
            delete=recorder("delete"),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_functions.config, "resolve_connection_uri", lambda uri, kind=None: None
    )
    recorder_server = _ToolRecorder()
    sandbox_functions.register_sandbox_tools(recorder_server)
    return recorder_server.tools


def test_a_sandboxs_daemon_follows_it(fake_restd, tmp_path, monkeypatch, gui_mode):
    """Started after the server, stopped before it and before its delete."""
    executable, _ = fake_restd
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    sandboxes = str(tmp_path / "sandboxes")
    directory = restd.sandbox_deployment_dir(sandboxes, 3311)
    port = _set_up(executable, directory)
    calls = []
    tools = _sandbox_tools(monkeypatch, calls)
    monkeypatch.setattr(sandbox_functions, "_is_listening", lambda port: False)

    try:
        message = asyncio.run(tools["sandbox.start"](None, 3311, sandbox_dir=sandboxes))
        assert f"serves https://localhost:{port}" in message
        assert restd.status(directory)["status"] == "running"

        asyncio.run(tools["sandbox.stop"](None, 3311, sandbox_dir=sandboxes))
        assert restd.status(directory)["status"] == "stopped"

        restd.start(executable, directory)
        asyncio.run(tools["sandbox.delete"](None, 3311, sandbox_dir=sandboxes))
        assert restd.status(directory)["status"] == "stopped"
        assert calls == [("start", 3311), ("stop", 3311), ("delete", 3311)]
    finally:
        restd.stop(directory)


def test_a_daemon_that_does_not_start_leaves_the_sandbox_started(
    fake_restd, tmp_path, monkeypatch, gui_mode
):
    executable, _ = fake_restd
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    sandboxes = str(tmp_path / "sandboxes")
    _set_up(executable, restd.sandbox_deployment_dir(sandboxes, 3311))
    monkeypatch.setenv("FAKE_RESTD_MODE", "start-fails")
    tools = _sandbox_tools(monkeypatch, [])

    message = asyncio.run(tools["sandbox.start"](None, 3311, sandbox_dir=sandboxes))

    assert message.startswith("Sandbox instance on port 3311 started.")
    assert "Its REST Daemon did not start" in message


# --- the install ---------------------------------------------------------------


def test_the_install_script_is_run_and_its_failure_reported(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("install.sh")

    script = tmp_path / "install.sh"
    marker = tmp_path / "ran"
    script.write_text(
        f'echo "==> Installing $MARIADB_RESTD_TAG"\ntouch "{marker}"\n'
        'if [ "$MARIADB_RESTD_TAG" = v0 ]; then echo "install.sh: no release v0" >&2; exit 1; fi\n'
    )
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_URL_POSIX", script.as_uri())

    assert "Installing v1" in restd_install.install("v1")
    assert marker.exists()
    with pytest.raises(Exception, match="install.sh: no release v0"):
        restd_install.install("v0")


def test_an_install_script_that_cannot_be_downloaded_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(
        restd_install, "INSTALL_SCRIPT_URL_POSIX", (tmp_path / "missing.sh").as_uri()
    )
    monkeypatch.setattr(
        restd_install, "INSTALL_SCRIPT_URL_WINDOWS", (tmp_path / "missing.ps1").as_uri()
    )
    with pytest.raises(Exception, match="put it on the PATH"):
        restd_install.install()


# --- for real ------------------------------------------------------------------


def _sandbox_port() -> int:
    """A free port whose tenfold is a free port too, for the sandbox's daemon."""
    for port in range(4000, 6500):
        if restd.is_port_free(port) and restd.is_port_free(port * 10):
            return port
    pytest.skip("no free port pair was found")


@pytest.mark.e2e
def test_a_sandbox_really_gets_a_rest_daemon():
    """With the real mariadb-restd: set up, served, and following the sandbox.

    Needs a mariadb-restd where rest.configure_restd looks for one, and a
    MariaDB server of 11.7 or later on the PATH for the sandbox.
    """
    if restd.find_restd() is None:
        pytest.skip(f"no mariadb-restd in {restd.searched_places()}")
    if not helpers.server_binary_available():
        pytest.skip("no MariaDB server on the PATH")

    import ssl
    import urllib.error
    import urllib.request

    port = _sandbox_port()

    def served(url) -> bool:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            urllib.request.urlopen(url, context=context, timeout=5)
        except urllib.error.HTTPError:
            return True
        except OSError:
            return False
        return True

    async def run():
        async with helpers.mcp_session(["db", "sandbox", "rest"], timeout=300) as call:
            deployed = await call("sandbox.deploy", {"port": port, "password": "Sandbox1!"})
            assert not deployed.is_error, helpers.tool_payload(deployed)
            try:
                connected = await call(
                    "db.connect", {"uri": f"mariadb://root@127.0.0.1:{port}"}
                )
                connection_id = helpers.tool_payload(connected)
                configured = await call(
                    "rest.configure_restd",
                    {"connection_id": connection_id, "sandbox_port": port},
                )
                assert not configured.is_error, helpers.tool_payload(configured)
                daemon = helpers.tool_payload(configured)
                assert daemon["port"] == port * 10
                assert daemon["status"] == "running"
                assert served(daemon["url"])
                await call("db.close", {"connection_id": connection_id})

                await call("sandbox.stop", {"port": port, "password": "Sandbox1!"})
                assert not served(daemon["url"])
                started = await call("sandbox.start", {"port": port})
                assert "Its REST Daemon serves" in helpers.tool_payload(started)
                assert served(daemon["url"])

                listed = helpers.tool_payload(await call("rest.get_restd_status"))
                # One deployment comes back as the one object, not a list.
                listed = [listed] if isinstance(listed, dict) else listed
                assert [d["status"] for d in listed if d["sandbox_port"] == port] == [
                    "running"
                ]
            finally:
                await call("sandbox.stop", {"port": port, "password": "Sandbox1!"})
                await call("sandbox.delete", {"port": port})

            assert not os.path.exists(daemon["directory"])

    asyncio.run(run())


def test_a_listed_deployment_needs_no_allowed_path(fake_restd, home, monkeypatch):
    """What configure_restd made is not a path for the agent to have trusted."""
    executable, _ = fake_restd
    listed = restd.connection_deployment_dir("db1", 3306)
    elsewhere = str(home / "elsewhere")
    _set_up(executable, listed)
    _set_up(executable, elsewhere)
    monkeypatch.setattr(general, "is_gui_mode", lambda: False)
    asked = []

    async def require(ctx, path):
        asked.append(path)
        raise Exception("not allowed")

    monkeypatch.setattr(rest_functions.general, "require_allowed_path", require)
    tools = _tools()

    assert asyncio.run(tools["rest.stop_restd"](None, listed))["status"] == "stopped"
    with pytest.raises(Exception, match="not allowed"):
        asyncio.run(tools["rest.stop_restd"](None, elsewhere))
    assert asked == [elsewhere]
