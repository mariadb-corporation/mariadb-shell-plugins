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

from mrs_plugin.lib import core, database


def query_rest_objects(
    session,
    rest_object_id=None,
    schema_id=None,
    request_path=None,
    rest_object_name=None,
    include_enable_state=None,
    object_types=None,
):

    # Build SQL based on which input has been provided

    sql = """
        SELECT o.id, o.rest_schema_id, o.name, o.request_path,
            o.requires_auth, o.enabled, o.object_type,
            o.items_per_page, o.comments,
            sc.request_path AS schema_request_path,
            CONCAT(h.name, se.url_context_root) AS host_ctx,
            o.crud_operations, o.format as crud_operation_format,
            o.media_type, o.auto_detect_media_type,
            o.auth_stored_procedure, o.options,
            o.metadata, o.internal,
            al.changed_at,
            CONCAT(sc.name, '.', o.name) AS qualified_name,
            se.id AS service_id, sc.name AS schema_name
        FROM <metadata>.rest_object o
            LEFT OUTER JOIN <metadata>.rest_schema sc
                ON sc.id = o.rest_schema_id
            LEFT OUTER JOIN <metadata>.service se
                ON se.id = sc.service_id
            LEFT JOIN <metadata>.url_host h
                ON se.url_host_id = h.id
            LEFT OUTER JOIN (
                SELECT new_row_id AS id, MAX(changed_at) as changed_at
                FROM <metadata>.audit_log
                WHERE table_name = 'rest_object'
                GROUP BY new_row_id) al
            ON al.id = o.id
        """

    params = []
    wheres = []
    if rest_object_id is not None:
        wheres.append("o.id = ?")
        params.append(rest_object_id)
    else:
        if schema_id is not None:
            wheres.append("o.rest_schema_id = ?")
            params.append(schema_id)
        if request_path is not None:
            wheres.append("o.request_path = ?")
            params.append(request_path)
        if rest_object_name is not None:
            wheres.append("o.name = ?")
            params.append(rest_object_name)
        if object_types is not None:
            if len(object_types) > 1:
                s = "(" + ("o.object_type = ? OR " * len(object_types))
                wheres.append(s[0:-4] + ")")
                for t in object_types:
                    params.append(t)
            elif len(object_types) == 1:
                wheres.append("o.object_type = ?")
                params.append(object_types[0])

    if include_enable_state is not None:
        wheres.append("o.enabled = ?")
        params.append("1" if include_enable_state else "0")

    sql += core._generate_where(wheres)
    sql += " ORDER BY o.request_path"

    return core.MrsDbExec(sql, params).exec(session).items


def get_data_mappings(session, rest_object_id):
    return database.get_data_mappings(session, rest_object_id)


def get_data_mapping_fields_with_references(session, data_mapping_id, binary_formatter=None):
    return database.get_data_mapping_fields_with_references(
        session, data_mapping_id, binary_formatter=binary_formatter
    )

