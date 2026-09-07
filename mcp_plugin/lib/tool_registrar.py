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

**This is LOAD-BEARING on MCP SDK 2.1 and later**, and the reason is worth
knowing, because the SDK changed its mind and the module was briefly deleted on
the strength of the older behaviour (commit d530e97d, reverted):

* 1.28.x and 2.0.0 APPEND a tool's message whatever type was raised -
  ``Tool.run`` ends in a single ``except Exception`` that raises
  ``ToolError(f"Error executing tool {self.name}: {e}")``. Against those two,
  this module changes nothing a client sees.
* 2.1.0 sorts a failure into one of three buckets instead
  (``mcp/server/mcpserver/tools/base.py``): ``ToolError`` and ``ResourceError``
  keep their message, ``MCPError`` becomes a JSON-RPC protocol error, and
  **everything else is re-raised as
  ``UnexpectedToolError(f"Error executing tool {self.name}")``** - a crash,
  whose own text is logged server-side and withheld from the client.

``mysqlsh.Error`` lands in that third bucket, so without this module every
anticipated refusal - a path that is not allowed, an unknown connection id, an
unsupported object type - reaches the model as a bare "Error executing tool
<name>" with the reason stripped off. Measured, not read: deleting the module
turned exactly three tests red on the CI shell 26.9.0 (run 34115890193, PR #19)
and the same three locally once the bundled SDK was 2.1.1, while restoring it
returns the suite to green.

Converting here is portable rather than a patch for one version: on 1.28.x and
2.0.0 the client-visible payload is byte-identical either way, because this
raises ``ToolError(str(exc))`` and ``str(ToolError(s)) == s``, so the SDK
composes the same string whether or not it had to wrap anything.

Three exception types are deliberately NOT converted, mirroring the buckets
above: ``ToolError`` and ``ResourceError``, which the SDK already reports with
their own message, and ``MCPError``, which MEANS "answer with a protocol error"
- the SDK re-raises it ahead of its own generic handler for that reason, and
converting it here would quietly downgrade it to a tool failure.

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
            from mcp.server.mcpserver.exceptions import ResourceError, ToolError
            from mcp.shared.exceptions import MCPError

            # What the SDK already handles faithfully goes straight through; see
            # the module docstring for the buckets these mirror. All three names
            # have been importable from these two modules since 2.0.0, so naming
            # them does not tie the plugin to 2.1.
            reported_as_is = (ToolError, ResourceError, MCPError)

            @wraps(func)
            async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    return await func(*call_args, **call_kwargs)
                except reported_as_is:
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            @wraps(func)
            def sync_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    return func(*call_args, **call_kwargs)
                except reported_as_is:
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            wrapper = (
                async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper
            )
            return server.tool(*args, **kwargs)(wrapper)

        return decorator

    return mcp_tool
