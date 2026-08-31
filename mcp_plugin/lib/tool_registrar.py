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

"""Tool registration that reports a tool's own failure text to the client.

By default the SDK replaces the message of an unanticipated exception with a
generic "Error executing tool <name>", keeping the detail server-side. These
tools wrap shell APIs whose exception text is the useful part of the answer, so
the decorator here re-raises as ``ToolError`` to let that text through.

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
