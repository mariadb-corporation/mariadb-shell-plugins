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

"""Tests for the restd group: REST Daemon deployments and their tools.

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

from mcp_plugin.lib import general, restd_functions, restd, restd_install, sandbox_functions
import mcp_plugin.tests.unit.helpers as helpers

_FAKE_RESTD = r'''
import json, os, signal, socket, sys, time
state = os.environ["FAKE_RESTD_STATE"]
mode = os.environ.get("FAKE_RESTD_MODE", "")
argv = sys.argv[1:]
if argv == ["--version"]:
    # As the real one prints it.
    version = os.environ.get("FAKE_RESTD_VERSION", "0.0.0")
    print(f"MariaDB REST Daemon  Ver {version} for Linux on x86_64 (Source distribution)")
    sys.exit(0)
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
    # As the real one: each --conf-set-option sets or adds an option.
    http_server = {"port": "", "ssl": "1"}
    for a in argv:
        if a.startswith("--conf-set-option=http_server."):
            option, _, value = a[len("--conf-set-option=http_server."):].partition("=")
            http_server[option] = value
    for folder in ("log", "run", "data"):
        os.makedirs(os.path.join(directory, folder))
    with open(os.path.join(directory, "mariadb-restd.conf"), "w") as file:
        file.write(
            f"[DEFAULT]\nlogging_folder={directory}/log\n[http_server]\n"
            + "".join(f"{option}={value}\n" for option, value in http_server.items())
            + "[mariadb_rest_service]\nmetadata_user=mariadb_restd_abc123\n"
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


_PROXY_VARIABLES = (
    "MARIADB_RESTD_PROXY_HOST",
    "MARIADB_RESTD_PROXY_PORTS",
    "MARIADB_RESTD_PROXY_PORT_OFFSET",
)


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch, tmp_path):
    """No TLS proxy, unless a test sets one: an IDE may run the suite with one,
    and a server may have the file that sets one for every user."""
    for name in _PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(restd, "PROXY_FILE", str(tmp_path / "proxy.conf"))


# The real one, before no_real_install points it elsewhere.
_REAL_INSTALL_SCRIPT_BASE_URL = restd_install.INSTALL_SCRIPT_BASE_URL


@pytest.fixture(autouse=True)
def no_real_install(request, monkeypatch, tmp_path):
    """No unit test downloads a real install script: one that has to install
    points INSTALL_SCRIPT_BASE_URL at scripts of its own, or stubs install().
    Without this, a fake mariadb-restd older than the default minimum made a
    setup install the newest release from GitHub. The e2e tests may."""
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setattr(
            restd_install, "INSTALL_SCRIPT_BASE_URL",
            (tmp_path / "no-real-install").as_uri() + "/",
        )


def _behind_a_proxy(monkeypatch, daemon_port, public_port=8443, host="wb.example.com"):
    """A TLS proxy serving public_port, whose daemon listens on daemon_port."""
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", host)
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", str(public_port))
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORT_OFFSET", str(daemon_port - public_port))


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
    # New enough for a setup that names no version, whatever the default is:
    # tools/update_version raises it with each release.
    monkeypatch.setenv("FAKE_RESTD_VERSION", restd.DEFAULT_MINIMUM_VERSION)

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


def test_mariadb_restd_is_looked_for_on_the_path_then_installed(
    home, tmp_path, monkeypatch
):
    """As the extension finds the shell; of the installed releases, the newest."""
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

    # A folder that names no version is not the install script's, newest
    # looking or not.
    executable(prefix / "zz-build" / "bin" / "mariadb-restd")
    executable(prefix / "bin" / "mariadb-restd")
    assert restd.find_restd() == newest

    # ~/.local/bin is not searched: the shell's locator does not search it,
    # and the link there leads into the prefix, which is.
    executable(home / ".local" / "bin" / "mariadb-restd")
    assert restd.find_restd() == newest

    on_path = executable(tmp_path / "path" / "mariadb-restd")
    monkeypatch.setenv("PATH", f"{tmp_path / 'path'}:/usr/bin:/bin")
    assert restd.find_restd() == on_path

    # An explicit one is not searched past, and must be there.
    assert restd.find_restd(str(prefix / "1.9.0")) == str(
        prefix / "1.9.0" / "bin" / "mariadb-restd"
    )
    with pytest.raises(Exception, match="no mariadb-restd executable"):
        restd.find_restd(str(tmp_path / "nowhere"))


def test_with_a_minimum_version_an_older_mariadb_restd_is_passed_over(
    home, tmp_path, monkeypatch
):
    """As the extension finds the shell: each place in turn, too old skipped."""
    if os.name == "nt":
        pytest.skip("POSIX layout")

    def executable(path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho 'MariaDB REST Daemon  Ver {version} for Linux'\n")
        path.chmod(0o755)
        return str(path)

    prefix = home / ".local" / "share" / "mariadb-restd"
    managed = executable(prefix / "26.10.1" / "bin" / "mariadb-restd", "26.10.1")
    on_path = executable(tmp_path / "path" / "mariadb-restd", "26.10.0")
    monkeypatch.setenv("PATH", f"{tmp_path / 'path'}:/usr/bin:/bin")

    assert restd.restd_version(on_path) == (26, 10, 0)
    assert restd.find_restd() == on_path
    assert restd.find_restd(minimum_version="26.10.0") == on_path
    assert restd.find_restd(minimum_version="26.10.1") == managed
    assert restd.find_restd(minimum_version="26.11.0") is None
    # An explicit one is used as it is.
    assert restd.find_restd(on_path, "26.11.0") == on_path


def test_the_version_is_read_as_the_extension_reads_the_shells(
    home, tmp_path, monkeypatch
):
    """Ver first, else any version; stderr too; always x.y.z."""
    if os.name == "nt":
        pytest.skip("POSIX layout")

    assert restd.parse_version_output(
        "MariaDB REST Daemon  Ver 26.10.1 for Darwin 25.6.0 on arm64"
    ) == (26, 10, 1)
    assert restd.parse_version_output("mariadb-restd ver v26.11.0") == (26, 11, 0)
    assert restd.parse_version_output("mariadb-restd 26.10.2") == (26, 10, 2)
    assert restd.parse_version_output("no version here") is None
    # A year and month alone is no mariadb-restd version, nor a folder of one.
    assert restd.parse_version("26.10") is None
    assert restd.parse_version_output("Ver 26.10") is None

    def executable(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return str(path)

    on_stderr = executable(
        tmp_path / "a" / "mariadb-restd", "echo 'MariaDB REST Daemon  Ver 26.10.1' >&2"
    )
    assert restd.restd_version(on_stderr) == (26, 10, 1)
    failing = executable(
        tmp_path / "b" / "mariadb-restd", "echo 'Ver 26.10.1'; echo broken >&2; exit 3"
    )
    log = []
    assert restd.restd_version(failing, log.append) is None
    assert "exit code 3 - broken" in log[0]


def test_the_search_says_what_it_passed_over_and_why(home, tmp_path, monkeypatch):
    """As the extension's locator logs its search for the shell."""
    if os.name == "nt":
        pytest.skip("POSIX layout")

    def executable(path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\necho 'MariaDB REST Daemon  Ver {version}'\n")
        path.chmod(0o755)
        return str(path)

    prefix = home / ".local" / "share" / "mariadb-restd"
    executable(tmp_path / "path" / "mariadb-restd", "26.9.0")
    monkeypatch.setenv("PATH", f"{tmp_path / 'path'}:/usr/bin:/bin")
    executable(prefix / "26.9.0" / "bin" / "mariadb-restd", "26.9.0")
    # Named new enough, but the binary says otherwise: what it reports wins.
    lying = executable(prefix / "26.12.0" / "bin" / "mariadb-restd", "26.9.0")
    (prefix / "26.11.0").mkdir(parents=True)
    (prefix / "notes").mkdir()

    log = []
    assert restd.find_restd(minimum_version="26.10.1", log=log.append) is None
    text = "\n".join(log)
    assert "on the PATH" in text and "is 26.9.0, older than the 26.10.1" in text
    assert f"Local installations in {prefix}: 26.11.0, 26.12.0, 26.9.0, notes" in text
    assert "26.9.0 is older than 26.10.1; not used." in text
    assert f"26.11.0 has no {prefix / '26.11.0' / 'bin' / 'mariadb-restd'}" in text
    assert f"{lying} is 26.9.0, older than the 26.10.1 required; not used." in text
    assert "notes" not in text.split("26.9.0, notes", 1)[1]


def test_the_windows_prefix_takes_localappdata_as_the_extension_does(monkeypatch):
    """Only one not set at all falls back to ~/AppData/Local."""
    monkeypatch.delenv("MARIADB_RESTD_PREFIX", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "")
    monkeypatch.setattr(restd.os, "name", "nt")
    try:
        empty = restd.install_prefix()
        monkeypatch.delenv("LOCALAPPDATA")
        unset = restd.install_prefix()
    finally:
        monkeypatch.undo()

    assert empty == os.path.join("", "Programs", "mariadb-restd")
    assert unset == os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "Programs", "mariadb-restd"
    )


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


def test_behind_a_proxy_a_daemon_listens_above_the_first_free_public_port(monkeypatch):
    """The proxy's public ports only, the daemon's own port checked."""
    used = {8443, 8444}  # the proxy itself listens on its public ports
    monkeypatch.setattr(restd, "is_listening", lambda port: port in used)
    monkeypatch.setattr(restd, "_can_bind", lambda port: port not in used)
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "wb.example.com")
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "8443-8445")

    assert restd.choose_port() == 18443
    used.add(18443)
    assert restd.choose_port() == 18444
    with pytest.raises(Exception, match="8443-8445 the TLS proxy serves is free"):
        restd.choose_port(taken={18444, 18445})

    assert restd.proxied_port(8445) == 18445
    with pytest.raises(Exception, match="not one of the ports 8443-8445"):
        restd.proxied_port(8446)
    assert restd.public_port(18444) == 8444
    assert restd.public_port(8444) is None


def test_the_proxy_settings_are_checked(monkeypatch):
    assert restd.proxy() is None

    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "wb.example.com")
    assert restd.proxy() == {
        "host": "wb.example.com", "first": 8443, "last": 8462, "offset": 10000
    }

    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "8443:8462")
    with pytest.raises(Exception, match="<first>-<last> or one port"):
        restd.proxy()
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "9000-8000")
    with pytest.raises(Exception, match="the first not above the last"):
        restd.proxy()
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "9000")
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORT_OFFSET", "60000")
    with pytest.raises(Exception, match="do not fit"):
        restd.proxy()
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORT_OFFSET", "ten")
    with pytest.raises(Exception, match="has to be a number"):
        restd.proxy()
    # Daemon ports among the public ones, where the proxy itself listens.
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "8443-8462")
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORT_OFFSET", "19")
    with pytest.raises(Exception, match="has to be above 19"):
        restd.proxy()
    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORT_OFFSET", "20")
    assert restd.proxy()["offset"] == 20
    # The host goes into an https URL as it is.
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "https://wb.example.com")
    with pytest.raises(Exception, match="has to be a host name"):
        restd.proxy()
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "[::1]")
    assert restd.proxy()["host"] == "[::1]"

    # A wrong setting is reported by a setup, not hidden in what is read
    # about the deployments there are (their status, a stop, a delete).
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "wb.example.com/")
    with pytest.raises(Exception, match="has to be a host name"):
        restd.proxy()
    assert restd.proxy(strict=False) is None
    assert restd.public_port(18443) is None


def test_the_proxy_can_be_set_for_every_user_in_a_file(monkeypatch):
    """For an MCP server started without the user's environment; the
    environment, when it names a host, wins whole."""
    with open(restd.PROXY_FILE, "w", encoding="utf-8") as file:
        file.write(
            "# set by the server's administrator\n"
            "MARIADB_RESTD_PROXY_HOST=rest.example.com\n"
            "MARIADB_RESTD_PROXY_PORTS=9443-9445\n"
            "UNRELATED=1\n"
        )
    assert restd.proxy() == {
        "host": "rest.example.com", "first": 9443, "last": 9445, "offset": 10000
    }

    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "other.example.com")
    assert restd.proxy() == {
        "host": "other.example.com", "first": 8443, "last": 8462, "offset": 10000
    }


def test_a_wrong_proxy_setting_does_not_hide_a_deployment(fake_restd, tmp_path, monkeypatch):
    """Its status still says what the daemon's files say, and it still stops."""
    executable, _ = fake_restd
    port = helpers.find_free_port()
    _behind_a_proxy(monkeypatch, port)
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory, port=port)

    monkeypatch.setenv("MARIADB_RESTD_PROXY_PORTS", "8443:8462")
    with pytest.raises(Exception, match="<first>-<last> or one port"):
        restd.choose_port()

    deployment = restd.read_deployment(directory)
    assert deployment["port"] == port
    assert deployment["public_port"] is None
    assert deployment["url"] == f"http://localhost:{port}"
    assert restd.status(directory)["status"] == "stopped"


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


def test_behind_a_proxy_a_daemon_serves_plain_http_on_the_loopback(fake_restd, tmp_path, monkeypatch):
    """And reports the proxy's address, where browsers find it."""
    executable, runs = fake_restd
    port = helpers.find_free_port()
    _behind_a_proxy(monkeypatch, port)
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory, port=port)

    (run,) = runs()
    assert "--conf-set-option=http_server.bind_address=127.0.0.1" in run["argv"]
    assert "--conf-set-option=http_server.ssl=0" in run["argv"]
    assert not any("ssl_cert" in argument for argument in run["argv"])

    deployment = restd.read_deployment(directory)
    assert deployment["port"] == port
    assert deployment["public_port"] == 8443
    assert deployment["url"] == "https://wb.example.com:8443"

    restd.start(executable, directory)
    try:
        assert restd.status(directory)["status"] == "running"
    finally:
        restd.stop(directory)


def test_a_daemon_set_up_before_the_proxy_keeps_its_own_address(fake_restd, tmp_path, monkeypatch):
    """It serves HTTPS itself, whatever port it has."""
    executable, _ = fake_restd
    port = helpers.find_free_port()
    directory = str(tmp_path / "deployment")
    _set_up(executable, directory, port=port)
    _behind_a_proxy(monkeypatch, port)

    deployment = restd.read_deployment(directory)
    assert deployment["public_port"] is None
    assert deployment["url"] == f"https://localhost:{port}"


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
    assert restd_functions._missing_privileges(grants, "mariadb_rest_service") == []


def test_an_account_granted_exactly_what_the_setup_needs_passes():
    grants = [
        "GRANT CREATE USER ON *.* TO `admin`@`%` WITH GRANT OPTION",
        "GRANT SELECT, INSERT, UPDATE ON `mariadb_rest_service`.* TO `admin`@`%`",
        "GRANT `mariadb_rest_service_meta_provider` TO `admin`@`%` WITH ADMIN OPTION",
        "GRANT `mariadb_rest_service_data_provider` TO `admin`@`%` WITH ADMIN OPTION",
    ]
    assert restd_functions._missing_privileges(grants, "mariadb_rest_service") == []


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
        assert restd_functions._missing_privileges(grants, "mariadb_rest_service") == [], target

    # Escaped, the characters are themselves: no wildcard for another name.
    grants = needed + [r"GRANT SELECT, INSERT, UPDATE ON `mariadbXrest\_service`.* TO `admin`@`%`"]
    assert "SELECT on mariadb_rest_service.*" in restd_functions._missing_privileges(
        grants, "mariadb_rest_service"
    )


def test_what_an_account_lacks_is_named():
    grants = [
        "GRANT USAGE ON *.* TO `dev`@`%`",
        "GRANT SELECT ON `mariadb_rest_service`.* TO `dev`@`%`",
        "GRANT `mariadb_rest_service_meta_provider` TO `dev`@`%`",
    ]
    assert restd_functions._missing_privileges(grants, "mariadb_rest_service") == [
        "CREATE USER on *.*",
        "the GRANT OPTION on *.*",
        "INSERT on mariadb_rest_service.*",
        "UPDATE on mariadb_rest_service.*",
        "the role mariadb_rest_service_meta_provider WITH ADMIN OPTION",
        "the role mariadb_rest_service_data_provider WITH ADMIN OPTION",
    ]


def test_the_roles_carry_the_metadata_schemas_prefix_and_postfix():
    assert restd_functions._role_names("acme_mariadb_rest_service_eu") == [
        "acme_mariadb_rest_service_meta_provider_eu",
        "acme_mariadb_rest_service_data_provider_eu",
    ]


# --- the tools -----------------------------------------------------------------


def _tools(function_groups=("db", "restd")):
    recorder = helpers.ToolRecorder()
    restd_functions.register_restd_tools(recorder, list(function_groups))
    return recorder.tools


def test_setting_up_needs_the_db_group():
    """Starting, stopping, listing and deleting do not."""
    always = {
        "restd.get_restd_status",
        "restd.start_restd",
        "restd.stop_restd",
        "restd.delete_restd_config",
        "restd.trust_certificate",
        "restd.get_certificate_status",
    }
    assert set(_tools(["restd"])) == always
    assert set(_tools()) == always | {"restd.configure_restd"}


def test_rest_is_a_default_group_but_not_a_multi_tenant_one():
    """It runs a daemon on this machine."""
    assert "restd" in general.DEFAULT_FUNCTION_GROUPS
    assert "restd" not in general.MULTI_TENANT_FUNCTION_GROUPS


def test_the_tools_are_served():
    names = set(helpers.list_tool_names(["db", "restd"]))
    assert "restd.configure_restd" in names
    assert "restd.get_restd_status" in names


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
    """restd.configure_restd against a fake connection and mariadb-restd.

    Yields a function taking the tool's arguments and the server's metadata
    schemas and grants, returning (result, statements, passwords).
    """
    executable, _ = fake_restd
    monkeypatch.setenv("PATH", f"{os.path.dirname(executable)}:/usr/bin:/bin")
    started = []

    def run(schemas=(), grants=_ROOT_GRANTS, **arguments):
        statements, passwords = [], []
        monkeypatch.setattr(
            restd_functions.db_functions,
            "connection_credentials",
            lambda connection_id, client: ("mariadb://root@127.0.0.1:3311", "stored"),
        )
        monkeypatch.setattr(
            restd_functions,
            "_parse_uri",
            lambda uri: {"scheme": "mariadb", "user": "root", "host": "127.0.0.1", "port": 3311},
        )

        def open_session(connection, password):
            passwords.append(password)
            return _Session(list(schemas), list(grants), statements)

        monkeypatch.setattr(restd_functions, "_open_session", open_session)
        arguments.setdefault("sandbox_dir", str(tmp_path / "sandboxes"))
        result = asyncio.run(_tools()["restd.configure_restd"](None, "c1", **arguments))
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


def test_behind_a_proxy_the_port_named_is_a_public_one(configure, home, monkeypatch):
    """And no certificate is made: the proxy serves the server's own."""
    port = helpers.find_free_port()
    _behind_a_proxy(monkeypatch, port)

    with pytest.raises(Exception, match="not one of the ports 8443-8443"):
        configure(schemas=["mariadb_rest_service"], port=port)

    result, _, _ = configure(schemas=["mariadb_rest_service"], port=8443)

    assert result["status"] == "running"
    assert result["port"] == port
    assert result["url"] == "https://wb.example.com:8443"
    assert not os.path.exists(restd_certs.certs_dir())


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

    def install(tag=None, **_):
        installs.append(tag)
        monkeypatch.setenv("PATH", f"{os.path.dirname(fake)}:/usr/bin:/bin")
        return "installed"

    monkeypatch.setattr(restd_functions.restd_install, "install", install)
    result, _, _ = configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())

    assert installs == [None]
    assert result["installed"] is True
    assert result["install_output"] == "installed"


def test_a_pinned_version_installs_that_release_tag(
    configure, home, monkeypatch, tmp_path
):
    """As the extension pins the shell: an older daemon is not used."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.10.0")
    installs = []

    def install(tag=None, **_):
        installs.append(tag)
        monkeypatch.setenv("FAKE_RESTD_VERSION", "26.10.1")
        return "installed"

    monkeypatch.setattr(restd_functions.restd_install, "install", install)
    port = helpers.find_free_port()

    result, _, _ = configure(
        schemas=["mariadb_rest_service"], port=port, restd_version="26.10.1"
    )
    assert installs == ["v26.10.1"]
    assert result["installed"] is True

    with pytest.raises(Exception, match="has to be <major>.<minor>.<patch>"):
        configure(schemas=["mariadb_rest_service"], restd_version="latest")


def test_without_a_version_an_older_daemon_gets_the_newest_release(
    configure, home, monkeypatch, tmp_path
):
    """As the shell's launchers: a default lowest version, the newest installed."""
    monkeypatch.delenv("MARIADB_RESTD_VERSION", raising=False)
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    installs = []

    def install(tag=None, **_):
        installs.append(tag)
        monkeypatch.setenv("FAKE_RESTD_VERSION", restd.DEFAULT_MINIMUM_VERSION)
        return "installed"

    monkeypatch.setattr(restd_functions.restd_install, "install", install)

    result, _, _ = configure(
        schemas=["mariadb_rest_service"], port=helpers.find_free_port()
    )
    assert installs == [None]
    assert result["installed"] is True
    assert "install_warning" not in result


def test_a_newest_release_below_the_lowest_version_is_used_with_a_warning(
    configure, home, monkeypatch, tmp_path
):
    """As the launchers: worth saying, not worth refusing over."""
    monkeypatch.setenv("MARIADB_RESTD_VERSION", "99.0.0")
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.10.1")
    installs = []
    monkeypatch.setattr(
        restd_functions.restd_install,
        "install",
        lambda tag=None, **_: installs.append(tag) or "installed",
    )

    result, _, _ = configure(
        schemas=["mariadb_rest_service"], port=helpers.find_free_port()
    )
    assert installs == [None]
    assert result["install_warning"] == (
        "The installed mariadb-restd 26.10.1 is below the required 99.0.0; "
        "using it anyway."
    )


def _failing_install(installs, then=None):
    """An install() that fails, after running then() - another install
    finishing meanwhile, say."""
    def install(tag=None, **_):
        installs.append(tag)
        if then is not None:
            then()
        raise Exception("mariadb-restd could not be installed: no network")
    return install


def test_a_failed_agent_install_uses_what_another_install_put_in_place(
    configure, home, monkeypatch, tmp_path
):
    """As the launchers: the version decides, not the installer's exit status."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    installs = []
    monkeypatch.setattr(
        restd_functions.restd_install, "install",
        _failing_install(installs, lambda: monkeypatch.setenv(
            "FAKE_RESTD_VERSION", restd.DEFAULT_MINIMUM_VERSION)),
    )

    result, _, _ = configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())

    assert installs == [None]
    assert "install failed (mariadb-restd could not be installed: no network)" in result["install_warning"]
    assert "another install put in place" in result["install_warning"]


def test_a_failed_agent_install_starts_the_old_daemon_with_a_warning(
    configure, home, monkeypatch, tmp_path
):
    """As the launchers: after a failed install, the only one there is."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    installs = []
    monkeypatch.setattr(restd_functions.restd_install, "install", _failing_install(installs))

    result, _, _ = configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())

    assert installs == [None]
    assert result["install_warning"].startswith(
        f"No mariadb-restd {restd.DEFAULT_MINIMUM_VERSION} or newer could be installed "
        "(mariadb-restd could not be installed: no network); using the 26.9.0 one at"
    )
    assert "no network" in result["install_output"]


def test_a_failed_agent_install_with_no_daemon_at_all_fails(configure, home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    installs = []
    monkeypatch.setattr(restd_functions.restd_install, "install", _failing_install(installs))

    with pytest.raises(Exception, match="no network"):
        configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())
    assert installs == [None]


def test_a_failed_pinned_install_does_not_fall_back(configure, home, monkeypatch):
    """The VS Code extension's pin stays strict, as it is for the shell."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    installs = []
    monkeypatch.setattr(restd_functions.restd_install, "install", _failing_install(installs))

    with pytest.raises(Exception, match="no network"):
        configure(schemas=["mariadb_rest_service"], restd_version="26.11.0")
    assert installs == ["v26.11.0"]


def test_an_agent_install_looks_again_once_it_has_its_turn(
    configure, home, monkeypatch, tmp_path
):
    """As the launchers: the install it waited for usually put one in place."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    installs = []
    monkeypatch.setattr(
        restd_functions.restd_install, "install",
        lambda tag=None, **_: installs.append(tag) or "installed",
    )
    take = restd_install.InstallTurn.take

    def take_after_another_install(turn, *args, **kwargs):
        monkeypatch.setenv("FAKE_RESTD_VERSION", restd.DEFAULT_MINIMUM_VERSION)
        return take(turn, *args, **kwargs)

    monkeypatch.setattr(restd_install.InstallTurn, "take", take_after_another_install)

    result, _, _ = configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())

    assert installs == []
    assert "installed meanwhile" in result["install_output"]
    assert "install_warning" not in result


def test_an_environment_tag_pins_an_agent_install(configure, home, monkeypatch):
    """As the Hermes launchers pin the shell: MARIADB_RESTD_TAG is installed."""
    monkeypatch.setenv("FAKE_RESTD_VERSION", "26.9.0")
    monkeypatch.setenv("MARIADB_RESTD_TAG", "v26.11.0")
    installs = []

    def install(tag=None, **_):
        installs.append(tag)
        monkeypatch.setenv("FAKE_RESTD_VERSION", "26.11.0")
        return "installed"

    monkeypatch.setattr(restd_functions.restd_install, "install", install)
    configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())
    assert installs == ["v26.11.0"]

    monkeypatch.setenv("MARIADB_RESTD_TAG", "latest")
    with pytest.raises(Exception, match="MARIADB_RESTD_TAG has to be"):
        configure(schemas=["mariadb_rest_service"], port=helpers.find_free_port())


def test_an_agent_is_asked_before_anything_is_installed(configure, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(general, "is_gui_mode", lambda: False)
    monkeypatch.setattr(
        restd_functions.restd_install,
        "install",
        lambda tag=None: pytest.fail("installed without asking"),
    )

    # With no client to ask, the answer is no. No sandbox_dir: an agent would
    # be asked to trust that path first.
    with pytest.raises(Exception, match="was not installed") as raised:
        configure(schemas=["mariadb_rest_service"], sandbox_dir=None)
    # With what the search looked at, as the extension logs it for the shell.
    assert "Looking for mariadb-restd on the PATH" in str(raised.value)


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

    monkeypatch.setattr(restd_functions.db_functions, "use_session", _Use)
    tools = _tools()

    # Not without a connection to drop the account with.
    with pytest.raises(Exception, match="connection_id"):
        asyncio.run(tools["restd.delete_restd_config"](None, directory))
    assert os.path.isdir(directory)

    asyncio.run(tools["restd.delete_restd_config"](None, directory, connection_id="c1"))

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
            for entry in _tools()["restd.get_restd_status"]()
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
    recorder_server = helpers.ToolRecorder()
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

    marker = tmp_path / "ran"
    # Each release's script at its own tag, as the extension's for the shell.
    for ref in ("v0", "v1"):
        (tmp_path / ref).mkdir()
        (tmp_path / ref / "install.sh").write_text(
            f'echo "==> Installing $MARIADB_RESTD_TAG with the {ref} script"\n'
            f'touch "{marker}"\n'
            'if [ "$MARIADB_RESTD_TAG" = v0 ]; then echo "install.sh: no release v0" >&2; exit 1; fi\n'
        )
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_BASE_URL", tmp_path.as_uri() + "/")

    assert "Installing v1 with the v1 script" in restd_install.install("v1")
    assert marker.exists()
    with pytest.raises(Exception, match="install.sh: no release v0"):
        restd_install.install("v0")


def test_the_install_falls_back_to_a_prerelease_as_the_shell_does(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("install.sh")

    # Only prereleases are published: a run without --pre-release fails. The
    # newest is installed with main's script, a pinned one with its tag's.
    for ref in ("main", "v1"):
        (tmp_path / ref).mkdir()
        (tmp_path / ref / "install.sh").write_text(
            'echo "args:[$*] tag:[$MARIADB_RESTD_TAG] env:[$MARIADB_RESTD_PRERELEASE]"\n'
            'if [ -z "$*" ] && [ -z "$MARIADB_RESTD_TAG" ]; then\n'
            '  echo "install.sh: could not download SHA256SUMS" >&2; exit 1\n'
            "fi\n"
        )
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_BASE_URL", tmp_path.as_uri() + "/")

    monkeypatch.delenv("MARIADB_RESTD_PRERELEASE", raising=False)
    output = restd_install.install()
    assert "args:[] tag:[]" in output
    assert "args:[--pre-release] tag:[]" in output

    assert "args:[] tag:[v1]" in restd_install.install("v1")

    monkeypatch.setenv("MARIADB_RESTD_PRERELEASE", "1")
    output = restd_install.install()
    assert "args:[--pre-release] tag:[] env:[]" in output
    assert "args:[] tag:[]" not in output

    monkeypatch.setenv("MARIADB_RESTD_PRERELEASE", "0")
    with pytest.raises(Exception, match="could not download SHA256SUMS"):
        restd_install.install()


def test_on_windows_the_prerelease_is_asked_for_through_the_environment(monkeypatch):
    """As the shell's .cmd launcher does: MARIADB_RESTD_PRERELEASE=1."""
    runs = []

    class _Completed:
        def __init__(self, returncode):
            self.returncode, self.stdout, self.stderr = returncode, "", ""

    def run(command, env, on_start=None):
        runs.append((command, env.get("MARIADB_RESTD_PRERELEASE")))
        return _Completed(1 if len(runs) == 1 else 0)

    monkeypatch.setattr(restd_install, "_download", lambda url, pinned: b"")
    monkeypatch.setattr(restd_install, "_run", run)
    monkeypatch.delenv("MARIADB_RESTD_PRERELEASE", raising=False)
    monkeypatch.setattr(restd_install.os, "name", "nt")
    try:
        restd_install.install()
    finally:
        monkeypatch.undo()

    assert [env for _, env in runs] == [None, "1"]
    assert runs[0][0] == runs[1][0]
    assert not any("PreRelease" in part for part in runs[1][0])


def test_the_install_script_is_the_pinned_releases_own_as_the_shells_is(monkeypatch):
    """At the release's tag, as the extension's installScriptUrl; else on main."""
    base = "https://github.com/mariadb-corporation/rest-daemon/raw/"
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_BASE_URL", _REAL_INSTALL_SCRIPT_BASE_URL)
    monkeypatch.setattr(restd_install.os, "name", "posix")
    try:
        posix = (restd_install.script_url("v26.10.1"), restd_install.script_url())
        monkeypatch.setattr(restd_install.os, "name", "nt")
        windows = restd_install.script_url("v26.10.1")
    finally:
        monkeypatch.undo()

    assert posix == (base + "v26.10.1/install.sh", base + "main/install.sh")
    assert windows == base + "v26.10.1/install.ps1"


def test_install_sh_is_run_with_posix_sh_as_the_shells_is(monkeypatch):
    runs = []

    class _Completed:
        returncode, stdout, stderr = 0, "", ""

    def run(command, env, on_start=None):
        runs.append(command)
        return _Completed()

    monkeypatch.setattr(restd_install, "_download", lambda url, pinned: b"")
    monkeypatch.setattr(restd_install, "_run", run)
    monkeypatch.setattr(restd_install.os, "name", "posix")
    try:
        restd_install.install("v1")
    finally:
        monkeypatch.undo()

    assert runs[0][0] == "/bin/sh"
    assert runs[0][1].endswith("install.sh")


def test_install_ps1_is_run_remote_signed_as_the_shells_is(monkeypatch):
    """The extension's buildInstallCommand: RemoteSigned, never Bypass."""
    runs = []

    class _Completed:
        returncode, stdout, stderr = 0, "", ""

    def run(command, env, on_start=None):
        runs.append(command)
        return _Completed()

    monkeypatch.setattr(restd_install, "_download", lambda url, pinned: b"")
    monkeypatch.setattr(restd_install, "_run", run)
    monkeypatch.setattr(restd_install.os, "name", "nt")
    try:
        restd_install.install("v1")
    finally:
        monkeypatch.undo()

    assert runs[0][:-1] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-File",
    ]
    assert runs[0][-1].endswith("install.ps1")


def test_the_install_is_logged_as_the_shells_is(tmp_path, monkeypatch):
    """The extension's log of the shell's install: URL, command, lines, exit."""
    if os.name == "nt":
        pytest.skip("install.sh")

    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "install.sh").write_text(
        'echo "==> Downloading"\n'
        "printf '###   10.0%%\\r######  60.0%%\\r' >&2\n"
        "printf '\\n' >&2\n"
        'echo "==> Installed"\n'
    )
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_BASE_URL", tmp_path.as_uri() + "/")

    log = restd_install.install("v1").split("\n")

    url = (tmp_path / "v1" / "install.sh").as_uri()
    assert log[0] == f"Installing mariadb-restd 1 with {url}."
    assert log[1].startswith("Running the installer: MARIADB_RESTD_TAG=v1 /bin/sh ")
    assert log[2:4] == ["  installer: ==> Downloading", "  installer: ==> Installed"]
    assert re.fullmatch(r"The installer exited with code 0 after \d+\.\ds\.", log[4])
    assert len(log) == 5


def test_a_failed_install_gives_its_reason_as_the_shells_does(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("install.sh")

    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "install.sh").write_text(
        'echo "==> Downloading"\n'
        'echo "curl: (6) Could not resolve host: github.com" >&2\n'
        'echo "install.sh: could not download the package" >&2\n'
        'echo "  from the v1 release" >&2\n'
        "exit 3\n"
    )
    monkeypatch.setattr(restd_install, "INSTALL_SCRIPT_BASE_URL", tmp_path.as_uri() + "/")

    with pytest.raises(Exception) as raised:
        restd_install.install("v1")

    message = str(raised.value)
    assert message.startswith(
        "mariadb-restd 1 could not be installed: could not download the package "
        "from the v1 release (curl: (6) Could not resolve host: github.com) "
        "(the installer exited with code 3).\n\n"
    )
    assert "  installer: install.sh: could not download the package" in message
    assert "The installer exited with code 3 after" in message


@pytest.mark.parametrize(
    "line, kept",
    [
        ("==> Downloading", "==> Downloading"),
        ("  plain text  ", "plain text"),
        ("###   10.0%\r######  60.0%\r######## 100.0%", None),
        ("#=#=#\rdone", "done"),
        ("   ", None),
        ("-=O=-", None),
    ],
)
def test_curl_progress_bars_are_left_out_of_the_log(line, kept):
    assert restd_install.clean_line(line) == kept


@pytest.mark.parametrize(
    "lines, reason",
    [
        (["install.ps1: no package for windows / arm64."], "no package for windows / arm64."),
        # Wrapped onto indented lines until the sentence ends, two at most.
        (
            ["install.sh: could not replace /x: a program", "  is running from it.", "  more"],
            "could not replace /x: a program is running from it.",
        ),
        (["install.sh: done:", "  not joined"], "done:"),
        (["curl: (22) The requested URL returned error: 404"],
         "curl: (22) The requested URL returned error: 404"),
        (["noise", "Access is denied.", "more noise"], "Access is denied."),
        (["nothing to see"], None),
    ],
)
def test_the_failure_reason_is_picked_as_the_shells_is(lines, reason):
    assert restd_install.summarize_failure(lines) == reason


def test_a_pinned_install_script_that_cannot_be_downloaded_names_only_the_cause(
    tmp_path, monkeypatch
):
    """The extension's user passes no restd_path: no advice to."""
    monkeypatch.setattr(
        restd_install, "INSTALL_SCRIPT_BASE_URL", (tmp_path / "missing").as_uri() + "/"
    )
    with pytest.raises(Exception, match="could not be downloaded from") as raised:
        restd_install.install("v1")
    assert "restd_path" not in str(raised.value)


def test_committed_checksums_go_only_with_their_own_tag(monkeypatch):
    """The Hermes launchers export MARIADB_RESTD_SHA256SUMS for MARIADB_RESTD_TAG."""
    seen = []

    class _Completed:
        returncode, stdout, stderr = 0, "", ""

    def run(command, env, on_start=None):
        seen.append(env.get("MARIADB_RESTD_SHA256SUMS"))
        return _Completed()

    monkeypatch.setattr(restd_install, "_download", lambda url, pinned: b"")
    monkeypatch.setattr(restd_install, "_run", run)
    monkeypatch.setenv("MARIADB_RESTD_TAG", "v26.11.0")
    monkeypatch.setenv("MARIADB_RESTD_SHA256SUMS", "/plugin/mariadb-restd-v26.11.0.SHA256SUMS")
    monkeypatch.setenv("MARIADB_RESTD_PRERELEASE", "0")

    restd_install.install("v26.11.0")
    restd_install.install("v27.1.0")
    restd_install.install()

    assert seen == ["/plugin/mariadb-restd-v26.11.0.SHA256SUMS", None, None]


@pytest.fixture
def turn_prefix(tmp_path):
    if os.name == "nt":
        pytest.skip("installs take turns on POSIX only, as the launchers")
    return tmp_path / "prefix"


def _sleeper():
    import subprocess
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def test_an_install_takes_its_turn_and_gives_it_back(turn_prefix):
    turn = restd_install.InstallTurn(str(turn_prefix), lambda message: None)
    assert turn.take() is True
    assert (turn_prefix / restd_install.TURN_LOCK / "pid").read_text().split() == [str(os.getpid())]
    turn.release()
    assert not (turn_prefix / restd_install.TURN_LOCK).exists()


def test_an_install_waits_for_another_and_gives_up_after_the_lock_wait(turn_prefix, monkeypatch):
    other = _sleeper()
    try:
        for lock in (restd_install.TURN_LOCK, restd_install.INSTALL_LOCK):
            (turn_prefix / lock).mkdir(parents=True)
            (turn_prefix / lock / "pid").write_text(f"{other.pid}\n")
            notes = []
            monkeypatch.setenv("MARIADB_RESTD_LOCK_WAIT", "2")
            turn = restd_install.InstallTurn(str(turn_prefix), notes.append)
            assert turn.take(sleep=lambda _: time.sleep(1.05)) is False
            assert notes[0].startswith("waiting for ") and "up to 2s" in notes[0]
            assert notes[-1].startswith("gave up after 2s waiting for ")
            assert (turn_prefix / lock).exists()
            (turn_prefix / lock / "pid").unlink()
            (turn_prefix / lock).rmdir()
    finally:
        other.kill()
        other.wait()


def test_a_lock_whose_install_is_gone_is_cleared(turn_prefix):
    gone = _sleeper()
    gone.kill()
    gone.wait()
    (turn_prefix / restd_install.TURN_LOCK).mkdir(parents=True)
    (turn_prefix / restd_install.TURN_LOCK / "pid").write_text(f"{gone.pid}\n")
    notes = []
    turn = restd_install.InstallTurn(str(turn_prefix), notes.append)
    assert turn.take() is True
    assert any("clearing a lock left by an install that is gone" in note for note in notes)
    turn.release()


def test_the_lock_is_left_to_an_installer_that_outlives_the_server(turn_prefix):
    installer = _sleeper()
    try:
        turn = restd_install.InstallTurn(str(turn_prefix), lambda message: None)
        assert turn.take() is True
        turn.add_installer(installer.pid)
        turn.release()
        lock = turn_prefix / restd_install.TURN_LOCK
        assert lock.exists()
        # The installer's alone: this server goes on running.
        assert (lock / "pid").read_text().split() == [str(installer.pid)]
    finally:
        installer.kill()
        installer.wait()
    # Its installer gone, the next install clears it.
    turn = restd_install.InstallTurn(str(turn_prefix), lambda message: None)
    assert turn.take() is True
    turn.release()


def test_an_install_script_that_cannot_be_downloaded_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(
        restd_install, "INSTALL_SCRIPT_BASE_URL", (tmp_path / "missing").as_uri() + "/"
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

    Needs a mariadb-restd where restd.configure_restd looks for one, and a
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
        async with helpers.mcp_session(["db", "sandbox", "restd"], timeout=300) as call:
            deployed = await call("sandbox.deploy", {"port": port, "password": "Sandbox1!"})
            assert not deployed.is_error, helpers.tool_payload(deployed)
            try:
                connected = await call(
                    "db.connect", {"uri": f"mariadb://root@127.0.0.1:{port}"}
                )
                connection_id = helpers.tool_payload(connected)
                configured = await call(
                    "restd.configure_restd",
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

                listed = helpers.tool_payload(await call("restd.get_restd_status"))
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
            ["db", "sandbox", "restd"], timeout=300, gui=True
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
                    "restd.configure_restd", {"connection_id": connection_id}
                )
                assert not configured.is_error, helpers.tool_payload(configured)
                daemon = helpers.tool_payload(configured)
                assert daemon["directory"] == directory
                assert (daemon["host"], daemon["server_port"], daemon["socket"]) == (
                    "localhost", port, socket_path
                )
                assert daemon["status"] == "running"

                deleted = await call("restd.delete_restd_config", {
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

    monkeypatch.setattr(restd_functions.general, "require_allowed_path", require)
    tools = _tools()

    assert asyncio.run(tools["restd.stop_restd"](None, listed))["status"] == "stopped"
    with pytest.raises(Exception, match="not allowed"):
        asyncio.run(tools["restd.stop_restd"](None, elsewhere))
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
        assert asyncio.run(tools["restd.start_restd"](None, tilde))["status"] == "running"
        assert asyncio.run(tools["restd.stop_restd"](None, tilde))["status"] == "stopped"
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
    assert asyncio.run(tools["restd.trust_certificate"](None))["changed"] is True
    # An agent with no client to ask is refused.
    monkeypatch.setattr(general, "is_gui_mode", lambda: False)
    with pytest.raises(Exception, match="did not confirm"):
        asyncio.run(tools["restd.trust_certificate"](None))
    assert calls == [1]


def test_the_certificate_status_makes_nothing(home, monkeypatch):
    status = _tools()["restd.get_certificate_status"]

    assert status()["exists"] is False
    assert status()["trusted"] is False
    assert not os.path.exists(restd_certs.certs_dir())

    restd_certs.ensure()
    monkeypatch.setattr(restd_certs, "is_trusted", lambda: True)
    assert status() == {"exists": True, "trusted": True,
                        "ca_path": os.path.join(restd_certs.certs_dir(), "rootCA.crt"),
                        "proxied": False}


def test_behind_a_proxy_there_is_no_certificate_to_trust(home, monkeypatch, gui_mode):
    """The proxy serves the server's own; the local CA is neither made nor added."""
    monkeypatch.setenv("MARIADB_RESTD_PROXY_HOST", "wb.example.com")
    monkeypatch.setattr(restd_certs, "trust", lambda: pytest.fail("trusted a CA"))
    tools = _tools()

    assert tools["restd.get_certificate_status"]() == {
        "exists": False, "trusted": True, "ca_path": None, "proxied": True
    }
    assert asyncio.run(tools["restd.trust_certificate"](None)) == {
        "trusted": True, "changed": False, "ca_path": None
    }
    assert not os.path.exists(restd_certs.certs_dir())


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
