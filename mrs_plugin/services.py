# Copyright (c) 2021, 2026, Oracle and/or its affiliates.
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
import mrs_plugin.lib as lib
from .interactive import (
    resolve_service,
    service_query_selection,
)
from pathlib import Path
import os
import shutil
import json
import tomllib
import datetime
from typing import Literal


def verify_value_keys(**kwargs):
    for key in kwargs["value"].keys():
        if (
            key
            not in [
                "url_host_id",
                "url_context_root",
                "url_protocol",
                "url_host_name",
                "enabled",
                "comments",
                "options",
                "auth_path",
                "auth_completed_url",
                "auth_completed_url_validation",
                "auth_completed_page_content",
                "auth_apps",
                "metadata",
                "in_development",
                "published",
                "name",
            ]
            and key != "delete"
        ):
            raise Exception(f"Attempting to change an invalid service value.")


def resolve_service_ids(**kwargs):
    value = kwargs.get("value")
    session = kwargs.get("session")

    service_id = kwargs.pop("service_id", None)
    url_context_root = kwargs.pop("url_context_root", None)
    url_host_name = kwargs.pop("url_host_name", None)
    interactive = lib.core.get_interactive_default()
    allow_multi_select = kwargs.pop("allow_multi_select", False)
    kwargs.pop("url_protocol", None)

    kwargs["service_ids"] = []

    if service_id is not None:
        kwargs["service_ids"] = [service_id]
    else:
        # Get the right service_id(s) if service_id is not given
        if not url_context_root:
            # Check if there already is at least one service
            rows = (
                lib.core.select(
                    table="service", cols=["COUNT(*) AS service_count", "MAX(id) AS id"]
                )
                .exec(session)
                .items
            )
            if len(rows) == 0 or rows[0]["service_count"] == 0:
                Exception("No service available.")

            # If there are more services, let the user select one or all
            if interactive:
                if allow_multi_select:
                    caption = (
                        "Please select a service index, type "
                        "'hostname/root_context' or type '*' "
                        "to select all: "
                    )
                else:
                    caption = (
                        "Please select a service index or type "
                        "'hostname/root_context'"
                    )

                services = lib.services.get_services(session=session)
                selection = lib.core.prompt_for_list_item(
                    item_list=services,
                    prompt_caption=caption,
                    item_name_property="host_ctx",
                    given_value=None,
                    print_list=True,
                    allow_multi_select=allow_multi_select,
                )
                if not selection or selection == "":
                    raise ValueError("Operation cancelled.")

                if allow_multi_select:
                    kwargs["service_ids"] = [item["id"] for item in selection]
                else:
                    kwargs["service_ids"].append(selection["id"])
        else:
            # Lookup the service id
            res = session.run_sql(
                """
                SELECT se.id FROM `mysql_rest_service_metadata`.`service` se
                    LEFT JOIN `mysql_rest_service_metadata`.url_host h
                        ON se.url_host_id = h.id
                WHERE h.name = ? AND se.url_context_root = ?
                """,
                [url_host_name if url_host_name else "", url_context_root],
            )
            row = res.fetch_one()
            if row:
                kwargs["service_ids"].append(row.get_field("id"))

    if len(kwargs["service_ids"]) == 0:
        raise ValueError("The specified service was not found.")

    for service_id in kwargs["service_ids"]:
        service = lib.services.get_service(service_id=service_id, session=session)

        # Determine changes in the url_context_root for this service
        if value is not None and "url_context_root" in value:
            url_ctx_root = value["url_context_root"]

            if interactive and not url_ctx_root:
                url_ctx_root = lib.services.prompt_for_url_context_root(
                    default=service.get("url_context_root")
                )

            # If the context root has changed, check if the new one is valid
            if service["url_context_root"] != url_ctx_root:
                if not url_ctx_root or not url_ctx_root.startswith("/"):
                    raise ValueError("The url_context_root has to start with '/'.")

    return kwargs


def resolve_url_context_root(required=False, **kwargs):
    url_context_root = kwargs.get("url_context_root")
    if url_context_root is None and lib.core.get_interactive_default():
        url_context_root = kwargs["url_context_root"] = (
            lib.services.prompt_for_url_context_root()
        )

    if required and url_context_root is None:
        raise Exception("No context path given. Operation cancelled.")
    if url_context_root is not None and not url_context_root.startswith("/"):
        raise Exception(
            f"The url_context_root [{url_context_root}] has to start with '/'."
        )

    return kwargs


def resolve_url_host_name(required=False, **kwargs):
    url_host_name = kwargs.get("url_host_name")

    if lib.core.get_interactive_default():
        if url_host_name is None:
            url_host_name = lib.core.prompt(
                "Please enter the host name for this service (e.g. "
                "None or localhost) [None]: ",
                {"defaultValue": "None"},
            ).strip()

    if url_host_name and url_host_name.lower() == "none":
        url_host_name = None

    kwargs["url_host_name"] = url_host_name

    return kwargs


def resolve_url_protocol(**kwargs):
    if kwargs.get("url_protocol") is None:
        if lib.core.get_interactive_default():
            kwargs["url_protocol"] = lib.services.prompt_for_service_protocol()
        else:
            kwargs["url_protocol"] = ["HTTP", "HTTPS"]

    return kwargs


def resolve_comments(**kwargs):
    if lib.core.get_interactive_default():
        if kwargs.get("comments") is None:
            kwargs["comments"] = lib.core.prompt_for_comments()

    return kwargs


def call_update_service(op_text, **kwargs):

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        kwargs["session"] = session
        kwargs = resolve_service_ids(**kwargs)

        with lib.core.MrsDbTransaction(session):
            lib.services.update_services(**kwargs)

            if lib.core.get_interactive_result():
                if len(kwargs["service_ids"]) == 1:
                    return f"The service has been {op_text}."
                return f"The services have been {op_text}."
            return True
    return False


def file_name_using_language_convention(
    name, sdk_language: Literal["typescript", "python"] = "typescript"
):
    if sdk_language == "python":
        return lib.core.convert_to_snake_case(name)
    if sdk_language == "swift":
        return lib.core.convert_path_to_pascal_case(name)
    return name


def default_copyright_header(
    sdk_language: Literal["typescript", "python"] = "typescript",
):
    header = "Copyright (c) 2023, 2026, Oracle and/or its affiliates."
    if sdk_language == "typescript":
        return f"// {header}"
    return f"# {header}"


def generate_create_statement(**kwargs):
    lib.core.convert_ids_to_uuid(["service_id"], kwargs)
    lib.core.try_convert_ids_to_uuid(["service"], kwargs)

    include_database_endpoints = kwargs.get("include_database_endpoints", False)
    include_static_endpoints = kwargs.get("include_static_endpoints", False)
    include_dynamic_endpoints = kwargs.get("include_dynamic_endpoints", False)
    service_query = service_query_selection(**kwargs)

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        service = resolve_service(session, service_query=service_query)

        if service is None:
            raise ValueError("The specified service was not found.")

        return lib.services.get_service_create_statement(
            session,
            service,
            include_database_endpoints,
            include_static_endpoints,
            include_dynamic_endpoints,
        )


def store_create_statement(**kwargs):
    lib.core.convert_ids_to_uuid(["service_id"], kwargs)
    lib.core.try_convert_ids_to_uuid(["service"], kwargs)

    include_database_endpoints = kwargs.get("include_database_endpoints", False)
    include_static_endpoints = kwargs.get("include_static_endpoints", False)
    include_dynamic_endpoints = kwargs.get("include_dynamic_endpoints", False)
    service_query = service_query_selection(**kwargs)
    file_path = kwargs.get("file_path")
    zip = kwargs.get("zip", False)

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        service = resolve_service(session, service_query=service_query)

        if service is None:
            raise ValueError("The specified service was not found.")

        lib.services.store_service_create_statement(
            session,
            service,
            file_path,
            zip,
            include_database_endpoints,
            include_static_endpoints,
            include_dynamic_endpoints,
        )


@plugin_function("mrs.get.sdkBaseClasses", shell=True, cli=True, web=True)
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


@plugin_function("mrs.get.sdkServiceClasses", shell=True, cli=True, web=True)
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


@plugin_function("mrs.dump.sdkServiceFiles", shell=True, cli=True, web=True)
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

    # Try to read the mrs_config from the directory
    mrs_config = get_stored_sdk_options(directory=directory)
    if mrs_config is None and options is None:
        raise Exception(
            f"No SDK options given and no existing SDK config found in the directory {directory}"
        )

    if mrs_config is None:
        mrs_config = {}

    config_service_id = mrs_config.get("serviceId")
    service_id = options.get(
        "service_id",
        (
            None
            if config_service_id is None
            else lib.core.id_to_uuid(config_service_id, "mrs.config.json")
        ),
    )
    mrs_config["serviceUrl"] = options.get("service_url", mrs_config.get("serviceUrl"))
    mrs_config["addAppBaseClass"] = options.get(
        "add_app_base_class", mrs_config.get("addAppBaseClass")
    )
    mrs_config["dbConnectionUri"] = options.get(
        "db_connection_uri", mrs_config.get("dbConnectionUri")
    )
    mrs_config["version"] = options.get("version", mrs_config.get("version"))

    # internally, we use the programming language name in lowercase
    source_sdk_language = options.get(
        "sdk_language", mrs_config.get("sdkLanguage", "TypeScript")
    )
    sdk_language = None if source_sdk_language is None else source_sdk_language.lower()

    mrs_config["generationDate"] = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    if mrs_config.get("serviceUrl") is None:
        raise Exception("The service URL is required.")

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        url_context_root = options.get("url_context_root")

        service = resolve_service(session, url_context_root or service_id, True, True)

        if service.get("enabled") == 0:
            raise Exception("Generating the MRS SDK requires a service to be enabled.")

        mrs_config["serviceId"] = lib.core.convert_id_to_base64_string(
            service.get("id")
        )

        service_name = lib.core.convert_path_to_camel_case(
            service.get("url_context_root")
        )

        if sdk_language == "typescript":
            file_type = "ts"
            base_classes_file = os.path.join(directory, "MrsBaseClasses.ts")
            metadata = Path(
                os.path.dirname(os.path.abspath(__file__)),
                "sdk",
                sdk_language,
                "package.json",
            ).read_text()
            version = json.loads(metadata).get("version")

        elif sdk_language == "python":
            file_type = "py"
            base_classes_file = os.path.join(directory, "mrs_base_classes.py")
            metadata = Path(
                os.path.dirname(os.path.abspath(__file__)),
                "sdk",
                sdk_language,
                "pyproject.toml",
            ).read_text()
            version = tomllib.loads(metadata).get("project").get("version")

        elif sdk_language == "swift":
            file_type = "swift"
            base_classes_file = os.path.join(directory, "MrsBaseClasses.swift")
            version = (
                Path(
                    os.path.dirname(os.path.abspath(__file__)),
                    "sdk",
                    sdk_language.lower(),
                    "VERSION",
                )
                .read_text()
                .split("\n")[1]
            )  # index 0 is the copyright notice

        else:
            raise lib.sdk.LanguageNotSupportedError(source_sdk_language)

        # by now, we are sure the SDK language is, in fact, supported
        mrs_config["sdkLanguage"] = lib.sdk.SUPPORTED_LANGUAGES.get(sdk_language)
        mrs_config["header"] = options.get(
            "header", default_copyright_header(sdk_language)
        )
        mrs_config["version"] = version

        # Ensure the directory path exists
        Path(directory).mkdir(parents=True, exist_ok=True)

        base_classes = get_sdk_base_classes(sdk_language=sdk_language, session=session)
        with open(base_classes_file, "w") as f:
            f.write(base_classes)

        file_name = file_name_using_language_convention(service_name, sdk_language)

        service_classes = get_sdk_service_classes(
            service_id=service.get("id"),
            service_url=mrs_config["serviceUrl"],
            sdk_language=sdk_language,
            session=session,
        )
        with open(os.path.join(directory, f"{file_name}.{file_type}"), "w") as f:
            f.write(service_classes)

        add_app_base_class = mrs_config.get("addAppBaseClass")

        if (
            add_app_base_class is not None
            and isinstance(add_app_base_class, str)
            and add_app_base_class != ""
        ):
            path = os.path.abspath(__file__)
            file_path = Path(
                os.path.dirname(path), "sdk", sdk_language, add_app_base_class
            )
            shutil.copy(file_path, os.path.join(directory, add_app_base_class))

        # cspell:ignore timespec
        conf_file = Path(directory, "mrs.config.json")
        with open(conf_file, "w") as f:
            f.write(json.dumps(mrs_config, indent=4))

        # TODO: this should be in a separate function (maybe context-aware for each language)
        if sdk_language == "python":
            # In Python, we should create a "__init__.py" file to be able to import the directory as a regular package
            package_file = Path(directory, "__init__.py")
            with open(package_file, "w") as f:
                copyright_header = mrs_config["header"]
                f.write(copyright_header)

    return True


@plugin_function("mrs.get.sdkOptions", shell=True, cli=True, web=True)
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


@plugin_function("mrs.get.runtimeManagementCode", shell=True, cli=True, web=True)
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


@plugin_function("mrs.dump.serviceProject", shell=True, cli=True, web=True)
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


@plugin_function("mrs.load.serviceProject", shell=True, cli=True, web=True)
def load_service_project(**kwargs):
    """Loads a previously dumped REST service project.

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
