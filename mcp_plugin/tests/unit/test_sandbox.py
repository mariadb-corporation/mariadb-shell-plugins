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

"""Sandbox lifecycle tests for the MariaDB MCP server, driven over stdio.

The lifecycle is split into two parts ordered around the rest of the suite by
the ``pytest_collection_modifyitems`` hook in conftest.py:

* ``test_sandbox_deploy`` runs first and deploys the shared sandbox instance.
* All other tests run in between and use that shared sandbox (see the
  ``sandbox`` fixture and ``test_db_sql.py``).
* ``test_sandbox_shutdown`` runs last and stops and deletes the sandbox,
  verifying the teardown.

The db and shutdown tests skip when ``sandbox.deployed`` is False (i.e. the
deployment failed). All are skipped if no MariaDB/MySQL server binary is on the
PATH.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver mariadbd

import asyncio
import os
import socket
from types import SimpleNamespace

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

from mcp_plugin.lib import config, sandbox_functions
import mcp_plugin.tests.unit.helpers as helpers

# A deploy initializes a data directory and starts the server, so it needs a
# far more generous timeout than a normal tool call.
DEPLOY_TIMEOUT = 300


def _sandbox_call(tool_name, arguments, timeout=None):
    """Calls a sandbox tool over stdio and returns the CallToolResult."""
    return helpers.call_tool(
        function_groups=["sandbox"],
        tool_name=tool_name,
        arguments=arguments,
        timeout=timeout,
    )


def test_sandbox_deploy(sandbox):
    """Part 1: deploys the shared sandbox and verifies it is up. Runs first."""
    # TLS is disabled so the deploy does not depend on a working openssl for
    # certificate generation; a sandbox is for local testing only.
    deploy_result = _sandbox_call(
        "sandbox.deploy",
        {
            "port": sandbox.port,
            "sandbox_dir": sandbox.sandbox_dir,
            "password": sandbox.password,
            "ssl": False,
        },
        timeout=DEPLOY_TIMEOUT,
    )
    assert deploy_result.is_error is False
    # Mark the sandbox as deployed so dependent tests run (and are torn down).
    sandbox.deployed = True
    assert os.path.isdir(sandbox.instance_dir)

    # Deploy registers the instance as a configured connection, so it now shows
    # up in db.list_connections.
    listed = helpers.tool_payload(
        helpers.call_tool(function_groups=["db"], tool_name="db.list_connections")
    )
    assert sandbox.uri in listed

    # Filed in the Sandboxes folder, which the list above does not show: an
    # agent sees a plain URI, and only the extension sees the folder.
    stored = config.resolve_connection_uri(sandbox.uri)
    assert config.get_connection_path(stored) == config.SANDBOX_CONNECTION_PATH

    # The deployed instance reports a vendor and a version.
    vendor = helpers.tool_payload(
        _sandbox_call(
            "sandbox.vendor",
            {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
        )
    )
    assert vendor in ("MariaDB", "MySQL")

    server_version = helpers.tool_payload(
        _sandbox_call(
            "sandbox.version",
            {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
        )
    )
    assert isinstance(server_version, str) and server_version != ""


def test_sandbox_shutdown(sandbox):
    """Part 2: stops and deletes the shared sandbox. Runs last."""
    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    stop_result = _sandbox_call(
        "sandbox.stop",
        {
            "port": sandbox.port,
            "sandbox_dir": sandbox.sandbox_dir,
            "password": sandbox.password,
        },
    )
    assert stop_result.is_error is False

    delete_result = _sandbox_call(
        "sandbox.delete",
        {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
    )
    assert delete_result.is_error is False

    # After a successful delete the instance directory is gone.
    assert not os.path.isdir(sandbox.instance_dir)

    # Delete also removes the connection that deploy registered. An empty
    # connection list yields no content blocks, i.e. a None payload.
    listed = helpers.tool_payload(
        helpers.call_tool(function_groups=["db"], tool_name="db.list_connections")
    ) or []
    assert sandbox.uri not in listed


def test_sandbox_dir_outside_allowed_paths_is_rejected(allowed_temp_dir, tmp_path):
    """A sandbox_dir outside the allowed paths must be rejected by the server."""
    disallowed_dir = str(tmp_path / "not_allowed")
    os.makedirs(disallowed_dir, exist_ok=True)

    result = _sandbox_call(
        "sandbox.vendor",
        {"port": helpers.find_free_port(), "sandbox_dir": disallowed_dir},
    )

    # The path guard raises, which the MCP server reports as a tool error.
    assert result.is_error is True
    payload = helpers.tool_payload(result)
    assert isinstance(payload, str) and "not allowed" in payload


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _list_instances_tool(monkeypatch, sandbox_dir, versions=None):
    """Registers the sandbox tools against stand-ins; returns list_instances.

    Driven in-process: what is checked is how the default sandbox path is
    read, and a real deploy per case would cost minutes to lay out a few
    directories. The shell's ``sandbox`` and ``shell`` globals are the seams.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        sandbox_dir: What the shell's ``sandboxDir`` option should answer.
        versions: The version sandbox.version reports per port; a port not
            in it reports None.

    Returns:
        A ``(list_instances, asked)`` pair: the tool function, and the list
        every call to sandbox.version is recorded in as ``(port, options)``.
    """
    import mysqlsh.globals

    versions = versions or {}
    asked = []

    def version(port, options):
        asked.append((port, options))
        return versions.get(port)

    monkeypatch.setattr(
        mysqlsh.globals, "sandbox", SimpleNamespace(version=version), raising=False
    )
    monkeypatch.setattr(
        mysqlsh.globals,
        "shell",
        SimpleNamespace(options={"sandboxDir": sandbox_dir}),
        raising=False,
    )

    recorder = _ToolRecorder()
    sandbox_functions.register_sandbox_tools(recorder)
    return recorder.tools["sandbox.list_instances"], asked


def _make_instance(base_dir, port):
    """Lays out what the shell leaves behind for a deployed instance."""
    instance_dir = os.path.join(str(base_dir), str(port))
    os.makedirs(instance_dir)
    with open(os.path.join(instance_dir, "my.cnf"), "w") as f:
        f.write("[mysqld]\n")


def test_list_instances_is_served_with_the_sandbox_group():
    """sandbox.list_instances is advertised when the sandbox group is served."""
    assert "sandbox.list_instances" in helpers.list_tool_names(["sandbox"])


def test_list_instances_without_a_sandbox_path_is_empty(monkeypatch, tmp_path):
    """Nothing deployed yet means no sandbox path, which is not an error."""
    list_instances, asked = _list_instances_tool(
        monkeypatch, str(tmp_path / "never_created")
    )

    assert list_instances() == []
    assert asked == []


def test_list_instances_reports_port_version_and_status(monkeypatch, tmp_path):
    """Each instance is reported by port, with its version and whether it runs.

    Only ``<port>/my.cnf`` counts as an instance: the shell's boilerplate
    directory, a stray file and a port directory without an option file are
    all skipped.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        running_port = listener.getsockname()[1]
        stopped_port = helpers.find_free_port()

        _make_instance(tmp_path, running_port)
        _make_instance(tmp_path, stopped_port)
        os.makedirs(tmp_path / "3399")
        os.makedirs(tmp_path / "boilerplate-mariadb-12.3.2-MariaDB")
        (tmp_path / "3398").write_text("")

        list_instances, asked = _list_instances_tool(
            monkeypatch,
            str(tmp_path),
            versions={running_port: "12.3.2", stopped_port: "11.8.9"},
        )
        instances = list_instances()

    assert instances == sorted(
        [
            {"port": running_port, "version": "12.3.2", "status": "running"},
            {"port": stopped_port, "version": "11.8.9", "status": "stopped"},
        ],
        key=lambda instance: instance["port"],
    )
    # Asked of the path that was listed, not left to the shell's default.
    assert {options["sandboxDir"] for _, options in asked} == {str(tmp_path)}


def test_list_instances_keeps_an_unknown_version(monkeypatch, tmp_path):
    """An instance whose version cannot be determined is listed, as None."""
    port = helpers.find_free_port()
    _make_instance(tmp_path, port)
    list_instances, _ = _list_instances_tool(monkeypatch, str(tmp_path))

    assert list_instances() == [{"port": port, "version": None, "status": "stopped"}]


def test_list_instances_expands_the_home_directory(monkeypatch, tmp_path):
    """The option may be spelled with ``~``, as the shell itself accepts."""
    monkeypatch.setenv("HOME", str(tmp_path))
    port = helpers.find_free_port()
    _make_instance(tmp_path / "sandboxes", port)
    list_instances, asked = _list_instances_tool(monkeypatch, "~/sandboxes")

    assert [instance["port"] for instance in list_instances()] == [port]
    assert asked[0][1]["sandboxDir"] == str(tmp_path / "sandboxes")


def test_list_instances_reports_one_instance_by_port(monkeypatch, tmp_path):
    """A port asks about that instance alone, and still answers a list."""
    wanted = helpers.find_free_port()
    other = helpers.find_free_port()
    _make_instance(tmp_path, wanted)
    _make_instance(tmp_path, other)
    list_instances, asked = _list_instances_tool(
        monkeypatch, str(tmp_path), versions={wanted: "12.3.2", other: "11.8.9"}
    )

    assert list_instances(port=wanted) == [
        {"port": wanted, "version": "12.3.2", "status": "stopped"}
    ]
    # Only the instance asked about was asked for its version.
    assert [port for port, _ in asked] == [wanted]


def test_list_instances_by_port_reports_a_running_instance(monkeypatch, tmp_path):
    """The one instance asked about is probed like any other."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        _make_instance(tmp_path, port)
        list_instances, _ = _list_instances_tool(monkeypatch, str(tmp_path))

        assert list_instances(port=port) == [
            {"port": port, "version": None, "status": "running"}
        ]


def test_list_instances_by_port_without_an_instance_is_empty(monkeypatch, tmp_path):
    """No sandbox on the port - or no sandbox path at all - is an empty list."""
    port = helpers.find_free_port()
    # A directory with no my.cnf is not an instance.
    os.makedirs(tmp_path / str(port))
    list_instances, asked = _list_instances_tool(monkeypatch, str(tmp_path))

    assert list_instances(port=port) == []

    list_instances, asked = _list_instances_tool(
        monkeypatch, str(tmp_path / "never_created")
    )
    assert list_instances(port=port) == []
    assert asked == []


def _registered(monkeypatch, gui, sandbox_calls, stored, deleted=None, stored_under=None):
    """Registers the sandbox tools in or out of GUI mode, against stand-ins.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        gui (bool): Whether the server is in GUI mode.
        sandbox_calls (list): Every call to the shell's sandbox, as
            ``(name, port, options)``.
        stored (list): Every stored connection, as ``(uri, kind, path)``.
        deleted (list): Every deleted connection, as ``(uri, kind)``.
        stored_under (dict): kind -> the URI configured in that list, for
            resolve_connection_uri to answer with.

    Returns:
        The registered tools, by name.
    """
    import mysqlsh.globals

    def recorder(name):
        return lambda port, options=None: sandbox_calls.append((name, port, options))

    monkeypatch.setattr(
        mysqlsh.globals,
        "sandbox",
        SimpleNamespace(deploy=recorder("deploy"), delete=recorder("delete")),
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_functions.config,
        "store_connection",
        lambda uri, password, kind=None, path=None: stored.append((uri, kind, path)),
    )
    monkeypatch.setattr(
        sandbox_functions.config,
        "resolve_connection_uri",
        lambda uri, kind=None: (stored_under or {}).get(kind),
    )
    monkeypatch.setattr(
        sandbox_functions.config,
        "delete_connection",
        lambda uri, kind=None: (deleted if deleted is not None else []).append(
            (uri, kind)
        ),
    )
    monkeypatch.setattr(sandbox_functions.general, "is_gui_mode", lambda: gui)

    recorder_server = _ToolRecorder()
    sandbox_functions.register_sandbox_tools(recorder_server)
    return recorder_server.tools


def test_deploy_offers_mcp_access_only_with_gui():
    """--gui advertises mcp_access on sandbox.deploy; without it, nothing does."""
    gui = helpers.list_tools(["sandbox"], gui=True)["sandbox.deploy"]
    plain = helpers.list_tools(["sandbox"])["sandbox.deploy"]

    assert gui.input_schema["properties"]["mcp_access"]["default"] is True
    assert "mcp_access" in gui.description
    assert "mcp_access" not in plain.input_schema["properties"]
    assert "mcp_access" not in plain.description
    # Only the one argument differs.
    assert set(gui.input_schema["properties"]) - set(
        plain.input_schema["properties"]
    ) == {"mcp_access"}


def test_deploy_stores_the_connection_in_the_list_asked_for(monkeypatch):
    """mcp_access picks the shared list or the extension's own, in /Sandboxes."""
    calls, stored = [], []
    tools = _registered(monkeypatch, True, calls, stored)

    asyncio.run(tools["sandbox.deploy"](None, 3399, password="pw"))
    asyncio.run(tools["sandbox.deploy"](None, 3398, password="pw", mcp_access=False))

    assert [(uri, kind) for uri, kind, _ in stored] == [
        ("mariadb://root@127.0.0.1:3399", config.CONNECTION_KIND_MCP),
        ("mariadb://root@127.0.0.1:3398", config.CONNECTION_KIND_GUI),
    ]
    assert {path for _, _, path in stored} == {config.SANDBOX_CONNECTION_PATH}
    # Not an option of the shell's own deploy.
    assert all("mcpAccess" not in options for _, _, options in calls)


def test_deploy_without_gui_stores_in_the_shared_list(monkeypatch):
    """An agent's sandbox goes where the agent can open it."""
    calls, stored = [], []
    tools = _registered(monkeypatch, False, calls, stored)

    asyncio.run(tools["sandbox.deploy"](None, 3399, password="pw"))

    assert stored[0][1] == config.CONNECTION_KIND_MCP


def test_delete_removes_the_connection_from_either_list(monkeypatch):
    """Whichever list deploy put the connection in, delete finds it there."""
    calls, stored, deleted = [], [], []
    tools = _registered(
        monkeypatch,
        True,
        calls,
        stored,
        deleted,
        stored_under={config.CONNECTION_KIND_GUI: "mariadb://root@127.0.0.1:3399"},
    )

    asyncio.run(tools["sandbox.delete"](None, 3399))

    assert deleted == [("mariadb://root@127.0.0.1:3399", config.CONNECTION_KIND_GUI)]
