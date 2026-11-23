# Copyright (c) 2021, 2026, Oracle and/or its affiliates.
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

"""Sub-Module for managing MRS services"""

# cSpell:ignore mysqlsh, mrs

from mysqlsh.plugin_manager import plugin_function
import rest_plugin.lib as lib
from .interactive import resolve_service
from pathlib import Path
import os
import shutil
import json
import tomllib
import datetime
from typing import Literal


def file_name_using_language_convention(
    name, sdk_language: Literal["typescript", "python"] = "typescript"
):
    if sdk_language == "python":
        return lib.core.convert_to_snake_case(name)
    if sdk_language == "swift":
        return lib.core.convert_path_to_pascal_case(name)
    return name


# The header earlier versions wrote by default. A folder that has it stored
# gets none when it is made again: the SDK files carry no header unless one
# is given.
_FORMER_DEFAULT_HEADERS = frozenset(
    f"{comment} Copyright (c) 2023, 2026, Oracle and/or its affiliates."
    for comment in ("//", "#")
)


@plugin_function("rest.get.sdkBaseClasses", shell=True, cli=True, web=True)
def get_sdk_base_classes(**kwargs):
    """Returns the SDK base classes source for the given language

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        sdk_language (str): The SDK language to generate
        prepare_for_runtime (bool): Prepare code to be used in Monaco at runtime
        session (object): The database session to use.

    Returns:
        The SDK base classes source
    """
    # internally, we use the programming language name in lowercase
    sdk_language = kwargs.get("sdk_language", "TypeScript").lower()
    prepare_for_runtime = kwargs.get("prepare_for_runtime", False)

    return lib.sdk.get_base_classes(
        sdk_language=sdk_language, prepare_for_runtime=prepare_for_runtime
    )


@plugin_function("rest.get.sdkServiceClasses", shell=True, cli=True, web=True)
def get_sdk_service_classes(**kwargs):
    """Returns the SDK service classes source for the given language

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        service_id (str): The id of the service
        service_url (str): The url of the service
        sdk_language (str): The SDK language to generate
        prepare_for_runtime (bool): Prepare code to be used in Monaco at runtime
        session (object): The database session to use.

    Returns:
        The SDK base classes source
    """
    lib.core.convert_ids_to_uuid(["service_id"], kwargs)

    service_id = kwargs.get("service_id")
    # internally, we use the programming language name in lowercase
    sdk_language = kwargs.get("sdk_language", "TypeScript").lower()
    prepare_for_runtime = kwargs.get("prepare_for_runtime", False)
    service_url = kwargs.get("service_url")

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        service = resolve_service(
            session=session,
            service_query=service_id,
            required=False,
            auto_select_single=True,
        )

        return lib.sdk.generate_service_sdk(
            service=service,
            sdk_language=sdk_language,
            session=session,
            prepare_for_runtime=prepare_for_runtime,
            service_url=service_url,
        )


# The keys of mrs.config.json that tell where an SDK came from: a service on a
# server (serviceId, dbConnectionUri) or one a schema project defines
# (msmProject, written by msm.dumpRestSdk). A dump writes those of its own
# source and drops the others, so a client rebuilding the SDK goes back to
# where it came from last.
SDK_SOURCE_KEYS = ("serviceId", "dbConnectionUri", "msmProject")


def sdk_files_config(directory, options):
    """The SDK options a dump writes into a directory: those stored there
    (mrs.config.json), updated with the options given, the language checked.

    Shared with msm.dumpRestSdk, which generates the SDK of a REST service
    an MSM project defines, without a server.

    Returns:
        The options to write; sdkLanguage is the language's own spelling.
    """
    mrs_config = get_stored_sdk_options(directory=directory)
    options = options or {}

    mrs_config["serviceUrl"] = options.get("service_url", mrs_config.get("serviceUrl"))
    mrs_config["addAppBaseClass"] = options.get(
        "add_app_base_class", mrs_config.get("addAppBaseClass")
    )
    mrs_config["generationDate"] = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    if mrs_config.get("serviceUrl") is None:
        raise Exception("The service URL is required.")

    source_sdk_language = options.get(
        "sdk_language", mrs_config.get("sdkLanguage", "TypeScript")
    )
    # internally, we use the programming language name in lowercase
    sdk_language = (source_sdk_language or "").lower()
    if sdk_language not in lib.sdk.SUPPORTED_LANGUAGES:
        raise lib.sdk.LanguageNotSupportedError(source_sdk_language)
    stored_sdk_language = mrs_config.get("sdkLanguage")
    mrs_config["sdkLanguage"] = lib.sdk.SUPPORTED_LANGUAGES[sdk_language]
    # The stored header is kept, unless it was written for another language's
    # comment syntax
    stored_header = mrs_config.get("header")
    if (
        stored_header is None
        or stored_header in _FORMER_DEFAULT_HEADERS
        or stored_sdk_language != mrs_config["sdkLanguage"]
    ):
        stored_header = ""
    mrs_config["header"] = options.get("header", stored_header)

    return mrs_config


def write_sdk_files(directory, mrs_config, url_context_root, service_classes, source):
    """Writes the SDK files of a service into a directory.

    Args:
        directory (str): The directory, made where it is missing
        mrs_config (dict): The SDK options from sdk_files_config(); written
            as mrs.config.json
        url_context_root (str): The service's request path, which names the
            service's file
        service_classes: A function returning the source of the service's
            classes
        source (dict): Where the SDK comes from, of SDK_SOURCE_KEYS; the
            others are dropped

    Returns:
        The paths of the files written
    """
    for key in SDK_SOURCE_KEYS:
        mrs_config.pop(key, None)
    mrs_config.update(source)

    sdk_language = mrs_config["sdkLanguage"].lower()
    service_name = lib.core.convert_path_to_camel_case(url_context_root)
    sdk_folder = Path(os.path.dirname(os.path.abspath(__file__)), "sdk", sdk_language)

    if sdk_language == "typescript":
        file_type = "ts"
        base_classes_file = os.path.join(directory, "MrsBaseClasses.ts")
        version = json.loads((sdk_folder / "package.json").read_text()).get("version")
    elif sdk_language == "python":
        file_type = "py"
        base_classes_file = os.path.join(directory, "mrs_base_classes.py")
        version = (
            tomllib.loads((sdk_folder / "pyproject.toml").read_text())
            .get("project")
            .get("version")
        )
    else:
        file_type = "swift"
        base_classes_file = os.path.join(directory, "MrsBaseClasses.swift")
        # index 0 is the copyright notice
        version = (sdk_folder / "VERSION").read_text().split("\n")[1]
    mrs_config["version"] = version

    # Generated before anything is written, so a failure leaves the folder as it was
    code = service_classes()

    # Ensure the directory path exists
    Path(directory).mkdir(parents=True, exist_ok=True)
    written = []

    base_classes = get_sdk_base_classes(sdk_language=sdk_language)
    with open(base_classes_file, "w") as f:
        f.write(base_classes)
    written.append(base_classes_file)

    file_name = file_name_using_language_convention(service_name, sdk_language)
    service_file = os.path.join(directory, f"{file_name}.{file_type}")
    with open(service_file, "w") as f:
        f.write(code)
    written.append(service_file)

    add_app_base_class = mrs_config.get("addAppBaseClass")
    if isinstance(add_app_base_class, str) and add_app_base_class != "":
        target = os.path.join(directory, add_app_base_class)
        shutil.copy(sdk_folder / add_app_base_class, target)
        written.append(target)

    # cspell:ignore timespec
    conf_file = Path(directory, "mrs.config.json")
    with open(conf_file, "w") as f:
        f.write(json.dumps(mrs_config, indent=4))
    written.append(str(conf_file))

    # TODO: this should be in a separate function (maybe context-aware for each language)
    if sdk_language == "python":
        # In Python, we should create a "__init__.py" file to be able to import the directory as a regular package
        package_file = Path(directory, "__init__.py")
        with open(package_file, "w") as f:
            f.write(mrs_config["header"])
        written.append(str(package_file))

    return written


@plugin_function("rest.dump.sdkServiceFiles", shell=True, cli=True, web=True)
def dump_sdk_service_files(**kwargs):
    """Dumps the SDK service files for a REST Service

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        directory (str): The directory to store the .mrs.sdk folder with the files
        options (dict): Several options how the SDK should be created
        session (object): The database session to use.

    Allowed options for options:
        service_id (str): The ID of the service the SDK should be generated for. If not specified, the service
            identified by the url_context_root parameter is used.
        url_context_root (str): The request path of the service the SDK should be generated for. If not specified, the default service
            is used.
        db_connection_uri (str): The dbConnectionUri that was used to export the SDK files
        sdk_language (str): The SDK language to generate
        add_app_base_class (str): The additional AppBaseClass file name
        service_url (str): The url of the service
        version (integer): The version of the generated files
        generationDate (str): The generation date of the SDK files
        header (str): The header to use for the SDK files

    Returns:
        True on success
    """
    directory = kwargs.get("directory")
    options = kwargs.get("options", {})

    lib.core.convert_ids_to_uuid(["service_id"], options)

    if not directory:
        if lib.core.get_interactive_default():
            directory = lib.core.prompt(
                "Please enter the directory the folder with the SDK files should be placed:"
            )
            if not directory:
                print("Cancelled.")
                return False
        else:
            raise Exception("No directory given.")

    mrs_config = sdk_files_config(directory, options)

    config_service_id = mrs_config.get("serviceId")
    service_id = options.get(
        "service_id",
        (
            None
            if config_service_id is None
            else lib.core.id_to_uuid(config_service_id, "mrs.config.json")
        ),
    )
    db_connection_uri = options.get("db_connection_uri", mrs_config.get("dbConnectionUri"))

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        url_context_root = options.get("url_context_root")
        # A folder msm.dumpRestSdk made from a schema project names the
        # project's service; without a service given, that one is taken
        # where the server has it, else the server's current or only one
        project_service = (mrs_config.get("msmProject") or {}).get("service")

        if not url_context_root and service_id is None and project_service:
            service = resolve_service(session, project_service, False, True)
            if not service:
                raise Exception("Operation cancelled. Unable to identify target service.")
        else:
            service = resolve_service(session, url_context_root or service_id, True, True)

        if service.get("enabled") == 0:
            raise Exception("Generating the MRS SDK requires a service to be enabled.")

        write_sdk_files(
            directory,
            mrs_config,
            service.get("url_context_root"),
            lambda: get_sdk_service_classes(
                service_id=service.get("id"),
                service_url=mrs_config["serviceUrl"],
                sdk_language=mrs_config["sdkLanguage"],
                session=session,
            ),
            {
                "serviceId": lib.core.convert_id_to_base64_string(service.get("id")),
                "dbConnectionUri": db_connection_uri,
            },
        )

    return True


@plugin_function("rest.get.sdkOptions", shell=True, cli=True, web=True)
def get_stored_sdk_options(directory):
    """Reads the SDK service option file located in a given directory

    Args:
        directory (str): The directory where the mrs.config.json file is stored

    Returns:
        The SDK options stored in that directory otherwise None
    """

    # Try to read the mrs_config from the directory
    mrs_config = {}
    conf_file = Path(directory, "mrs.config.json")
    if conf_file.is_file():
        try:
            with open(conf_file) as f:
                mrs_config = json.load(f)
        except:
            pass

    if "addAppBaseClass" in mrs_config:
        if isinstance(mrs_config["addAppBaseClass"], int):
            del mrs_config["addAppBaseClass"]

    return mrs_config


@plugin_function("rest.get.runtimeManagementCode", shell=True, cli=True, web=True)
def get_runtime_management_code(**kwargs):
    """Returns the SDK service classes source for the given language

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        session (object): The database session to use.

    Returns:
        The SDK base classes source
    """

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        return lib.sdk.get_mrs_runtime_management_code(session)


@plugin_function("rest.dump.serviceProject", shell=True, cli=True, web=True)
def dump_service_as_project(**kwargs):
    """Dump a REST Service as a project. In this project, you can add the necessary
    services and schemas.

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        services (list): The list of services to include in the project.
        schemas (list): The list of schemas to include in the project.
        settings (dict): The details for the project.
        destination (str): The destination where the project should be created.
        overwrite (bool): Overwrite the file, if already exists.
        zip (bool): The final directory is to be zipped.
        session (object): The database session to use.


    Allowed options for services:
        name (str): The name of the service.
        include_database_endpoints (str): If the database endpoints are to be included.
        include_static_endpoints (str): If the static endpoints are to be included.
        include_dynamic_endpoints (str): If the dynamic endpoints are to be included.

    Allowed options for schemas:
        name (str): The name of the schema.
        file_path (str): The schema file path.

    Allowed options for settings:
        name (str): The name of the project.
        icon_path (str): The path for the project icon.
        description (str): A project description.
        publisher (str): The publisher name.
        version (str): The project version.

    Returns:
        True if the file was saved.
    """

    def checked_path(path):
        lib.core.validate_path_for_filesystem(path)
        return os.path.expanduser(path) if path else path

    destination = checked_path(kwargs.get("destination"))
    services = kwargs.get("services")
    schemas = [
        {**schema, "file_path": checked_path(schema.get("file_path"))}
        for schema in kwargs.get("schemas") or []
    ]
    project_settings = dict(kwargs.get("settings") or {})
    project_settings["icon_path"] = checked_path(project_settings.get("icon_path"))
    create_zip = kwargs.get("zip")

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        lib.services.store_project_validations(
            session,
            destination=destination,
            services=services,
            schemas=schemas,
            project_settings=project_settings,
            create_zip=create_zip,
        )

        lib.services.store_project(
            session,
            destination=destination,
            services=services,
            schemas=schemas,
            project_settings=project_settings,
            create_zip=create_zip,
        )


@plugin_function("rest.load.serviceProject", shell=True, cli=True, web=True)
def load_service_project(**kwargs):
    """Loads a previously dumped REST service project.

    Loads the database schemas of the project, then creates its REST
    services. The database schemas may exist already (a dump is loaded with
    ignoreExistingObjects); a REST service of the project must not exist
    yet. When creating a REST service fails, the services this load created
    are dropped again; the database schemas stay as far as they were loaded.

    Args:
        **kwargs: Options to determine what should be generated.

    Keyword Args:
        source (str): The path where to store the file.
        session (object): The database session to use.
    """
    source = kwargs.get("source")

    if (
        not lib.services.is_zipfile(source)
        and not os.path.isdir(source)
        and not lib.services.is_github_shortcut(source)
        and not lib.services.is_url(source)
    ):
        raise ValueError(
            "The source must be a ZIP file, a directory, a URL or a Github shortcut."
        )

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        lib.services.load_project(session, source)


@plugin_function("rest.dump.service", shell=True, cli=True, web=True)
def dump_service(service_path, file_path, **kwargs):
    """Writes the REST SQL script that recreates a REST service to a file.

    The script is the result of SHOW CREATE REST SERVICE ... INCLUDING
    <endpoints> ENDPOINTS. It does not include the database schemas the
    service is based on.

    Args:
        service_path (str): The request path of the REST service.
        file_path (str): The path of the file to write.
        **kwargs: Additional options

    Keyword Args:
        endpoints (str): The endpoints to include: DATABASE, DATABASE AND
            STATIC, DATABASE AND STATIC AND DYNAMIC or ALL. Default DATABASE.
            An empty string includes only the CREATE REST SERVICE statement.
        overwrite (bool): Overwrite the file, if it already exists.
        session (object): The database session to use.

    Returns:
        None
    """
    lib.core.validate_path_for_filesystem(file_path)
    file_path = os.path.expanduser(file_path)
    if os.path.exists(file_path) and not kwargs.get("overwrite"):
        raise ValueError(f"The file '{file_path}' already exists.")

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        lib.services.dump_service_script(
            session, service_path, file_path, kwargs.get("endpoints", "DATABASE")
        )

        if lib.core.get_interactive_default():
            print(f"REST service {service_path} dumped to {file_path}.")


@plugin_function("rest.load.service", shell=True, cli=True, web=True)
def load_service(file_path, **kwargs):
    """Runs the REST SQL script of a REST service written by rest.dump.service.

    The script starts with CREATE OR REPLACE REST SERVICE, so it replaces a
    REST service of the same path. Its statements commit one by one: when a
    later one fails, the service stays as far as the script got, and a
    service it replaced is gone.

    Args:
        file_path (str): The path of the REST SQL script.
        **kwargs: Additional options

    Keyword Args:
        as_path (str): Create the REST service under this request path
            instead of the one in the script.
        session (object): The database session to use.

    Returns:
        None
    """
    file_path = os.path.expanduser(file_path)
    if not os.path.isfile(file_path):
        raise ValueError(f"The file '{file_path}' does not exist.")

    with open(file_path) as f:
        script = f.read()

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        lib.services.load_service_script(session, script, kwargs.get("as_path"))

        if lib.core.get_interactive_default():
            print(f"REST service loaded from {file_path}.")
