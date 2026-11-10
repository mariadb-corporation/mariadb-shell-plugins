# Copyright (c) 2025, 2026, Oracle and/or its affiliates.
# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The MariaDB Shell Schema Management Plugin - Modeling"""

import json
import os

from mysqlsh.plugin_manager import plugin_function
from msm_plugin import lib


def _project(kwargs) -> str:
    return kwargs.get("schema_project_path") or lib.core.get_working_dir()


def _json_argument(value, kwargs: dict, name: str, what: str):
    """An argument given as itself or, on the command line, as a JSON file
    (the keyword argument <name>_file)."""
    file_path = kwargs.get(f"{name}_file")
    if file_path is None:
        return value
    if value:
        raise ValueError(f"Give the {what} as {name} or as {name}_file, not both.")
    try:
        with open(os.path.expanduser(file_path), "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise ValueError(f"The {what} file could not be read: {e}")
    except json.JSONDecodeError as e:
        raise ValueError(f"The {what} file `{file_path}` is not valid JSON: {e}")


@plugin_function("msm.listSchemaDiagrams", shell=True, cli=True, web=True)
def list_schema_diagrams(**kwargs) -> dict:
    """Lists the schema diagrams of a schema project

    The diagrams are kept in MSM Section 160 of the development script, the
    `msm_schema_diagrams` VIEW: a snapshot of the schema objects the CREATE
    statements define, and one row per EER diagram.

    Args:
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        schema_definition: the snapshot's table count and whether it matches the development
        script as it is now (None without a snapshot), and diagrams: their names, figure and
        connection counts.
    """
    result = lib.modeling.list_diagrams(_project(kwargs))
    if lib.core.get_interactive_default():
        schema = result["schema_definition"]
        if schema is None:
            print("The project has no schema snapshot yet, see msm.updateSchemaDefinition().")
        else:
            print(
                f"Schema snapshot: {schema['tables']} table(s), "
                + ("current" if schema["current"] else "OUT OF DATE")
            )
        for diagram in result["diagrams"]:
            print(f"  {diagram['name']}: {diagram['figures']} figure(s)")
    else:
        return result


@plugin_function("msm.getSchemaDiagrams", shell=True, cli=True, web=True)
def get_schema_diagrams(**kwargs) -> list:
    """Returns the rows of a schema project's diagrams VIEW

    Args:
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        diagram (str): Only this diagram, with the schema snapshot. Defaults to all rows.

    Returns:
        The rows, each name, format (msm-schema/1 for the snapshot, msm-diagram/1 for a
        diagram) and the document.
    """
    return lib.modeling.get_diagram_rows(_project(kwargs), kwargs.get("diagram"))


@plugin_function("msm.getSchemaDiagramImage", shell=True, cli=True, web=True)
def get_schema_diagram_image(diagram: str, figure_id: str, **kwargs) -> str:
    """Returns the picture of an image figure of a schema diagram

    The MCP server's msm.get_schema_diagrams leaves pictures out; this returns one.

    Args:
        diagram (str): The name of the diagram.
        figure_id (str): The id of the ImageFigure.
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        The picture, a data URL.
    """
    return lib.modeling.get_diagram_image(_project(kwargs), diagram, figure_id)


@plugin_function("msm.updateSchemaDefinition", shell=True, cli=True, web=True)
def update_schema_definition(**kwargs) -> dict:
    """Makes the schema snapshot of a project's diagrams again

    The snapshot is made from the CREATE statements of the development script, with the files
    its SOURCE directives include, by the MariaDB SQL language server's binary mariadb-lsp.
    Diagrams lose the figures and connections of objects the schema no longer has.
    msm.prepareRelease does this too.

    Args:
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The number of tables, views, routines, triggers, events and sequences, the diagrams that
        changed and the files written.
    """
    result = lib.modeling.update_schema_definition(_project(kwargs), kwargs.get("lsp_path"))
    if lib.core.get_interactive_default():
        print(
            f"The schema snapshot holds {result['tables']} table(s), {result['views']} view(s), "
            f"{result['routines']} routine(s) and {result['triggers']} trigger(s)."
        )
        if result["changed_diagrams"]:
            print("Diagrams that lost objects: " + ", ".join(result["changed_diagrams"]))
    else:
        return result


@plugin_function("msm.setSchemaDiagram", shell=True, cli=True, web=True)
def set_schema_diagram(document: dict = None, **kwargs) -> list:
    """Adds a diagram to a schema project, or replaces the one of the same name

    On the command line give the document as a JSON file with --documentFile, or one option
    per member with each value as JSON, for example --format=msm-diagram/1 --name=Main
    --layers='[]' --figures='[...]' --connections='[]'.

    Args:
        document (dict): The diagram, an msm-diagram/1 document: format, name, layers, figures
            and connections.
        **kwargs: Additional options.

    Keyword Args:
        document_file (str): A JSON file holding the diagram, instead of document.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        The files written.
    """
    document = _json_argument(document, kwargs, "document", "diagram")
    if document is None:
        raise ValueError("No diagram given: pass document or document_file.")
    return lib.modeling.set_diagram(_project(kwargs), dict(document))


@plugin_function("msm.editSchema", shell=True, cli=True, web=True)
def edit_schema(edits: list = None, base_hash: str = None, **kwargs) -> dict:
    """Changes schema objects in the SQL of a schema project

    Tables, columns, indexes, keys, views and routines are changed in the CREATE statements of
    the development script, and the files it includes, by the MariaDB SQL language server's
    binary mariadb-lsp; diagrams follow renamed objects, and the schema snapshot is made again.
    Once the project has a release, the statements that take a deployed schema along are kept
    in development/<schema>_update_next.sql, whose sections msm.prepareRelease moves into the
    next release's update script. Everything is written, or nothing.

    On the command line give the edits as a JSON file with --editsFile.

    Args:
        edits (list): The edits in order, each an object with its op: rename_table,
            rename_view, rename_column, rename_routine, add_table, drop_table, add_column,
            modify_column, drop_column, set_table_options, add_index, drop_index,
            set_primary_key, add_foreign_key, drop_foreign_key, add_view, drop_view,
            add_routine, drop_routine, with their members as the language server's
            docs/edit-format.md gives them (modify_column takes new_column, the whole column).
        base_hash (str): The source_hash of the schema snapshot the edits were made against. They
            are refused when the project's snapshot was made again since.
        **kwargs: Additional options.

    Keyword Args:
        edits_file (str): A JSON file holding the edits, instead of edits.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        rows (the diagrams VIEW afterwards), files written, migration (the statements kept for
        the next update script), migration_file and notes on what was not changed.
    """
    edits = _json_argument(edits, kwargs, "edits", "edits")
    if base_hash is None:
        raise ValueError("No base_hash given: the source_hash of the snapshot the edits are for.")
    if hasattr(edits, "__iter__") and not isinstance(edits, str) and not hasattr(edits, "keys"):
        # The shell's dictionaries as Python's; anything else is for
        # edit_schema to refuse, naming the edit.
        edits = [dict(edit) if hasattr(edit, "keys") else edit for edit in edits]
    return lib.editing.edit_schema(_project(kwargs), edits, base_hash, kwargs.get("lsp_path"))


@plugin_function("msm.removeSchemaDiagram", shell=True, cli=True, web=True)
def remove_schema_diagram(name: str, **kwargs) -> list:
    """Removes a diagram from a schema project

    Args:
        name (str): The name of the diagram.
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        The files written.
    """
    return lib.modeling.remove_diagram(_project(kwargs), name)


@plugin_function("msm.renameSchemaDiagram", shell=True, cli=True, web=True)
def rename_schema_diagram(name: str, new_name: str, **kwargs) -> list:
    """Renames a diagram of a schema project

    Only the name changes; the diagram's figures, pictures and place among the diagrams stay.
    The new name may not be one another diagram of the project has, in any case.

    Args:
        name (str): The name of the diagram.
        new_name (str): Its new name.
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        The files written.
    """
    return lib.modeling.rename_diagram(_project(kwargs), name, new_name)


@plugin_function("msm.createSchemaDiagram", shell=True, cli=True, web=True)
def create_schema_diagram(name: str, **kwargs) -> dict:
    """Adds a diagram of the schema's tables to a schema project, laid out

    The tables are laid out in layers: a table referred to by a foreign key goes above the
    tables referring to it, with as few crossing connections as the layout finds, and tables
    without foreign keys are packed in rows below. Every foreign key between two tables shown
    gets a connection. Views and sequences get a figure each, routines are listed on one
    routine group figure and events on one event group figure, placed below the tables; none
    of them gets connections. The schema snapshot is made
    again first where the development script changed since it was made, which needs the
    mariadb-lsp binary.

    On the command line give the lists as JSON, for example --tables='["customer","orders"]'.

    Args:
        name (str): The name of the new diagram.
        **kwargs: Additional options.

    Keyword Args:
        tables (list): The tables to show. Defaults to all tables of the schema.
        views (list): Views to show as well. Defaults to none.
        routines (list): Routines (procedures and functions) to list on a routine group figure.
            Defaults to none.
        events (list): Events to list on an event group figure. Defaults to none.
        sequences (list): Sequences to show. Defaults to none.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The diagram's name, its number of figures and connections, and the files written.
    """
    result = lib.modeling.create_diagram(
        _project(kwargs),
        name,
        kwargs.get("tables"),
        kwargs.get("lsp_path"),
        views=kwargs.get("views"),
        routines=kwargs.get("routines"),
        events=kwargs.get("events"),
        sequences=kwargs.get("sequences"),
    )
    if lib.core.get_interactive_default():
        print(
            f"The diagram `{name}` shows {result['figures']} figure(s) with "
            f"{result['connections']} connection(s)."
        )
    else:
        return result


@plugin_function("msm.layoutSchemaDiagram", shell=True, cli=True, web=True)
def layout_schema_diagram(name: str, **kwargs) -> dict:
    """Adds tables, views, sequences, routines and events to a diagram of a schema project and lays its figures out

    By default only the figures without a position (the objects just added, or figures a
    document given to msm.setSchemaDiagram leaves left and top out of) are laid out, as a block
    below the others; with keep_placed set to false every table, view, sequence and group
    figure is laid out again. Figures on a layer, notes and images keep their place. Foreign keys
    between tables on the diagram that are not drawn get a connection. The schema snapshot is
    made again first where the development script changed since it was made, which needs the
    mariadb-lsp binary.

    On the command line give the lists as JSON, for example --tables='["invoice"]'.

    Args:
        name (str): The name of the diagram.
        **kwargs: Additional options.

    Keyword Args:
        tables (list): Tables to add to the diagram. Defaults to none.
        views (list): Views to add. Defaults to none.
        routines (list): Routines to list on the diagram's routine group figure, which is added
            where the diagram has none. Defaults to none.
        events (list): Events to list on the diagram's event group figure, likewise. Defaults
            to none.
        sequences (list): Sequences to add. Defaults to none.
        keep_placed (bool): Whether figures that have a position keep it. Defaults to true.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The diagram's name, the objects added, the figures laid out (the tables, views, sequences
        and groups they show), the number of connections added, and the files written.
    """
    keep_placed = kwargs.get("keep_placed")
    result = lib.modeling.layout_diagram(
        _project(kwargs),
        name,
        kwargs.get("tables"),
        True if keep_placed is None else bool(keep_placed),
        kwargs.get("lsp_path"),
        views=kwargs.get("views"),
        routines=kwargs.get("routines"),
        events=kwargs.get("events"),
        sequences=kwargs.get("sequences"),
    )
    if lib.core.get_interactive_default():
        print(
            f"Laid out {len(result['placed'])} figure(s) of the diagram `{name}`"
            + (f", {len(result['added'])} of them added" if result["added"] else "")
            + "."
        )
    else:
        return result


def _prompted_copyright_holder(kwargs) -> str:
    """An import's copyright holder, asked for in an interactive shell where none is given."""
    copyright_holder = kwargs.get("copyright_holder")
    if (
        copyright_holder is None
        and not kwargs.get("copyrights")
        and lib.core.get_interactive_default()
    ):
        copyright_holder = lib.core.prompt("Enter the name of the copyright holder: ")
    return copyright_holder


def _print_notes(result: dict) -> None:
    for warning in result["warnings"]:
        print(f"Note: {warning}")


@plugin_function("msm.importWorkbenchModel", shell=True, cli=True, web=True)
def import_workbench_model(model_path: str, target_path: str, **kwargs) -> dict:
    """Creates a schema project from a MySQL Workbench model (.mwb)

    The development script gets the CREATE statements of a schema of the model: its tables and
    foreign keys, and the rows the model holds as INSERTs (MSM Section 140), its views, routines,
    triggers and events (Section 150), and its roles with their GRANTs (Section 170). A section
    larger than the project's maxInlineSectionSize goes into breakout files under
    development/sections/. User-defined types are replaced by their SQL definition. Then the
    schema snapshot is made from that SQL by the mariadb-lsp binary, and the model's EER
    diagrams are added. Scripts attached to the model are not imported, they are listed.

    Args:
        model_path (str): The .mwb file.
        target_path (str): The folder to create the project folder in.
        **kwargs: Additional options.

    Keyword Args:
        schema (str): The schema of the model to import. Needed when the model has several.
        copyright_holder (str): The name of the copyright holder.
        license (str): The license to use for the project.
        copyrights (list): The copyright notices of the project, instead of copyright_holder.
        year_of_creation (str): The year of creation of the copyright. Defaults to the current year.
        overwrite_existing (bool): If the project folder already exists, overwrite it.
        enforce_target_path (bool): Create the target_path if it does not yet exist.
        add_to_project_list (bool): Whether to add the new project to the project list (see
            msm.listProjects). Defaults to True.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The project's path and schema, the numbers of tables, views, routines, triggers, events,
        roles and inserted rows, the diagrams, the attached scripts that were not imported,
        warnings on what else was left out, and the files written.
    """
    copyright_holder = _prompted_copyright_holder(kwargs)

    result = lib.modeling.create_project_from_workbench_model(
        model_path,
        target_path,
        copyright_holder=copyright_holder,
        license=kwargs.get("license"),
        schema=kwargs.get("schema"),
        overwrite_existing=bool(kwargs.get("overwrite_existing", False)),
        enforce_target_path=bool(kwargs.get("enforce_target_path", False)),
        copyrights=kwargs.get("copyrights"),
        year_of_creation=kwargs.get("year_of_creation"),
        lsp_path=kwargs.get("lsp_path"),
    )
    if kwargs.get("add_to_project_list", True):
        lib.projects.add_project(result["project_path"])

    if lib.core.get_interactive_default():
        print(
            f"Created the project `{result['project_path']}` with {result['tables']} table(s), "
            f"{result['views']} view(s), {result['routines']} routine(s), "
            f"{result['triggers']} trigger(s), {result['events']} event(s), "
            f"{result['roles']} role(s) and {len(result['diagrams'])} diagram(s)."
        )
        if result["scripts_not_imported"]:
            print(
                "Scripts of the model that were not imported: "
                + ", ".join(result["scripts_not_imported"])
            )
        _print_notes(result)
    else:
        return result


@plugin_function("msm.listLiveSchemaObjects", shell=True, cli=True, web=True)
def list_live_schema_objects(schema: str, **kwargs) -> dict:
    """Lists the objects of a schema on a server that msm.importLiveSchema can import

    Args:
        schema (str): The schema on the server.
        **kwargs: Additional options.

    Keyword Args:
        session (object): The database session to use. Defaults to the global session.

    Returns:
        The tables (with their comments and triggers), views, procedures, functions, triggers
        (with their tables), events and sequences of the schema, the version msm deployed of it
        where it has one, and msm's own objects, which are never imported.
    """
    session = lib.core.get_current_session(kwargs.get("session", None))
    result = lib.live_schema.list_schema_objects(session, schema)
    if lib.core.get_interactive_default():
        print(json.dumps(result, indent=4))
        return None
    return result


@plugin_function("msm.importLiveSchema", shell=True, cli=True, web=True)
def import_live_schema(schema: str, target_path: str, **kwargs) -> dict:
    """Creates a schema project from a schema on a server

    The development script gets the CREATE statements the server gives for the schema's objects,
    or for the selected ones: its tables and sequences (MSM Section 140), its views, routines,
    triggers and events (Section 150). DEFINER clauses, the schema's own name in qualified names
    and the tables' AUTO_INCREMENT counters are left out, so the project deploys anywhere under
    any name. A section larger than the project's maxInlineSectionSize goes into breakout files
    under development/sections/. Then the schema snapshot is made from that SQL by the
    mariadb-lsp binary. Data, users, roles and grants are not imported, nor msm's own objects.

    Args:
        schema (str): The schema on the server; the project gets its name.
        target_path (str): The folder to create the project folder in.
        **kwargs: Additional options.

    Keyword Args:
        objects (dict): The objects to import, {tables, views, procedures, functions, triggers,
            events, sequences: [names]}; a kind left out imports none of it. Defaults to all
            objects. A trigger needs its table.
        session (object): The database session to use. Defaults to the global session.
        copyright_holder (str): The name of the copyright holder.
        license (str): The license to use for the project.
        copyrights (list): The copyright notices of the project, instead of copyright_holder.
        year_of_creation (str): The year of creation of the copyright. Defaults to the current year.
        overwrite_existing (bool): If the project folder already exists, overwrite it.
        enforce_target_path (bool): Create the target_path if it does not yet exist.
        add_to_project_list (bool): Whether to add the new project to the project list (see
            msm.listProjects). Defaults to True.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The project's path and schema, the numbers of tables, sequences, views, routines, triggers
        and events, the version msm deployed of the schema where it has one, warnings on what was
        left out, and the files written.
    """
    session = lib.core.get_current_session(kwargs.get("session", None))
    copyright_holder = _prompted_copyright_holder(kwargs)

    result = lib.modeling.create_project_from_live_schema(
        session,
        schema,
        target_path,
        objects=kwargs.get("objects"),
        copyright_holder=copyright_holder,
        license=kwargs.get("license"),
        overwrite_existing=bool(kwargs.get("overwrite_existing", False)),
        enforce_target_path=bool(kwargs.get("enforce_target_path", False)),
        copyrights=kwargs.get("copyrights"),
        year_of_creation=kwargs.get("year_of_creation"),
        lsp_path=kwargs.get("lsp_path"),
    )
    if kwargs.get("add_to_project_list", True):
        lib.projects.add_project(result["project_path"])

    if lib.core.get_interactive_default():
        print(
            f"Created the project `{result['project_path']}` with {result['tables']} table(s), "
            f"{result['sequences']} sequence(s), {result['views']} view(s), "
            f"{result['routines']} routine(s), {result['triggers']} trigger(s) and "
            f"{result['events']} event(s)."
        )
        _print_notes(result)
    else:
        return result
