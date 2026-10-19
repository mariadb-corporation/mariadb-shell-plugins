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

from rest_plugin.lib import core


def query_schemas(
    session,
    schema_id=None,
    service_id=None,
    schema_name=None,
    request_path=None,
    include_enable_state=None,
    auto_select_single=False,
):

    if not session:
        raise ValueError("The session is invalid.")

    if schema_id is None and auto_select_single:
        record = (
            core.select(
                table="rest_schema", cols=["count(*) as service_count", "min(id)"]
            )
            .exec(session)
            .first
        )

        if record["service_count"] == 1:
            schema_id = record["id"]

    if request_path and not request_path.startswith("/"):
        raise Exception("The request_path has to start with '/'.")

    # Build SQL based on which input has been provided

    sql = """
        SELECT sc.id, sc.name, sc.service_id, sc.request_path,
            sc.requires_auth, sc.enabled, sc.items_per_page, sc.comments, se.url_host_id,
            CONCAT(h.name, se.url_context_root) AS host_ctx,
            sc.options, sc.metadata, sc.schema_type, sc.internal
        FROM <metadata>.rest_schema sc
            LEFT OUTER JOIN <metadata>.service se
                ON se.id = sc.service_id
            LEFT JOIN <metadata>.url_host h
                ON se.url_host_id = h.id
        """
    params = []
    wheres = []
    if schema_id:
        wheres.append("sc.id = ?")
        params.append(schema_id)
    else:
        if service_id:
            wheres.append("sc.service_id = ?")
            params.append(service_id)
        if request_path:
            wheres.append("sc.request_path = ?")
            params.append(request_path)
        if schema_name:
            wheres.append("sc.name = ?")
            params.append(schema_name)
    if include_enable_state is not None:
        wheres.append("sc.enabled = ?")
        params.append(True if include_enable_state else False)

    sql += core._generate_where(wheres)
    sql += " ORDER BY sc.request_path"

    return core.MrsDbExec(sql, params).exec(session).items


def get_schemas(session, service_id: bytes = None, include_enable_state=None):
    """Returns all schemas for the given MRS service

    Args:
        session (object): The database session to use.
        service_id: The id of the service to list the schemas from
        include_enable_state (bool): Only include schemas with the given
            enabled state

    Returns:
        List of dicts representing the schemas
    """
    return query_schemas(
        session, service_id=service_id, include_enable_state=include_enable_state
    )


def get_schema(
    session,
    schema_id: bytes = None,
    service_id: bytes = None,
    schema_name=None,
    request_path=None,
    auto_select_single=False,
):
    """Gets a specific MRS schema

    Args:
        session (object): The database session to use.
        request_path (str): The request_path of the schema
        schema_name (str): The name of the schema
        schema_id: The id of the schema
        service_id: The id of the service

    Returns:
        The schema as dict or None on error in interactive mode
    """
    result = query_schemas(
        session,
        schema_id=schema_id,
        service_id=service_id,
        schema_name=schema_name,
        request_path=request_path,
        auto_select_single=False,
    )
    return result[0] if result else None

