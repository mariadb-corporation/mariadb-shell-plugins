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
from mrs_plugin.lib import core


def get_content_files(
    session,
    content_set_id: bytes,
    include_enable_state: bool | None = False,
    include_file_content=False,
):
    """Returns all files for the given content set

    Args:
        content_set_id: The id of the content_set to list the items from
        include_enable_state (bool): Only include db_objects with the given
            enabled state
        session (object): The database session to use

    Returns:
        A list of dicts representing the files of the content set
    """
    if not content_set_id:
        raise ValueError("No content set specified.")

    sql = f"""
        SELECT f.id, f.content_set_id, f.request_path,
            f.requires_auth, f.enabled, f.size,
            cs.request_path AS content_set_request_path,
            CONCAT(h.name, se.url_context_root) AS host_ctx,
            f.options,
            al.changed_at{", f.content" if include_file_content else ""}
        FROM mysql_rest_service_metadata.content_file f
            LEFT OUTER JOIN mysql_rest_service_metadata.content_set cs
                ON cs.id = f.content_set_id
            LEFT OUTER JOIN mysql_rest_service_metadata.service se
                ON se.id = cs.service_id
            LEFT JOIN mysql_rest_service_metadata.url_host h
                ON se.url_host_id = h.id
            LEFT OUTER JOIN (
                SELECT new_row_id AS id, MAX(changed_at) as changed_at
                FROM mysql_rest_service_metadata.audit_log
                WHERE table_name = 'content_file'
                GROUP BY new_row_id) al
            ON al.id = f.id
        WHERE f.content_set_id = ? /*=3*/
        """

    if include_enable_state is not None:
        sql += "AND f.enabled = " f"{'TRUE' if include_enable_state else 'FALSE'} "

    return core.MrsDbExec(sql, [content_set_id]).exec(session).items
