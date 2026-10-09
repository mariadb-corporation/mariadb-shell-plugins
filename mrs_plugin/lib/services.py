# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
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

from mrs_plugin.lib import core, schemas, database

import re
import os
from zipfile import ZipFile, is_zipfile
import json
import shutil
from datetime import datetime
import mysqlsh
from tempfile import TemporaryDirectory
from urllib.parse import urlparse, ParseResult
import urllib.request
import ssl


def query_services(
    session,
    service_id: bytes = None,
    url_context_root=None,
    url_host_name="",
    get_default=False,
    developer_list=None,
    auth_app_id=None,
):
    """Query MRS services

    Query the existing services. Filters may be applied as the 'service_id' or
    the 'url_context_root' with the 'url_host_name'.

    In the case no service is found, the default service may be fetched if the
    'get_default' is set to True.

    To get the default service, don't set any other filters and set 'get_default'
    to True.

    Args:
        session (object): The database session to use.
        service_id: The id of the service
        url_context_root (str): The context root for this service
        get_default (bool): Whether to return the default service

    Returns:
        The list of found services.
    """
    if url_context_root and not url_context_root.startswith("/"):
        raise Exception("The url_context_root has to start with '/'.")

    url_host_name = ""  # no longer supported

    current_service_id = get_current_service_id(session)
    if not current_service_id:
        current_service_id = core.NIL_UUID

    wheres = []
    params = [current_service_id]

    sql = f"""
        SELECT se.id, se.enabled, se.published, se.url_protocol, h.name AS url_host_name,
            se.url_context_root, se.comments, se.options, se.url_host_id,
            CONCAT(h.name, se.url_context_root) AS host_ctx,
            (SELECT CONCAT(COALESCE(CONCAT(GROUP_CONCAT(IF(item REGEXP '^[A-Za-z0-9_]+$', item, QUOTE(item)) ORDER BY item), '@'), ''), h.name, se.url_context_root) FROM JSON_TABLE(
                JSON_UNQUOTE(JSON_EXTRACT(se.in_development, '$.developers')), '$[*]' COLUMNS (item text path '$')
                ) AS jt) AS full_service_path,
            se.auth_path, se.auth_completed_url,
            se.auth_completed_url_validation,
            se.auth_completed_page_content,
            se.metadata, se.parent_id,
            se.id = ? as is_current,
            se.in_development,
            (SELECT GROUP_CONCAT(IF(item REGEXP '^[A-Za-z0-9_]+$', item, QUOTE(item)) ORDER BY item)
                FROM JSON_TABLE(
                JSON_UNQUOTE(JSON_EXTRACT(se.in_development, '$.developers')), '$[*]' COLUMNS (item text path '$')
                ) AS jt) AS sorted_developers,
            se.name,
            (SELECT JSON_ARRAYAGG(aa.name) FROM <metadata>.`service_has_auth_app` sa2
                JOIN <metadata>.`auth_app` AS aa ON
                    sa2.auth_app_id = aa.id
            WHERE sa2.service_id = se.id) AS auth_apps
        FROM <metadata>.`service` se
            LEFT JOIN <metadata>.url_host h
                ON se.url_host_id = h.id
        """

    if auth_app_id is not None:
        sql += """
            JOIN <metadata>.`service_has_auth_app` sa
                ON se.id = sa.service_id AND sa.auth_app_id = ?
            """
        params.append(auth_app_id)
        # Make sure that each user only sees the services that are either public or the user is a developer of
        # wheres.append("(in_development IS NULL OR "
        #               "SUBSTRING_INDEX(CURRENT_USER(),'@',1) MEMBER OF(JSON_UNQUOTE(JSON_EXTRACT(in_development, '$.developers'))))")

    if service_id:
        wheres.append("se.id = ?")
        params.append(service_id)
    elif (
        url_context_root is not None
        and url_host_name is not None
        and developer_list is None
    ):
        wheres.append("h.name = ?")
        wheres.append("url_context_root = ?")
        params.append(url_host_name)
        params.append(url_context_root)
        wheres.append("se.in_development IS NULL")
    elif get_default:
        # if nothing else is supplied and get_default is True, then get the default service
        wheres = ["se.id = ?"]
        params = [current_service_id, current_service_id]

        return (
            core.MrsDbExec(sql + core._generate_where(wheres), params)
            .exec(session)
            .items
        )

    having = ""
    if developer_list is not None:

        def quote(s):
            return f"'{s}'"

        # Build the sorted_developer string that matches the selected column, use same quoting as MySQL
        developer_list.sort()
        sorted_developers = ",".join(
            (
                dev
                if re.match("^[A-Za-z0-9_-]*$", dev)
                else quote(re.sub(r"(['\\])", "\\\\\\1", dev, 0, re.MULTILINE))
            )
            for dev in developer_list
        )
        # Only the computed alias may go into HAVING: with ONLY_FULL_GROUP_BY,
        # MariaDB refuses plain columns there (1463), while MySQL lets them
        # through. Host and path are ordinary WHERE conditions.
        wheres.append("h.name = ?")
        wheres.append("url_context_root = ?")
        params.append(url_host_name)
        params.append(url_context_root)
        having = "\nHAVING sorted_developers = ?"
        params.append(sorted_developers)

    result = (
        core.MrsDbExec(
            sql
            + core._generate_where(wheres)
            + having
            + "\nORDER BY se.url_context_root, h.name, sorted_developers",
            params,
        )
        .exec(session)
        .items
    )

    if len(result) == 0 and get_default:
        # No service was found s if we should get the default, then lets get it
        wheres = ["se.id = ?"]
        params = [current_service_id, current_service_id]

        result = (
            core.MrsDbExec(sql + core._generate_where(wheres), params)
            .exec(session)
            .items
        )

    return result


def get_service(
    session,
    service_id: bytes = None,
    url_context_root=None,
    url_host_name=None,
    get_default=False,
    developer_list=None,
):
    """Gets a specific MRS service

    If no service is specified, the service that is set as current service is
    returned if it was defined before

    Args:
        session (object): The database session to use.
        service_id: The id of the service
        url_context_root (str): The context root for this service
        get_default (bool): Whether to return the default service

    Returns:
        The service as dict or None on error in interactive mode
    """
    # url_host_name kept as a param for temporary backwards compat, but is no longer supported
    result = query_services(
        session,
        service_id=service_id,
        url_context_root=url_context_root,
        url_host_name="",
        get_default=get_default,
        developer_list=developer_list,
    )
    return result[0] if len(result) == 1 else None


def get_services(session):
    """Get a list of MRS services

    Args:
        session (object): The database session to use.

    Returns:
        List of dicts representing the services
    """
    return query_services(session)


def get_current_service(session):
    service_id = get_current_service_id(session)

    return get_service(session=session, service_id=service_id)


def get_current_service_id(session):
    """Returns the current service

    Args:
        session (object): The database session to use.

    Returns:
        The current or default service or None if no default is set
    """
    if not session:
        raise RuntimeError("A valid session is required.")

    config = core.ConfigFile()

    current_objects = config.settings.get("current_objects", [])

    # Try to find the settings for the connection which the service resides on
    connection_settings = list(
        filter(
            lambda item: item["connection"] == core.get_session_uri(session),
            current_objects,
        )
    )

    if not connection_settings:
        return None

    # Settings written by earlier versions hold the id in its '0x' form.
    return core.id_to_uuid(
        connection_settings[0].get("current_service_id"),
        "current_service_id",
        allowNone=True,
    )


def store_project_validations(
    session,
    destination: str,
    services: list,
    schemas: list,
    project_settings: dict,
    create_zip: bool,
):
    core.validate_path_for_filesystem(destination)

    for service_data in services:
        service_name = service_data["name"]
        service = get_service(session, url_context_root=service_name)
        if service is None:
            raise Exception(f"The service '{service_name}' was not found.")

    if schemas:
        for schema_request_path in schemas:
            file_path = schema_request_path.get("file_path")
            if file_path and not (
                os.path.exists(file_path)
                and (os.path.isfile(file_path) or os.path.isdir(file_path))
            ):

                raise Exception(f"The given schema '{file_path}' was not found")

    if project_settings["icon_path"]:
        if not os.path.isfile(project_settings["icon_path"]):
            raise Exception("The icon path is not valid.")


def auto_detect_project_dependencies(session, service_id):
    """This function will auto-detect the schemas that this service
    depends on"""
    result = []

    for schema in schemas.get_schemas(session, service_id):
        if schema["schema_type"] == "DATABASE_SCHEMA":
            result.append({"name": schema["name"], "file_path": None})

    return result


# The endpoint selections of SHOW CREATE REST SERVICE ... INCLUDING ... ENDPOINTS,
# each one a superset of the former
ENDPOINT_SELECTIONS = (
    "DATABASE",
    "DATABASE AND STATIC",
    "DATABASE AND STATIC AND DYNAMIC",
    "ALL",
)


def endpoint_selection(
    include_database_endpoints: bool,
    include_static_endpoints: bool,
    include_dynamic_endpoints: bool,
):
    """Returns the endpoint selection for the given flags, None for none.

    The dynamic endpoints include the static ones and those the database ones.
    """
    if include_dynamic_endpoints:
        return "ALL"
    if include_static_endpoints:
        return "DATABASE AND STATIC"
    if include_database_endpoints:
        return "DATABASE"
    return None


def service_script(session, service_path: str, endpoints: str | None = None) -> str:
    """Returns the REST SQL script that recreates a service.

    The script comes from the shell's mrs module through session.run_sql:
    SHOW CREATE REST SERVICE with INCLUDING <endpoints> ENDPOINTS.
    """
    including = ""
    if endpoints:
        endpoints = " ".join(endpoints.upper().split())
        if endpoints not in ENDPOINT_SELECTIONS:
            raise ValueError(
                f"Invalid endpoints '{endpoints}', use one of "
                + ", ".join(ENDPOINT_SELECTIONS)
                + "."
            )
        including = f" INCLUDING {endpoints} ENDPOINTS"
    return session.run_sql(
        f"SHOW CREATE REST SERVICE {core.quote_service_path(service_path)}{including}"
    ).fetch_one()[0]


def dump_service_script(
    session, service_path: str, file_path: str, endpoints: str | None = None
):
    """Writes the REST SQL script of a service to a file."""
    script = service_script(session, service_path, endpoints)
    with open(file_path, "w") as f:
        f.write(script)
        if not script.endswith("\n"):
            f.write("\n")


# The service path of the two statements a service script starts with: the
# path may carry a developer list (`dev`@/path) and backtick-quoted parts.
SERVICE_PATH = r"((?:`(?:[^`]|``)*`|[^\s;`])+)"
SERVICE_SCRIPT_START = (
    re.compile(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?REST\s+SERVICE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        + SERVICE_PATH,
        re.IGNORECASE,
    ),
    re.compile(r"^USE\s+REST\s+SERVICE\s+" + SERVICE_PATH, re.IGNORECASE),
)


def load_service_script(session, script: str, as_path: str | None = None):
    """Runs the REST SQL script of a service.

    With as_path, the service is created under that request path. The script
    (SHOW CREATE REST SERVICE ... INCLUDING ... ENDPOINTS) names the service
    only in its first two statements, CREATE REST SERVICE and USE REST
    SERVICE; the other statements act on the current service. as_path
    replaces the whole service path there, a developer list included.
    """
    statements = split_sql_script(script)

    if as_path:
        new_path = core.quote_service_path(as_path)
        for i, pattern in enumerate(SERVICE_SCRIPT_START):
            if i >= len(statements):
                break
            match = pattern.match(statements[i])
            if match is None:
                if i == 0:
                    raise ValueError(
                        "The script does not start with a CREATE REST SERVICE statement."
                    )
                break
            statements[i] = (
                statements[i][: match.start(1)]
                + new_path
                + statements[i][match.end(1) :]
            )

    for statement in statements:
        session.run_sql(statement)


def store_project(
    session,
    destination: str,
    services: list,
    schemas: list,
    project_settings: dict,
    create_zip: bool,
):

    # expand the destination path
    destination = os.path.expanduser(destination)

    config = {
        "name": project_settings["name"],
        "version": project_settings["version"],
        "restServices": [],
        "schemas": [],
        "creationDate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if project_settings["publisher"]:
        config["publisher"] = project_settings["publisher"]

    if project_settings["description"]:
        config["description"] = project_settings["description"]

    # remove the ".zip" from the destination to create the temp directory
    temp_dir = (
        destination[:-4] if create_zip and destination.endswith(".zip") else destination
    )

    os.makedirs(temp_dir, exist_ok=True)

    # copy icon if set to do so
    if project_settings["icon_path"]:
        config["icon"] = f"appIcon{os.path.splitext(project_settings["icon_path"])[1]}"
        icon_target_path = os.path.join(temp_dir, config["icon"])
        shutil.copy(project_settings["icon_path"], icon_target_path)

    for service_data in services:
        service = get_service(session, url_context_root=service_data["name"])

        # This is a special case for when the project dump is triggered by the
        # frontend. The only thing that is asked from the user will be the
        # destination path. A single service will be used (the one selected by
        # the user). In this case, we'll try to auto-detect the project dependencies.
        # This should be solved when we build a full dialog where the user can choose
        # what to add to the project.
        if len(services) == 1 and not schemas == 0:
            schemas = auto_detect_project_dependencies(session, service["id"])

        # create the path by removing the '/' in the service request path
        target_file_name = f"{service_data["name"][1:]}.service.mrs.sql"
        file_path = os.path.join(temp_dir, target_file_name)

        dump_service_script(
            session,
            service_data["name"],
            file_path,
            endpoint_selection(
                service_data["include_database_endpoints"],
                service_data["include_static_endpoints"],
                service_data["include_dynamic_endpoints"],
            ),
        )

        config["restServices"].append(
            {
                "serviceName": service_data["name"],
                "fileName": target_file_name,
            }
        )

    for schema_data in schemas:
        schema_target = os.path.join(temp_dir, schema_data["name"])
        schema_format = None
        schema_relative_path = core.make_string_valid_for_filesystem(
            schema_data["name"]
        )

        if schema_data.get("file_path"):
            # The schema dump already exists, so we just need to copy it
            if os.path.isdir(schema_data["file_path"]):
                schema_format = "folder"

                shutil.copytree(schema_data["file_path"], schema_target)
            else:
                schema_target = f"{schema_target}.sql"
                schema_relative_path = f"{schema_relative_path}.sql"
                schema_format = "sqlFile"

                shutil.copy(schema_data["file_path"], schema_target)
        else:
            # Create a schema dump into the target directory
            schema_format = "dump"

            # Set the mysqlsh session to the one that was given
            if "shell.Object" in str(type(session)):
                mysqlsh.globals.shell.set_session(session)
            else:
                mysqlsh.globals.shell.set_session(session.session)
            mysqlsh.globals.util.dump_schemas(
                [schema_data["name"]],
                f"file://{schema_target}",
                {
                    "skipUpgradeChecks": True,
                    "showProgress": False,
                },
            )

        config["schemas"].append(
            {
                "schemaName": schema_data["name"],
                "path": schema_relative_path,
                "format": schema_format,  # sqlFile or folder or dump
            }
        )

    with open(os.path.join(temp_dir, "mrs.package.json"), "w") as f:
        json.dump(config, f, indent=4)

    if create_zip:
        zf = ZipFile(destination, "w")
        for dirname, subdirs, files in os.walk(temp_dir):
            for filename in files:
                zip_filename = os.path.join(dirname, filename)[
                    len(temp_dir) + 1 :
                ]  # truncate the base directory
                zf.write(os.path.join(dirname, filename), arcname=zip_filename)
        zf.close()

        shutil.rmtree(temp_dir)


def split_sql_script(sql_script):
    """The statements of a script, without empty ones"""
    commands = (command.strip() for command in mysqlsh.mysql.split_script(sql_script))
    return [command for command in commands if command]


def run_sql_script(session, sql_script):
    for command in split_sql_script(sql_script):
        session.run_sql(command)


def is_url(url) -> bool:
    result: ParseResult = urlparse(url)
    return all([result.scheme, result.netloc])


def is_github_shortcut(url) -> bool:
    if "/" not in url:
        return False

    parts = url.split("/")
    if parts[0] not in ["github.com", "github"]:
        return False
    if len(parts) != 3:
        return False

    return True


class LoadProjectFileContext:
    def __init__(self, path: str) -> None:
        self.path = path
        self.download_dir = None
        self.extract_dir = None
        self.repo = None

        # if it's a GitHub shortcut, resolve it to download the zip
        # for the master sources
        if is_github_shortcut(path):
            branch = "main"
            if "|" in path:
                path, branch = path.split("|")

            _, user, self.repo = path.split("/")
            path = (
                f"https://github.com/{user}/{self.repo}/archive/refs/heads/{branch}.zip"
            )

        # if it's a remote file, download it
        if is_url(path):
            self.download_dir = TemporaryDirectory(delete=False)
            self.path = os.path.join(self.download_dir.name, "download.zip")

            with urllib.request.urlopen(
                path, context=ssl._create_unverified_context()
            ) as response:

                with open(self.path, "w+b") as f:
                    f.write(response.read())

        # if the file is a zip file, extract it to a directory
        if is_zipfile(self.path):
            self.extract_dir = TemporaryDirectory(delete=False)
            zip_file = ZipFile(self.path)
            zip_file.extractall(self.extract_dir.name)
            self.path = self.extract_dir.name

            sub_items = os.listdir(self.path)
            if len(sub_items) == 1 and os.path.isdir(
                os.path.join(self.path, sub_items[0])
            ):
                self.path = os.path.join(self.path, sub_items[0])

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if self.download_dir:
            self.download_dir.cleanup()
            self.download_dir = None

        if self.extract_dir:
            self.extract_dir.cleanup()
            self.extract_dir = None

        return False


def load_project(session, path: str):
    with LoadProjectFileContext(path) as base_directory:

        project_file = os.path.join(base_directory, "mrs.package.json")
        project_config = None

        with open(project_file, "r") as f:
            project_config = json.load(f)

        with core.MrsDbTransaction(session):
            for schema in project_config.get("schemas", []):
                if database.get_schema(session, schema) is not None:
                    raise ValueError(f"The schema '{schema}' already exists.")

                schema_path = os.path.join(base_directory, schema["path"])

                if schema["format"] == "sqlFile":
                    with open(schema_path) as f:
                        run_sql_script(session, f.read())
                elif schema["format"] == "folder":
                    folder_path = schema_path
                    only_files = [
                        f
                        for f in os.listdir(folder_path)
                        if os.path.isfile(os.path.join(folder_path, f))
                        and f.endswith(".sql")
                    ]

                    for file in only_files:
                        file = os.path.join(schema_path, file)
                        with open(file) as f:
                            run_sql_script(session, f.read())

                elif schema["format"] == "dump":
                    if "shell.Object" in str(type(session)):
                        mysqlsh.globals.shell.set_session(session)
                    else:
                        mysqlsh.globals.shell.set_session(session.session)

                    with core.ServerLocalInFile(session, True):
                        mysqlsh.globals.util.load_dump(
                            schema_path, ignoreExistingObjects=True
                        )
                else:
                    raise Exception("Invalid schema format.")

            for service in project_config.get("restServices", []):
                if (
                    get_service(session, url_context_root=service["serviceName"])
                    is not None
                ):
                    raise ValueError(
                        f"The service '{service["serviceName"]}' already exists."
                    )

                service_file = os.path.join(base_directory, service["fileName"])
                with open(service_file) as f:
                    load_service_script(session, f.read())


def get_service_sdk_data(session, service_id, binary_formatter=None):
    return database.get_sdk_service_data(
        session, service_id, binary_formatter=binary_formatter
    )
