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

"""Tests for lib/tool_registrar.py, which db, msm and sandbox register through.

What a CLIENT ends up seeing is covered by the stdio round trips elsewhere
(`test_db_sql.py`, `test_msm.py`, `test_sandbox.py`): those are the tests that
went red when the module was deleted against MCP SDK 2.1, and they are the ones
that matter. What they cannot reach is the wrapper's behaviour for exception
types no tool group raises today, which is precisely where getting it wrong is
silent - a converted `MCPError` still produces a plausible-looking error result,
just the wrong KIND of one. Hence these, which call the wrapper directly.

`_FakeServer` stands in for the MCPServer so nothing here starts a server or
speaks the protocol: it hands the wrapper straight back, which is the whole
object under test.
"""

# cSpell:ignore mysqlsh

import asyncio

import pytest

from mcp_plugin.lib.tool_registrar import tool_registrar


class _FakeServer:
    """An MCPServer stand-in whose ``tool`` returns the wrapper it is given.

    Records what it was called with, so the forwarding of registration
    arguments can be asserted too.
    """

    def __init__(self):
        self.calls = []

    def tool(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return lambda wrapper: wrapper


def _register(func, **kwargs):
    """Registers ``func`` on a fake server and returns (wrapper, server)."""
    server = _FakeServer()
    wrapper = tool_registrar(server)(**kwargs)(func)
    return wrapper, server


# --- what the three groups actually raise ---------------------------------


def test_a_shell_error_becomes_a_tool_error_with_its_text_intact():
    """The conversion this module exists for.

    `mysqlsh.Error` is the one type db, msm and sandbox raise, and on SDK 2.1 it
    is also the one the SDK would treat as a crash and strip the message from.
    The text has to survive verbatim, `Shell Error: ` prefix included - that
    prefix is `str(mysqlsh.Error)`, not something added here.
    """
    pytest.importorskip("mcp")

    from mcp.server.mcpserver.exceptions import ToolError

    import mysqlsh

    def refuses():
        raise mysqlsh.Error("Access to path '/nope' is not allowed.")

    wrapper, _ = _register(refuses, name="db.refuses")

    with pytest.raises(ToolError) as raised:
        wrapper()

    assert str(raised.value) == "Shell Error: Access to path '/nope' is not allowed."
    assert isinstance(raised.value.__cause__, mysqlsh.Error)


def test_a_shell_error_from_an_async_tool_becomes_a_tool_error():
    """The same conversion on the async path.

    There are two wrappers, picked by `inspect.iscoroutinefunction`, and the msm
    and sandbox tools that hit the path guard are async - so the async branch is
    not the incidental one.
    """
    pytest.importorskip("mcp")

    from mcp.server.mcpserver.exceptions import ToolError

    import mysqlsh

    async def refuses():
        raise mysqlsh.Error("'x@y:3306' is not a configured connection.")

    wrapper, _ = _register(refuses, name="msm.refuses")

    with pytest.raises(ToolError) as raised:
        asyncio.run(wrapper())

    assert str(raised.value) == "Shell Error: 'x@y:3306' is not a configured connection."


# --- what must pass through untouched ------------------------------------


def test_a_tool_error_is_re_raised_as_the_same_object():
    """A tool that already raised the right type is not re-wrapped.

    Asserted by identity rather than by message: re-wrapping would produce an
    equal-looking `ToolError` and lose the original's `__cause__`.
    """
    pytest.importorskip("mcp")

    from mcp.server.mcpserver.exceptions import ToolError

    original = ToolError("said it properly the first time")

    def raises_tool_error():
        raise original

    wrapper, _ = _register(raises_tool_error, name="db.tool_error")

    with pytest.raises(ToolError) as raised:
        wrapper()

    assert raised.value is original


def test_a_resource_error_is_re_raised_as_the_same_object():
    """`ResourceError` is reported with its own message by the SDK too.

    It reaches a tool from `ctx.read_resource()`, and SDK 2.1 handles it in the
    same branch as `ToolError`, so converting it here would gain nothing and
    lose the type.
    """
    pytest.importorskip("mcp")

    from mcp.server.mcpserver.exceptions import ResourceError

    original = ResourceError("that resource is not readable")

    def raises_resource_error():
        raise original

    wrapper, _ = _register(raises_resource_error, name="db.resource_error")

    with pytest.raises(ResourceError) as raised:
        wrapper()

    assert raised.value is original


def test_a_protocol_error_is_not_downgraded_to_a_tool_error():
    """An `MCPError` means "answer with a JSON-RPC error", and must stay one.

    The SDK re-raises it ahead of its own generic handler for that reason. This
    wrapper caught bare `Exception` before, so it would have converted one into
    a tool failure - not reachable through today's tools, since
    `general._confirm_trust_path` catches everything `ctx.elicit` can raise, but
    the wrapper sits in front of every db, msm and sandbox tool and must not be
    the thing that swallows a protocol error.
    """
    pytest.importorskip("mcp")

    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.shared.exceptions import MCPError

    original = MCPError(-32601, "method not found")

    async def raises_protocol_error():
        raise original

    wrapper, _ = _register(raises_protocol_error, name="sandbox.protocol_error")

    with pytest.raises(MCPError) as raised:
        asyncio.run(wrapper())

    assert raised.value is original
    assert not isinstance(raised.value, ToolError)


# --- the registration side ------------------------------------------------


def test_registration_arguments_and_the_tool_identity_are_forwarded():
    """The decorator is a `server.tool` replacement, not a reinterpretation.

    Whatever it is handed goes to `server.tool` unchanged, and the wrapper keeps
    the wrapped function's name and docstring - the SDK builds a tool's schema
    and description from those, so losing them would change what clients are
    advertised.
    """
    pytest.importorskip("mcp")

    def documented_tool():
        """A one-line description the SDK would publish."""
        return "fine"

    wrapper, server = _register(
        documented_tool, name="db.documented", title="Documented"
    )

    assert wrapper() == "fine"
    assert server.calls == [((), {"name": "db.documented", "title": "Documented"})]
    assert wrapper.__name__ == "documented_tool"
    assert wrapper.__doc__ == "A one-line description the SDK would publish."
