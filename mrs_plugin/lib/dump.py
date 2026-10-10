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

# How far below the highest exported id an export looks again. Audit log ids
# follow the order the rows were inserted in, not the order their
# transactions committed: with concurrent writers, and much more so on a
# Galera cluster with several write nodes, a row with a lower id can become
# visible after a higher one was exported. Each export re-reads this many ids
# below its position and skips the ids it has already written, which the
# position file keeps.
AUDIT_LOG_ID_OVERLAP = 1000


def read_audit_log_position(audit_log_position_file):
    """Reads the position and the recently exported ids from the position file

    Returns:
        (position, exported_ids): the highest exported id (0 without a file)
        and the ids exported in the overlap window below it, or None when the
        file does not list them.
    """
    if not os.path.isfile(audit_log_position_file):
        return 0, []
    with open(audit_log_position_file, "r") as f:
        try:
            data = json.loads(f.read())
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict) or not isinstance(data.get("position"), int):
        raise ValueError(
            f"Invalid audit log position in file {audit_log_position_file}"
        )
    return data["position"], data.get("exportedIds")


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
        + "WHERE table_schema = ? and table_name='audit_log'"
    )
    metadata_schema = core.metadata_schema(session)
    row = core.MrsDbExec(sql, [metadata_schema]).exec(session).first
    if row["mrs_available"] == 0:
        return

    if when_server_is_writeable:
        # Check if the server is read only, if so, do not write the log
        sql = "SELECT @@global.read_only AS read_only"
        row = core.MrsDbExec(sql).exec(session).first
        if row["read_only"] == 1:
            return

    if audit_log_position_file is None:
        audit_log_position_file = os.path.join(
            os.path.dirname(file_path), "mrs_audit_log_position.json"
        )

    # Read the audit_log_position from the audit_log_position_file if it has
    # not been given explicitly. Without the list of the recently exported ids
    # (an explicit position, or a file without it), everything up to the
    # position counts as exported.
    if audit_log_position is None:
        audit_log_position, exported_ids = read_audit_log_position(
            audit_log_position_file
        )
    else:
        exported_ids = None
    window_start = max(0, audit_log_position - AUDIT_LOG_ID_OVERLAP)

    # Write the audit log to the file, re-reading the overlap window for rows
    # committed after higher ids were exported
    sql = "SELECT *, @@server_uid AS server_uid FROM <metadata>.`audit_log` WHERE `id` > ?"
    if starting_from_today:
        sql += " AND `changed_at` >= CURDATE()"
    sql += " ORDER BY `id`"
    read = core.MrsDbExec(sql).exec(session, [window_start]).items
    if exported_ids is None:
        rows = [row for row in read if row["id"] > audit_log_position]
        exported_ids = []
    else:
        already_exported = set(exported_ids)
        rows = [row for row in read if row["id"] not in already_exported]

    # After this export, every id read is exported; keep those in the overlap
    # window below the new position
    new_position = max([audit_log_position] + [row["id"] for row in rows])
    exported_ids = sorted(
        id
        for id in set(exported_ids) | {row["id"] for row in read}
        if id > new_position - AUDIT_LOG_ID_OVERLAP
    )

    if len(rows) > 0:
        with open(file_path, "a") as f:
            for row in rows:
                schema_name = row.get("schema_name")
                if schema_name is None:
                    schema_name = metadata_schema
                f.write(
                    f'{row.get("changed_at")} {row.get("id")} {row.get("changed_by")} '
                    + f'{row.get("server_uid")} '
                    + f'{schema_name}.{row.get("table_name")} {row.get("dml_type")} '
                    + json.dumps(row.get("old_row_data", {}))
                    + " "
                    + json.dumps(row.get("new_row_data", {}))
                    + "\n"
                )

    # Write the new audit log position to the audit_log_position_path file
    if audit_log_position_file is not None and new_position > 0:
        with open(audit_log_position_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "position": new_position,
                        "exportedIds": exported_ids,
                        "updateTime": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    indent=4,
                )
            )
