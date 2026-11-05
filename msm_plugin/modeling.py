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
    document_file = kwargs.get("document_file")
    if document_file is not None:
        if document:
            raise ValueError("Give the diagram as document or as document_file, not both.")
        try:
            with open(os.path.expanduser(document_file), "r", encoding="utf-8") as f:
                document = json.load(f)
        except OSError as e:
            raise ValueError(f"The diagram file could not be read: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"The diagram file `{document_file}` is not valid JSON: {e}")
    if document is None:
        raise ValueError("No diagram given: pass document or document_file.")
    return lib.modeling.set_diagram(_project(kwargs), dict(document))


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


@plugin_function("msm.createSchemaDiagram", shell=True, cli=True, web=True)
def create_schema_diagram(name: str, **kwargs) -> dict:
    """Adds a diagram of the schema's tables to a schema project, laid out

    The tables are laid out in layers: a table referred to by a foreign key goes above the
    tables referring to it, with as few crossing connections as the layout finds, and tables
    without foreign keys are packed in rows below. Every foreign key between two tables shown
    gets a connection. The schema snapshot is made again first where the development script
    changed since it was made, which needs the mariadb-lsp binary.

    On the command line give the tables as JSON, for example --tables='["customer","orders"]'.

    Args:
        name (str): The name of the new diagram.
        **kwargs: Additional options.

    Keyword Args:
        tables (list): The tables to show. Defaults to all tables of the schema.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The diagram's name, its number of figures and connections, and the files written.
    """
    result = lib.modeling.create_diagram(
        _project(kwargs), name, kwargs.get("tables"), kwargs.get("lsp_path")
    )
    if lib.core.get_interactive_default():
        print(
            f"The diagram `{name}` shows {result['figures']} table(s) with "
            f"{result['connections']} connection(s)."
        )
    else:
        return result


@plugin_function("msm.layoutSchemaDiagram", shell=True, cli=True, web=True)
def layout_schema_diagram(name: str, **kwargs) -> dict:
    """Adds tables to a diagram of a schema project and lays its figures out

    By default only the figures without a position (the tables just added, or figures a
    document given to msm.setSchemaDiagram leaves left and top out of) are laid out, as a block
    below the others; with keep_placed set to false every figure is laid out again. Figures on
    a layer keep their place. Foreign keys between tables on the diagram that are not drawn get
    a connection. The schema snapshot is made again first where the development script changed
    since it was made, which needs the mariadb-lsp binary.

    On the command line give the tables as JSON, for example --tables='["invoice"]'.

    Args:
        name (str): The name of the diagram.
        **kwargs: Additional options.

    Keyword Args:
        tables (list): Tables to add to the diagram. Defaults to none.
        keep_placed (bool): Whether figures that have a position keep it. Defaults to true.
        schema_project_path (str): The path to the schema project. Defaults to the working directory.
        lsp_path (str): The mariadb-lsp binary or its folder. By default it is looked for next to
            the shell, on the PATH and in ~/.local/bin.

    Returns:
        The diagram's name, the tables added, the tables laid out, the number of connections
        added, and the files written.
    """
    keep_placed = kwargs.get("keep_placed")
    result = lib.modeling.layout_diagram(
        _project(kwargs),
        name,
        kwargs.get("tables"),
        True if keep_placed is None else bool(keep_placed),
        kwargs.get("lsp_path"),
    )
    if lib.core.get_interactive_default():
        print(
            f"Laid out {len(result['placed'])} table(s) of the diagram `{name}`"
            + (f", {len(result['added'])} of them added" if result["added"] else "")
            + "."
        )
    else:
        return result


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
    copyright_holder = kwargs.get("copyright_holder")
    if (
        copyright_holder is None
        and not kwargs.get("copyrights")
        and lib.core.get_interactive_default()
    ):
        copyright_holder = lib.core.prompt("Enter the name of the copyright holder: ")

    result = lib.modeling.create_project_from_workbench_model(
        model_path,
        target_path,
        copyright_holder=copyright_holder,
        license=kwargs.get("license"),
        schema=kwargs.get("schema"),
        overwrite_existing=bool(kwargs.get("overwrite_existing", False)),
        enforce_target_path=bool(kwargs.get("enforce_target_path", False)),
        copyrights=kwargs.get("copyrights"),
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
        for warning in result["warnings"]:
            print(f"Note: {warning}")
    else:
        return result
