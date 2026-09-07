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

"""Tool registration that re-raises a shell API's exception as a ``ToolError``.

Used by the ``db``, ``msm`` and ``sandbox`` groups, whose tools WRAP shell plugin
functions and so raise ``mysqlsh.Error``. The ``migrator`` group does not use it:
those tools drive a program of their own, are not wrappers around anything, and
raise ``ToolError`` themselves (see
:mod:`mcp_plugin.lib.migrator_functions`).

**On MCP SDK 2.0 this is belt to the SDK's braces, not load-bearing.** It was
added when the SDK was understood to replace an unanticipated exception's message
with a generic "Error executing tool <name>" and keep the detail server-side.
That is not what 2.0.0 does: ``Tool.run`` wraps EVERY exception as
``ToolError(f"Error executing tool {self.name}: {e}")``
(``mcp/server/mcpserver/tools/base.py:181``) and ``_handle_call_tool`` then puts
``str(e)`` into the content block (``mcp/server/mcpserver/server.py:424``), so the
original text is appended rather than replaced whatever type was raised. Measured,
not read: with the wrapper below reduced to a plain ``server.tool`` pass-through,
``test_sandbox_dir_outside_allowed_paths_is_rejected`` - a real stdio round trip
asserting on the message - still passes.

So this module could be dropped and the three groups registered directly. It is
kept because that is a change to three groups' error handling for no behavioural
gain, and because the premise may differ again on another SDK version - the
1.x-to-2.0 history here is full of such reversals. Do not, however, cite the
old "the SDK swallows the message" reasoning: it is not true of the SDK in use.

Nothing in this module imports the MCP SDK at module import time: the shell
imports this plugin package eagerly, and pulling in ``mcp`` that early binds
``mcp.client.stdio.stdio_client``'s ``errlog=sys.stderr`` default to the shell's
``mysqlsh.shell_stderr``, which has no usable ``fileno()``.
"""

import inspect
from functools import wraps
from typing import Any, Callable


def tool_registrar(server):
    """Returns a ``server.tool`` replacement bound to ``server``.

    The returned decorator factory forwards every argument to ``server.tool``
    unchanged, so it accepts whatever that accepts.

    Args:
        server: The MCPServer the tools are registered on.

    Returns:
        A decorator factory with the same signature as ``server.tool``.
    """

    def mcp_tool(*args: Any, **kwargs: Any):
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            from mcp.server.mcpserver.exceptions import ToolError

            @wraps(func)
            async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    return await func(*call_args, **call_kwargs)
                except ToolError:
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            @wraps(func)
            def sync_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    return func(*call_args, **call_kwargs)
                except ToolError:
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            wrapper = (
                async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper
            )
            return server.tool(*args, **kwargs)(wrapper)

        return decorator

    return mcp_tool
