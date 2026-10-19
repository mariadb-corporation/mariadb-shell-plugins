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

"""Sub-Module for managing MRS content sets"""

# cSpell:ignore mysqlsh, mrs

from mysqlsh.plugin_manager import plugin_function
import rest_plugin.lib as lib

import json


@plugin_function("rest.get.fileMrsScriptDefinitions", shell=True, cli=True, web=True)
def get_file_mrs_script_definitions(path, **kwargs):
    """Returns the MRS Scripts definitions for the given file

    Args:
        path (str): The path to check
        **kwargs: Additional options

    Keyword Args:
        language (str): The language the MRS Scripts are written in

    Returns:
        A Dict with the MRS Scripts definitions or None if the lastModification matches the one of the file
    """

    language = kwargs.get("language")
    if language is None:
        raise ValueError("No language given.")

    script_def = lib.content_sets.get_file_mrs_script_definitions(
        path=path, language=language
    )

    return script_def


@plugin_function("rest.get.folderMrsScriptLanguage", shell=True, cli=True, web=True)
def get_folder_mrs_scripts_language(path, **kwargs):
    """Checks if the given path contains MRS Scripts

    Args:
        path (str): The path to check
        **kwargs: Additional options

    Keyword Args:
        ignore_list (str): The list of file patterns to ignore, separated by comma

    Returns:
        "TypeScript" or None
    """

    ignore_list = kwargs.get("ignore_list", "*node_modules/*")

    mrs_scripts_language = lib.content_sets.get_folder_mrs_scripts_language(
        path, ignore_list
    )

    if lib.core.get_interactive_default():
        if mrs_scripts_language is not None:
            print(
                f"This folder contains MRS Scripts written in {mrs_scripts_language}."
            )
        else:
            print("This folder does not contain MRS Scripts.")
    else:
        return mrs_scripts_language


@plugin_function("rest.get.folderMrsScriptDefinitions", shell=True, cli=True, web=True)
def get_folder_mrs_script_definitions(path, **kwargs):
    """Returns the MRS Scripts definitions for the given folder

    Args:
        path (str): The path to check
        **kwargs: Additional options

    Keyword Args:
        ignore_list (str): The list of file patterns to ignore, separated by comma
        language (str): The language the MRS Scripts are written in
        send_gui_message (object): The function to send a message to he GUI.

    Returns:
        A Dict with the MRS Scripts definitions
    """

    ignore_list = kwargs.get("ignore_list", "*node_modules/*")
    language = kwargs.get(
        "language", lib.content_sets.get_folder_mrs_scripts_language(path, ignore_list)
    )
    send_gui_message = kwargs.get("send_gui_message")

    if language is None:
        raise ValueError("The given file path does not contain any MRS Scripts.")

    script_def = lib.content_sets.get_folder_mrs_script_definitions(
        path=path,
        ignore_list=ignore_list,
        language=language,
        send_gui_message=send_gui_message,
    )

    if lib.core.get_interactive_default():
        print(json.dumps(script_def, indent=4))
    else:
        return script_def


@plugin_function("rest.load.contentSet", shell=True, cli=True, web=True)
def load_content_set(directory=None, content_set_path=None, **kwargs):
    """Uploads the files of a directory to a new REST content set.

    Sends a CREATE REST CONTENT SET statement and one CREATE REST CONTENT
    FILE statement per file, and registers the MRS scripts of the files with
    ALTER REST CONTENT SET ... LOAD TYPESCRIPT SCRIPTS.

    With open_api_ui, the files are the REST service's OpenAPI UI instead,
    Swagger UI downloaded from github.com and patched to show the service's
    OpenAPI catalog and to sign in with the REST Daemon. No directory is
    given then.

    Args:
        directory (str): The directory holding the files. Not given with
            open_api_ui.
        content_set_path (str): The request path of the content set. With
            open_api_ui, /openApiUi when not given.
        **kwargs: Additional options

    Keyword Args:
        service_path (str): The request path of the REST service, by default
            the current one.
        ignore_list (str): The list of file patterns to ignore, separated by
            comma, matched against the path relative to the directory.
            Default "*node_modules/*, */.*". Not with open_api_ui.
        load_scripts (bool): Register the MRS scripts of the files. By default
            they are registered if the directory holds any. Not with
            open_api_ui.
        replace (bool): Replace the content set, if it already exists.
        open_api_ui (bool): Upload the REST service's OpenAPI UI instead of
            a directory's files. Default False.
        requires_auth (bool): Whether the content set and its files require
            authentication. By default they do, except the OpenAPI UI.
        session (object): The database session to use.
        send_gui_message (object): The function to send a message to the GUI.

    Returns:
        A dict with the request paths of the uploaded files and the result
        message
    """
    content_set_path = lib.content_sets.check_load_options(
        directory,
        content_set_path,
        kwargs.get("open_api_ui", False),
        kwargs.get("ignore_list"),
        kwargs.get("load_scripts"),
    )

    if kwargs.get("open_api_ui", False):
        # Before the transaction, which a slow github.com would otherwise
        # keep open: inside it, the files are read from where they are kept.
        lib.open_api_ui.download_assets(kwargs.get("send_gui_message"))

    with lib.core.MrsDbSession(
        exception_handler=lib.core.print_exception, **kwargs
    ) as session:
        with lib.core.MrsDbTransaction(session):
            result = lib.content_sets.load_content_set(
                session,
                directory,
                content_set_path,
                service_path=kwargs.get("service_path"),
                ignore_list=kwargs.get("ignore_list"),
                load_scripts=kwargs.get("load_scripts"),
                replace=kwargs.get("replace", False),
                send_gui_message=kwargs.get("send_gui_message"),
                open_api_ui=kwargs.get("open_api_ui", False),
                requires_auth=kwargs.get("requires_auth"),
            )

        if lib.core.get_interactive_default():
            print(
                f"{len(result['files'])} file(s) uploaded to the REST content set "
                f"{content_set_path}."
            )
            if result["message"]:
                print(result["message"])
        else:
            return result
