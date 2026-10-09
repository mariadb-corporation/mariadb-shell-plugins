# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
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
import sys
import time
import re


@plugin_function("mrs.dump.auditLog", shell=True, cli=True, web=True)
def export_audit_log(file_path, **kwargs):
    """Exports the MRS audit log to a file

    Args:
        file_path (str): The file path to write the audit log to.
        **kwargs: Additional keyword arguments.

    Keyword Args:
        audit_log_position_file (str): The file containing the audit log position. If not provided, a
            mrs_audit_log_position.json file next to the file_path will be created.
        audit_log_position (int): The audit log position to export from. Defaults to 0.
        starting_from_today (bool): Whether to start exporting from today. Defaults to true.
        when_server_is_writeable (bool): Whether to only write out the log when the MySQL server is writeable. Defaults to false.
        session (object): The database session to use.

    Returns:
        None
    """
    session = kwargs.get("session", None)

    try:
        with lib.core.MrsDbSession(session=session) as session:
            lib.dump.export_audit_log(file_path=file_path, session=session, **kwargs)

        # Print successful check/dump to stdout
        print(
            time.strftime("%Y-%m-%d %H:%M:%S")
            + " All audit log events have been dumped.\n"
        )
    except Exception as e:
        # Print the error to stderr and re-raise it
        print(
            time.strftime("%Y-%m-%d %H:%M:%S")
            + " "
            + re.sub(r"/n", "\\n", str(e))
            + "\n",
            file=sys.stderr,
        )
        raise e
