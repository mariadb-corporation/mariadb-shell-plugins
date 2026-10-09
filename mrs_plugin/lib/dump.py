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

from mrs_plugin.lib import core
import os
import json
import time


def export_audit_log(
    file_path,
    audit_log_position_file=None,
    audit_log_position=None,
    starting_from_today=True,
    when_server_is_writeable=False,
    session=None,
):
    if file_path is None:
        raise ValueError("No file_path for the audit log file given.")
    file_path = os.path.expanduser(file_path)

    # Check if MRS is available
    sql = (
        "SELECT COUNT(*) AS mrs_available FROM information_schema.TABLES "
        + "WHERE table_schema = 'mysql_rest_service_metadata' and table_name='audit_log'"
    )
    row = core.MrsDbExec(sql).exec(session).first
    if row["mrs_available"] == 0:
        return

    if when_server_is_writeable:
        # Check if the server is in offline mode or super read only, if so, do not write the log
        sql = "SELECT @@global.offline_mode as offline_mode, @@global.super_read_only as super_read_only"
        row = core.MrsDbExec(sql).exec(session).first
        if row["offline_mode"] == 1 or row["super_read_only"] == 1:
            return

    if audit_log_position_file is None:
        audit_log_position_file = os.path.join(
            os.path.dirname(file_path), "mrs_audit_log_position.json"
        )

    # Read the audit_log_position from the audit_log_position_file if it has not been given explicitly
    # and the audit_log_position_file already exists
    if audit_log_position is None and os.path.isfile(audit_log_position_file):
        with open(audit_log_position_file, "r") as f:
            try:
                audit_log_position = json.loads(f.read()).get("position", None)
                if audit_log_position is None:
                    raise ValueError(f"Invalid audit log position in file {
                        audit_log_position}")
            except json.JSONDecodeError:
                raise ValueError(f"Invalid audit log position in file {
                    audit_log_position}")
    else:
        audit_log_position = 0

    # Write the audit log to the file
    sql = "SELECT *, @@server_uuid AS server_uuid FROM `mysql_rest_service_metadata`.`audit_log` WHERE `id` > ?"
    if starting_from_today:
        sql += " AND `changed_at` >= CURDATE()"
    sql += " ORDER BY `id`"
    rows = core.MrsDbExec(sql).exec(session, [audit_log_position]).items
    if len(rows) > 0:
        with open(file_path, "a") as f:
            for row in rows:
                schema_name = row.get("schema_name")
                if schema_name is None:
                    schema_name = "mysql_rest_service_metadata"
                f.write(
                    f'{row.get("changed_at")} {row.get("id")} {row.get("changed_by")} '
                    + f'{row.get("server_uuid")} '
                    + f'{schema_name}.{row.get("table_name")} {row.get("dml_type")} '
                    + json.dumps(row.get("old_row_data", {}))
                    + " "
                    + json.dumps(row.get("new_row_data", {}))
                    + "\n"
                )
        audit_log_position = rows[-1]["id"]

    # Write the new audit log position to the audit_log_position_path file
    if audit_log_position_file is not None and audit_log_position > 0:
        with open(audit_log_position_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "position": audit_log_position,
                        "updateTime": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    indent=4,
                )
            )
