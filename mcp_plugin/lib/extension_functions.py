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

"""MCP tools that other shell plugins offer: extension groups.

Any shell plugin can offer a function as an MCP tool where it defines it::

    @plugin_function("orders.listOpen", mcp={"group": "orders", "readOnly": True})
    def list_open(customer, limit=20, session=None):
        ...

The shell keeps every ``@plugin_function`` in its registry
(``mysqlsh.plugin_manager.registrar.get_registry()``), with the ``mcp``
definition checked and its defaults filled in. This module reads the
registry when the server is built - every plugin is loaded by then - and
registers each function of a served extension group as the tool
``<group>.<name>``. The plugin imports neither this plugin nor the MCP SDK.

What a tool is published with comes from the function's signature and its
docstring, as the shell parsed it: the parameters, typed from their
``Args:`` entries, the brief, the details and the arguments as the
description, ``**options`` as one ``options`` object. Two parameter names
are filled in rather than published:

* ``session`` is the session of a connection the client opened with
  ``db.connect``: the tool takes that ``connection_id`` instead, and the
  function runs inside :func:`mcp_plugin.lib.db_functions.use_session`, with
  every safeguard a db tool has. Such a tool needs the db group served.
* ``task``, in a tool with ``task``, is the background task the function
  runs as (``mysqlsh.task_manager``): the tool returns the task's id at once
  and the ``task.*`` tools follow it. Its session is a new one, opened for
  the task with :func:`mcp_plugin.lib.db_functions.session_opener`.

The parameters named in ``paths`` are checked against the allowed paths,
the user asked to trust one that is not, before anything else happens.

An extension group is served only when it is asked for: named in
``--functionGroups``, or in the ``extensionGroups`` setting that ``mcp
setup --extensionGroups`` and the setup menu edit, which serves it by
default. Installing a plugin alone never gives agents new tools. No
extension group is served to tenants.

Nothing here imports the MCP SDK at module scope (see
:mod:`mcp_plugin.lib.tool_registrar`): the setup and the server's checks
read the registry long before a server is built.
"""

import datetime
import decimal
import inspect
import re
from typing import Any, Optional

from mcp_plugin.lib import general
from mcp_plugin.lib.tool_registrar import tool_error

# The parameters filled in instead of published (see the module docstring)
SESSION_PARAMETER = "session"
TASK_PARAMETER = "task"

# What a tool with a session takes instead, and the name ctx, which every
# tool function is given, and which a plugin function cannot use therefore
CONNECTION_ID_PARAMETER = "connection_id"
CONTEXT_PARAMETER = "ctx"

# The one argument **options is published as
OPTIONS_PARAMETER = "options"

# How many tasks of one extension group a client may have running at once
MAX_RUNNING_EXTENSION_TASKS = 4

# A group or tool name, as the shell checks it
_NAME = re.compile("^[a-z][a-z0-9_]*$")

# The groups no extension may use: the built-in ones, and the task tools'
_RESERVED_GROUPS = (*general.SUPPORTED_FUNCTION_GROUPS, "task")

# The parameter types of the shell's docstring grammar, as Python types
_TYPES = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "bool": bool,
    "boolean": bool,
    "float": float,
    "dictionary": dict,
    "dict": dict,
    "array": list,
    "list": list,
}

# Claude Code cuts a tool description loaded upfront here (see server.md)
MAX_DESCRIPTION_LENGTH = 4096


class ExtensionTool:
    """One registry entry offered as an MCP tool, and what it is published as.

    Attributes:
        definition: The shell's FunctionData.
        group (str): The extension group.
        name (str): The tool name within the group.
        tool_name (str): ``<group>.<name>``.
        mcp (dict): The checked mcp definition.
        parameters (list): The published parameters, as ``(name, annotation,
            default)``, the default ``inspect.Parameter.empty`` for a required
            one.
        takes_session (bool): Whether the function has a session parameter.
        takes_task (bool): Whether it is given the task it runs as.
        options_name (str): The name of its ``**`` parameter, or None.
        problem (str): Why the function cannot be a tool, or None.
    """

    def __init__(self, definition):
        self.definition = definition
        self.mcp = definition.mcp
        self.group = self.mcp["group"]
        self.name = self.mcp["name"]
        self.tool_name = f"{self.group}.{self.name}"
        self.parameters = []
        self.takes_session = False
        self.takes_task = False
        self.options_name = None
        self.problem = None
        self._documented = {
            parameter.definition.name: parameter
            for parameter in definition.parameters
        }

        try:
            self._read_signature()
        except ValueError as e:
            self.problem = str(e)

    @property
    def source(self) -> str:
        """The function as the shell names it, e.g. ``orders.listOpen``."""
        return self.definition.fully_qualified_name or self.definition.name

    def _read_signature(self) -> None:
        """Sorts the function's parameters into published and filled in."""
        signature = inspect.signature(self.definition.function)
        published = []
        for parameter in signature.parameters.values():
            name = parameter.name
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError(f"it takes *{name}, which a tool cannot pass")
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                raise ValueError(f"its parameter {name} is positional-only")
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                self.options_name = name
                continue
            if name == SESSION_PARAMETER:
                self.takes_session = True
                continue
            if name == TASK_PARAMETER and self.mcp["task"]:
                self.takes_task = True
                continue
            if name.startswith("_") or name in (
                CONTEXT_PARAMETER, CONNECTION_ID_PARAMETER, OPTIONS_PARAMETER
            ):
                raise ValueError(
                    f"its parameter {name} is a name the tool cannot publish"
                )
            published.append(
                (name, self._annotation(name, parameter.default), parameter.default)
            )

        if self.takes_session:
            published.insert(0, (CONNECTION_ID_PARAMETER, str, inspect.Parameter.empty))
        if self.options_name is not None:
            published.append((OPTIONS_PARAMETER, Optional[dict], None))
        self.parameters = published

    def _annotation(self, name, default):
        """The type a parameter is published with, from its documentation."""
        documented = self._documented.get(name)
        python_type = _TYPES.get(documented.type if documented else "", Any)
        if default is None and python_type is not Any:
            return Optional[python_type]
        return python_type

    def description(self) -> str:
        """The tool's description: the docstring, the arguments as published."""
        docs = self.definition.docs
        lines = [docs.brief or self.source]

        details = [_plain_text(line) for line in (docs.details or [])]
        details = [line for line in details if line]
        if details:
            lines.append("")
            lines.extend(details)

        arguments = []
        for name, _annotation, _default in self.parameters:
            if name == CONNECTION_ID_PARAMETER:
                arguments.append(f"    {name}: The UUID returned by db.connect.")
                continue
            documented = self.options_name if name == OPTIONS_PARAMETER else name
            arguments.append(f"    {name}: {self._brief(documented)}")
            arguments.extend(self._option_lines(documented))
        if arguments:
            lines.extend(["", "Args:", *arguments])

        if self.mcp["task"]:
            lines.extend([
                "",
                "Runs as a background task: returns its task_id at once, "
                "which task.get follows.",
            ])

        return "\n".join(lines)

    def _brief(self, name) -> str:
        documented = self._documented.get(name)
        return documented.docs.brief if documented else ""

    def _option_lines(self, name) -> list:
        """The documented options of a dict parameter, one line each."""
        documented = self._documented.get(name)
        if documented is None:
            return []
        lines = []
        for option in documented.options:
            kind = f" ({option.type})" if option.type else ""
            required = ", required" if option.required else ""
            lines.append(f"        {option.name}{kind}{required}: {option.docs.brief}")
        return lines

    def arguments(self, published: dict) -> tuple:
        """Splits a call's arguments into the connection and the function's.

        Args:
            published (dict): The arguments as the tool was called with them.

        Returns:
            tuple: The connection id (or None) and the keyword arguments of
            the function, ``options`` spread into them.
        """
        arguments = dict(published)
        connection_id = arguments.pop(CONNECTION_ID_PARAMETER, None)
        options = arguments.pop(OPTIONS_PARAMETER, None)
        if options:
            clashing = sorted(set(options) & set(arguments))
            if clashing:
                raise tool_error(
                    f"The options of {self.tool_name} cannot set "
                    f"{', '.join(clashing)}, which are arguments of their own."
                )
            arguments.update(options)

        return connection_id, arguments


def _plain_text(line) -> str:
    """A line of the shell's help markup as plain text."""
    return (
        str(line).replace("<b>", "").replace("</b>", "").replace("@li ", "- ").strip()
    )


def _registry() -> list:
    """The shell's plugin function registry: every @plugin_function."""
    try:
        from mysqlsh.plugin_manager import registrar
    except ImportError:  # pragma: no cover - a shell without plugin support
        return []

    return list(registrar.get_registry())


def extension_tools() -> list:
    """The functions the loaded plugins offer as MCP tools.

    A shell without the mcp argument has no ``mcp`` attribute on its
    entries, which reads as none offered. A group that is a built-in one is
    left out here, as is a second function of the same tool name: both are
    reported when the server is built (see :func:`register_extension_tools`).

    Returns:
        list: :class:`ExtensionTool` objects, in registration order.
    """
    return [
        ExtensionTool(definition)
        for definition in _registry()
        if getattr(definition, "mcp", None)
    ]


def available_groups() -> dict:
    """The extension groups the loaded plugins offer, and their tools.

    Returns:
        dict: Each group a usable name (not a built-in group's), to the
        sorted names of the functions offering tools in it, e.g.
        ``{"orders": ["orders.listOpen"]}``.
    """
    groups = {}
    for definition in _registry():
        mcp = getattr(definition, "mcp", None)
        if mcp and mcp["group"] not in _RESERVED_GROUPS:
            groups.setdefault(mcp["group"], []).append(
                definition.fully_qualified_name or definition.name
            )

    return {group: sorted(sources) for group, sources in sorted(groups.items())}


def extension_groups_in(function_groups) -> list:
    """The groups of a server's function groups that are not built-in ones.

    Args:
        function_groups: The groups a server serves.

    Returns:
        list: Those that are extension groups, in the order given.
    """
    return [
        group for group in function_groups
        if group not in general.SUPPORTED_FUNCTION_GROUPS
    ]


def check_group_name(group) -> str:
    """Returns an extension group's name, or raises saying why it is not one.

    Raises:
        ValueError: If it is not lower case letters, digits and underscores,
            or names a built-in group.
    """
    group = str(group).strip()
    if not _NAME.match(group):
        raise ValueError(
            f"'{group}' is not an extension group name: lower case letters, "
            "digits and underscores, starting with a letter."
        )
    if group in _RESERVED_GROUPS:
        raise ValueError(
            f"'{group}' is a built-in function group, not an extension group."
        )

    return group


def _plain(value):
    """A function's result as JSON values: what a tool can return.

    A shell result (of ``run_sql``) becomes the rows of its result set, as
    ``db.execute_sql`` returns them; datetimes and decimals become their
    text, anything else unknown its ``str``.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if hasattr(value, "fetch_all") and hasattr(value, "get_columns"):
        # Read as db.execute_sql reads a result set: the same labels and the
        # same text for binary, date and time values
        from mcp_plugin.lib import db_functions

        return db_functions._read_result_set(value)["rows"]
    if hasattr(value, "keys") and hasattr(value, "__getitem__"):
        # The shell's own Dict
        return {str(key): _plain(value[key]) for key in value.keys()}
    if hasattr(value, "__iter__") and hasattr(value, "__len__"):
        # The shell's own List
        return [_plain(item) for item in value]

    return str(value)


def _make_tool_function(tool, description, run):
    """Builds the function the SDK registers: the tool's published signature.

    The SDK builds a tool's schema from its function's signature and calls it
    with keyword arguments, so the function takes ``**arguments`` and states
    the published parameters in its ``__signature__``, as
    :func:`mcp_plugin.lib.tool_registrar.register_with_gui_argument` does:
    every one keyword-only, so the order of required and optional ones does
    not matter, after ``ctx``, typed as the SDK's Context so the SDK fills it
    in.

    Args:
        tool (ExtensionTool): The tool.
        description (str): Its description.
        run: ``async run(ctx, arguments)``, called with the published
            arguments as a dict.

    Returns:
        The async function.
    """
    from mcp.server.mcpserver import Context

    async def function(ctx: Context, **arguments):
        return await run(ctx, arguments)

    function.__name__ = function.__qualname__ = f"{tool.group}__{tool.name}"
    function.__doc__ = description
    function.__signature__ = inspect.Signature([
        inspect.Parameter(CONTEXT_PARAMETER, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          annotation=Context),
        *(
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY,
                              annotation=annotation, default=default)
            for name, annotation, default in tool.parameters
        ),
    ])

    return function


def register_extension_tools(server, function_groups) -> bool:
    """Registers the tools of the served extension groups.

    A tool is left out, with a line in the log saying why, when its group is
    a built-in one, its name is taken already, its function cannot be
    published, or it needs a session and the db group is not served. A tool
    with ``gui`` is left out quietly without ``--gui``, as the db tools only
    that mode has are.

    Args:
        server: The MCPServer.
        function_groups: Every group the server serves: the extension groups
            among them are served, and the db group decides about the tools
            that work on a connection.

    Returns:
        bool: Whether a tool that starts tasks was registered, so the task
        tools are needed.
    """
    from mcp.types import ToolAnnotations

    from mcp_plugin.lib.tool_registrar import tool_registrar

    register = tool_registrar(server)
    groups = set(extension_groups_in(function_groups))
    serves_db = general.FUNCTION_GROUP_DB in function_groups
    registered = {}
    starts_tasks = False

    for tool in extension_tools():
        if tool.group not in groups:
            continue

        refusal = None
        if tool.group in _RESERVED_GROUPS:
            refusal = f"{tool.group} is a built-in function group"
        elif tool.problem:
            refusal = tool.problem
        elif tool.tool_name in registered:
            refusal = f"{registered[tool.tool_name]} already offers {tool.tool_name}"
        elif tool.takes_session and not serves_db:
            refusal = "it works on a connection, and the db group is not served"
        if refusal:
            general.log_event(
                f"server: extension tool {tool.tool_name} of {tool.source} "
                f"not served: {refusal}"
            )
            continue
        if tool.mcp["gui"] and not general.is_gui_mode():
            continue

        description = tool.description()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            general.log_event(
                f"server: the description of extension tool {tool.tool_name} "
                f"is {len(description)} characters long; Claude Code cuts it "
                f"at {MAX_DESCRIPTION_LENGTH}"
            )

        function = _make_tool_function(tool, description, _runner(tool))
        annotations = None
        if tool.mcp["readOnly"] or tool.mcp["destructive"]:
            annotations = ToolAnnotations(
                readOnlyHint=tool.mcp["readOnly"] or None,
                destructiveHint=tool.mcp["destructive"] or None,
            )
        register(name=tool.tool_name, annotations=annotations)(function)
        registered[tool.tool_name] = tool.source
        starts_tasks = starts_tasks or tool.mcp["task"]
        general.log_event(
            f"server: serving extension tool {tool.tool_name} of {tool.source}"
        )

    return starts_tasks


def _call(tool, arguments, session=None):
    """Calls a tool's function, with the session where it takes one."""
    if session is not None:
        arguments = {**arguments, SESSION_PARAMETER: session}

    return _plain(tool.definition.function(**arguments))


def _runner(tool):
    """Returns ``async run(ctx, arguments)``, which carries out a call."""
    import anyio.to_thread

    from mcp_plugin.lib import db_functions

    async def run(ctx, published):
        # Awaited before any session is taken: asking the user to trust a
        # path must not happen while a connection's lock is held.
        for path_parameter in tool.mcp["paths"]:
            await general.require_allowed_path(ctx, published.get(path_parameter))

        client = general.get_client_identity(ctx)
        connection_id, arguments = tool.arguments(published)

        if tool.mcp["task"]:
            return await _start_task(tool, client, connection_id, arguments)

        def call():
            if not tool.takes_session:
                return _call(tool, arguments)
            with db_functions.use_session(connection_id, client) as session:
                return _call(tool, arguments, session)

        return await anyio.to_thread.run_sync(call)

    return run


async def _start_task(tool, client, connection_id, arguments):
    """Starts a tool's function as a background task; returns its id."""
    import anyio.to_thread

    from mcp_plugin.lib import db_functions, tasks

    open_session = None
    if tool.takes_session:
        # Resolved before the task starts, so a wrong connection fails the
        # call itself. On a worker thread: dropping an expired connection
        # waits for it.
        open_session = await anyio.to_thread.run_sync(
            db_functions.session_opener, connection_id, client
        )

    attributes = {"tool": tool.tool_name}
    if connection_id is not None:
        attributes["connection_id"] = connection_id
    task = tasks.create(
        tool.group, tool.name, tool.tool_name, client,
        MAX_RUNNING_EXTENSION_TASKS, attributes,
    )

    def work(task):
        if tool.takes_task:
            arguments[TASK_PARAMETER] = task
        if open_session is None:
            return _call(tool, arguments)
        session = open_session()
        try:
            return _call(tool, arguments, session)
        finally:
            session.close()

    tasks.start(task, work)

    return {"task_id": task.id, "status": task.status}
