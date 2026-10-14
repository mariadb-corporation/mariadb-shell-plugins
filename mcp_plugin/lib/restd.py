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

"""MariaDB REST Daemon (mariadb-restd) deployments.

A deployment is the directory ``mariadb-restd --setup -d <dir>`` writes: the
configuration, a keyring, a TLS certificate, and the daemon's log and run
folders. This module finds the mariadb-restd executable, sets a deployment
up, starts and stops its daemon and lists the deployments it knows of. The
MCP tools are in :mod:`mcp_plugin.lib.rest_functions`, and the sandbox tools
use it to have a sandbox's daemon follow the sandbox.

Deployments live in two places:

* ``<sandbox>/mariadb-restd`` for a sandbox's daemon, so it goes when the
  sandbox goes;
* ``~/.mariadb-restd/<host>_<port>`` for any other server (on Windows
  ``%APPDATA%\\MariaDB\\mariadb-restd``, beside the shell's own folder).

Three things about mariadb-restd decide how it is driven here:

* ``--setup`` has no option for a password. It asks for what the URI does not
  carry on stdout and reads the answer from stdin, so the admin password is
  written to the setup's own stdin - never onto its command line, where any
  user of the machine could read it. Without ``--jwt-secret`` it would also
  ask for a JWT secret, and keep asking until it reads a non-empty one, so one
  is always generated and passed.
* The daemon does not detach. The ``start.sh`` the setup writes runs it with a
  plain ``&``, which leaves it the caller's terminal and output, and always
  exits 0 whether the daemon came up or not. It is started here as the
  shell's sandbox starts a server instead: in a session of its own, so it
  keeps running after the MCP server and VS Code are gone.
* It writes its pid file only once its plugins are loaded, and leaves it
  behind when it is killed, which would refuse every later start ("PID file
  ... found. Already running?"). A pid file whose process is gone is removed
  before a start.
"""

# cSpell:ignore mysqlsh MariaDB restd keyring setsid ROUTER

import configparser
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse
from typing import Optional

from mcp_plugin.lib import general
from mcp_plugin.lib.tool_registrar import tool_error

# The executable, as the packages name it.
RESTD_NAME = "mariadb-restd"

# The folder a sandbox's deployment is in, inside the sandbox's own folder.
SANDBOX_DEPLOYMENT_DIR = "mariadb-restd"

# What this plugin records about a deployment, beside what --setup wrote: the
# server it was set up for, which the configuration only has as an address,
# and whether it belongs to a sandbox.
DEPLOYMENT_FILE = "deployment.json"

# The files --setup writes and the daemon keeps, relative to a deployment.
CONFIG_FILE = "mariadb-restd.conf"
PID_FILE = os.path.join("run", "mariadb-restd.pid")
LOG_FILE = os.path.join("log", "mariadb-restd.log")
# What the daemon writes to its console, mostly errors from before its
# configuration is read and its own log is open.
CONSOLE_LOG_FILE = os.path.join("log", "console.log")

# The metadata schema --setup serves when none is named.
DEFAULT_METADATA_SCHEMA = "mariadb_rest_service"

# The HTTPS port --setup gives a deployment that is not a sandbox's, and the
# first one tried; the next free one is taken when it is in use.
DEFAULT_PORT = 8443

# A sandbox's daemon listens on ten times the sandbox's port (33110 for a
# sandbox on 3311), so the two are easy to relate - where that is a port.
SANDBOX_PORT_FACTOR = 10
MAX_PORT = 65535

# How long a setup, a start and a stop may take.
SETUP_TIMEOUT = 120
START_TIMEOUT = 30
STOP_TIMEOUT = 30

# How much of a log an error quotes.
_LOG_TAIL_LINES = 20

# Only accounts --setup generated are dropped with a deployment; an account
# the user named with --metadata-account is theirs.
_GENERATED_ACCOUNT = re.compile(r"^mariadb_restd_[A-Za-z0-9]+$")


# --------------------------------------------------------------------------- #
# Where things are
# --------------------------------------------------------------------------- #
def deployments_root() -> str:
    """Returns the folder the deployments for servers other than sandboxes are in.

    ``~/.mariadb-restd``, and on Windows ``%APPDATA%\\MariaDB\\mariadb-restd``,
    the folder the shell keeps its own configuration beside.

    Returns:
        The absolute path, whether or not it exists yet.
    """
    if os.name == "nt":
        app_data = os.environ.get("APPDATA", "") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming"
        )
        return os.path.join(app_data, "MariaDB", RESTD_NAME)

    return os.path.join(os.path.expanduser("~"), f".{RESTD_NAME}")


def connection_deployment_dir(host: str, port: int) -> str:
    """Returns the deployment folder for the server at host:port.

    Args:
        host (str): The server's host name or address.
        port (int): The server's port.

    Returns:
        ``<deployments root>/<host>_<port>``, with the characters a file name
        cannot hold (an IPv6 address's colons, say) replaced.
    """
    safe_host = re.sub(r"[^A-Za-z0-9._-]", "-", host) or "localhost"
    return os.path.join(deployments_root(), f"{safe_host}_{port}")


def sandbox_deployment_dir(sandbox_base_dir: str, sandbox_port: int) -> str:
    """Returns the deployment folder of a sandbox's daemon.

    Args:
        sandbox_base_dir (str): The folder the sandboxes are in.
        sandbox_port (int): The sandbox's port.

    Returns:
        ``<sandbox base>/<port>/mariadb-restd``.
    """
    return os.path.join(
        os.path.abspath(os.path.expanduser(sandbox_base_dir)),
        str(sandbox_port),
        SANDBOX_DEPLOYMENT_DIR,
    )


def is_deployment(directory: str) -> bool:
    """Returns whether a folder holds a mariadb-restd deployment."""
    return os.path.isfile(os.path.join(directory, CONFIG_FILE))


# --------------------------------------------------------------------------- #
# Finding the executable
# --------------------------------------------------------------------------- #
def install_prefix() -> str:
    """Returns the folder mariadb-restd's install script unpacks releases into.

    The same layout as the shell's install script: one folder per version,
    ``~/.local/share/mariadb-restd/<version>`` with links in ``~/.local/bin``,
    and ``%LOCALAPPDATA%\\Programs\\mariadb-restd\\<version>`` on Windows.
    ``MARIADB_RESTD_PREFIX`` moves it, as ``MARIADB_SHELL_PREFIX`` does the
    shell's.

    Returns:
        The absolute path, whether or not it exists yet.
    """
    override = os.environ.get("MARIADB_RESTD_PREFIX", "")
    if override:
        return override

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        return os.path.join(local_app_data, "Programs", RESTD_NAME)

    # Not general.get_data_home(): the install script ignores XDG_DATA_HOME,
    # and this has to find what it installed.
    return os.path.join(os.path.expanduser("~"), ".local", "share", RESTD_NAME)


def _binary_name() -> str:
    return f"{RESTD_NAME}.exe" if os.name == "nt" else RESTD_NAME


def _version_key(name: str) -> tuple:
    """Sorts version folder names numerically, anything else first."""
    parts = re.findall(r"\d+", name)
    return (1, tuple(int(part) for part in parts)) if parts else (0, ())


def _managed_binaries() -> list:
    """The executables of the installed releases, newest first."""
    prefix = install_prefix()
    try:
        names = os.listdir(prefix)
    except OSError:
        return []

    binaries = []
    for name in sorted(names, key=_version_key, reverse=True):
        candidate = os.path.join(prefix, name, "bin", _binary_name())
        if os.path.isfile(candidate):
            binaries.append(candidate)

    return binaries


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_restd(restd_path: Optional[str] = None) -> Optional[str]:
    """Finds the mariadb-restd executable.

    Looked for on the PATH, then in ``~/.local/bin``, which is where the
    install script links it but is not on every PATH, and then among the
    releases the install script unpacked. On Windows the install script's
    links are ``.cmd`` forwarders, which cannot be started detached the way
    the daemon has to be, so the real ``.exe`` is looked for there.

    A link is fine: mariadb-restd resolves its own path to find its plugins
    and libraries, and a link leads it to the real one.

    Args:
        restd_path (str): An executable to use instead, or a folder holding
            it. Not searched any further when given.

    Returns:
        The absolute path of the executable, or None if there is none.

    Raises:
        ToolError: If restd_path is given and holds no executable.
    """
    if restd_path:
        candidate = os.path.abspath(os.path.expanduser(restd_path))
        if os.path.isdir(candidate):
            for inner in (
                os.path.join(candidate, _binary_name()),
                os.path.join(candidate, "bin", _binary_name()),
            ):
                if _is_executable(inner):
                    return inner
        elif _is_executable(candidate):
            return candidate
        raise tool_error(f"There is no {RESTD_NAME} executable at '{restd_path}'.")

    found = shutil.which(_binary_name())
    if found:
        return os.path.abspath(found)

    if os.name != "nt":
        linked = os.path.join(general.get_bin_home(), RESTD_NAME)
        if _is_executable(linked):
            return linked

    binaries = _managed_binaries()
    return binaries[0] if binaries else None


def searched_places() -> str:
    """Says where find_restd looks, for the error that it found nothing."""
    places = ["the PATH"]
    if os.name != "nt":
        places.append(general.get_bin_home())
    places.append(install_prefix())
    return ", ".join(places)


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
def is_listening(port: int) -> bool:
    """Returns True if something accepts TCP connections on localhost:port."""
    try:
        with socket.create_connection(("localhost", port), timeout=1.0):
            return True
    except OSError:
        return False


def _can_bind(port: int) -> bool:
    """Returns True if a listening socket could be opened on every interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # On Windows SO_REUSEADDR would let the probe share a port another
        # program is listening on, so it is set on POSIX only, where it just
        # ignores the connections still in TIME_WAIT.
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False

    return True


def is_port_free(port: int, taken=()) -> bool:
    """Returns whether a daemon could be given the port.

    Args:
        port (int): The port.
        taken: Ports to count as used although nothing listens on them: those
            of the deployments whose daemons are stopped.

    Returns:
        True if it is a port, not in taken, and nothing uses it.
    """
    return (
        0 < port <= MAX_PORT
        and port not in taken
        and not is_listening(port)
        and _can_bind(port)
    )


def choose_port(sandbox_port: Optional[int] = None, taken=()) -> int:
    """Picks the port for a new deployment's daemon.

    A sandbox's daemon gets ten times the sandbox's port when that is a port
    and free; otherwise, and for any other server, the first free one from
    8443 up.

    Args:
        sandbox_port (int): The port of the sandbox the daemon is for, if any.
        taken: Ports to count as used; see :func:`is_port_free`.

    Returns:
        The port.

    Raises:
        ToolError: If no port from 8443 up is free.
    """
    if sandbox_port is not None:
        preferred = sandbox_port * SANDBOX_PORT_FACTOR
        if is_port_free(preferred, taken):
            return preferred

    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if is_port_free(port, taken):
            return port

    raise tool_error(f"No free port was found from {DEFAULT_PORT} up.")


# --------------------------------------------------------------------------- #
# A deployment's files
# --------------------------------------------------------------------------- #
def _read_config(directory: str) -> configparser.ConfigParser:
    """Reads a deployment's configuration, a router-style INI file."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(os.path.join(directory, CONFIG_FILE), encoding="utf-8")
    return parser


def read_deployment(directory: str) -> dict:
    """Reads what a deployment's files say about it.

    Args:
        directory (str): The deployment's folder.

    Returns:
        dict: ``directory``, ``port`` (the daemon's), ``url``,
        ``metadata_schema``, ``accounts`` (the server accounts the daemon
        uses), ``rest_daemon_id``, ``log_path``, and what deployment.json
        holds: ``host`` and ``server_port`` of the server, ``connection_uri``
        and ``sandbox_port`` (None when it is not a sandbox's).
    """
    config = _read_config(directory)
    port = config.getint("http_server", "port", fallback=DEFAULT_PORT)
    ssl = config.get("http_server", "ssl", fallback="1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    service = "mysql_rest_service"
    accounts = []
    for option in ("mysql_user", "mysql_user_data_access"):
        account = config.get(service, option, fallback="")
        if account and account not in accounts:
            accounts.append(account)

    logging_folder = config.get(
        "DEFAULT", "logging_folder", fallback=os.path.join(directory, "log")
    )

    recorded = {}
    try:
        with open(os.path.join(directory, DEPLOYMENT_FILE), encoding="utf-8") as file:
            recorded = json.load(file)
    except (OSError, ValueError):
        pass

    host, server_port = recorded.get("host"), recorded.get("server_port")
    if host is None:
        # Set up by hand: the configuration has the server's address.
        destination = config.get(service, "mysql_read_write_destinations", fallback="")
        first = destination.split(",")[0].strip()
        if first:
            host, _, port_text = first.rpartition(":")
            if not host:
                host, port_text = first, "3306"
            host = host.strip("[]")
            server_port = int(port_text) if port_text.isdigit() else None

    return {
        "directory": directory,
        "port": port,
        "url": f"{'https' if ssl else 'http'}://localhost:{port}",
        "metadata_schema": config.get(
            service, "metadata_schema", fallback=DEFAULT_METADATA_SCHEMA
        ),
        "accounts": accounts,
        "rest_daemon_id": config.get(service, "rest_daemon_id", fallback=None),
        "log_path": os.path.join(logging_folder, os.path.basename(LOG_FILE)),
        "host": host,
        "server_port": server_port,
        "connection_uri": recorded.get("connection_uri"),
        "sandbox_port": recorded.get("sandbox_port"),
    }


def _write_deployment_file(directory: str, record: dict) -> None:
    path = os.path.join(directory, DEPLOYMENT_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as file:
        json.dump(record, file, indent=2)
    os.replace(path + ".tmp", path)


def deployment_dirs(sandbox_base_dir: Optional[str] = None) -> list:
    """Lists the folders of every deployment this plugin knows of.

    Those of the sandboxes in sandbox_base_dir, and those in the deployments
    root, sorted by path.

    Args:
        sandbox_base_dir (str): The folder the sandboxes are in, or None to
            leave the sandboxes out.

    Returns:
        list: The deployment folders.
    """
    root = deployments_root()
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    found = [
        os.path.join(root, name)
        for name in names
        if is_deployment(os.path.join(root, name))
    ]

    if sandbox_base_dir:
        base = os.path.abspath(os.path.expanduser(sandbox_base_dir))
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        found += [
            sandbox_deployment_dir(base, int(name))
            for name in names
            if name.isdigit() and is_deployment(sandbox_deployment_dir(base, int(name)))
        ]

    return sorted(found)


def taken_ports(sandbox_base_dir: Optional[str] = None) -> set:
    """The daemon ports of every known deployment, running or not."""
    ports = set()
    for directory in deployment_dirs(sandbox_base_dir):
        try:
            ports.add(read_deployment(directory)["port"])
        except (configparser.Error, ValueError):
            continue
    return ports


# --------------------------------------------------------------------------- #
# The process
# --------------------------------------------------------------------------- #
def _read_pid(directory: str) -> Optional[int]:
    try:
        with open(os.path.join(directory, PID_FILE), encoding="utf-8") as file:
            return int(file.read().strip())
    except (OSError, ValueError):
        return None


def _is_alive(pid: int) -> bool:
    """Returns whether a process is still running."""
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)

    # A daemon this server started and that has exited is its zombie until it
    # is reaped, and a zombie still answers signal 0.
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def running_pid(directory: str) -> Optional[int]:
    """Returns the pid of a deployment's daemon if it is running, else None."""
    pid = _read_pid(directory)
    if pid is None or not _is_alive(pid):
        return None
    return pid


def _remove_pid_file(directory: str) -> None:
    try:
        os.remove(os.path.join(directory, PID_FILE))
    except FileNotFoundError:
        pass


def _tail(path: str, lines: int = _LOG_TAIL_LINES) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            return "".join(file.readlines()[-lines:]).strip()
    except OSError:
        return ""


def status(directory: str) -> dict:
    """Reports a deployment and whether its daemon is running.

    Args:
        directory (str): The deployment's folder.

    Returns:
        dict: What :func:`read_deployment` reports, without the accounts and
        the daemon's id, plus ``status`` ('running' or 'stopped') and ``pid``.
    """
    report = read_deployment(directory)
    del report["accounts"], report["rest_daemon_id"]
    pid = running_pid(directory)
    report["status"] = "running" if pid is not None else "stopped"
    report["pid"] = pid
    return report


def _spawn_detached(args: list, env: dict, console_path: str) -> subprocess.Popen:
    """Starts a process that outlives this one, its output going to a file.

    As the shell's sandbox starts a server: a session of its own on POSIX, so
    no signal for this process's group reaches it; on Windows a hidden console
    of its own (CREATE_NO_WINDOW rather than DETACHED_PROCESS, which would
    make a console program open a visible one).
    """
    os.makedirs(os.path.dirname(console_path), exist_ok=True)
    with open(console_path, "ab") as console:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": console,
            "stderr": subprocess.STDOUT,
            "env": env,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(args, **kwargs)


def start(restd: Optional[str], directory: str, timeout: float = START_TIMEOUT) -> dict:
    """Starts a deployment's daemon, unless it is running already.

    Started detached, so that it keeps running when the MCP server ends, and
    waited for until it accepts connections on its port.

    Args:
        restd (str): The mariadb-restd executable, or None to find it.
        directory (str): The deployment's folder.
        timeout (float): Seconds to wait for the daemon to listen.

    Returns:
        dict: The deployment's :func:`status`.

    Raises:
        ToolError: If the folder is no deployment, there is no executable, or
            the daemon exits or does not listen in time; the error quotes the
            end of its logs.
    """
    if not is_deployment(directory):
        raise tool_error(f"'{directory}' is no {RESTD_NAME} deployment.")

    if running_pid(directory) is not None:
        return status(directory)

    # Left by a daemon that was killed; mariadb-restd would refuse to start.
    _remove_pid_file(directory)

    if restd is None:
        restd = find_restd()
        if restd is None:
            raise tool_error(
                f"{RESTD_NAME} was not found in {searched_places()}."
            )

    deployment = read_deployment(directory)
    if is_listening(deployment["port"]):
        raise tool_error(
            f"Port {deployment['port']} is in use by another program, so the "
            f"REST Daemon of '{directory}' cannot listen on it."
        )

    os.makedirs(os.path.join(directory, "run"), exist_ok=True)
    env = dict(os.environ)
    # How the start script the setup writes says where the pid file goes.
    env["ROUTER_PID"] = os.path.join(directory, PID_FILE)

    console_path = os.path.join(directory, CONSOLE_LOG_FILE)
    process = _spawn_detached(
        [restd, "-c", os.path.join(directory, CONFIG_FILE)], env, console_path
    )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise tool_error(
                f"The REST Daemon of '{directory}' exited with code "
                f"{exit_code} at start.\n{_failure_log(directory, deployment)}"
            )
        if is_listening(deployment["port"]):
            return status(directory)
        time.sleep(0.2)

    # Neither up nor gone: it is not left half started.
    _terminate(process.pid, STOP_TIMEOUT)
    _remove_pid_file(directory)
    raise tool_error(
        f"The REST Daemon of '{directory}' did not listen on port "
        f"{deployment['port']} within {timeout:g} seconds.\n"
        f"{_failure_log(directory, deployment)}"
    )


def _failure_log(directory: str, deployment: dict) -> str:
    """The ends of a daemon's console output and log, for an error."""
    parts = []
    for path in (os.path.join(directory, CONSOLE_LOG_FILE), deployment["log_path"]):
        tail = _tail(path)
        if tail:
            parts.append(f"{path}:\n{tail}")
    return "\n".join(parts) or "It wrote nothing to its logs."


def _terminate(pid: int, timeout: float) -> None:
    """Asks a process to end, and kills it if it does not in time."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return
        time.sleep(0.1)

    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except (ProcessLookupError, OSError):
        return

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _is_alive(pid):
        time.sleep(0.1)


def stop(directory: str, timeout: float = STOP_TIMEOUT) -> dict:
    """Stops a deployment's daemon, unless it is stopped already.

    It is asked to end, waited for, and killed if it does not end in time.

    Args:
        directory (str): The deployment's folder.
        timeout (float): Seconds to wait for it to end before killing it.

    Returns:
        dict: The deployment's :func:`status`.

    Raises:
        ToolError: If the folder is no deployment.
    """
    if not is_deployment(directory):
        raise tool_error(f"'{directory}' is no {RESTD_NAME} deployment.")

    pid = running_pid(directory)
    if pid is not None:
        _terminate(pid, timeout)

    # It removes the file itself when it ends cleanly, but not when killed.
    _remove_pid_file(directory)
    return status(directory)


def delete_directory(directory: str) -> None:
    """Stops a deployment's daemon and deletes the deployment's folder.

    Args:
        directory (str): The deployment's folder.
    """
    if is_deployment(directory):
        stop(directory)

    # Windows can hold on to a file of a process that has only just ended.
    for attempt in range(5):
        try:
            shutil.rmtree(directory)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.5)


# --------------------------------------------------------------------------- #
# The setup
# --------------------------------------------------------------------------- #
def _setup_uri(user: str, host: str, port: int) -> str:
    """The account URI --setup takes, without a password."""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{urllib.parse.quote(user, safe='')}@{host}:{port}"


def setup(
    restd: str,
    directory: str,
    connection: dict,
    password: str,
    port: int,
    metadata_schema: str = DEFAULT_METADATA_SCHEMA,
    record: Optional[dict] = None,
    timeout: float = SETUP_TIMEOUT,
) -> str:
    """Runs ``mariadb-restd --setup`` into a folder.

    Args:
        restd (str): The mariadb-restd executable.
        directory (str): The deployment's folder; it must not exist yet, or
            be empty.
        connection (dict): The admin account's connection data, as
            ``shell.parse_uri`` gives it: ``user``, ``host``, ``port`` and
            perhaps ``socket``.
        password (str): The admin account's password, written to the
            setup's stdin.
        port (int): The port the daemon will listen on.
        metadata_schema (str): The REST metadata schema it will serve.
        record (dict): What to keep in the deployment's deployment.json.
        timeout (float): Seconds the setup may take.

    Returns:
        str: What the setup printed.

    Raises:
        ToolError: If the setup fails; the error quotes what it printed.
    """
    host = connection.get("host") or "localhost"
    command = [
        restd,
        "--setup",
        _setup_uri(connection.get("user") or "root", host, connection.get("port") or 3306),
        "--directory",
        directory,
        # Without it the setup asks for one, and keeps asking. Hex, and
        # attached with "=": a value that starts with "-" is taken for the
        # next option.
        f"--jwt-secret={secrets.token_hex(32)}",
        f"--conf-set-option=http_server.port={port}",
    ]
    if connection.get("socket"):
        command += ["--socket", connection["socket"]]
    if metadata_schema != DEFAULT_METADATA_SCHEMA:
        command += ["--metadata-schema", metadata_schema]

    # --setup creates the deployment's folder but not the one it is in, the
    # first time ~/.mariadb-restd itself.
    os.makedirs(os.path.dirname(os.path.abspath(directory)), exist_ok=True)

    try:
        completed = subprocess.run(
            command,
            input=f"{password}\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise tool_error(
            f"{RESTD_NAME} --setup did not finish within {timeout:g} seconds."
        ) from error
    except OSError as error:
        raise tool_error(f"{RESTD_NAME} could not be run: {error}") from error

    # The password prompt is part of stdout, and says nothing.
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0:
        raise tool_error(
            f"{RESTD_NAME} --setup failed (exit code {completed.returncode}).\n"
            f"{output}"
        )

    _write_deployment_file(directory, record or {})
    return output


def generated_accounts(deployment: dict) -> list:
    """The server accounts --setup created for a deployment, to drop with it."""
    return [
        account
        for account in deployment["accounts"]
        if _GENERATED_ACCOUNT.match(account)
    ]
