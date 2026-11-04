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

"""MCP tools wrapping the MariaDB Schema Management (``msm``) plugin.

Each tool registered here wraps the corresponding function of the
``msm_plugin/management.py`` module and is exposed on the MCPServer built
in :mod:`mcp_plugin.lib.server`.

The tools run while the shell is in non-interactive mode, so the wrapped
``msm`` plugin functions return their results instead of prompting for input.
Path arguments are authorized through
:func:`mcp_plugin.lib.general.require_allowed_path`, which may ask the user - via
MCP elicitation - to trust a path that is not yet allowed.

The project list tools (``msm.list_projects`` and the three that change the
list) go to :mod:`msm_plugin.lib.projects` directly rather than through the
shell-facing wrappers: in multi-tenant mode every user has a list of their
own, selected by the ``owner`` those functions take, and the shell functions -
which serve the shell user's one list - have no business taking it. The owner
is the calling user's ``mcp_user_id`` there and None otherwise.

Every entry of the list says whether LLMs may work on the project
(``mcp_access``). Only the ``--gui`` server - the VS Code extension's - sets it
and sees the projects that say no; a server without ``--gui`` leaves them out
of msm.list_projects and refuses every path in them (see
:func:`mcp_plugin.lib.general.require_project_access`), so an agent can neither
read such a project nor grant itself access. A project the extension adds
starts without access, one an agent adds with it.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

import inspect
from typing import Optional

from mcp_plugin.lib import db_functions, general
from mcp_plugin.lib.tool_registrar import tool_registrar


def _project_list_owner(ctx):
    """Returns whose project list a request works on: the user's, or None."""
    return general.get_client_identity(ctx).user


# The arguments only a --gui server advertises, by tool, with the lines that
# describe them; each tool's docstring has a placeholder line for them.
_GUI_ARGUMENT_DOCS = {
    "msm.create_project": """            mcp_access: Whether LLMs may work on the project through an MCP
                server started without --gui. Defaults to False: the
                project stays the extension's until the user says otherwise.
""",
    "msm.add_project": """            mcp_access: Whether LLMs may work on the project through an MCP
                server started without --gui. Left out, a listed project
                keeps its setting and a new one gets none.
""",
    "msm.update_project": """            new_mcp_access: Whether LLMs may work on the project through an
                MCP server started without --gui. Left out, it keeps its
                setting.
""",
}

_GUI_ARGUMENTS = {
    "msm.create_project": "mcp_access",
    "msm.add_project": "mcp_access",
    "msm.update_project": "new_mcp_access",
}


def _register_with_gui_argument(tool, name, function) -> None:
    """Registers a tool whose MCP access argument only a --gui server offers.

    Whether LLMs may work on a project is the user's call, made in the VS Code
    extension: a server without --gui serves the agents the setting restricts,
    so the argument is taken out of the signature the SDK builds the schema
    from - and its lines out of the description - rather than advertised and
    refused, as sandbox.deploy does with its own. The function keeps the
    parameter at its default.

    Args:
        tool: The tool decorator factory of the server.
        name (str): The tool's name.
        function: The tool's implementation.

    Returns:
        None
    """
    argument = _GUI_ARGUMENTS[name]
    # The placeholder line sits at column 0, so the compiler's docstring
    # dedent found nothing to strip; cleandoc does it once it is filled.
    if general.is_gui_mode():
        function.__doc__ = function.__doc__.replace(
            "{gui_argument_doc}\n", _GUI_ARGUMENT_DOCS[name]
        )
    else:
        function.__doc__ = function.__doc__.replace("{gui_argument_doc}\n", "")
        signature = inspect.signature(function)
        function.__signature__ = signature.replace(
            parameters=[
                parameter
                for parameter in signature.parameters.values()
                if parameter.name != argument
            ]
        )
    function.__doc__ = inspect.cleandoc(function.__doc__)

    tool(name=name)(function)


def register_msm_tools(server, function_groups=()) -> None:
    """Registers the MariaDB Schema Management tools on the given server.

    All tools but msm.deploy_schema work on a schema project on disk and are
    always registered. msm.deploy_schema needs a database connection opened
    with db.connect, so it is only registered when the db function group is
    served as well - it would have no way to obtain a connection otherwise.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    import os

    import anyio.to_thread
    from mcp.server.mcpserver import Context
    from msm_plugin import management as msm
    from msm_plugin.lib import management as msm_management
    from msm_plugin.lib import projects as msm_projects

    tool = tool_registrar(server)

    def _kwargs(**pairs) -> dict:
        """Builds a kwargs dict, dropping keys whose value is None."""
        return {key: value for key, value in pairs.items() if value is not None}

    async def _authorize_project(ctx, schema_project_path) -> None:
        """Authorizes a project path; None is the working directory.

        The working directory is where the plugin goes for a project left
        out, so a project LLMs may not work on is refused there too.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        if schema_project_path is None:
            general.require_project_access(ctx, os.getcwd())

    async def create_project(
        ctx: Context,
        schema_name: str,
        target_path: str,
        copyright_holder: Optional[str] = None,
        license: Optional[str] = None,
        overwrite_existing: bool = False,
        allow_special_chars: bool = False,
        enforce_target_path: bool = False,
        copyrights: Optional[list] = None,
        mcp_access: bool = False,
    ) -> Optional[str]:
        """Creates a new database schema project folder.

        Args:
            schema_name: The name of the database schema.
            target_path: The path the project folder should be created in.
            copyright_holder: The name of the copyright holder.
            license: The license to use for the project.
            overwrite_existing: Overwrite the project folder if it exists.
            allow_special_chars: Allow all characters in the schema name.
            enforce_target_path: Create the target_path if it does not exist.
            copyrights: The copyright notices of the project, for a project held
                by more than one copyright holder. Each entry takes a holder, a
                yearOfCreation, an optional yearOfLastUpdate and a tracksUpdates
                flag marking the holders whose year follows the current one.
                When given, it takes the place of copyright_holder.
{gui_argument_doc}

        Returns:
            The path of the created project folder. The project is added to
            the project list (see msm.list_projects) at the top level.
        """
        await general.require_allowed_path(ctx, target_path)
        if overwrite_existing:
            # Replacing a project deletes it first: not one kept from LLMs.
            general.require_project_access(
                ctx,
                os.path.join(
                    os.path.abspath(os.path.expanduser(target_path)),
                    msm_management.convert_string_to_valid_filename(schema_name)
                    + ".msm.project",
                ),
            )
        # Read before anything else: the caller's list is where the project
        # goes, not the shell user's, which the wrapper would add it to.
        owner = _project_list_owner(ctx)
        project_path = msm.create_new_project_folder(
            schema_name=schema_name,
            target_path=target_path,
            copyright_holder=copyright_holder,
            add_to_project_list=False,
            **_kwargs(
                license=license,
                overwrite_existing=overwrite_existing,
                allow_special_chars=allow_special_chars,
                enforce_target_path=enforce_target_path,
                copyrights=copyrights,
            ),
        )
        # The extension's projects start as its own; an agent's have to be
        # open to it, or it could not go on with what it made.
        msm_projects.add_project(
            project_path,
            owner=owner,
            mcp_access=mcp_access if general.is_gui_mode() else True,
        )

        return project_path

    _register_with_gui_argument(tool, "msm.create_project", create_project)

    @tool(name="msm.list_projects")
    async def list_projects(ctx: Context) -> list:
        """Lists the schema projects in the project list.

        The project list holds the schema projects the user works with, so a
        client can show them without searching the disk. Each is filed in a
        folder of the list and may carry a caption and a color. A project
        enters the list through msm.add_project, or when msm.create_project
        creates it.

        Returns:
            One entry per project, sorted by path: its "project_path", the
            "folder" of the list it is filed in ("/" for the top level, for
            example "/Shop/Production" for a nested folder), the "caption"
            and "color" to show it with (each "" for none), whether LLMs may
            work on it through a server without --gui ("mcp_access"; such a
            server lists only those that may), the "schema_name"
            its settings file states (null where that cannot be read) and
            whether the folder still "exists" as a project - one that was
            moved or deleted stays listed, so it can be shown as missing and
            removed.
        """
        return msm_projects.list_projects(
            _project_list_owner(ctx), mcp_access_only=not general.is_gui_mode()
        )

    async def add_project(
        ctx: Context,
        project_path: str,
        folder: Optional[str] = None,
        caption: Optional[str] = None,
        color: Optional[str] = None,
        mcp_access: Optional[bool] = None,
    ) -> dict:
        """Adds an existing schema project to the project list.

        Adding a project that is listed already changes the details given
        and keeps the others, so this is safe to call again.

        Args:
            project_path: The path of the schema project folder, which has to
                hold a msm.project.json.
            folder: The folder of the list to file it in, such as "/Shop";
                "/" is the top level. Left out, a new entry goes to the top
                level and a listed one stays where it is.
            caption: The caption to show it with; "" removes it. Left out, a
                listed project keeps its caption.
            color: The color to show it in (red, orange, yellow, green, blue
                or purple); "" removes it. Left out, a listed project keeps
                its color.
{gui_argument_doc}

        Returns:
            The project as msm.list_projects reports it.
        """
        await general.require_allowed_path(ctx, project_path)
        gui = general.is_gui_mode()
        return msm_projects.add_project(
            project_path,
            folder,
            caption,
            color,
            owner=_project_list_owner(ctx),
            mcp_access=mcp_access if gui else None,
            default_mcp_access=not gui,
        )

    _register_with_gui_argument(tool, "msm.add_project", add_project)

    async def update_project(
        ctx: Context,
        project_path: str,
        new_folder: Optional[str] = None,
        new_caption: Optional[str] = None,
        new_color: Optional[str] = None,
        new_mcp_access: Optional[bool] = None,
    ) -> dict:
        """Changes how a listed schema project is filed and shown.

        Args:
            project_path: The project, as msm.list_projects reports it. It
                does not have to exist on disk any more.
            new_folder: The folder of the list to move it to, such as
                "/Archive"; "/" is the top level. Left out, it stays where it
                is.
            new_caption: The caption to show it with; "" removes it. Left
                out, it keeps its caption.
            new_color: The color to show it in (red, orange, yellow, green,
                blue or purple); "" removes it. Left out, it keeps its color.
{gui_argument_doc}

        Returns:
            The project as msm.list_projects reports it.
        """
        general.require_project_access(ctx, project_path)
        return msm_projects.update_project(
            project_path,
            new_folder,
            new_caption,
            new_color,
            owner=_project_list_owner(ctx),
            mcp_access=new_mcp_access if general.is_gui_mode() else None,
        )

    _register_with_gui_argument(tool, "msm.update_project", update_project)

    @tool(name="msm.remove_project")
    async def remove_project(ctx: Context, project_path: str) -> str:
        """Removes a schema project from the project list.

        Only the list changes: the project folder on disk is left as it is.

        Args:
            project_path: The project, as msm.list_projects reports it.

        Returns:
            The project path as it was listed.
        """
        general.require_project_access(ctx, project_path)
        return msm_projects.remove_project(project_path, owner=_project_list_owner(ctx))

    @tool(name="msm.get_project_information")
    async def get_project_information(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[dict]:
        """Returns information about a database schema project.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            A dict with information about the schema project.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.get_project_information(**_kwargs(schema_project_path=schema_project_path))

    @tool(name="msm.set_development_version")
    async def set_development_version(
        ctx: Context,
        version: str,
        schema_project_path: Optional[str] = None,
    ) -> None:
        """Sets the development version of a schema project.

        Args:
            version: The new development version (major.minor.patch).
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            None
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.set_development_version(
            **_kwargs(version=version, schema_project_path=schema_project_path)
        )

    @tool(name="msm.get_released_versions")
    async def get_released_versions(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns all released versions of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of released versions.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.get_released_versions(**_kwargs(schema_project_path=schema_project_path))

    @tool(name="msm.get_last_released_version")
    async def get_last_released_version(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last released version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last released version.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.get_last_released_version(**_kwargs(schema_project_path=schema_project_path))

    @tool(name="msm.get_last_deployment_version")
    async def get_last_deployment_version(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last deployment version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last deployment version.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.get_last_deployment_version(
            **_kwargs(schema_project_path=schema_project_path)
        )

    @tool(name="msm.prepare_release")
    async def prepare_release(
        ctx: Context,
        version: str,
        next_version: Optional[str] = None,
        schema_project_path: Optional[str] = None,
        allow_to_stay_on_same_version: bool = False,
        overwrite_existing: bool = False,
    ) -> Optional[list]:
        """Prepares a new database schema release.

        Args:
            version: The version to create for the release (major.minor.patch).
            next_version: The next development version.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            allow_to_stay_on_same_version: Allow staying on the same version
                for further development work.
            overwrite_existing: Overwrite existing files.

        Returns:
            The list of generated files.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.prepare_release(
            **_kwargs(
                version=version,
                next_version=next_version,
                schema_project_path=schema_project_path,
                allow_to_stay_on_same_version=allow_to_stay_on_same_version,
                overwrite_existing=overwrite_existing,
            )
        )

    @tool(name="msm.get_sql_content_from_section")
    async def get_sql_content_from_section(
        ctx: Context, file_path: str, section_id: str
    ) -> Optional[str]:
        """Returns the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.

        Returns:
            The SQL content as a string.
        """
        await general.require_allowed_path(ctx, file_path)
        return msm.get_sql_content_from_section(file_path=file_path, section_id=section_id)

    @tool(name="msm.set_section_sql_content")
    async def set_section_sql_content(
        ctx: Context, file_path: str, section_id: str, sql_content: str
    ) -> None:
        """Sets the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.
            sql_content: The SQL content to set.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, file_path)
        return msm.set_section_sql_content(
            file_path=file_path, section_id=section_id, sql_content=sql_content
        )

    @tool(name="msm.generate_deployment_script")
    async def generate_deployment_script(
        ctx: Context,
        version: str,
        schema_project_path: Optional[str] = None,
        overwrite_existing: bool = False,
    ) -> Optional[str]:
        """Generates the deployment script for a release.

        Args:
            version: The version to create the deployment script for.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            overwrite_existing: Overwrite existing files.

        Returns:
            The file name of the deployment script.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.generate_deployment_script(
            **_kwargs(
                version=version,
                schema_project_path=schema_project_path,
                overwrite_existing=overwrite_existing,
            )
        )

    @tool(name="msm.get_deployment_script_versions")
    async def get_deployment_script_versions(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the list of deployment script versions.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of deployed versions.
        """
        await _authorize_project(ctx, schema_project_path)
        return msm.get_deployment_script_versions(
            **_kwargs(schema_project_path=schema_project_path)
        )

    # Registered last, and only together with the db group: deploying needs a
    # connection opened with db.connect, so without that group the tool could
    # never obtain one and is left unadvertised rather than exposed as something
    # that cannot succeed.
    if general.FUNCTION_GROUP_DB in function_groups:

        @tool(name="msm.deploy_schema")
        async def deploy_schema(
            ctx: Context,
            connection_id: str,
            version: Optional[str] = None,
            schema_project_path: Optional[str] = None,
            backup_directory: Optional[str] = None,
            backup: bool = False,
        ) -> Optional[str]:
            """Deploys a version of a database schema onto an open connection.

            Runs the deployment script of the given version, which has to have
            been generated with msm.generate_deployment_script first. If the
            schema already exists it is updated to the requested version.

            Args:
                connection_id: The UUID returned by db.connect, identifying the
                    connection to deploy onto.
                version: The version to deploy. Defaults to the latest version a
                    deployment script exists for.
                schema_project_path: The path to the schema project. Defaults to
                    the current working directory.
                backup_directory: The directory to write the backup to. Only
                    used when backup is enabled.
                backup: Whether to dump an existing schema before updating it,
                    so it can be restored if the update fails. Defaults to
                    False.

            Returns:
                A message describing what was deployed.
            """
            await _authorize_project(ctx, schema_project_path)
            await general.require_allowed_path(ctx, backup_directory)

            # Read here, while this is still the request's own context; the
            # worker thread below has no request context to read it from.
            client = general.get_client_identity(ctx)

            def _deploy():
                with db_functions.use_session(connection_id, client) as session:
                    return msm.deploy_schema(
                        session=session,
                        **_kwargs(
                            version=version,
                            schema_project_path=schema_project_path,
                            backup_directory=backup_directory,
                            backup=backup,
                        ),
                    )

            # On a worker thread, not here. This function is a coroutine, so its
            # body runs on the thread driving the event loop, and everything in
            # _deploy blocks: waiting for the connection's lock, and then running
            # a whole deployment script on it. Inline, that would stop the server
            # answering any client at all for as long as it took. The sync db
            # tools get this for free - the SDK already runs them on a worker
            # thread - and use_session now refuses to be entered anywhere else.
            return await anyio.to_thread.run_sync(_deploy)
