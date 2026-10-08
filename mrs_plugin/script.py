# Copyright (c) 2023, 2025, Oracle and/or its affiliates.
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

from mysqlsh.plugin_manager import plugin_function
import mrs_plugin.lib as lib


@plugin_function("mrs.run.script", shell=True, cli=True, web=True)
def run_mrs_script(mrs_script=None, **kwargs):
    """Run the given MRS script

    Args:
        mrs_script (str): The script to execute
        **kwargs: Additional options

    Keyword Args:
        path (str): The path of the script to run
        current_service_id (str): The id of the current service
        current_service (str): The url_context_root of the current service
        current_service_host (str): The url_context_root of the current service URL
        current_schema_id (str): The id of the current schema
        current_schema (str): The full path of the current schema
        session (object): The database session to use.

    Returns:
        A list of results as defined in /gui/tools/src/protocol/results_mrs.txt
    """

    interactive = lib.core.get_interactive_default()

    try:
        results = lib.script.run_mrs_script(mrs_script=mrs_script, **kwargs)

        if interactive is True:
            for res in results:
                if res.get("type") == "success":
                    result = res.get("result")
                    if result and len(result) > 0:
                        print(lib.core.format_result(result))
                    if (
                        res.get("affectedItemsCount") is None
                        and res.get("message") is not None
                    ):
                        print(res.get("message"))
                    elif res.get("affectedItemsCount") is not None:
                        print(
                            f'{res.get("affectedItemsCount")} row{"s" if res.get("affectedItemsCount") > 1 else ""} affected.'
                        )
                else:
                    print(f'ERROR: {res.get("message")}')

            if len(results) == 0:
                print("No statements executed.")
        else:
            return results

    except Exception as e:
        if interactive is True:
            print(f"{e}")
            # For debugging, re-raise the exception
            raise
        else:
            raise


# The REST SQL statements (CREATE REST SERVICE, SHOW REST SERVICES, ...) are
# handled by the MRS module built into the MariaDB Shell, which registers the
# "MRS" SQL handler. This plugin no longer registers one, so both can be
# loaded together; the plugin only adds its functions to the built-in mrs
# object.
