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

"""Tests for GUI mode - ``mcp start-server --gui``.

GUI mode says the client is the MariaDB VS Code extension: a user interface the
user drives on this machine over stdio, rather than an autonomous agent. Two
things follow from that, and both are a real widening of what a client can do,
so what is tested here is as much where the widening STOPS as that it happens:

* Every local path is accessible, so neither the allowed-path list nor the
  elicitation that grows it applies.
* The connection list becomes writable over the protocol, through two tools
  that exist in this mode only, and there are two lists from here on - the
  shared ``mcp`` one and the extension's own ``gui`` one - which every one of
  these tools names explicitly.

Neither may leak into a server started without ``--gui``: the GUI list must be
unreachable there, and the tools that write it must not be advertised at all.

Everything runs in-process against a stub session, so no database server is
needed. The ``gui_mode`` fixture is what puts the process-wide flag back, since
in a real run it is set once by a server that then blocks forever.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

from types import SimpleNamespace

import pytest

import mysqlsh

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError

from mcp_plugin.lib import config, db_functions, general, server
import mcp_plugin.tests.unit.helpers as helpers


# The identity a request over stdio presents, which is what the extension is.
STDIO_CONTEXT = SimpleNamespace(request_context=SimpleNamespace(request=None))


class _StubSession:
    """Stands in for an open shell session."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _registered_tools(monkeypatch, opened=None):
    """Registers the db tools with the session factory stubbed out.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        opened: A list collecting a ``(uri, kind)`` pair per session opened, so
            a test can see which connection list a session was opened from.

    Returns:
        The registered tools, by name.
    """
    def _fake_open_session(uri, kind=None):
        if opened is not None:
            opened.append((uri, config.normalize_connection_kind(kind)))

        return _StubSession()

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)

    tools = _ToolRecorder()
    db_functions.register_db_tools(tools)

    return tools.tools


def _empty_both_connection_lists():
    """Empties both connection lists so a test can assert on them exactly.

    The clean_config fixture backs the lists up and restores them afterwards
    but does NOT clear them first, so without this a developer's own configured
    connections turn up in these assertions.
    """
    for kind in config.SUPPORTED_CONNECTION_KINDS:
        for uri in config.list_stored_connection_uris(kind):
            config.delete_connection(uri, kind)


# --- the flag itself -------------------------------------------------------


def test_gui_mode_is_off_until_a_server_says_otherwise(gui_mode):
    """It is a global, so what matters is that it is off by default.

    The fixture has turned it on; turning it off again has to be enough to put
    every GUI-mode behaviour back, since that is all stopping a server does.
    """
    assert general.is_gui_mode() is True

    general.set_gui_mode(False)
    assert general.is_gui_mode() is False
    assert config.usable_connection_kinds() == (config.CONNECTION_KIND_MCP,)


# --- full path access ------------------------------------------------------


def test_every_path_is_allowed_in_gui_mode(gui_mode, clean_config, tmp_path):
    """The allow-list is off, and off without being written to.

    The extension names paths the user picked in VS Code's own file dialogs, so
    there is nothing left to confirm. The list itself must be left alone all the
    same: turning GUI mode off has to put the old answer back exactly, which it
    would not if a GUI-mode call had been quietly adding entries.
    """
    config.set_allowed_paths([])

    outside = tmp_path / "not-allowed" / "script.sql"
    assert config.is_path_allowed(str(outside)) is True

    general.set_gui_mode(False)
    assert config.is_path_allowed(str(outside)) is False
    assert config.get_allowed_paths() == []


def test_a_path_is_not_elicited_in_gui_mode(gui_mode, clean_config):
    """require_allowed_path asks nobody, since there is nothing to ask about.

    The context is one that would REFUSE if it were consulted, which is how
    this tells "allowed without asking" from "asked and got a yes".
    """
    import asyncio

    config.set_allowed_paths([])

    class _RefusingContext:
        async def elicit(self, message, schema):
            raise AssertionError("GUI mode must not elicit a path")

    asyncio.run(
        general.require_allowed_path(_RefusingContext(), "/anywhere/at/all")
    )

    # Without GUI mode the same call is refused, which is what says the pass
    # above came from the mode and not from the path being allowed anyway.
    general.set_gui_mode(False)
    with pytest.raises(mysqlsh.Error) as refused:
        asyncio.run(
            general.require_allowed_path(_RefusingContext(), "/anywhere/at/all")
        )

    assert "is not allowed" in str(refused.value)


# --- which tools are served ------------------------------------------------


def test_the_connection_tools_are_served_in_gui_mode_only(monkeypatch, gui_mode):
    """Writing the connection list is what --gui unlocks, and only --gui.

    Everywhere else the list is curated with mcp.setup, deliberately out of
    reach of the clients that use it: a client that could add a connection
    could give itself credentials for a server nobody configured.
    """
    written = (
        "db.add_connection", "db.delete_connection", "db.test_connection",
        "db.update_connection",
    )

    tools = _registered_tools(monkeypatch)
    for name in written:
        assert name in tools

    general.set_gui_mode(False)
    tools = _registered_tools(monkeypatch)
    for name in written:
        assert name not in tools

    # db.list_connections is served either way; only its signature differs.
    assert "db.list_connections" in tools


def test_list_connections_takes_no_kind_outside_gui_mode(monkeypatch):
    """The plain tool has no way to name the GUI list, so it cannot read it.

    The SDK builds a tool's input schema from its signature, so the parameter
    not being there is the whole of what keeps an ordinary MCP client from
    asking for the extension's connections.
    """
    import inspect

    tools = _registered_tools(monkeypatch)
    assert inspect.signature(tools["db.list_connections"]).parameters == {}


# --- db.list_connections / db.add_connection / db.delete_connection --------


def test_the_two_lists_are_reported_one_at_a_time(
    monkeypatch, gui_mode, clean_config
):
    """The extension works with both lists, so it asks for one of them.

    Reporting them together would lose which list an entry came from, and the
    extension needs that to delete it again.
    """
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)

    config.store_connection("mariadb://mcp_one@127.0.0.1:3306", "pw")
    config.store_connection(
        "mariadb://gui_one@127.0.0.1:3306", "pw", config.CONNECTION_KIND_GUI
    )

    assert tools["db.list_connections"]() == [
        {"uri": "mariadb://mcp_one@127.0.0.1:3306", "path": "/", "kind": "mcp"}
    ]
    assert tools["db.list_connections"](config.CONNECTION_KIND_MCP) == [
        {"uri": "mariadb://mcp_one@127.0.0.1:3306", "path": "/", "kind": "mcp"}
    ]
    assert tools["db.list_connections"](config.CONNECTION_KIND_GUI) == [
        {"uri": "mariadb://gui_one@127.0.0.1:3306", "path": "/", "kind": "gui"}
    ]


def test_adding_a_connection_stores_it_normalized(
    monkeypatch, gui_mode, clean_config
):
    """What goes in is the URI as written; what is stored is the one spelling.

    db.list_connections then reports that spelling, and db.connect resolves
    every other one onto it - which only works if the stored key is normalized
    on the way in, as mcp.setup does it.
    """
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)

    stored = tools["db.add_connection"](
        "mariadb://gui_add@127.0.0.1", "pw", config.CONNECTION_KIND_GUI, False
    )

    assert stored == "mariadb://gui_add@127.0.0.1:3306"
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [stored]
    assert (
        config.get_connection_password(stored, config.CONNECTION_KIND_GUI)
        == "pw"
    )
    # Nothing reached the list the call did not name.
    assert config.list_connection_uris() == []

    # Adding it again updates the password rather than failing: the extension
    # has no separate "change the password" call, and re-adding is how a user
    # corrects one they got wrong.
    # Spelled as it would have been stored before the scheme was kept, which is
    # also what proves an old key is REPLACED rather than left beside the new
    # one: two spellings of one connection resolve to neither.
    tools["db.add_connection"](
        "gui_add@127.0.0.1:3306", "new-pw", config.CONNECTION_KIND_GUI, False
    )
    assert config.list_stored_connection_uris(config.CONNECTION_KIND_GUI) == [
        stored
    ]
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [stored]
    assert (
        config.get_connection_password(stored, config.CONNECTION_KIND_GUI)
        == "new-pw"
    )


def test_both_lists_are_reported_in_one_call_each_entry_saying_which(
    monkeypatch, gui_mode, clean_config
):
    """What the extension asks for: both lists at once, MCP first.

    Once both are in one answer, the kind on each entry is the only thing
    left that says which list a connection is in - and one URI can be in
    both.
    """
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)
    both = "mariadb://both@127.0.0.1:3306"
    config.store_connection("mariadb://mcp_b@127.0.0.1:3306", "pw")
    config.store_connection("mariadb://mcp_a@127.0.0.1:3306", "pw")
    config.store_connection(both, "pw")
    config.store_connection(
        both, "pw", config.CONNECTION_KIND_GUI, "/Mine"
    )

    assert tools["db.list_connections"](" ALL ") == [
        {"uri": "mariadb://both@127.0.0.1:3306", "path": "/", "kind": "mcp"},
        {"uri": "mariadb://mcp_a@127.0.0.1:3306", "path": "/", "kind": "mcp"},
        {"uri": "mariadb://mcp_b@127.0.0.1:3306", "path": "/", "kind": "mcp"},
        {"uri": both, "path": "/Mine", "kind": "gui"},
    ]


def test_all_names_no_list_a_connection_can_be_stored_in(
    monkeypatch, gui_mode, clean_config
):
    """It is for reading both; nothing can be written to it."""
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError, match="not a known connection kind"):
        tools["db.add_connection"](
            "mariadb://nowhere@127.0.0.1", "pw", config.CONNECTION_KIND_ALL,
            False,
        )


def test_the_folder_of_a_connection_is_reported_in_gui_mode_only(
    monkeypatch, gui_mode, clean_config
):
    """The extension shows folders; an agent never sees one.

    Outside GUI mode db.list_connections is the plain URI list it always was,
    so a filed connection reads exactly like any other there.
    """
    _empty_both_connection_lists()
    uri = "mariadb://filed@127.0.0.1:3306"
    config.store_connection(uri, "pw", path="/Sandboxes/note_app")

    tools = _registered_tools(monkeypatch)
    assert tools["db.list_connections"]() == [
        {"uri": uri, "path": "/Sandboxes/note_app", "kind": "mcp"}
    ]

    general.set_gui_mode(False)
    plain = _registered_tools(monkeypatch)
    assert plain["db.list_connections"]() == [uri]


def test_adding_a_connection_files_it_in_a_folder(
    monkeypatch, gui_mode, clean_config
):
    """The folder goes into the key; re-adding without one keeps it there."""
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)
    gui = config.CONNECTION_KIND_GUI

    stored = tools["db.add_connection"](
        "mariadb://foldered@127.0.0.1", "pw", gui, False, "Sandboxes/note_app/"
    )

    assert tools["db.list_connections"](gui) == [
        {"uri": stored, "path": "/Sandboxes/note_app", "kind": "gui"}
    ]

    # A password corrected by adding again must not move it to the top.
    tools["db.add_connection"](stored, "new-pw", gui, False)
    assert tools["db.list_connections"](gui) == [
        {"uri": stored, "path": "/Sandboxes/note_app", "kind": "gui"}
    ]
    assert config.get_connection_password(stored, gui) == "new-pw"

    tools["db.add_connection"](stored, "new-pw", gui, False, "/")
    assert tools["db.list_connections"](gui) == [
        {"uri": stored, "path": "/", "kind": "gui"}
    ]


def test_a_folder_name_with_a_colon_is_refused_before_anything_is_stored(
    monkeypatch, gui_mode, clean_config
):
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError, match="contains a ':'"):
        tools["db.add_connection"](
            "mariadb://colon@127.0.0.1", "pw", config.CONNECTION_KIND_GUI,
            False, "/a:b",
        )

    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []


def test_updating_moves_a_connection_to_another_folder(
    monkeypatch, gui_mode, clean_config
):
    """A move keeps the password and leaves no key behind in the old folder."""
    _empty_both_connection_lists()
    gui = config.CONNECTION_KIND_GUI
    uri = "mariadb://mover@127.0.0.1:3306"
    config.store_connection(uri, "kept", gui, "/Old")
    tools = _registered_tools(monkeypatch)

    assert tools["db.update_connection"](
        uri, None, gui, None, None, "/New/Nested"
    ) == uri
    assert tools["db.list_connections"](gui) == [
        {"uri": uri, "path": "/New/Nested", "kind": "gui"}
    ]
    assert config.get_connection_password(uri, gui) == "kept"
    assert config.list_stored_connection_uris(gui) == [uri]

    # Changing the URI or the list without naming a folder keeps it.
    moved = tools["db.update_connection"](
        uri, "mariadb://mover@127.0.0.1:3307", gui, config.CONNECTION_KIND_MCP,
    )
    assert tools["db.list_connections"](config.CONNECTION_KIND_MCP) == [
        {"uri": moved, "path": "/New/Nested", "kind": "mcp"}
    ]
    assert tools["db.list_connections"](gui) == []

    # And "/" takes it back to the top level.
    tools["db.update_connection"](
        moved, None, config.CONNECTION_KIND_MCP, None, None, "/"
    )
    assert tools["db.list_connections"](config.CONNECTION_KIND_MCP) == [
        {"uri": moved, "path": "/", "kind": "mcp"}
    ]
    assert config.get_connection_password(moved) == "kept"


def test_a_connection_that_does_not_open_is_not_stored(
    monkeypatch, gui_mode, clean_config
):
    """Verification is on by default, and a failure stores nothing.

    Reported with the shell's own text, since "access denied" and "connection
    refused" call for different things from the user.
    """
    _empty_both_connection_lists()
    def _refuse(uri, password):
        raise mysqlsh.Error("Access denied for user 'gui_bad'@'127.0.0.1'")

    from mcp_plugin.lib import setup_cli

    monkeypatch.setattr(setup_cli, "verify_connection", _refuse)

    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as failed:
        tools["db.add_connection"](
            "mariadb://gui_bad@127.0.0.1:3306", "wrong",
            config.CONNECTION_KIND_GUI,
        )

    assert "Access denied" in str(failed.value)
    assert "was not stored" in str(failed.value)
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []

    # Verified and accepted, it is stored - with the password that was checked.
    verified = []
    monkeypatch.setattr(
        setup_cli, "verify_connection", lambda uri, pw: verified.append((uri, pw))
    )
    tools["db.add_connection"](
        "mariadb://gui_bad@127.0.0.1:3306", "right",
        config.CONNECTION_KIND_GUI,
    )

    assert verified == [("mariadb://gui_bad@127.0.0.1:3306", "right")]
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [
        "mariadb://gui_bad@127.0.0.1:3306"
    ]


def test_a_password_in_the_uri_is_refused_rather_than_dropped(
    monkeypatch, gui_mode, clean_config
):
    """Normalization strips it, so using it would store no password at all."""
    _empty_both_connection_lists()
    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as refused:
        tools["db.add_connection"](
            "gui_pw:secret@127.0.0.1:3306", "", config.CONNECTION_KIND_GUI, False
        )

    assert "carries a password" in str(refused.value)
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []

    with pytest.raises(ToolError) as invalid:
        tools["db.add_connection"]("not a uri", "", None, False)

    assert "not a valid connection URI" in str(invalid.value)


def test_deleting_a_connection_takes_only_the_list_it_names(
    monkeypatch, gui_mode, clean_config
):
    """The same server can be in both lists; deleting one must not take both."""
    _empty_both_connection_lists()
    uri = "mariadb://gui_del@127.0.0.1:3306"
    config.store_connection(uri, "mcp-pw")
    config.store_connection(uri, "gui-pw", config.CONNECTION_KIND_GUI)

    tools = _registered_tools(monkeypatch)

    # Spelled differently from the stored key, as a client may.
    deleted = tools["db.delete_connection"](
        "mariadb://gui_del@127.0.0.1", config.CONNECTION_KIND_GUI
    )

    assert deleted == uri
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []
    assert config.list_connection_uris() == [uri]

    with pytest.raises(ToolError) as refused:
        tools["db.delete_connection"](uri, config.CONNECTION_KIND_GUI)

    assert "not a configured 'gui' connection" in str(refused.value)


def test_deleting_a_connection_closes_what_is_open_on_it(
    monkeypatch, gui_mode, clean_config
):
    """A deleted connection is revoked at once, not at the next reopen.

    Left to the re-validation in _open_session, a UUID handed out before the
    deletion goes on working for as long as its session lives - which for a
    connection in steady use is until it reaches CONNECTION_MAX_LIFETIME.
    """
    _empty_both_connection_lists()
    uri = "mariadb://gui_open@127.0.0.1:3306"
    config.store_connection(uri, "gui-pw", config.CONNECTION_KIND_GUI)
    config.store_connection(uri, "mcp-pw")

    tools = _registered_tools(monkeypatch)
    db_functions._sessions.clear()

    connection_id = tools["db.connect"](STDIO_CONTEXT, uri)
    assert connection_id in db_functions._sessions

    # The MCP entry for the same server is untouched, so a connection opened on
    # it must survive - the two are different connections.
    tools["db.delete_connection"](uri, config.CONNECTION_KIND_GUI)

    assert connection_id not in db_functions._sessions

    with pytest.raises(ToolError) as gone:
        tools["db.list_schemas"](STDIO_CONTEXT, connection_id)

    assert "No open connection found" in str(gone.value)

    db_functions._sessions.clear()


def test_updating_a_connection_carries_its_password_over(
    monkeypatch, gui_mode, clean_config
):
    """Re-keying must not need the password, because nothing can read one.

    A connection is keyed by URI and list, so changing the host or ticking
    the MCP box means a new key. db.add_connection would need the password
    for that, and there is no tool that hands one back - on purpose. This
    moves the secret without anybody seeing it.
    """
    _empty_both_connection_lists()
    config.store_connection(
        "mariadb://old@127.0.0.1:3306", "kept", config.CONNECTION_KIND_GUI
    )

    tools = _registered_tools(monkeypatch)

    moved = tools["db.update_connection"](
        "mariadb://old@127.0.0.1:3306", "mariadb://new@127.0.0.1:3307",
        config.CONNECTION_KIND_GUI, None, None,
    )

    assert moved == "mariadb://new@127.0.0.1:3307"
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [moved]
    assert config.get_connection_password(moved, config.CONNECTION_KIND_GUI) == "kept"


def test_updating_moves_a_connection_between_the_two_lists(
    monkeypatch, gui_mode, clean_config
):
    """Which is how the MCP access checkbox applies to an existing one."""
    _empty_both_connection_lists()
    uri = "mariadb://switch@127.0.0.1:3306"
    config.store_connection(uri, "pw", config.CONNECTION_KIND_GUI)

    tools = _registered_tools(monkeypatch)

    assert tools["db.update_connection"](
        uri, None, config.CONNECTION_KIND_GUI, config.CONNECTION_KIND_MCP, None,
    ) == uri

    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []
    assert config.list_connection_uris() == [uri]
    assert config.get_connection_password(uri) == "pw"

    # And back again, which is unticking the box.
    tools["db.update_connection"](
        uri, None, config.CONNECTION_KIND_MCP, config.CONNECTION_KIND_GUI, None,
    )
    assert config.list_connection_uris() == []
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [uri]


def test_updating_can_replace_just_the_password(
    monkeypatch, gui_mode, clean_config
):
    """Setting a new password must not disturb the key or the open state."""
    _empty_both_connection_lists()
    uri = "mariadb://pw@127.0.0.1:3306"
    config.store_connection(uri, "old", config.CONNECTION_KIND_GUI)

    tools = _registered_tools(monkeypatch)
    db_functions._sessions.clear()
    connection_id = tools["db.connect"](STDIO_CONTEXT, uri)

    assert tools["db.update_connection"](
        uri, None, config.CONNECTION_KIND_GUI, None, "new",
    ) == uri

    assert config.get_connection_password(uri, config.CONNECTION_KIND_GUI) == "new"
    # Nothing moved, so the open connection is still the same connection.
    assert connection_id in db_functions._sessions

    db_functions._sessions.clear()


def test_updating_nothing_is_a_no_op(monkeypatch, gui_mode, clean_config):
    """A save that changed nothing must not briefly unconfigure it."""
    _empty_both_connection_lists()
    uri = "mariadb://same@127.0.0.1:3306"
    config.store_connection(uri, "pw", config.CONNECTION_KIND_GUI)

    tools = _registered_tools(monkeypatch)

    assert tools["db.update_connection"](
        uri, uri, config.CONNECTION_KIND_GUI, config.CONNECTION_KIND_GUI, None,
    ) == uri
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [uri]
    assert config.get_connection_password(uri, config.CONNECTION_KIND_GUI) == "pw"


def test_updating_closes_what_was_open_on_the_old_key(
    monkeypatch, gui_mode, clean_config
):
    """The connection that UUID was opened on no longer exists under that name."""
    _empty_both_connection_lists()
    config.store_connection(
        "mariadb://moving@127.0.0.1:3306", "pw", config.CONNECTION_KIND_GUI
    )

    tools = _registered_tools(monkeypatch)
    db_functions._sessions.clear()
    connection_id = tools["db.connect"](
        STDIO_CONTEXT, "mariadb://moving@127.0.0.1:3306"
    )

    tools["db.update_connection"](
        "mariadb://moving@127.0.0.1:3306", "mariadb://moved@127.0.0.1:3306",
        config.CONNECTION_KIND_GUI, None, None,
    )

    assert connection_id not in db_functions._sessions
    db_functions._sessions.clear()


def test_updating_an_unknown_connection_is_refused(
    monkeypatch, gui_mode, clean_config
):
    """And a new URI is held to the same rules as db.add_connection."""
    _empty_both_connection_lists()
    config.store_connection(
        "mariadb://known@127.0.0.1:3306", "pw", config.CONNECTION_KIND_GUI
    )

    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as missing:
        tools["db.update_connection"]("mariadb://nope@127.0.0.1:3306", None,
                                      config.CONNECTION_KIND_GUI, None, None)
    assert "not a configured 'gui' connection" in str(missing.value)

    with pytest.raises(ToolError) as carries:
        tools["db.update_connection"]("mariadb://known@127.0.0.1:3306",
                                      "new:secret@127.0.0.1:3306",
                                      config.CONNECTION_KIND_GUI, None, None)
    assert "carries a password" in str(carries.value)

    with pytest.raises(ToolError) as invalid:
        tools["db.update_connection"]("mariadb://known@127.0.0.1:3306",
                                      "not a uri",
                                      config.CONNECTION_KIND_GUI, None, None)
    assert "not a valid connection URI" in str(invalid.value)


def test_testing_a_connection_stores_nothing(
    monkeypatch, gui_mode, clean_config
):
    """The Test button has to answer BEFORE the connection exists.

    db.connect cannot answer it - it only opens configured connections - and
    db.add_connection stores on success, so neither is a test. This one opens
    a session and closes it again, leaving both lists untouched either way.
    """
    _empty_both_connection_lists()

    from mcp_plugin.lib import setup_cli

    tried = []
    monkeypatch.setattr(
        setup_cli, "verify_connection",
        lambda uri, pw: tried.append((uri, pw)),
    )

    tools = _registered_tools(monkeypatch)

    message = tools["db.test_connection"]("mariadb://tester@127.0.0.1", "pw")

    # Normalized before it is tried, so a test and a later store agree on
    # which connection was checked.
    assert tried == [("mariadb://tester@127.0.0.1:3306", "pw")]
    assert "mariadb://tester@127.0.0.1:3306" in message
    assert config.list_connection_uris() == []
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []


def test_testing_without_a_password_uses_the_stored_one(
    monkeypatch, gui_mode, clean_config
):
    """Editing a connection must not mean retyping its password to test it.

    Nothing can read a stored password back, so the fallback has to happen
    server-side - the same place db.update_connection moves one.
    """
    _empty_both_connection_lists()
    config.store_connection(
        "mariadb://stored@127.0.0.1:3306", "kept", config.CONNECTION_KIND_GUI
    )

    from mcp_plugin.lib import setup_cli

    tried = []
    monkeypatch.setattr(
        setup_cli, "verify_connection", lambda uri, pw: tried.append((uri, pw)),
    )

    tools = _registered_tools(monkeypatch)
    tools["db.test_connection"]("mariadb://stored@127.0.0.1:3306", None)

    assert tried == [("mariadb://stored@127.0.0.1:3306", "kept")]


def test_testing_an_unknown_connection_without_a_password_says_so(
    monkeypatch, gui_mode, clean_config
):
    """There is nothing to fall back to, and guessing an empty one would lie."""
    _empty_both_connection_lists()

    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as refused:
        tools["db.test_connection"]("mariadb://nobody@127.0.0.1:3306", None)

    assert "no stored password" in str(refused.value)


def test_a_failed_test_reports_the_shell_and_stores_nothing(
    monkeypatch, gui_mode, clean_config
):
    """A failure is the useful answer, so it carries the shell's own words."""
    _empty_both_connection_lists()

    from mcp_plugin.lib import setup_cli

    def _refuse(uri, password):
        raise mysqlsh.Error("Access denied for user 'tester'@'127.0.0.1'")

    monkeypatch.setattr(setup_cli, "verify_connection", _refuse)

    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as failed:
        tools["db.test_connection"](
            "mariadb://tester@127.0.0.1:3306", "wrong"
        )

    assert "Access denied" in str(failed.value)
    assert config.list_connection_uris() == []
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []


def test_testing_refuses_a_password_in_the_uri(
    monkeypatch, gui_mode, clean_config
):
    """Same rule as db.add_connection, so the two cannot disagree."""
    tools = _registered_tools(monkeypatch)

    with pytest.raises(ToolError) as refused:
        tools["db.test_connection"]("tester:secret@127.0.0.1:3306", "")

    assert "carries a password" in str(refused.value)

    with pytest.raises(ToolError) as invalid:
        tools["db.test_connection"]("not a uri", "")

    assert "not a valid connection URI" in str(invalid.value)


# --- db.connect across the two lists ---------------------------------------


def test_a_gui_connection_is_preferred_where_both_lists_name_it(
    monkeypatch, gui_mode, clean_config
):
    """Two entries for one server hold two passwords; the GUI one is meant.

    In GUI mode the entry the user last worked with through the extension is
    the one they mean. The choice has to be recorded, too: the password is read
    under the kind on every reopen, so a connection opened from the wrong list
    would work now and fail later.
    """
    _empty_both_connection_lists()
    shared = "mariadb://gui_pref@127.0.0.1:3306"
    mcp_only = "mariadb://mcp_pref@127.0.0.1:3306"

    config.store_connection(shared, "mcp-pw")
    config.store_connection(shared, "gui-pw", config.CONNECTION_KIND_GUI)
    config.store_connection(mcp_only, "mcp-pw")

    opened = []
    tools = _registered_tools(monkeypatch, opened)
    db_functions._sessions.clear()

    shared_id = tools["db.connect"](STDIO_CONTEXT, shared)
    assert opened[-1] == (shared, config.CONNECTION_KIND_GUI)
    assert db_functions._sessions[shared_id].kind == config.CONNECTION_KIND_GUI

    # The MCP list still answers for everything the GUI list does not name.
    tools["db.connect"](STDIO_CONTEXT, mcp_only)
    assert opened[-1] == (mcp_only, config.CONNECTION_KIND_MCP)

    db_functions._sessions.clear()


def test_a_gui_connection_cannot_be_opened_without_gui_mode(
    monkeypatch, clean_config
):
    """The extension's own list is out of reach of an ordinary MCP client."""
    uri = "mariadb://gui_hidden@127.0.0.1:3306"
    config.store_connection(uri, "gui-pw", config.CONNECTION_KIND_GUI)

    tools = _registered_tools(monkeypatch)
    db_functions._sessions.clear()

    with pytest.raises(ToolError) as refused:
        tools["db.connect"](STDIO_CONTEXT, uri)

    assert "not a configured connection" in str(refused.value)


def test_a_session_is_reopened_against_its_own_list(gui_mode, clean_config):
    """The re-validation every open makes is per list, not per URI.

    A connection deleted from the GUI list has to be revoked even where the MCP
    list happens to name the same server - which it would not be if any list
    holding the URI counted.
    """
    uri = "mariadb://gui_revoke@127.0.0.1:3306"
    config.store_connection(uri, "mcp-pw")

    with pytest.raises(mysqlsh.Error) as refused:
        db_functions._open_session(uri, config.CONNECTION_KIND_GUI)

    assert "no longer a configured connection" in str(refused.value)


# --- the real command line -------------------------------------------------


def test_the_gui_option_is_a_real_command_line_option():
    """--gui reaches a server the shell really started, and changes its tools.

    Everything above drives the plugin in-process, so none of it would notice
    the option being spelled differently on the command line - or the shell
    swallowing it. This starts a server the way the extension does and asks it
    what it serves.
    """
    pytest.importorskip("mcp")

    plain = helpers.list_tool_names(["db"])
    assert "db.add_connection" not in plain
    assert "db.delete_connection" not in plain
    assert "db.test_connection" not in plain
    assert "db.update_connection" not in plain
    assert "db.list_connections" in plain

    served = helpers.list_tool_names(["db"], gui=True)
    assert "db.add_connection" in served
    assert "db.delete_connection" in served
    assert "db.test_connection" in served
    assert "db.update_connection" in served
    assert "db.list_connections" in served


# --- serving GUI mode over HTTP --------------------------------------------


def test_serving_gui_mode_over_http_is_warned_about(capsys):
    """It is allowed, because there are reasons to, and it is said out loud.

    GUI mode assumes the client is the extension, which owns the process and
    talks over stdio. Over HTTP nothing checks that, and the server has no
    authentication - so the assumption is simply handed to whoever reaches the
    port, full file access included.
    """
    server._warn_if_gui_mode_over_http(True, "127.0.0.1", 8080)
    warning = capsys.readouterr().err

    assert "--gui over HTTP" in warning
    assert "127.0.0.1:8080" in warning
    assert "NO AUTHENTICATION" in warning

    server._warn_if_gui_mode_over_http(False, "127.0.0.1", 8080)
    assert capsys.readouterr().err == ""


def test_the_gui_option_reaches_the_server(monkeypatch):
    """mcp.startServer forwards --gui, and defaults it off.

    Read off the option dict rather than from a positional, so an older shell
    passing nothing gets a server that behaves exactly as it did before.
    """
    import mcp_plugin.server as start_server_module
    from mcp_plugin.lib import server as server_lib

    started = []
    monkeypatch.setattr(
        server_lib, "start", lambda **kwargs: started.append(kwargs)
    )

    start_server_module.start_server(transport="stdio", gui=True)
    assert started[-1]["gui"] is True

    start_server_module.start_server(transport="stdio")
    assert started[-1]["gui"] is False

    # The options the CLI hands over as comma-separated strings still arrive as
    # lists, which --gui must not have disturbed.
    start_server_module.start_server(
        transport="stdio", function_groups="db, msm", allowed_hosts="a, b"
    )
    assert started[-1]["function_groups"] == ["db", "msm"]
    assert started[-1]["allowed_hosts"] == ["a", "b"]
