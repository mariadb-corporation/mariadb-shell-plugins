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

Used by the ``db``, ``msm`` and ``sandbox`` groups, whose tools call shell APIs
and shell plugin functions - ``shell.open_session``, a session's ``run_sql``,
the ``msm`` plugin - which raise ``mysqlsh.Error`` and ``mysqlsh.DBError``. The
tools' own refusals are raised as ``ToolError`` from the start, through
:func:`tool_error`. The ``migrator`` group does not use the registrar: those
tools drive a program of their own, are not wrappers around anything, and raise
``ToolError`` themselves (see :mod:`mcp_plugin.lib.migrator_functions`).

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

The wrapper is also where a multi-tenant server checks who is calling, because
it is the one place every tool of every group passes through (see
:func:`_check_caller`): a tool that forgot the check would otherwise be a tool
anyone can call.

Nothing in this module imports the MCP SDK at module import time: the shell
imports this plugin package eagerly, and pulling in ``mcp`` that early binds
``mcp.client.stdio.stdio_client``'s ``errlog=sys.stderr`` default to the shell's
``mysqlsh.shell_stderr``, which has no usable ``fileno()``.
"""

import inspect
import re
from functools import wraps
from typing import Any, Callable


def tool_error(message: str) -> Exception:
    """Returns a ``ToolError`` carrying ``message``, for a tool to raise.

    A function rather than an import because the plugin's modules are imported
    when the shell starts, and ``ToolError`` pulls in the MCP SDK (see the
    module docstring); by the time a tool raises one, the server has loaded it.

    Args:
        message (str): What the client is told.

    Returns:
        The ``ToolError``.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    return ToolError(message)


# --- Published tool names ----------------------------------------------------
#
# The tools are registered as ``<group>.<name>``. A server whose
# ``mcp setup --toolNameSeparator`` is not ``.`` publishes them with that
# separator instead, for gateways that refuse a dot in a tool name although the
# MCP specification (SEP-986) allows it - Arcade, and OpenAI's function names.
# The tools also name each other, in their descriptions ("the UUID returned by
# db.connect") and in their errors, so those are rewritten to match: a model
# told to call a tool that does not exist would be worse than no hint at all.

_separator = "."

# The tools registered under a dotted name, which is what gets rewritten; a
# dotted word that is not one of them (a table, a schema) never is.
_registered = set()

_TOOL_REFERENCE = re.compile(r"\b[a-z]+\.[a-z_]+\b")


def tool_group(tool_name) -> str:
    """Returns the group of a tool, whichever separator its name uses."""
    return re.split(r"[._-]", str(tool_name), maxsplit=1)[0]


def published_name(tool_name, separator=None) -> str:
    """Returns the name a tool is published under.

    Args:
        tool_name: The tool's name, as group.name.
        separator (str): The separator to publish it with, or None for the
            one the server being built uses (see use_tool_name_separator).
    """
    return str(tool_name).replace(
        ".", _separator if separator is None else separator, 1
    )


def translate_tool_names(text):
    """Rewrites the tools a text names to the names they are published under."""
    if _separator == "." or not text:
        return text

    return _TOOL_REFERENCE.sub(
        lambda match: published_name(match.group(0)) if match.group(0) in _registered
        else match.group(0),
        text,
    )


def _translating_errors(func):
    """Wraps a tool so that the tools its errors name are published names."""
    from mcp.server.mcpserver.exceptions import ToolError

    @wraps(func)
    async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
        try:
            return await func(*call_args, **call_kwargs)
        except ToolError as error:
            raise ToolError(translate_tool_names(str(error))) from error

    @wraps(func)
    def sync_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
        try:
            return func(*call_args, **call_kwargs)
        except ToolError as error:
            raise ToolError(translate_tool_names(str(error))) from error

    return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper


def use_tool_name_separator(server, separator: str) -> None:
    """Makes every tool registered on server from now on use the separator.

    Called before the groups register their tools; :func:`finish_tool_names`
    after them. With ``.`` nothing is changed.

    Args:
        server: The MCPServer.
        separator (str): What separates a tool's group from its name.
    """
    global _separator

    _separator = separator
    _registered.clear()
    if separator == ".":
        return

    register = server.tool

    def tool(*args: Any, **kwargs: Any):
        name = kwargs.get("name")
        if name:
            _registered.add(name)
            kwargs = {**kwargs, "name": published_name(name)}
        decorate = register(*args, **kwargs)

        return lambda func: decorate(_translating_errors(func))

    server.tool = tool


# The schema keywords whose keys are names, each mapping to a schema.
_NAME_MAPS = ("properties", "patternProperties", "$defs", "definitions")


def _is_null(schema) -> bool:
    return schema == {"type": "null"}


def _trim_schema(schema):
    """Returns the published schema without what only costs tokens.

    The SDK builds it from the function's signature through pydantic, and
    only publishes it: a call is validated with the pydantic model, never
    with this schema, so what is dropped here changes nothing the server
    accepts. Dropped are:

    - the titles: every parameter gets its own name in words ("Connection
      Id") and every schema its function's name, which a model never uses;
    - the null alternative of an optional parameter: ``Optional[str] =
      None`` is published as ``anyOf [string, null], default null``, which
      a model reads as "leave it out" just as it reads a plain type that is
      not required - and an explicit null is still accepted, the model
      allows it. The ``default: null`` goes with it, as a null default of
      an omitted parameter says nothing and would not match the type left;
      real defaults (false, "json") stay.

    Together they were a fifth of the whole tool listing a client sends
    with every request. The keys of a name map (properties, $defs) are
    parameter and model names, not keywords: a parameter called title is
    kept.
    """
    if isinstance(schema, dict):
        if "anyOf" in schema:
            alternatives = schema["anyOf"]
            if len(alternatives) == 2 and any(map(_is_null, alternatives)):
                [kept] = [choice for choice in alternatives if not _is_null(choice)]
                rest = {
                    key: value
                    for key, value in schema.items()
                    if key != "anyOf" and not (key == "default" and value is None)
                }
                return _trim_schema({**kept, **rest})
        return {
            key: (
                {name: _trim_schema(value) for name, value in value.items()}
                if key in _NAME_MAPS and isinstance(value, dict)
                else _trim_schema(value)
            )
            for key, value in schema.items()
            if key != "title"
        }
    if isinstance(schema, list):
        return [_trim_schema(value) for value in schema]
    return schema


def finish_tool_names(server) -> None:
    """Rewrites the tools the registered tools' descriptions name, and
    trims every tool's published parameter schema (see _trim_schema).

    Afterwards, because a description can name a tool registered after its
    own. Reaches into the SDK's private tool manager, as the descriptions are
    only settled once a tool is registered; test_tool_names_with_a_separator
    pins it.
    """
    import json

    for tool in server._tool_manager.list_tools():
        tool.parameters = _trim_schema(tool.parameters)
        if _separator == ".":
            continue
        tool.description = translate_tool_names(tool.description)
        tool.parameters = json.loads(translate_tool_names(json.dumps(tool.parameters)))


def _tool_scope(tool_name) -> str:
    """Returns the scope a tool needs: ``mcp:`` and the tool's group."""
    return f"mcp:{tool_group(tool_name)}"


def _context_argument(signature, call_args, call_kwargs):
    """Returns the ``ctx`` a tool was called with, or None if it takes none."""
    try:
        bound = signature.bind_partial(*call_args, **call_kwargs)
    except TypeError:
        return None

    return bound.arguments.get("ctx")


def _check_caller(tool_name, signature, call_args, call_kwargs) -> None:
    """Refuses a tool call a multi-tenant server must not run.

    Every call has to come from an authenticated user - which the SDK's bearer
    authentication already demands of every HTTP request, so this is the
    second, independent check - and the user's token has to grant the scope of
    the tool's group. A server that is not multi-tenant checks nothing here.

    Args:
        tool_name (str): The name the tool is registered under.
        signature: The tool function's ``inspect.Signature``.
        call_args: The positional arguments it was called with.
        call_kwargs: The keyword arguments it was called with.

    Raises:
        ToolError: If the call is refused.
    """
    # Imported here: general imports this module.
    from mcp_plugin.lib import general

    if not general.is_multi_tenant():
        return

    principal = general.get_principal(_context_argument(signature, call_args, call_kwargs))
    if principal is None:
        raise tool_error(
            "This server serves authenticated users only, and the request was "
            "not authenticated."
        )

    scope = _tool_scope(tool_name)
    if scope not in principal.scopes:
        general.log_event(
            f"auth: REFUSED {published_name(tool_name)} to "
            f"{general.log_user(principal.mcp_user_id)}, whose token lacks the "
            f"scope {scope}"
        )
        raise tool_error(
            f"The tool {published_name(tool_name)} needs the scope '{scope}', which your "
            "access was not granted."
        )


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
            tool_name = kwargs.get("name") or func.__name__
            # Once, here, not on every call: it is what finds the ctx argument.
            signature = inspect.signature(func)

            @wraps(func)
            async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    _check_caller(tool_name, signature, call_args, call_kwargs)
                    return await func(*call_args, **call_kwargs)
                except reported_as_is:
                    raise
                except Exception as exc:
                    raise ToolError(str(exc)) from exc

            @wraps(func)
            def sync_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                try:
                    _check_caller(tool_name, signature, call_args, call_kwargs)
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
