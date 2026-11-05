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
def set_schema_diagram(document: dict, **kwargs) -> list:
    """Adds a diagram to a schema project, or replaces the one of the same name

    Args:
        document (dict): The diagram, an msm-diagram/1 document: format, name, layers, figures
            and connections.
        **kwargs: Additional options.

    Keyword Args:
        schema_project_path (str): The path to the schema project. Defaults to the working directory.

    Returns:
        The files written.
    """
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
