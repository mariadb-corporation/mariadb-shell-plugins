# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The REST client SDK of a service a schema project defines (Section 180).

Writes the files rest.dumpSdkServiceFiles writes for a service on a server,
with rest_plugin's own generator, from the document rest_sdk_data builds out
of the project's outline. The folder's mrs.config.json records the project
(msmProject: {path, service}, path relative to the folder where it can be)
and no serviceId, so a client can make the SDK again without a connection.
"""

import os
from typing import Optional

from msm_plugin.lib import diagrams, lsp, management, modeling, rest_paths
from msm_plugin.lib import rest_sdk_data


def require_lsp(lsp_path: Optional[str] = None) -> tuple:
    """(binary, whether its outline has column_types); raises where the
    binary cannot tell the REST definition as the SDK needs it."""
    binary = lsp.require_lsp(lsp_path)
    features = lsp.outline_features(binary)
    # How msm reads the REST definition anywhere, and the objects' details
    if not lsp.REST_OUTLINE_FEATURES | rest_sdk_data.SDK_OUTLINE_FEATURES <= features:
        raise lsp.LspError(
            f"The REST SDK of a schema project is made with the MariaDB SQL language server, "
            f"and the installed one ({binary}) cannot tell the details of REST objects yet. "
            f"Update {lsp.LSP_BINARY_NAME}, or the MariaDB Shell, which comes with it."
        )
    return binary, rest_sdk_data.COLUMN_TYPES_FEATURE in features


def _find_service(outlined: dict, service: str) -> dict:
    key = rest_paths.path_key(service)
    for node in (outlined.get("rest") or {}).get("services", []):
        if node.get("path") and rest_paths.path_key(node["path"]) == key:
            return node
    raise ValueError(f"Section 180 of the project defines no REST service {service}.")


def service_data(
    schema_project_path: str, service: str, lsp_path: Optional[str] = None
) -> tuple:
    """(service node of the outline, service document, warnings) of a
    service the project's Section 180 defines."""
    binary, column_types = require_lsp(lsp_path)
    project = modeling._Project.load(schema_project_path)
    outlined = modeling.outline(project, binary)

    node = _find_service(outlined, service)
    warnings = []
    if not node.get("declared", True):
        warnings.append(
            f"Section 180 only names the REST service {node['path']}, it does not create it: "
            "the SDK holds the schemas and objects the project adds to it."
        )
    if node.get("enabled") is False:
        raise ValueError("Generating the MRS SDK requires a service to be enabled.")
    for note in diagrams.unread_statements(outlined):
        warnings.append(f"A statement was not read: {note.get('message')}")
    if not column_types:
        warnings.append(
            f"This {lsp.LSP_BINARY_NAME} cannot tell the server's form of column types: they "
            "are taken as written, so a type such as BOOLEAN or SERIAL may give another SDK "
            "type than on a server. Update the MariaDB Shell to fix that."
        )

    schema_name = management.get_effective_schema_name(project.settings)
    document = diagrams.schema_document_from_outline(outlined, schema_name, [], "")
    catalog = rest_sdk_data.Catalog(document, schema_name, column_types)
    seed = f"{os.path.realpath(schema_project_path)}\n{rest_paths.path_key(node['path'])}"
    service_res, more = rest_sdk_data.service_document(node, catalog, seed)
    return node, service_res, warnings + more


def _project_reference(schema_project_path: str, directory: str) -> str:
    """The project's path as the SDK folder records it: relative to the
    folder where it can be (not across drives), with forward slashes."""
    project = os.path.realpath(schema_project_path)
    try:
        path = os.path.relpath(project, os.path.realpath(directory))
    except ValueError:
        path = project
    return path.replace(os.sep, "/")


def dump_rest_sdk(
    schema_project_path: str,
    service: str,
    directory: str,
    options: Optional[dict] = None,
    lsp_path: Optional[str] = None,
) -> dict:
    """Writes the REST client SDK of a service the project defines.

    Args:
        schema_project_path: The schema project.
        service: The service's path as Section 180 writes it (/svc,
            mike,anna@/svc), compared as the server compares it.
        directory: The SDK folder, made where it is missing.
        options: service_url, sdk_language, add_app_base_class, header, as
            rest.dumpSdkServiceFiles takes them; those not given are the
            folder's stored ones ({} makes the SDK again as it was).
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        {directory, files, service, sdk_language, warnings}.
    """
    from rest_plugin import services as rest_services
    from rest_plugin.lib import sdk as rest_sdk

    if not directory:
        raise ValueError("No directory given.")
    directory = os.path.abspath(directory)
    options = dict(options or {})
    unknown = set(options) - {"service_url", "sdk_language", "add_app_base_class", "header"}
    if unknown:
        raise ValueError(f"Unknown SDK options: {', '.join(sorted(unknown))}.")

    mrs_config = rest_services.sdk_files_config(directory, options)
    node, service_res, warnings = service_data(schema_project_path, service, lsp_path)
    files = rest_services.write_sdk_files(
        directory,
        mrs_config,
        service_res["url_context_root"],
        lambda: rest_sdk.generate_service_sdk(
            service=None,
            session=None,
            sdk_language=mrs_config["sdkLanguage"].lower(),
            service_url=mrs_config["serviceUrl"],
            service_data=service_res,
        ),
        {
            # A project's service has no metadata id
            "serviceId": None,
            "msmProject": {
                "path": _project_reference(schema_project_path, directory),
                "service": node["path"],
            },
        },
    )
    return {
        "directory": directory,
        "files": files,
        "service": node["path"],
        "sdk_language": mrs_config["sdkLanguage"],
        "warnings": warnings,
    }
