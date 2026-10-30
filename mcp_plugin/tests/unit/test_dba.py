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

"""Tests for the dba group: cloud vendor deployments through the dba plugin.

What matters here is who gets what. An agent - a server without ``--gui`` -
gets the tools that read, with the API keys mcp setup stored for it; the VS
Code extension gets every tool, with its own keys. The vendor is a stub
registered with the dba plugin for the test, so nothing reaches a network.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

import sys
import time
from types import SimpleNamespace

import pytest

import mysqlsh

pytest.importorskip("mcp")
pytest.importorskip("dba_plugin")

from mcp.server.mcpserver.exceptions import ToolError

from dba_plugin.lib import api_keys, object_types, vendor as vendor_lib, vendors

from mysqlsh import task_manager

from mcp_plugin.lib import dba_functions, general, server, setup_cli, tasks
import mcp_plugin.tests.unit.helpers as helpers

READ_TOOLS = {
    "dba.list_vendors",
    "dba.list_vendor_object_support",
    "dba.get_vendor_object_info",
    "dba.list_vendor_objects",
    "dba.get_vendor_object_status",
}
GUI_TOOLS = READ_TOOLS | {
    "dba.set_api_key",
    "dba.deploy_vendor_object",
    "dba.delete_vendor_object",
}


# A request over stdio, where there is only ever the one client.
STDIO = SimpleNamespace(request_context=SimpleNamespace(request=None))


def _ended(started, timeout=5.0):
    """The snapshot of the task a tool started, once it ended."""
    task = tasks.get(started["task_id"], general.get_client_identity(STDIO))
    deadline = time.monotonic() + timeout
    while task.status not in task_manager.FINISHED_STATES:
        assert time.monotonic() < deadline, f"the task is still {task.status}"
        task.wait_for_change(task.version, 0.1)
    return task.snapshot()


class StubVendor(vendor_lib.Vendor):
    """A vendor answering from memory, recording the key it was opened with."""

    name = "stubcloud"
    display_name = "Stub Cloud"
    feature_sets = (object_types.CORE_COMPUTE,)
    opened_with = []
    deployed = []
    gone = []

    def __init__(self, api_key=None, project=None):
        super().__init__(api_key, project)
        StubVendor.opened_with.append(api_key)

    def create_client(self):
        return None

    def _list_vms(self):
        listed = [vendor_lib.make_object("vms", 1, "db1", "running", "zone-1", {"env": "prod"})]
        return [o for o in listed if o["id"] not in StubVendor.gone] + [
            vendor_lib.make_object("vms", 2, d["name"], "running", labels=d["labels"])
            for d in StubVendor.deployed
        ]

    def _deploy_vms(self, settings):
        settings = vendor_lib.check_settings("vms", settings)
        StubVendor.deployed.append(settings)
        return vendor_lib.make_object("vms", 2, settings["name"], "starting")

    def _delete_vms(self, obj):
        StubVendor.gone.append(obj["id"])
        return {"status": "deleting"}


@pytest.fixture
def stub_vendor(monkeypatch):
    """Registers the stub vendor, with no API keys but the test's own."""
    from mysqlsh.globals import shell

    monkeypatch.setitem(vendors._vendors, StubVendor.name, StubVendor)
    StubVendor.opened_with = []
    StubVendor.deployed = []
    StubVendor.gone = []
    task_manager.clear()

    saved = {
        name: shell.read_secret(name)
        for name in shell.list_secrets()
        if ":DBA_API_KEY:" in name
    }
    for name in saved:
        shell.delete_secret(name)
    try:
        yield StubVendor
    finally:
        for name in shell.list_secrets():
            if ":DBA_API_KEY:" in name:
                shell.delete_secret(name)
        for name, value in saved.items():
            shell.store_secret(name, value)


def _tools():
    recorder = helpers.ToolRecorder()
    dba_functions.register_dba_tools(recorder)
    return recorder.tools


def test_an_agent_gets_only_the_tools_that_read(stub_vendor):
    assert set(_tools()) == READ_TOOLS


def test_the_extension_gets_every_tool(stub_vendor, gui_mode):
    assert set(_tools()) == GUI_TOOLS


def test_no_tools_without_the_dba_plugin(monkeypatch):
    # A None entry in sys.modules makes the import fail as a missing package does.
    monkeypatch.setitem(sys.modules, "dba_plugin.lib", None)
    assert _tools() == {}


def test_an_agent_uses_the_mcp_keys(stub_vendor):
    api_keys.store_api_key("stubcloud", "gui-key", context=api_keys.CONTEXT_GUI)
    tools = _tools()

    with pytest.raises(ToolError, match="mcp setup --setDbaApiKey=stubcloud"):
        tools["dba.list_vendor_objects"]("stubcloud", "vms")

    api_keys.store_api_key("stubcloud", "mcp-key", context=api_keys.CONTEXT_MCP)
    [listed] = tools["dba.list_vendor_objects"]("stubcloud", "vms", tags="env=prod")
    assert listed["name"] == "db1"
    assert stub_vendor.opened_with == ["mcp-key"]

    # The real vendor plugins are listed too, wherever they are installed.
    [entry] = [e for e in tools["dba.list_vendors"]() if e["name"] == "stubcloud"]
    assert entry["api_key_projects"] == ["default"]


def test_the_extension_uses_its_own_keys(stub_vendor, gui_mode):
    api_keys.store_api_key("stubcloud", "mcp-key", context=api_keys.CONTEXT_MCP)
    tools = _tools()

    stored = tools["dba.set_api_key"]("stubcloud", "gui-key", "staging")
    assert stored == {
        "vendor": "stubcloud",
        "project": "staging",
        "secret": "GUI:DBA_API_KEY:stubcloud:staging",
    }

    started = tools["dba.deploy_vendor_object"](STDIO, "stubcloud", "vms", {"name": "db2"}, "staging")
    ended = _ended(started)
    assert ended["status"] == "completed"
    assert ended["result"]["name"] == "db2"
    assert stub_vendor.opened_with == ["gui-key"]
    # A vendor without projects of its own labels what it deploys for one.
    assert stub_vendor.deployed[0]["labels"] == {vendor_lib.PROJECT_LABEL: "staging"}


def test_a_refusal_is_a_tool_error(stub_vendor, gui_mode):
    api_keys.store_api_key("stubcloud", "gui-key")
    tools = _tools()

    with pytest.raises(ToolError, match="Stub Cloud does not support volumes"):
        tools["dba.list_vendor_objects"]("stubcloud", "volumes")
    with pytest.raises(ToolError, match="Unknown vendor 'nocloud'"):
        tools["dba.get_vendor_object_status"]("nocloud", "vms", "db1")
    with pytest.raises(ToolError, match="settings must be a dict naming the object"):
        tools["dba.deploy_vendor_object"](STDIO, "stubcloud", "vms", {})

    deleted = _ended(tools["dba.delete_vendor_object"](STDIO, "stubcloud", "vms", "db1"))
    assert deleted["result"]["deleted"]["id"] == "1"
    assert deleted["result"]["result"] == {"status": "deleting"}


def test_a_task_belongs_to_the_client_that_started_it(stub_vendor, gui_mode):
    api_keys.store_api_key("stubcloud", "gui-key")
    started = _tools()["dba.delete_vendor_object"](STDIO, "stubcloud", "vms", "db1")
    _ended(started)

    other = general.ClientIdentity("10.0.0.2", "session-2")
    assert [t.kind for t in tasks.list_for(general.get_client_identity(STDIO), "dba")] == ["delete_object"]
    assert tasks.list_for(other) == []
    with pytest.raises(ToolError, match="There is no task with the id"):
        tasks.get(started["task_id"], other)


def test_the_instructions_tell_an_agent_not_to_ask_for_a_key():
    instructions = server.server_instructions(["dba"])
    assert "Never ask the user for a cloud vendor's API key" in instructions
    assert "--setDbaApiKey=<vendor>" in instructions
    assert "API key" not in server.server_instructions(["db"])


# --- mcp setup ----------------------------------------------------------------


def test_setup_stores_an_mcp_key_from_the_environment(stub_vendor, monkeypatch, capsys):
    monkeypatch.setenv("STUB_TOKEN", "  env-key  ")
    setup_cli.apply(
        {"set_dba_api_key": "stubcloud", "dba_project": "prod", "dba_api_key_env": "STUB_TOKEN"}
    )

    assert "Stored the API key of stubcloud for project 'prod'" in capsys.readouterr().out
    assert api_keys.read_api_key("stubcloud", "prod", api_keys.CONTEXT_MCP) == "env-key"
    assert api_keys.read_api_key("stubcloud", "prod", api_keys.CONTEXT_GUI) is None

    assert setup_cli.configuration()["dba_api_keys"] == [{"vendor": "stubcloud", "project": "prod"}]

    setup_cli.apply({"delete_dba_api_key": "stubcloud", "dba_project": "prod"})
    assert "Deleted the API key of stubcloud for project 'prod'" in capsys.readouterr().out
    assert api_keys.list_api_keys(api_keys.CONTEXT_MCP) == []


@pytest.mark.parametrize(
    "options, refusal",
    [
        ({"set_dba_api_key": "nocloud", "dba_api_key_env": "STUB_TOKEN"}, "Unknown vendor 'nocloud'"),
        ({"set_dba_api_key": "stubcloud", "dba_api_key_env": "UNSET_TOKEN"}, "'UNSET_TOKEN', which is not set"),
        ({"set_dba_api_key": "stubcloud", "dba_api_key_env": "STUB_TOKEN", "dba_api_key_stdin": True}, "at most"),
        ({"dba_api_key_env": "STUB_TOKEN"}, "only applies to --setDbaApiKey"),
        ({"add_paths": "/tmp", "dba_project": "x"}, "--dbaProject only applies"),
        ({"set_dba_api_key": "stubcloud", "non_interactive": True}, "cannot prompt"),
        (
            {"set_dba_api_key": "stubcloud", "dba_api_key_stdin": True, "add_connection": "a@b", "password_stdin": True},
            "cannot both read stdin",
        ),
        ({"set_dba_api_key": "stubcloud", "dba_api_key_env": "STUB_TOKEN", "multi_tenant": True}, "multi-tenant"),
    ],
)
def test_setup_refuses_what_it_cannot_honour(stub_vendor, monkeypatch, options, refusal):
    monkeypatch.setenv("STUB_TOKEN", "env-key")
    monkeypatch.delenv("UNSET_TOKEN", raising=False)
    with pytest.raises(mysqlsh.Error, match=refusal):
        setup_cli.apply(options)
    assert api_keys.list_api_keys(api_keys.CONTEXT_MCP) == []
