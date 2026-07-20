# Copyright (c) 2026, MariaDB plc and/or its affiliates.
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
``msm_plugin/management.py`` module and is exposed on the FastMCP server built
in :mod:`mcp_plugin.lib.server`.

The tools run while the shell is in non-interactive mode, so the wrapped
``msm`` plugin functions return their results instead of prompting for input.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp

from typing import Optional

import mysqlsh

from mcp_plugin.lib import config


def register_msm_tools(server) -> None:
    """Registers the MariaDB Schema Management tools on the given server.

    Args:
        server: The FastMCP server instance to register the tools on.

    Returns:
        None
    """
    from msm_plugin import management as msm

    def _kwargs(**pairs) -> dict:
        """Builds a kwargs dict, dropping keys whose value is None."""
        return {key: value for key, value in pairs.items() if value is not None}

    def _require_allowed_path(path) -> None:
        """Raises unless the given path is within an allowed directory.

        A value of None is left to the msm plugin's own default handling.
        """
        if path is None:
            return
        if not config.is_path_allowed(path):
            raise mysqlsh.Error(
                f"Access to path '{path}' is not allowed. Add it (or a parent "
                "directory) to the allowed paths with mcp.setup."
            )

    @server.tool(name="msm.create_project")
    def create_project(
        schema_name: str,
        target_path: str,
        copyright_holder: Optional[str] = None,
        license: Optional[str] = None,
        overwrite_existing: bool = False,
        allow_special_chars: bool = False,
        enforce_target_path: bool = False,
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

        Returns:
            The path of the created project folder.
        """
        _require_allowed_path(target_path)
        return msm.create_new_project_folder(
            schema_name=schema_name,
            target_path=target_path,
            copyright_holder=copyright_holder,
            **_kwargs(
                license=license,
                overwrite_existing=overwrite_existing,
                allow_special_chars=allow_special_chars,
                enforce_target_path=enforce_target_path,
            ),
        )

    @server.tool(name="msm.get_project_information")
    def get_project_information(
        schema_project_path: Optional[str] = None,
    ) -> Optional[dict]:
        """Returns information about a database schema project.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            A dict with information about the schema project.
        """
        _require_allowed_path(schema_project_path)
        return msm.get_project_information(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.set_development_version")
    def set_development_version(
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
        _require_allowed_path(schema_project_path)
        return msm.set_development_version(
            **_kwargs(version=version, schema_project_path=schema_project_path)
        )

    @server.tool(name="msm.get_released_versions")
    def get_released_versions(
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns all released versions of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of released versions.
        """
        _require_allowed_path(schema_project_path)
        return msm.get_released_versions(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.get_last_released_version")
    def get_last_released_version(
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last released version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last released version.
        """
        _require_allowed_path(schema_project_path)
        return msm.get_last_released_version(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.get_last_deployment_version")
    def get_last_deployment_version(
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last deployment version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last deployment version.
        """
        _require_allowed_path(schema_project_path)
        return msm.get_last_deployment_version(
            **_kwargs(schema_project_path=schema_project_path)
        )

    @server.tool(name="msm.prepare_release")
    def prepare_release(
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
        _require_allowed_path(schema_project_path)
        return msm.prepare_release(
            **_kwargs(
                version=version,
                next_version=next_version,
                schema_project_path=schema_project_path,
                allow_to_stay_on_same_version=allow_to_stay_on_same_version,
                overwrite_existing=overwrite_existing,
            )
        )

    @server.tool(name="msm.get_sql_content_from_section")
    def get_sql_content_from_section(file_path: str, section_id: str) -> Optional[str]:
        """Returns the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.

        Returns:
            The SQL content as a string.
        """
        _require_allowed_path(file_path)
        return msm.get_sql_content_from_section(file_path=file_path, section_id=section_id)

    @server.tool(name="msm.set_section_sql_content")
    def set_section_sql_content(
        file_path: str, section_id: str, sql_content: str
    ) -> None:
        """Sets the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.
            sql_content: The SQL content to set.

        Returns:
            None
        """
        _require_allowed_path(file_path)
        return msm.set_section_sql_content(
            file_path=file_path, section_id=section_id, sql_content=sql_content
        )

    @server.tool(name="msm.generate_deployment_script")
    def generate_deployment_script(
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
        _require_allowed_path(schema_project_path)
        return msm.generate_deployment_script(
            **_kwargs(
                version=version,
                schema_project_path=schema_project_path,
                overwrite_existing=overwrite_existing,
            )
        )

    @server.tool(name="msm.get_deployment_script_versions")
    def get_deployment_script_versions(
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the list of deployment script versions.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of deployed versions.
        """
        _require_allowed_path(schema_project_path)
        return msm.get_deployment_script_versions(
            **_kwargs(schema_project_path=schema_project_path)
        )
