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

"""Tests for extension groups: MCP tools other shell plugins offer.

A plugin offers a function with ``@plugin_function(..., mcp={...})``; the
shell keeps it in its registry, and the server registers it as a tool. Most
tests here build registry entries with the shell's own FunctionData - what
``plugin_function`` builds - and put them in place of the registry, so no
plugin has to be loaded into the test process. The last tests start a real
server with a real plugin (tests/fixtures/extension_plugins/mcp_ext_test).
"""
# cSpell:ignore mysqlsh

import asyncio
import json
import os

import mysqlsh
import pytest
from mysqlsh.plugin_manager import registrar

import helpers
from mcp_plugin.lib import config, extension_functions, general, server, setup_cli, tasks

FIXTURE_PLUGINS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures",
    "extension_plugins",
)


# --- registry entries, as plugin_function builds them -------------------------


def list_orders(customer, limit=20, note=None, session=None):
    """Lists a customer's open orders.

    The newest first.

    Args:
        customer (str): The customer number.
        limit (int): The most orders returned.
        note (str): Something to say.
        session (object): The session to use.

    Returns:
        The orders.
    """
    return session.run_sql(f"orders of {customer} limit {limit}").value


def export_orders(target, **options):
    """Writes the orders to a file.

    Args:
        target (str): The file.
        **options (dict): How to write it.

    Keyword Args:
        format (str): csv or json.
        overwrite (bool): Replace a file that is there.
    """
    return {"target": target, "options": options}


def refuse(reason):
    """Refuses.

    Args:
        reason (str): Why.
    """
    raise mysqlsh.Error(f"Refused: {reason}")


def checksum(rounds, session=None, task=None):
    """Checksums the orders.

    Args:
        rounds (int): How many rounds.
        session (object): The session to use.
        task (object): The task.
    """
    for done in range(rounds):
        task.check_cancelled()
        task.progress(done, rounds)
        task.sleep(0.05)

    return {"checksum": session.run_sql("checksum").value, "rounds": rounds}


def entry(function, fully_qualified_name, **mcp):
    """A registry entry offering the function as a tool."""
    return registrar.PluginRegistrar.FunctionData(
        function, fully_qualified_name=fully_qualified_name, shell=False, mcp=mcp
    )


class Result:
    def __init__(self, value):
        self.value = value


class StubSession:
    """A session that answers every statement with the statement."""

    def __init__(self):
        self.statements = []
        self.closed = False

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        return Result(sql)

    def close(self):
        self.closed = True


@pytest.fixture
def registry(monkeypatch):
    """The registry the server reads: set it with ``registry[:] = [...]``."""
    entries = []
    monkeypatch.setattr(extension_functions, "_registry", lambda: list(entries))
    return entries


@pytest.fixture
def logged(monkeypatch):
    """The lines general.log_event writes."""
    lines = []
    monkeypatch.setattr(general, "log_event", lines.append)
    return lines


@pytest.fixture
def no_tasks():
    tasks.clear()
    yield
    tasks.clear()


def _tools(groups):
    mcp_server = server.build_mcp_server(groups)
    return mcp_server, {tool.name: tool for tool in asyncio.run(mcp_server.list_tools())}


def _function(mcp_server, name):
    """The function a tool runs, as registered: the tool_registrar wrapper."""
    return mcp_server._tool_manager.get_tool(name).fn


# --- the shell's side: what the registry holds ---------------------------------


def test_the_shell_checks_and_completes_the_definition():
    definition = entry(list_orders, "orders.listOrders", group="orders", readOnly=True)
    assert definition.mcp == {
        "group": "orders",
        "name": "list_orders",
        "readOnly": True,
        "destructive": False,
        "task": False,
        "gui": False,
        "paths": [],
    }
    # a function that is not offered has none
    plain = registrar.PluginRegistrar.FunctionData(refuse, fully_qualified_name="orders.refuse")
    assert plain.mcp is None

    with pytest.raises(Exception, match="names 'file', which is not a parameter"):
        entry(export_orders, "orders.export", group="orders", paths=["file"])
    with pytest.raises(Exception, match="'group' must be lower case"):
        entry(refuse, "orders.refuse", group="Orders")


def test_the_groups_the_plugins_offer(registry):
    registry[:] = [
        entry(list_orders, "orders.listOrders", group="orders"),
        entry(export_orders, "orders.exportOrders", group="orders"),
        entry(refuse, "files.refuse", group="files"),
        # a built-in group is never an extension group
        entry(refuse, "sneaky.refuse", group="db"),
        registrar.PluginRegistrar.FunctionData(refuse, fully_qualified_name="x.refuse"),
    ]

    assert extension_functions.available_groups() == {
        "files": ["files.refuse"],
        "orders": ["orders.exportOrders", "orders.listOrders"],
    }


def test_a_group_name_is_checked():
    assert extension_functions.check_group_name(" orders ") == "orders"
    for name in ("db", "task", "sql"):
        with pytest.raises(ValueError, match="is a built-in function group"):
            extension_functions.check_group_name(name)
    for name in ("Orders", "my-group", "1st", ""):
        with pytest.raises(ValueError, match="is not an extension group name"):
            extension_functions.check_group_name(name)


# --- what a tool is published as ------------------------------------------------


def test_a_tool_is_published_from_its_signature_and_docstring(registry, logged):
    registry[:] = [
        entry(list_orders, "orders.listOrders", group="orders", readOnly=True),
        entry(export_orders, "orders.exportOrders", group="orders", name="export",
              destructive=True, paths=["target"]),
    ]
    _, tools = _tools(["db", "orders"])

    listed = tools["orders.list_orders"]
    schema = listed.input_schema
    # the session is taken from a connection, never published
    assert set(schema["properties"]) == {"connection_id", "customer", "limit", "note"}
    assert schema["required"] == ["connection_id", "customer"]
    assert schema["properties"]["connection_id"] == {"type": "string"}
    assert schema["properties"]["limit"] == {"default": 20, "type": "integer"}
    assert schema["properties"]["note"] == {"type": "string"}
    assert listed.description.splitlines()[:3] == [
        "Lists a customer's open orders.", "", "The newest first."
    ]
    assert "    connection_id: The UUID returned by db.connect." in listed.description
    assert "    customer: The customer number." in listed.description
    assert "session" not in listed.description
    assert listed.annotations.read_only_hint is True

    export = tools["orders.export"]
    assert set(export.input_schema["properties"]) == {"target", "options"}
    assert export.input_schema["properties"]["options"] == {
        "additionalProperties": True, "type": "object"
    }
    assert "        format (string): csv or json." in export.description
    assert export.annotations.destructive_hint is True

    assert "server: serving extension tool orders.list_orders of orders.listOrders" in logged


def test_a_tool_on_a_connection_needs_the_db_group(registry, logged):
    registry[:] = [
        entry(list_orders, "orders.listOrders", group="orders"),
        entry(refuse, "orders.refuse", group="orders"),
    ]
    _, tools = _tools(["orders"])

    assert "orders.refuse" in tools
    assert "orders.list_orders" not in tools
    assert (
        "server: extension tool orders.list_orders of orders.listOrders not "
        "served: it works on a connection, and the db group is not served"
    ) in logged


def test_tools_that_cannot_be_served_are_left_out(registry, logged):
    def clashing(connection_id):
        """Takes a name the tool publishes itself.

        Args:
            connection_id (str): Mine.
        """

    registry[:] = [
        entry(refuse, "orders.refuse", group="orders"),
        entry(refuse, "other.refuse", group="orders", name="refuse"),
        entry(clashing, "orders.clashing", group="orders"),
        entry(refuse, "sneaky.refuse", group="db"),
    ]
    _, tools = _tools(["orders"])

    assert [name for name in tools if name.startswith("orders.")] == ["orders.refuse"]
    assert (
        "server: extension tool orders.refuse of other.refuse not served: "
        "orders.refuse already offers orders.refuse"
    ) in logged
    assert any(
        "orders.clashing" in line and "connection_id is a name the tool cannot publish" in line
        for line in logged
    )
    # a tool of a built-in group is never served, and the log says why
    assert (
        "server: extension tool db.refuse of sneaky.refuse not served: db is a "
        "built-in function group"
    ) in logged


def test_a_gui_tool_is_not_served_to_an_agent(registry):
    registry[:] = [entry(refuse, "orders.refuse", group="orders", gui=True)]

    assert "orders.refuse" not in _tools(["orders"])[1]


def test_a_gui_tool_is_served_with_gui(registry, gui_mode):
    registry[:] = [entry(refuse, "orders.refuse", group="orders", gui=True)]

    assert "orders.refuse" in _tools(["orders"])[1]


def test_only_the_groups_asked_for_are_served(registry):
    registry[:] = [
        entry(refuse, "orders.refuse", group="orders"),
        entry(refuse, "files.refuse", group="files"),
    ]
    _, tools = _tools(["db", "files"])

    assert "files.refuse" in tools
    assert "orders.refuse" not in tools


# --- calling a tool ----------------------------------------------------------------


def test_a_tool_runs_on_the_connection_of_its_client(registry, monkeypatch):
    registry[:] = [entry(list_orders, "orders.listOrders", group="orders")]
    mcp_server, _ = _tools(["db", "orders"])
    list_tool = _function(mcp_server, "orders.list_orders")

    with helpers.stub_db_tools(monkeypatch, StubSession) as stubbed:
        result = asyncio.run(
            list_tool(stubbed.context, connection_id=stubbed.connection_id, customer="c1")
        )
        assert result == "orders of c1 limit 20"
        assert stubbed.opened[0].statements == ["orders of c1 limit 20"]

        # another client is told the connection does not exist
        stranger = type(stubbed.context)(
            request_context=type(stubbed.context.request_context)(
                request=type(stubbed.context.request_context.request)(
                    client=type(stubbed.context.request_context.request.client)(
                        host="192.0.2.99", port=1
                    ),
                    headers=stubbed.context.request_context.request.headers,
                )
            )
        )
        with pytest.raises(Exception, match="connection") as raised:
            asyncio.run(list_tool(stranger, connection_id=stubbed.connection_id, customer="c1"))
        assert type(raised.value).__name__ == "ToolError"


def test_a_plugin_refusal_reaches_the_client(registry):
    registry[:] = [entry(refuse, "orders.refuse", group="orders")]
    mcp_server, _ = _tools(["orders"])

    with pytest.raises(Exception, match="Refused: not today") as raised:
        asyncio.run(_function(mcp_server, "orders.refuse")(None, reason="not today"))
    assert type(raised.value).__name__ == "ToolError"


def test_options_are_spread_into_the_function(registry, allowed_temp_dir):
    registry[:] = [
        entry(export_orders, "orders.exportOrders", group="orders", paths=["target"])
    ]
    mcp_server, _ = _tools(["orders"])
    export = _function(mcp_server, "orders.export_orders")
    target = os.path.join(allowed_temp_dir, "orders.csv")

    assert asyncio.run(export(None, target=target, options={"format": "csv"})) == {
        "target": target, "options": {"format": "csv"},
    }
    assert asyncio.run(export(None, target=target)) == {"target": target, "options": {}}
    with pytest.raises(Exception, match="cannot set target"):
        asyncio.run(export(None, target=target, options={"target": "/etc/passwd"}))


def test_a_path_that_is_not_allowed_is_refused(registry, clean_config):
    calls = []

    def write(target):
        """Writes.

        Args:
            target (str): The file.
        """
        calls.append(target)

    config.set_allowed_paths([])
    registry[:] = [entry(write, "orders.write", group="orders", paths=["target"])]
    mcp_server, _ = _tools(["orders"])

    with pytest.raises(Exception, match="not allowed|not within") as raised:
        asyncio.run(_function(mcp_server, "orders.write")(None, target="/etc/orders.csv"))
    assert type(raised.value).__name__ == "ToolError"
    assert calls == []


def test_a_result_is_made_json(registry):
    import datetime
    import decimal

    def values():
        """Returns values of every kind."""
        return {
            "when": datetime.date(2026, 11, 12),
            "amount": decimal.Decimal("1.50"),
            "rows": (1, 2),
            "binary": b"\x01\xff",
            "other": object,
        }

    registry[:] = [entry(values, "orders.values", group="orders")]
    mcp_server, _ = _tools(["orders"])

    result = asyncio.run(_function(mcp_server, "orders.values")(None))
    assert result == {
        "when": "2026-11-12", "amount": "1.50", "rows": [1, 2], "binary": "01ff",
        "other": str(object),
    }
    json.dumps(result)


# --- background tasks ----------------------------------------------------------------


def test_a_task_tool_runs_as_a_background_task(registry, monkeypatch, no_tasks):
    registry[:] = [entry(checksum, "orders.checksum", group="orders", task=True)]
    mcp_server, tools = _tools(["db", "orders"])

    # the task tools come with it
    assert "task.get" in tools
    assert "background task" in tools["orders.checksum"].description
    assert "task" not in tools["orders.checksum"].input_schema["properties"]

    with helpers.stub_db_tools(monkeypatch, StubSession) as stubbed:
        started = asyncio.run(
            _function(mcp_server, "orders.checksum")(
                stubbed.context, connection_id=stubbed.connection_id, rounds=3
            )
        )
        client = general.get_client_identity(stubbed.context)
        task = tasks.get(started["task_id"], client)

        assert helpers.finished(task, timeout=10) == tasks.COMPLETED
        assert task.summary()["task_type"] == "orders"
        assert task.snapshot()["result"] == {"checksum": "checksum", "rounds": 3}
        # its own session, closed when it ends
        assert stubbed.opened[-1].closed


def test_a_task_tool_can_be_cancelled(registry, monkeypatch, no_tasks):
    registry[:] = [entry(checksum, "orders.checksum", group="orders", task=True)]
    mcp_server, _ = _tools(["db", "orders"])

    with helpers.stub_db_tools(monkeypatch, StubSession) as stubbed:
        started = asyncio.run(
            _function(mcp_server, "orders.checksum")(
                stubbed.context, connection_id=stubbed.connection_id, rounds=1000
            )
        )
        task = tasks.get(started["task_id"], general.get_client_identity(stubbed.context))
        task.cancel()

        assert helpers.finished(task, timeout=10) == tasks.CANCELLED
        assert stubbed.opened[-1].closed


# --- which groups a server serves ------------------------------------------------------


def test_the_setting_serves_a_group_by_default(registry, logged, saved_settings):
    registry[:] = [entry(refuse, "orders.refuse", group="orders")]
    available = extension_functions.available_groups()

    config.set_extension_groups([])
    assert server._default_extension_groups(available) == []

    config.set_extension_groups(["orders", "gone"])
    assert config.get_extension_groups() == ["orders", "gone"]
    assert server._default_extension_groups(available) == ["orders"]
    assert (
        "server: extension group gone not served: no loaded plugin offers it"
    ) in logged

    with pytest.raises(mysqlsh.Error, match="is a built-in function group"):
        config.set_extension_groups(["db"])


def test_an_unknown_group_names_the_ones_offered(registry):
    registry[:] = [entry(refuse, "orders.refuse", group="orders")]

    with pytest.raises(mysqlsh.Error) as raised:
        server.start("127.0.0.1", 0, "stdio", ["db", "ordrs"])
    message = str(raised.value)
    assert "Unknown function group(s): ordrs." in message
    assert "The loaded plugins offer the extension group(s): orders." in message


def test_multi_tenant_mode_serves_no_extension_group(registry):
    registry[:] = [entry(refuse, "orders.refuse", group="orders")]

    with pytest.raises(mysqlsh.Error, match="extension group\\(s\\) orders are not available"):
        server._check_multi_tenant("streamable-http", ["db", "orders"], False)


def test_setup_sets_and_shows_the_groups(registry, capsys, saved_settings):
    registry[:] = [entry(refuse, "orders.refuse", group="orders")]

    setup_cli.apply({"extension_groups": "orders,later"})
    out = capsys.readouterr().out
    assert "The extension group orders is served by default: orders.refuse." in out
    assert "The extension group later is served by default once a loaded plugin offers it" in out
    assert config.get_extension_groups() == ["orders", "later"]

    setup_cli.apply({"show": True, "json": True})
    shown = json.loads(capsys.readouterr().out)
    assert shown["extension_groups"] == {
        "served_by_default": ["orders", "later"],
        "available": {"orders": ["orders.refuse"]},
    }

    setup_cli.apply({"extension_groups": ""})
    assert "No extension group is served unless --functionGroups names it." in (
        capsys.readouterr().out
    )
    assert config.get_extension_groups() == []

    with pytest.raises(mysqlsh.Error, match="not an extension group name"):
        setup_cli.apply({"extension_groups": "Bad-Name"})


# --- a real plugin, over stdio -----------------------------------------------------------


@pytest.fixture
def fixture_plugin(monkeypatch):
    """Puts the test plugin on the plugin path of the servers the test starts.

    MARIADB_SHELL_PLUGINS_PATH replaces the user's plugin folder, so that
    folder - where run_tests.py links this plugin - goes on it too.
    """
    current = os.environ.get("MARIADB_SHELL_PLUGINS_PATH")
    if not current:
        home = os.environ.get("MARIADB_SHELL_USER_CONFIG_HOME") or os.path.expanduser(
            "~/.mariadb-shell"
        )
        current = os.path.join(home, "plugins")
    monkeypatch.setenv(
        "MARIADB_SHELL_PLUGINS_PATH", os.pathsep.join([current, FIXTURE_PLUGINS])
    )


def test_a_plugin_offers_its_tools_over_stdio(fixture_plugin):
    tools = helpers.list_tools(["ext_test"])

    # serverVersion works on a connection, and db is not served
    assert sorted(tools) == ["ext_test.fail", "ext_test.greet"]
    assert tools["ext_test.greet"].annotations.read_only_hint is True

    greeted = helpers.call_tool(
        ["ext_test"], "ext_test.greet",
        {"name": "Ada", "times": 2, "options": {"shout": True}},
    )
    assert helpers.tool_payload(greeted) == {"greetings": ["HELLO ADA", "HELLO ADA"]}

    refused = helpers.call_tool(["ext_test"], "ext_test.fail", {"reason": "no"})
    assert refused.is_error
    assert "Refused: no" in refused.content[0].text


def test_a_plugin_tool_works_on_a_connection(fixture_plugin, sandbox):
    if not sandbox.deployed:
        pytest.skip("The sandbox was not deployed.")

    async def run():
        async with helpers.mcp_session(["db", "ext_test"]) as call:
            connected = await call("db.connect", {"uri": sandbox.uri})
            connection_id = helpers.tool_payload(connected)
            try:
                result = await call("ext_test.server_version", {"connection_id": connection_id})
                return helpers.tool_payload(result)
            finally:
                await call("db.close", {"connection_id": connection_id})

    # A run_sql result is returned as its rows; one row comes back as itself,
    # as tool_payload gathers one content block per row
    row = asyncio.run(run())
    assert "MariaDB" in row["version"]
