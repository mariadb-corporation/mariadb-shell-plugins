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

from typing import Optional, Union

from mcp_plugin.lib import db_functions, general, tasks
from mcp_plugin.lib.tool_registrar import register_with_gui_argument, tool_error, tool_registrar

# The tasks msm starts (msm.online_alter_table), and how many one client may
# have running at once: each holds two sessions.
TASK_TYPE_MSM = "msm"
MAX_RUNNING_MSM_TASKS = 4


# The arguments only a --gui server offers, by tool, with the lines that
# describe them (see tool_registrar.register_with_gui_argument). Whether LLMs
# may work on a project is the user's call, made in the VS Code extension: a
# server without --gui serves the agents the setting restricts.
_NEW_PROJECT_MCP_ACCESS = (
    "mcp_access",
    """            mcp_access: Whether LLMs may work on the project through an MCP
                server started without --gui. Defaults to False: the
                project stays the extension's until the user says otherwise.
""",
)
_GUI_ARGUMENTS = {
    "msm.create_project": _NEW_PROJECT_MCP_ACCESS,
    "msm.import_workbench_model": _NEW_PROJECT_MCP_ACCESS,
    "msm.add_project": (
        "mcp_access",
        """            mcp_access: Whether LLMs may work on the project through an MCP
                server started without --gui. Left out, a listed project
                keeps its setting and a new one gets none.
""",
    ),
    "msm.update_project": (
        "new_mcp_access",
        """            new_mcp_access: Whether LLMs may work on the project through an
                MCP server started without --gui. Left out, it keeps its
                setting.
""",
    ),
}


def _register_with_gui_argument(tool, name, function) -> None:
    """Registers an msm tool with its argument only a --gui server offers."""
    register_with_gui_argument(tool, name, function, *_GUI_ARGUMENTS[name])


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
    from msm_plugin import modeling as msm_modeling
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
        # In multi-tenant mode require_allowed_path checks the working
        # directory for None itself.
        if schema_project_path is None and not general.is_multi_tenant():
            general.require_project_access(ctx, os.getcwd())

    async def _in_project(ctx, schema_project_path, function, *args, **kwargs):
        """Runs an msm function on an authorized project, off the event loop.

        Every file a SOURCE directive of the project names is read (or, for a
        breakout file, written) without asking the user, so msm checks each
        with the rule db.execute_sql_script's includes are, inside
        source.checked_includes().
        """
        await _authorize_project(ctx, schema_project_path)
        user = general.get_client_identity(ctx).user
        kwargs = _kwargs(schema_project_path=schema_project_path, **kwargs)
        return await _with_checked_includes(user, function, *args, **kwargs)

    async def _with_checked_includes(user, function, *args, **kwargs):
        """Runs an msm function off the event loop, every include it reads
        or writes checked as db.execute_sql_script's are for the user."""
        from msm_plugin.lib import source

        def run():
            with source.checked_includes(lambda path: db_functions.allowed_include(path, user)):
                return function(*args, **kwargs)

        return await anyio.to_thread.run_sync(run)

    def _require_new_project_access(ctx, target_path, schema_name):
        """Checks the folder a new project gets, whether it is replaced
        (overwriting deletes it first) or made anew where a listed project's
        folder went missing: either way one kept from LLMs stays theirs, and
        adding it to the list must not open it."""
        general.require_project_access(
            ctx, msm_management.project_folder_path(target_path, schema_name)
        )

    async def _add_new_project(project_path, owner, mcp_access, *details):
        """Adds a new project to the owner's list. The extension's projects
        start as its own; an agent's have to be open to it, or it could not
        go on with what it made."""
        await anyio.to_thread.run_sync(
            lambda: msm_projects.add_project(
                project_path,
                *details,
                owner=owner,
                mcp_access=mcp_access if general.is_gui_mode() else True,
            )
        )

    def _lsp_path() -> Optional[str]:
        """The mariadb-lsp `mcp setup --lspPath` names; else msm looks for it
        as the sql tools do (next to the shell, on the PATH, ~/.local/bin)."""
        from mcp_plugin.lib import config

        return config.get_lsp_path() or None

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
        year_of_creation: Union[str, int, None] = None,
        folder: Optional[str] = None,
        caption: Optional[str] = None,
        color: Optional[str] = None,
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
                by more than one copyright holder. Each entry takes a holder
                (written into the notice as given, a final period included),
                a yearOfCreation, an optional yearOfLastUpdate and a
                tracksUpdates flag marking the holders whose yearOfLastUpdate
                is moved to the current year when a change is applied in a
                later year. When given, it takes the place of copyright_holder.
            year_of_creation: The year of creation of the copyright, such as
                "2024" for a schema that existed before. Defaults to the
                current year; an entry of copyrights without one gets it.
            folder: The folder of the project list to file it in, such as
                "/Shop". Defaults to the top level.
            caption: The caption to show it with in the project list.
            color: The color to show it in (red, orange, yellow, green, blue
                or purple).
{gui_argument_doc}

        Returns:
            The path of the created project folder. The project is added to
            the project list (see msm.list_projects).
        """
        await general.require_allowed_path(ctx, target_path)
        _require_new_project_access(ctx, target_path, schema_name)
        # Checked before the folder is made, which a bad one would outlive.
        msm_projects.normalize_details(folder, caption, color)
        # Read before anything else: the caller's list is where the project
        # goes, not the shell user's, which the wrapper would add it to.
        owner = general.get_client_identity(ctx).user
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
                year_of_creation=year_of_creation,
            ),
        )
        await _add_new_project(project_path, owner, mcp_access, folder, caption, color)
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
            removed - and its "development_version" (null where it cannot be
            read).
        """
        listed = msm_projects.list_projects(
            general.get_client_identity(ctx).user, mcp_access_only=not general.is_gui_mode()
        )
        for project in listed:
            project["development_version"] = _development_version(project)
        return listed

    def _development_version(project) -> Optional[str]:
        """Returns a listed project's development version, or None."""
        if not project["exists"]:
            return None
        try:
            return msm_management.get_schema_development_version(
                schema_project_path=project["project_path"]
            )
        except Exception:  # pylint: disable=broad-except
            # A broken project is still listed; its row shows no version.
            return None

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
            owner=general.get_client_identity(ctx).user,
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
            owner=general.get_client_identity(ctx).user,
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
        return msm_projects.remove_project(project_path, owner=general.get_client_identity(ctx).user)

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
        # In a later year it moves the copyright years along, which reads
        # the project's includes for the snapshot's hash
        return await _in_project(
            ctx, schema_project_path, msm.set_development_version, **_kwargs(version=version)
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

        A release first makes the schema snapshot of the diagrams again (see
        msm.update_schema_definition) and fails where it cannot.

        Returns:
            The list of generated files.
        """
        return await _in_project(
            ctx,
            schema_project_path,
            msm.prepare_release,
            **_kwargs(
                version=version,
                next_version=next_version,
                allow_to_stay_on_same_version=allow_to_stay_on_same_version,
                overwrite_existing=overwrite_existing,
                lsp_path=_lsp_path(),
            ),
        )

    @tool(name="msm.list_schema_diagrams")
    async def list_schema_diagrams(
        ctx: Context, schema_project_path: Optional[str] = None
    ) -> dict:
        """Lists the schema diagrams of a database schema project.

        A project keeps its diagrams in MSM Section 160 of the development
        script, the `msm_schema_diagrams` VIEW: one row `schema_definition`
        (format msm-schema/1) with a snapshot of the schema objects the
        CREATE statements define, as the MariaDB SQL language server
        outlines them, and one row per EER diagram (format msm-diagram/1).

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            schema_definition: the snapshot's table count and whether it was
            made from the development script as it is now (current), or null
            without one; diagrams: name, figures and connections of each.
        """
        return await _in_project(ctx, schema_project_path, msm_modeling.list_schema_diagrams)

    @tool(name="msm.get_schema_diagrams")
    async def get_schema_diagrams(
        ctx: Context,
        schema_project_path: Optional[str] = None,
        diagram: Optional[str] = None,
    ) -> list:
        """Returns the rows of a schema project's diagrams VIEW.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            diagram: Only this diagram, with the schema snapshot. Defaults
                to every row.

        Returns:
            The rows, each name, format and document. The msm-schema/1
            document holds the snapshot: tables (columns, indexes,
            foreign_keys with many, mandatory and referenced_mandatory,
            checks), views, routines, triggers, events and sequences as the
            language server's outline writes them, with their positions in
            files (0-based lines and UTF-16 characters; source n is
            files[n + 1]), and source_hash. An msm-diagram/1 document holds
            layers, figures and connections. An ImageFigure's picture, a
            data URL, is left out: image_size says its length, and
            msm.set_schema_diagram keeps the stored picture of a figure sent
            without one.
        """
        from msm_plugin.lib import diagrams as msm_diagrams

        rows = await _in_project(
            ctx, schema_project_path, msm_modeling.get_schema_diagrams, **_kwargs(diagram=diagram)
        )
        return msm_diagrams.elide_row_images(rows)

    @tool(name="msm.get_schema_diagram_image")
    async def get_schema_diagram_image(
        ctx: Context,
        diagram: str,
        figure_id: str,
        schema_project_path: Optional[str] = None,
    ) -> str:
        """Returns the picture of an ImageFigure of a schema project's diagram.

        msm.get_schema_diagrams leaves pictures out (image_size says their
        length); this returns one, for a client that draws the diagram.

        Args:
            diagram: The name of the diagram.
            figure_id: The id of the ImageFigure.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The picture, a data URL (up to 512 KB as msm imports them).
        """
        return await _in_project(
            ctx, schema_project_path, msm_modeling.get_schema_diagram_image, diagram, figure_id
        )

    @tool(name="msm.update_schema_definition")
    async def update_schema_definition(
        ctx: Context, schema_project_path: Optional[str] = None
    ) -> dict:
        """Makes the schema snapshot of a project's diagrams again.

        Use it after changing the CREATE statements of the development
        script. The snapshot is made by the MariaDB SQL language server's
        binary from the development script and the files its SOURCE
        directives include; a statement it cannot read fails the call.
        Diagrams lose the figures and connections of objects the schema no
        longer has. msm.prepare_release does this too.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The counts of tables, views, routines, triggers, events and
            sequences, the diagrams that changed and the files written.
        """
        return await _in_project(
            ctx,
            schema_project_path,
            msm_modeling.update_schema_definition,
            **_kwargs(lsp_path=_lsp_path()),
        )

    @tool(name="msm.split_sections")
    async def split_sections(
        ctx: Context,
        sections: Optional[list[str]] = None,
        schema_project_path: Optional[str] = None,
    ) -> dict:
        """Splits sections of a project's development script into files by object kind.

        Section 140 goes into development/sections/140-10_tables.sql,
        140-20_table_additions.sql and 140-30_inserts.sql, Section 150 into
        150-10_procedures_functions.sql, 150-20_views.sql,
        150-30_triggers.sql and 150-40_events.sql (routines first: a view
        cannot be created before a function it calls), and Section 170 into
        170_roles.sql; each section then includes its files by SOURCE
        directives. Statements keep the comments above them, and their order
        within a file. A section that includes a file already, has nothing to
        move, or has a statement that fits no file between two that move is
        left as it is and reported. Every section of the template the script
        lacks is added. A schema snapshot that matched the script before is
        made again.

        Args:
            sections: The sections to split, of "140", "150" and "170".
                Defaults to all three.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            split (the files of each section split), skipped (the reason for
            each section left as it is), sections_added (the template
            sections that were missing), files (the files written),
            snapshot_updated (whether the schema snapshot was made again), and
            snapshot_error (why it could not be, where it could not).
        """
        return await _in_project(
            ctx,
            schema_project_path,
            msm.split_sections,
            **_kwargs(sections=sections, lsp_path=_lsp_path()),
        )

    @tool(name="msm.set_schema_diagram")
    async def set_schema_diagram(
        ctx: Context, document: dict, schema_project_path: Optional[str] = None
    ) -> list:
        """Adds a diagram to a schema project, or replaces the one of that name.

        Args:
            document: The msm-diagram/1 document: format "msm-diagram/1",
                name, width, height, layers, figures and connections (each
                id, foreignKey, table - the table owning the foreign key -,
                startFigure, endFigure, visible). A figure has id, type, name,
                left, top, width, height, color and layer; a figure without
                left and top is placed by msm.layout_schema_diagram. By type:
                "TableFigure" has table (and triggersExpanded, for the
                card's section of the table's triggers), "ViewFigure" view,
                "SequenceFigure" sequence, "RoutineGroupFigure" routines
                (names), "EventGroupFigure" events (names), "NoteFigure" text,
                "ImageFigure" image (a data URL; left out, the figure keeps
                the picture stored under its id).
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The files written.
        """
        return await _in_project(
            ctx, schema_project_path, msm_modeling.set_schema_diagram, document
        )

    @tool(name="msm.edit_schema")
    async def edit_schema(
        ctx: Context,
        edits: list,
        base_hash: str,
        schema_project_path: Optional[str] = None,
    ) -> dict:
        """Changes schema objects in the SQL of a schema project.

        The CREATE statements are the source of truth, so tables, columns,
        indexes, keys, views and routines are changed in the development
        script and the files it includes (by the MariaDB SQL language
        server's binary): new tables go into MSM Section 140, views and
        routines into Section 150, written so it can run again. Diagrams
        follow renamed objects and the schema snapshot is made again. Once
        the project has a release, the statements that take a deployed
        schema along are kept for the next release's update script.
        Everything is written, or nothing.

        Args:
            edits: The edits, applied in order (a later one uses the names
                an earlier one gave), each an object with its op and members.
                rename_table (table, new_name), rename_view (view,
                new_name), rename_column (table, column, new_name),
                rename_routine (routine, new_name, kind), add_table (table),
                drop_table (table), add_column (table, column, after or
                first), modify_column (table, column, new_column),
                drop_column (table, column), set_table_options (table,
                options; null removes one), add_index (table, index),
                drop_index (table, and name, kind "primary" or columns),
                set_primary_key (table, columns), add_foreign_key (table,
                foreign_key), drop_foreign_key (table, and name or columns),
                add_view (view), drop_view (view), add_routine (routine),
                drop_routine (routine, kind). kind is "procedure" or
                "function", needed when both kinds have the name. Tables,
                columns, indexes, foreign keys, views and routines are given
                as msm.get_schema_diagrams' snapshot has them; a member left
                out takes the snapshot's default, so modify_column needs the
                column's whole definition. A binary without an op refuses it.
            base_hash: The snapshot's source_hash the edits were made
                against; refused when the snapshot was made again since.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            rows (the diagrams VIEW afterwards, the new snapshot first, with
            the source_hash for the next call; pictures left out as by
            msm.get_schema_diagrams), files written, migration (the
            statements kept for the next update script), migration_file and
            notes on what the edits could not change, for a person to check
            (still-referenced, no-migration, name-in-string, ...).
        """
        from msm_plugin.lib import diagrams as msm_diagrams

        result = await _in_project(
            ctx,
            schema_project_path,
            msm_modeling.edit_schema,
            edits,
            base_hash,
            **_kwargs(lsp_path=_lsp_path()),
        )
        result["rows"] = msm_diagrams.elide_row_images(result["rows"])
        return result

    @tool(name="msm.create_schema_diagram")
    async def create_schema_diagram(
        ctx: Context,
        name: str,
        tables: Optional[list[str]] = None,
        schema_project_path: Optional[str] = None,
        views: Optional[list[str]] = None,
        routines: Optional[list[str]] = None,
        events: Optional[list[str]] = None,
        sequences: Optional[list[str]] = None,
    ) -> dict:
        """Adds a diagram of the schema's tables to a schema project, laid out.

        The tables are laid out in layers: a table a foreign key refers to
        goes above the tables referring to it, with few crossing
        connections, and tables without foreign keys are packed in rows
        below. Every foreign key between two tables shown gets a connection.
        Views and sequences get a figure each, routines are listed on one
        routine group figure and events on one event group figure, placed
        below the tables, without connections. The schema
        snapshot is made again first where the development script changed
        since it was made.

        Args:
            name: The name of the new diagram.
            tables: The tables to show. Defaults to all tables of the schema.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            views: Views to show as well. Defaults to none.
            routines: Routines (procedures and functions) to list on a
                routine group figure. Defaults to none.
            events: Events to list on an event group figure. Defaults to
                none.
            sequences: Sequences to show. Defaults to none.

        Returns:
            name, figures and connections (counts), and the files written.
        """
        return await _in_project(
            ctx,
            schema_project_path,
            msm_modeling.create_schema_diagram,
            name,
            **_kwargs(
                tables=tables, views=views, routines=routines, events=events,
                sequences=sequences, lsp_path=_lsp_path(),
            ),
        )

    @tool(name="msm.layout_schema_diagram")
    async def layout_schema_diagram(
        ctx: Context,
        name: str,
        tables: Optional[list[str]] = None,
        keep_placed: bool = True,
        schema_project_path: Optional[str] = None,
        views: Optional[list[str]] = None,
        routines: Optional[list[str]] = None,
        events: Optional[list[str]] = None,
        sequences: Optional[list[str]] = None,
    ) -> dict:
        """Adds tables, views, sequences, routines and events to a diagram
        of a schema project and lays its figures out.

        With keep_placed (the default) only figures without a position are
        laid out - the objects added, or figures a document given to
        msm.set_schema_diagram leaves left and top out of - as a block below
        the others; with keep_placed false every table, view, sequence and
        group figure is laid out again. Figures on a layer, notes and images
        keep their place. Foreign keys between tables on the diagram that
        are not drawn get a connection. The schema snapshot is made again
        first where the development script changed.

        Args:
            name: The name of the diagram.
            tables: Tables to add to the diagram. Defaults to none.
            keep_placed: Whether figures that have a position keep it.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            views: Views to add. Defaults to none.
            routines: Routines to list on the diagram's routine group
                figure, which is added where the diagram has none. Defaults
                to none.
            events: Events to list on the diagram's event group figure,
                likewise. Defaults to none.
            sequences: Sequences to add. Defaults to none.

        Returns:
            name, added (the objects added), placed (the figures laid out, by
            the tables, views, sequences and groups they show),
            connections_added and the files written.
        """
        return await _in_project(
            ctx,
            schema_project_path,
            msm_modeling.layout_schema_diagram,
            name,
            **_kwargs(
                tables=tables,
                views=views,
                routines=routines,
                events=events,
                sequences=sequences,
                keep_placed=keep_placed,
                lsp_path=_lsp_path(),
            ),
        )

    async def import_workbench_model(
        ctx: Context,
        model_path: str,
        target_path: str,
        schema: Optional[str] = None,
        copyright_holder: Optional[str] = None,
        license: Optional[str] = None,
        overwrite_existing: bool = False,
        enforce_target_path: bool = False,
        copyrights: Optional[list] = None,
        year_of_creation: Union[str, int, None] = None,
        mcp_access: bool = False,
    ) -> dict:
        """Creates a database schema project from a MySQL Workbench model (.mwb).

        The development script gets the CREATE statements of a schema of the
        model: tables, foreign keys and the model's rows as INSERTs (MSM
        Section 140), views, routines, triggers and events (Section 150),
        roles and their GRANTs (Section 170), in breakout files under
        development/sections/ where a section is large. User-defined types are
        replaced by their SQL definition. The schema snapshot is then made
        from that SQL, and the model's EER diagrams are added. Scripts
        attached to the model are listed, not imported.

        Args:
            model_path: The .mwb file.
            target_path: The path the project folder should be created in.
            schema: The schema of the model to import; needed when the model
                has several.
            copyright_holder: The name of the copyright holder.
            license: The license to use for the project.
            overwrite_existing: Overwrite the project folder if it exists.
            enforce_target_path: Create the target_path if it does not exist.
            copyrights: The copyright notices of the project, instead of
                copyright_holder (see msm.create_project).
            year_of_creation: The year of creation of the copyright (see
                msm.create_project).
{gui_argument_doc}

        Returns:
            project_path, schema, the numbers of tables, views, routines,
            triggers, events, roles and inserted_rows, diagrams (names),
            scripts_not_imported, warnings and the files written. The project
            is added to the project list (see msm.list_projects).
        """
        from msm_plugin.lib import modeling as msm_lib_modeling
        from msm_plugin.lib import workbench as msm_workbench

        await general.require_allowed_path(ctx, model_path)
        await general.require_allowed_path(ctx, target_path)
        # Read once, here: the schema's name decides the project folder.
        model = await anyio.to_thread.run_sync(msm_workbench.read_model, model_path)
        _require_new_project_access(ctx, target_path, model.schema(schema)["name"])
        owner = general.get_client_identity(ctx).user
        result = await _with_checked_includes(
            owner,
            msm_lib_modeling.create_project_from_workbench_model,
            model_path,
            target_path,
            copyright_holder=copyright_holder,
            license=license,
            schema=schema,
            overwrite_existing=overwrite_existing,
            enforce_target_path=enforce_target_path,
            copyrights=copyrights,
            lsp_path=_lsp_path(),
            model=model,
            year_of_creation=year_of_creation,
        )
        await _add_new_project(result["project_path"], owner, mcp_access)
        return result

    _register_with_gui_argument(tool, "msm.import_workbench_model", import_workbench_model)

    @tool(name="msm.remove_schema_diagram")
    async def remove_schema_diagram(
        ctx: Context, name: str, schema_project_path: Optional[str] = None
    ) -> list:
        """Removes a diagram from a schema project.

        Args:
            name: The name of the diagram.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The files written.
        """
        return await _in_project(ctx, schema_project_path, msm_modeling.remove_schema_diagram, name)

    @tool(name="msm.rename_schema_diagram")
    async def rename_schema_diagram(
        ctx: Context, name: str, new_name: str, schema_project_path: Optional[str] = None
    ) -> list:
        """Renames a diagram of a schema project.

        Only the name changes: the diagram's figures, pictures and place among
        the diagrams stay as stored. Refused for a name another diagram of the
        project has already, compared case-insensitively, and for
        `schema_definition`, the schema row's name.

        Args:
            name: The name of the diagram.
            new_name: Its new name (surrounding whitespace is dropped).
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The files written; none where the name stays as it is.
        """
        return await _in_project(
            ctx, schema_project_path, msm_modeling.rename_schema_diagram, name, new_name
        )

    @tool(name="msm.get_online_alter_defaults")
    def get_online_alter_defaults() -> dict:
        """Returns the online ALTER TABLE options msm uses where nothing is set.

        The same as `defaults` of msm.get_online_alter_settings, without a
        project: for a live table's settings.

        Returns:
            {maxRebuildBytes, galeraMaxBytes, nboLock, maxLockedBytes, lockWait,
            maxRetries}.
        """
        return msm.get_online_alter_defaults()

    @tool(name="msm.get_online_alter_settings")
    async def get_online_alter_settings(
        ctx: Context, table: Optional[str] = None, schema_project_path: Optional[str] = None
    ) -> dict:
        """Returns a project's online ALTER TABLE setting, and what it gives a table.

        Deployments run each ALTER TABLE of an update the least disruptive way
        the server accepts, within these limits: msm.project.json's
        `onlineAlter`, with general values and a `tables` entry per table.

        Args:
            table: A table to resolve the options of.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            {defaults: the options where nothing is set, setting: the
            project's setting, normalized, table, enabled: whether it is on
            for the table, options: what the table gets (None where it is off
            or no table is given)}. The option keys: maxRebuildBytes,
            galeraMaxBytes, nboLock, maxLockedBytes, lockWait, maxRetries.
        """
        return await _in_project(
            ctx, schema_project_path, msm.get_online_alter_settings, **_kwargs(table=table)
        )

    @tool(name="msm.set_project_settings")
    async def set_project_settings(
        ctx: Context,
        settings: dict,
        schema_project_path: Optional[str] = None,
    ) -> list:
        """Changes settings of a schema project's msm.project.json.

        Only the keys given change; null removes an optional one (onlineAlter,
        substitutions, maxInlineSectionSize, schemaDependencies), so it takes
        msm's default again. Each value replaces the stored one as a whole:
        read the settings with msm.get_project_information, change them, send
        the keys back. Everything is checked first; nothing is written when
        anything is refused. Changed copyright years, holders or a changed
        license are brought into the notices and license blocks of the
        development files (released files stay as released), and a schema
        snapshot that was current is kept current.

        Args:
            settings: The keys to change:
                copyrights: [{holder, yearOfCreation (YYYY), yearOfLastUpdate
                    (YYYY, optional), tracksUpdates (whether a change in a
                    later year moves yearOfLastUpdate along)}]; the
                    copyrightHolder and yearOfCreation mirrors follow it.
                license: BSD, GPL-2.0, MIT, None or CUSTOM.
                customLicense: The text of a CUSTOM license; it may use
                    ${copyright_notices}, ${copyright_holder} and ${year}.
                onlineAlter: The whole online ALTER TABLE setting: enabled,
                    maxRebuildBytes (largest table size for an online
                    rebuild, INPLACE or COPY; 0 = never, null = no limit),
                    galeraMaxBytes, nboLock (SHARED or EXCLUSIVE to allow a
                    Galera NBO), maxLockedBytes (largest table size for a
                    step that blocks writes), lockWait (seconds per attempt),
                    maxRetries, and tables: {table: the same keys but
                    tables}. msm.get_online_alter_settings gives the defaults.
                substitutions: {name: {default, pattern}} for /*<msm:name>*/.
                maxInlineSectionSize: The largest section body, in
                    characters, kept inline in the development script.
                schemaDependencies: Names of schemas this one depends on.
                schemaName and schemaFileName cannot be changed.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The files written; none where nothing changed.
        """
        return await _in_project(
            ctx, schema_project_path, msm.set_project_settings, settings,
            **_kwargs(lsp_path=_lsp_path()),
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
        # In a later year it moves the copyright years of the file's project
        # along, which reads the project's includes for the snapshot's hash
        return await _with_checked_includes(
            general.get_client_identity(ctx).user,
            msm.set_section_sql_content,
            file_path=file_path,
            section_id=section_id,
            sql_content=sql_content,
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
        # In a later year it moves the copyright years along, which reads
        # the project's includes for the snapshot's hash
        return await _in_project(
            ctx,
            schema_project_path,
            msm.generate_deployment_script,
            **_kwargs(version=version, overwrite_existing=overwrite_existing),
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
            online_alter: Optional[dict] = None,
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
                online_alter: This deployment's online ALTER TABLE setting,
                    over the project's `onlineAlter` in msm.project.json. An
                    update runs each ALTER TABLE the least disruptive way the
                    server accepts (INSTANT, then NOCOPY, INPLACE and COPY
                    with LOCK=NONE, then with a lock); keys: enabled (false
                    runs them as written), maxRebuildBytes (largest table for
                    INPLACE or COPY; 0 = never, null = no limit),
                    galeraMaxBytes, nboLock (SHARED or EXCLUSIVE to allow
                    Galera NBO), maxLockedBytes (largest table for
                    a step that blocks writes), lockWait (seconds per
                    attempt), maxRetries, tables ({<table>: {the same keys}}).

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
                            online_alter=online_alter,
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

        @tool(name="msm.online_alter_table")
        async def online_alter_table(
            ctx: Context,
            connection_id: str,
            schema: str,
            table: str,
            spec: str,
            options: Optional[dict] = None,
        ) -> dict:
            """Starts one ALTER TABLE, run online, as a background task.

            Tries ALGORITHM=INSTANT, then NOCOPY, INPLACE and COPY with
            LOCK=NONE, then a Galera NBO where allowed, then LOCK=SHARED and
            LOCK=EXCLUSIVE: each step either runs the change that way or is
            refused at once, without touching data. The task runs in sessions
            of its own; each step is a stage, with the server's progress;
            task.cancel stops the running statement. Its result: {method,
            applied, reason, table_size, galera, steps}; method NEEDS_EXTERNAL (applied false) means no
            allowed step could run it and nothing was changed.

            Args:
                connection_id: The UUID returned by db.connect.
                schema: The table's schema.
                table: The table.
                spec: Everything after ALTER TABLE <table>, e.g. "ADD COLUMN
                    `note` VARCHAR(100), ADD INDEX `ix_note` (`note`)", without
                    ALGORITHM or LOCK.
                options: The limits: maxRebuildBytes (largest table size for an
                    online rebuild, INPLACE or COPY; 0 = never, null = no
                    limit; default 10 GiB),
                    galeraMaxBytes, nboLock, maxLockedBytes (largest table size
                    for a step that blocks writes; default no limit), lockWait
                    (seconds per attempt, default 5), maxRetries (default 10).

            Returns:
                A dict with the task_id to follow and the task's status.
            """
            from msm_plugin.lib import online_alter

            try:
                limits = online_alter.Options.from_dict(options)
                spec = online_alter.check_spec(spec)
            except ValueError as error:
                raise tool_error(str(error)) from error
            client = general.get_client_identity(ctx)

            # Resolved before the task starts, so that a wrong connection
            # fails the call itself. Its sessions are opened as db.connect's
            # are: still configured, under the user's default role.
            # (On a worker thread: dropping an expired connection waits for it.)
            open_session = await anyio.to_thread.run_sync(
                db_functions.session_opener, connection_id, client
            )
            task = tasks.create(
                TASK_TYPE_MSM, "online_alter_table",
                f"ALTER TABLE `{schema}`.`{table}` online", client, MAX_RUNNING_MSM_TASKS,
                {"connection_id": connection_id, "schema": schema, "table": table},
            )

            def work(task):
                session = open_session()
                try:
                    # The second session reads the progress and stops the
                    # statement on a cancel
                    monitor = open_session()
                    try:
                        return online_alter.run_in_task(
                            task, session, monitor, schema, table, spec, limits
                        )
                    finally:
                        monitor.close()
                finally:
                    session.close()

            tasks.start(task, work)
            return {"task_id": task.id, "status": task.status}
