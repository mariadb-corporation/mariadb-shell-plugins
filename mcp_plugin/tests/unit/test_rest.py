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
import re
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
    password = sys.stdin.readline()
    # As the real one: the JWT secret is asked for unless it is an option.
    jwt = "" if any(a.startswith("--jwt-secret") for a in argv) else sys.stdin.readline()
    record({"argv": argv, "stdin": password, "jwt": jwt})
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
            "[mariadb_rest_service]\nmetadata_user=mariadb_restd_abc123\n"
            "read_write_destinations=127.0.0.1:3311\n"
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
pid_file = os.environ["MARIADB_RESTD_PID"]
if os.path.exists(pid_file):
    print(f"PID file {pid_file} found. Already running?", file=sys.stderr, flush=True)
    sys.exit(1)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", port))
listener.listen()
if mode == "slow-pid":
    # The pid file only once every plugin is loaded, a while after the port.
    time.sleep(1)
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


def test_a_daemon_listens_on_the_first_free_port_from_8443(monkeypatch):
    """A sandbox's as any other's."""
    used = set()
    monkeypatch.setattr(restd, "is_listening", lambda port: port in used)
    monkeypatch.setattr(restd, "_can_bind", lambda port: port not in used)

    assert restd.choose_port() == 8443
    used.add(8443)
    assert restd.choose_port() == 8444
    # A stopped deployment's port counts as taken.
    assert restd.choose_port(taken={8444}) == 8445


# --- setup, start, stop --------------------------------------------------------


def test_the_setup_gets_the_password_on_stdin(fake_restd, tmp_path):
    """Never on its command line, and the JWT secret neither."""
    executable, runs = fake_restd
    directory = str(tmp_path / "deployment")
    port = _set_up(executable, directory, password="s3cret!")

    (run,) = runs()
    assert run["stdin"] == "s3cret!\n"
    assert not any("s3cret!" in argument for argument in run["argv"])
    assert run["argv"][:2] == ["--setup", "root@127.0.0.1:3311"]
    assert not any(argument.startswith("--jwt-secret") for argument in run["argv"])
    assert re.fullmatch(r"[0-9a-f]{64}\n", run["jwt"])
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


def test_a_pid_used_again_by_another_process_is_not_the_daemon(fake_restd, tmp_path):
    """The daemon was killed and its pid went to another program, which a
    stop must not end and a start must not take for the daemon."""
    import subprocess

    executable, _ = fake_restd
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory)
    other = subprocess.Popen(["sleep", "30"])
    try:
        with open(os.path.join(directory, restd.PID_FILE), "w") as file:
            file.write(str(other.pid))

        assert restd.status(directory)["status"] == "stopped"
        assert not os.path.exists(os.path.join(directory, restd.PID_FILE))

        with open(os.path.join(directory, restd.PID_FILE), "w") as file:
            file.write(str(other.pid))
        restd.stop(directory)
        assert other.poll() is None

        # Nor does looking reap it: its own Popen still learns how it ended.
        other.terminate()
        assert other.wait(timeout=5) == -15
    finally:
        if other.poll() is None:
            other.kill()


def test_a_started_daemon_is_running_once_its_pid_file_is_there(
    fake_restd, tmp_path, monkeypatch
):
    """mariadb-restd writes its pid file once all its plugins are loaded,
    which can be after its port listens: started, it is not stopped."""
    executable, _ = fake_restd
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory)
    monkeypatch.setenv("FAKE_RESTD_MODE", "slow-pid")

    try:
        report = restd.start(executable, directory)
        assert report["status"] == "running" and report["pid"] is not None
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


def test_a_schema_grant_is_read_as_the_server_reads_its_pattern():
    r"""`mariadb\_rest\_service`.* is the usual way to grant on the schema
    alone; `_` and `%` unescaped are wildcards."""
    needed = [
        "GRANT CREATE USER ON *.* TO `admin`@`%` WITH GRANT OPTION",
        "GRANT `mariadb_rest_service_meta_provider` TO `admin`@`%` WITH ADMIN OPTION",
        "GRANT `mariadb_rest_service_data_provider` TO `admin`@`%` WITH ADMIN OPTION",
    ]
    for target in (
        r"`mariadb\_rest\_service`.*",
        "`mariadb%`.*",
        "`MARIADB_REST_SERVICE`.*",
    ):
        grants = needed + [f"GRANT SELECT, INSERT, UPDATE ON {target} TO `admin`@`%`"]
        assert rest_functions._missing_privileges(grants, "mariadb_rest_service") == [], target

    # Escaped, the characters are themselves: no wildcard for another name.
    grants = needed + [r"GRANT SELECT, INSERT, UPDATE ON `mariadbXrest\_service`.* TO `admin`@`%`"]
    assert "SELECT on mariadb_rest_service.*" in rest_functions._missing_privileges(
        grants, "mariadb_rest_service"
    )


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
        "rest.trust_certificate",
        "rest.get_certificate_status",
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
    port = helpers.find_free_port()
    monkeypatch.setattr(restd, "choose_port", lambda taken: port)

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
        result = asyncio.run(tools["sandbox.start"](None, 3311, sandbox_dir=sandboxes))
        assert f"serves https://localhost:{port}" in result["message"]
        assert [(o["port"], o["status"], o["error"]) for o in result["restd"]] == [
            (port, "running", None)
        ]
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

    result = asyncio.run(tools["sandbox.start"](None, 3311, sandbox_dir=sandboxes))

    assert result["message"].startswith("Sandbox instance on port 3311 started.")
    assert "Its REST Daemon did not start" in result["message"]
    [outcome] = result["restd"]
    assert outcome["error"]
    assert outcome["directory"] == restd.sandbox_deployment_dir(sandboxes, 3311)
    assert outcome["log_path"]


def test_a_daemon_that_does_not_stop_does_not_keep_the_sandbox(
    fake_restd, tmp_path, monkeypatch, gui_mode
):
    """Reported per daemon; the sandbox is stopped all the same."""
    executable, _ = fake_restd
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    sandboxes = str(tmp_path / "sandboxes")
    _set_up(executable, restd.sandbox_deployment_dir(sandboxes, 3311))
    calls = []
    tools = _sandbox_tools(monkeypatch, calls)

    def refuse(directory):
        raise RuntimeError("will not stop")

    monkeypatch.setattr(restd, "stop", refuse)
    result = asyncio.run(tools["sandbox.stop"](None, 3311, sandbox_dir=sandboxes))

    assert calls == [("stop", 3311)]
    assert [outcome["error"] for outcome in result["restd"]] == ["will not stop"]


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
    """A free port for the sandbox."""
    for port in range(4000, 6500):
        if restd.is_port_free(port):
            return port
    pytest.skip("no free port was found")


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
                # The first free one from 8443 up, as for any server.
                assert daemon["port"] >= restd.DEFAULT_PORT
                assert daemon["status"] == "running"
                assert served(daemon["url"])
                # The one certificate of every daemon here, for localhost.
                served_pem = ssl.get_server_certificate(("localhost", daemon["port"]))
                with open(os.path.join(restd_certs.certs_dir(), "server.crt")) as f:
                    assert ssl.PEM_cert_to_DER_cert(served_pem) == \
                        ssl.PEM_cert_to_DER_cert(f.read())
                await call("db.close", {"connection_id": connection_id})

                await call("sandbox.stop", {"port": port, "password": "Sandbox1!"})
                assert not served(daemon["url"])
                started = await call("sandbox.start", {"port": port})
                assert "Its REST Daemon serves" in helpers.tool_payload(started)["message"]
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


@pytest.mark.e2e
def test_a_server_reached_through_its_socket_gets_the_daemon_of_its_port():
    """With the real mariadb-restd: set up through the sandbox's socket, the
    daemon is that of localhost and the port the server listens on, as for
    a TCP connection, and it is served.

    Needs what test_a_sandbox_really_gets_a_rest_daemon needs; it writes to
    the real ~/.mariadb-restd, and deletes what it wrote.
    """
    if os.name == "nt":
        pytest.skip("a socket is POSIX")
    if restd.find_restd() is None:
        pytest.skip(f"no mariadb-restd in {restd.searched_places()}")
    if not helpers.server_binary_available():
        pytest.skip("no MariaDB server on the PATH")

    port = _sandbox_port()
    directory = restd.connection_deployment_dir("localhost", port)
    assert not os.path.exists(directory)

    async def run():
        # --gui, as the extension serves it: the socket connection is added
        # with its password.
        async with helpers.mcp_session(
            ["db", "sandbox", "rest"], timeout=300, gui=True
        ) as call:
            deployed = await call("sandbox.deploy", {"port": port, "password": "Sandbox1!"})
            assert not deployed.is_error, helpers.tool_payload(deployed)
            socket_uri = None
            try:
                over_tcp = helpers.tool_payload(await call(
                    "db.connect", {"uri": f"mariadb://root@127.0.0.1:{port}"}
                ))
                row = helpers.tool_rows(await call("db.execute_sql", {
                    "connection_id": over_tcp, "sql": "SELECT @@socket"}))[0]
                socket_path = row["@@socket"] if isinstance(row, dict) else row[0]
                await call("db.close", {"connection_id": over_tcp})

                socket_uri = f"mariadb://root@({socket_path})"
                added = await call("db.add_connection",
                                   {"uri": socket_uri, "password": "Sandbox1!"})
                assert not added.is_error, helpers.tool_payload(added)
                connection_id = helpers.tool_payload(
                    await call("db.connect", {"uri": socket_uri}))

                configured = await call(
                    "rest.configure_restd", {"connection_id": connection_id}
                )
                assert not configured.is_error, helpers.tool_payload(configured)
                daemon = helpers.tool_payload(configured)
                assert daemon["directory"] == directory
                assert (daemon["host"], daemon["server_port"], daemon["socket"]) == (
                    "localhost", port, socket_path
                )
                assert daemon["status"] == "running"

                deleted = await call("rest.delete_restd_config", {
                    "directory": directory, "connection_id": connection_id})
                assert not deleted.is_error, helpers.tool_payload(deleted)
                await call("db.close", {"connection_id": connection_id})
            finally:
                if os.path.exists(directory):
                    restd.delete_directory(directory)
                if socket_uri is not None:
                    await call("db.delete_connection", {"uri": socket_uri})
                await call("sandbox.stop", {"port": port, "password": "Sandbox1!"})
                await call("sandbox.delete", {"port": port})

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


def test_a_deployment_reports_the_socket_it_was_set_up_through(fake_restd, tmp_path):
    """What the extension matches a socket connection on."""
    executable, _ = fake_restd
    through_socket = str(tmp_path / "socket")
    restd.setup(
        executable,
        through_socket,
        {"user": "root", "socket": "/tmp/mysql.sock"},
        "pw",
        helpers.find_free_port(),
        record={"host": "localhost", "server_port": 3306, "socket": "/tmp/mysql.sock"},
    )
    by_port = str(tmp_path / "port")
    _set_up(executable, by_port)

    assert restd.status(through_socket)["socket"] == "/tmp/mysql.sock"
    assert restd.status(by_port)["socket"] is None


def test_a_server_has_one_deployment_however_it_is_reached(home):
    """Through a socket, a server is localhost and the port it reports, as
    for a TCP connection; one listening on no port is its socket."""
    tcp = restd.server_deployment(
        {"host": "127.0.0.1", "port": 3306}, None, restd.DEFAULT_METADATA_SCHEMA
    )
    through_socket = restd.server_deployment(
        {"socket": "/tmp/mysql.sock"}, 3306, restd.DEFAULT_METADATA_SCHEMA
    )
    assert through_socket == (
        restd.connection_deployment_dir("localhost", 3306),
        "localhost",
        3306,
        "/tmp/mysql.sock",
    )
    assert tcp[1:] == ("127.0.0.1", 3306, None)

    only_socket = restd.server_deployment(
        {"socket": "/tmp/mysql.sock"}, 0, "acme_rest"
    )
    other = restd.server_deployment(
        {"socket": "/var/run/mysqld/mysql.sock"}, 0, "acme_rest"
    )
    assert os.path.basename(only_socket[0]).startswith("socket_mysql.sock_")
    assert only_socket[0].endswith("_acme_rest")
    assert only_socket[0] != other[0]
    assert only_socket[1:] == ("localhost", None, "/tmp/mysql.sock")


def test_a_deployment_can_be_named_from_the_home_folder(fake_restd, home, monkeypatch):
    """~ is expanded for the check, and so for what the tool does too."""
    executable, _ = fake_restd
    listed = restd.connection_deployment_dir("db1", 3306)
    _set_up(executable, listed)
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    tilde = os.path.join("~", os.path.relpath(listed, str(home)))
    tools = _tools()

    try:
        assert asyncio.run(tools["rest.start_restd"](None, tilde))["status"] == "running"
        assert asyncio.run(tools["rest.stop_restd"](None, tilde))["status"] == "stopped"
    finally:
        restd.stop(listed)


# --- the HTTPS certificate -----------------------------------------------------

from mcp_plugin.lib import restd_certs


def test_one_certificate_is_made_for_every_daemon(home):
    """In web-certs, for localhost; kept while it is good, made anew near its end."""
    from cryptography import x509

    paths = restd_certs.ensure()

    assert os.path.dirname(paths["cert"]) == restd_certs.certs_dir()
    assert restd_certs.certs_dir() == os.path.join(restd.deployments_root(), "web-certs")
    cert = restd_certs._load(paths["cert"])
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["localhost"]
    assert [str(ip) for ip in names.get_values_for_type(x509.IPAddress)] == [
        "127.0.0.1", "::1"]
    assert cert.issuer == restd_certs._load(paths["ca"]).subject
    # macOS refuses longer ones.
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 825
    if os.name != "nt":
        assert os.stat(paths["key"]).st_mode & 0o077 == 0
    # The CA's own key is not kept.
    assert sorted(os.listdir(restd_certs.certs_dir())) == [
        "rootCA.crt", "server.crt", "server.key"]

    first = restd_certs.ca_fingerprint()
    restd_certs.ensure()
    assert restd_certs.ca_fingerprint() == first

    # Near its end, a new one.
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(restd_certs, "RENEW_DAYS", 10_000)
        restd_certs.ensure()
    assert restd_certs.ca_fingerprint() != first


def test_the_setup_is_given_the_certificate(fake_restd, home, tmp_path):
    executable, runs = fake_restd
    paths = restd_certs.ensure()

    restd.setup(executable, str(tmp_path / "d"),
                {"user": "root", "host": "127.0.0.1", "port": 3311}, "pw",
                helpers.find_free_port(), certificate=paths)

    (run,) = runs()
    assert f"--conf-set-option=http_server.ssl_cert={paths['cert']}" in run["argv"]
    assert f"--conf-set-option=http_server.ssl_key={paths['key']}" in run["argv"]


def _store(monkeypatch, platform, answers):
    """A trust store answering commands from a table; records them."""
    commands = []

    def run(command, timeout=60):
        commands.append(command)
        for prefix, answer in answers:
            if command[: len(prefix)] == prefix:
                return answer(commands) if callable(answer) else answer
        return 0, ""

    monkeypatch.setattr(restd_certs, "_platform", lambda: platform)
    monkeypatch.setattr(restd_certs, "_run", run)
    return commands


def test_macos_trusts_the_ca_in_the_login_keychain(home, monkeypatch):
    """add-trusted-cert, which brings up the system's authorization dialog."""
    added = []
    commands = _store(monkeypatch, "macos", [
        (["security", "verify-cert"], lambda _: (0, "") if added else (1, "")),
        (["security", "add-trusted-cert"], lambda _: added.append(1) or (0, "")),
    ])

    report = restd_certs.trust()

    assert report["changed"] is True
    (add,) = [c for c in commands if c[1] == "add-trusted-cert"]
    assert add[:4] == ["security", "add-trusted-cert", "-r", "trustRoot"]
    assert add[-1] == os.path.join(restd_certs.certs_dir(), "rootCA.crt")
    assert add[4:6] == ["-k", os.path.join(str(home), "Library", "Keychains",
                                           "login.keychain-db")]
    # Trusted already: nothing is asked again.
    commands.clear()
    assert restd_certs.trust()["changed"] is False
    assert all(c[1] != "add-trusted-cert" for c in commands)


def test_a_cancelled_dialog_is_reported(home, monkeypatch):
    """The old wizard took a cancelled dialog for success."""
    _store(monkeypatch, "macos", [
        (["security", "verify-cert"], (1, "")),
        (["security", "add-trusted-cert"], (1, "SecTrustSettingsSetTrustSettings: "
                                                "The authorization was canceled by the user.")),
    ])

    with pytest.raises(Exception, match="confirmation was cancelled"):
        restd_certs.trust()


def test_windows_trusts_the_ca_in_the_users_root_store(home, monkeypatch):
    restd_certs.ensure()
    fingerprint = restd_certs.ca_fingerprint()
    spaced = " ".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))
    added = []
    commands = _store(monkeypatch, "windows", [
        (["certutil.exe", "-verifystore"],
         lambda _: (0, f"Cert Hash(sha1): {spaced}\n") if added else (1, "")),
        (["certutil.exe", "-addstore"], lambda _: added.append(1) or (0, "")),
    ])

    assert restd_certs.trust()["changed"] is True
    (add,) = [c for c in commands if c[1] == "-addstore"]
    assert add[:6] == ["certutil.exe", "-addstore", "-user", "-f", "ROOT",
                       os.path.join(restd_certs.certs_dir(), "rootCA.crt")]


def test_the_trust_tool_asks_an_agent_first(home, monkeypatch, gui_mode):
    calls = []
    monkeypatch.setattr(restd_certs, "is_trusted", lambda: False)
    monkeypatch.setattr(restd_certs, "trust", lambda: calls.append(1) or {
        "trusted": True, "changed": True, "ca_path": "x"})
    tools = _tools()

    # In the extension, the box the user ticked is the consent.
    assert asyncio.run(tools["rest.trust_certificate"](None))["changed"] is True
    # An agent with no client to ask is refused.
    monkeypatch.setattr(general, "is_gui_mode", lambda: False)
    with pytest.raises(Exception, match="did not confirm"):
        asyncio.run(tools["rest.trust_certificate"](None))
    assert calls == [1]


def test_the_certificate_status_makes_nothing(home, monkeypatch):
    status = _tools()["rest.get_certificate_status"]

    assert status()["exists"] is False
    assert status()["trusted"] is False
    assert not os.path.exists(restd_certs.certs_dir())

    restd_certs.ensure()
    monkeypatch.setattr(restd_certs, "is_trusted", lambda: True)
    assert status() == {"exists": True, "trusted": True,
                        "ca_path": os.path.join(restd_certs.certs_dir(), "rootCA.crt")}


def test_a_server_has_one_daemon_per_metadata_schema(home, tmp_path):
    """The default schema's keeps the plain name; any other's gets a suffix."""
    assert restd.connection_deployment_dir("db1", 3306).endswith("db1_3306")
    assert restd.connection_deployment_dir(
        "db1", 3306, "acme_mariadb_rest_service").endswith(
        "db1_3306_acme_mariadb_rest_service")
    assert restd.sandbox_deployment_dir("/s", 3311, "acme_mariadb_rest_service") \
        == os.path.join("/s", "3311", "mariadb-restd_acme_mariadb_rest_service")


def test_every_daemon_of_a_sandbox_is_found(fake_restd, tmp_path):
    executable, _ = fake_restd
    sandboxes = str(tmp_path / "sandboxes")
    first = restd.sandbox_deployment_dir(sandboxes, 3311)
    second = restd.sandbox_deployment_dir(sandboxes, 3311, "acme_mariadb_rest_service")
    _set_up(executable, first)
    _set_up(executable, second)

    assert restd.sandbox_deployment_dirs(sandboxes, 3311) == sorted([first, second])
    assert set(restd.deployment_dirs(sandboxes)) >= {first, second}
