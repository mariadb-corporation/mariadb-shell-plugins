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

"""
Module that deals with the "real" database schema instead of the MRS objects
"""

import json

from mrs_plugin.lib import core


def quote_identifier(identifier):
    return f"`{identifier.replace('`', '``')}`"


def get_schema(session, schema_name):
    return (
        core.select(
            table="INFORMATION_SCHEMA.SCHEMATA",
            cols="SCHEMA_NAME",
            where=["SCHEMA_NAME = ?"],
        )
        .exec(session, params=[schema_name])
        .first
    )


def stored_object_executes_as_invoker(session, schema_name, rest_object_name):
    sql = """
        SELECT SECURITY_TYPE FROM INFORMATION_SCHEMA.VIEWS
        WHERE TABLE_SCHEMA = ? and TABLE_NAME = ?
        UNION
        SELECT SECURITY_TYPE FROM INFORMATION_SCHEMA.ROUTINES
        WHERE LOWER(ROUTINE_SCHEMA) = LOWER(?) and ROUTINE_NAME = ?
    """

    row = (
        core.MrsDbExec(sql)
        .exec(session, [schema_name, rest_object_name, schema_name, rest_object_name])
        .first
    )

    return row and "INVOKER" in row["SECURITY_TYPE"]


def get_tables_used_in_view_including_required_grants(
    session, schema_name, rest_object_name
):
    if session.server_vendor == "MariaDB":
        return []

    sql = """
        SELECT TABLE_NAME AS OBJ_NAME FROM INFORMATION_SCHEMA.VIEW_TABLE_USAGE
        WHERE VIEW_SCHEMA = ? AND VIEW_NAME = ?
    """

    rows = core.MrsDbExec(sql).exec(session, [schema_name, rest_object_name]).items

    return [
        (row["OBJ_NAME"], "TABLE", ["SELECT", "INSERT", "UPDATE", "DELETE"])
        for row in rows
    ]


def get_routines_used_in_view_including_required_grants(
    session, schema_name, rest_object_name
):
    if session.server_vendor == "MariaDB":
        return []

    sql = """
        SELECT DISTINCT t1.SPECIFIC_NAME AS OBJ_NAME, t2.ROUTINE_TYPE OBJ_TYPE
        FROM INFORMATION_SCHEMA.VIEW_ROUTINE_USAGE t1
        JOIN INFORMATION_SCHEMA.ROUTINES t2
        ON t1.SPECIFIC_NAME = t2.ROUTINE_NAME
        WHERE t1.TABLE_SCHEMA = ? AND t1.TABLE_NAME = ?;
    """

    rows = core.MrsDbExec(sql).exec(session, [schema_name, rest_object_name]).items

    return [(row["OBJ_NAME"], row["OBJ_TYPE"], ["EXECUTE"]) for row in rows]


def get_objects_used_in_view_including_required_grants(
    session, schema_name, rest_object_name
):
    objects_with_grants = get_tables_used_in_view_including_required_grants(
        session, schema_name, rest_object_name
    )
    objects_with_grants.extend(
        get_routines_used_in_view_including_required_grants(
            session, schema_name, rest_object_name
        )
    )

    return objects_with_grants


def verify_privilege(privilege):
    valid_privileges = [
        "ALTER",
        "ALTER ROUTINE",
        "CREATE",
        "CREATE ROUTINE",
        "CREATE TEMPORARY TABLES",
        "CREATE VIEW",
        "DELETE",
        "DROP",
        "EVENT",
        "EXECUTE",
        "INDEX",
        "INSERT",
        "LOCK TABLES",
        "REFERENCES",
        "SELECT",
        "SHOW DATABASES",
        "SHOW VIEW",
        "TRIGGER",
        "UPDATE",
        "USAGE",
    ]
    if privilege.upper() not in valid_privileges:
        raise ValueError(
            f'Invalid privilege {privilege} specified. Valid privileges are {", ".join(valid_privileges)}.'
        )
    return privilege


def get_normalized_grant_privileges(privileges):
    norm_privileges = []

    if isinstance(privileges, str):
        norm_privileges = [
            {
                "privilege": verify_privilege(privileges),
            }
        ]
    elif isinstance(privileges, list):
        for priv in privileges:
            if isinstance(priv, str):
                norm_privileges.append(
                    {
                        "privilege": verify_privilege(priv),
                    }
                )
            elif isinstance(priv, dict):
                norm_privileges.append(
                    {
                        "privilege": verify_privilege(priv.get("privilege")),
                        "columnList": priv.get("columnList"),
                    }
                )

    return norm_privileges


def get_grant_statements_for_explicit_grants(grants, role):
    if grants is None:
        return []

    # Normalize privileges
    norm_grants = []
    if isinstance(grants, dict):
        norm_grants = [
            {
                "object": grants.get("object"),
                "schema": grants.get("schema"),
                "objectType": grants.get("objectType", None),
                "privileges": get_normalized_grant_privileges(grants.get("privileges")),
            }
        ]

    elif isinstance(grants, list):
        norm_grants = []
        for grant in grants:
            norm_grants.append(
                {
                    "object": grant.get("object"),
                    "schema": grant.get("schema"),
                    "objectType": grant.get("objectType", None),
                    "privileges": get_normalized_grant_privileges(
                        grant.get("privileges")
                    ),
                }
            )

    # Build grant statements
    grant_statements = []
    for grant in norm_grants:
        privileges = []
        for privilege in grant["privileges"]:
            if privilege.get("columnList", None) is not None:
                privileges.append(
                    privilege.get("privilege")
                    + " ("
                    + ", ".join(privilege.get("columnList"))
                    + ")"
                )
            else:
                privileges.append(privilege.get("privilege"))

        privileges = ", ".join(privileges)
        object_type = grant.get("objectType", None)
        if object_type not in ["PROCEDURE", "FUNCTION"]:
            object_type = ""

        grant_statements.append(
            f"GRANT {privileges} ON {object_type}"
            f"{quote_identifier(grant['schema'])}.{quote_identifier(grant['object'])} "
            + f"TO {quote_identifier(role)}"
        )

    return grant_statements


def get_grant_statements(
    session,
    schema_name,
    rest_object_name,
    grant_privileges,
    data_mappings,
    rest_object_type=None,
    explicit_grants=None,
    disable_automatic_grants=False,
):
    # We can not grant/revoke the information_schema
    if schema_name.lower() == "information_schema":
        return []

    role = core.metadata_role(session, "data_provider")

    if grant_privileges and not disable_automatic_grants:
        # We can only grant select on the performance_schema
        if schema_name.lower() == "performance_schema":
            grant_privileges = ["SELECT"]

        if rest_object_type == "PROCEDURE" or rest_object_type == "FUNCTION":
            grant_privileges = ["EXECUTE"]

        rest_objects = [(rest_object_name, rest_object_type, grant_privileges)]

        # A view that executes in invoker security context can perform only operations for which the invoker has
        # privileges. This means the MRS user needs the additional grants for the underlying tables.
        if rest_object_type == "VIEW" and stored_object_executes_as_invoker(
            session, schema_name, rest_object_name
        ):
            rest_objects.extend(
                [
                    (obj_name, obj_type, obj_grants)
                    for obj_name, obj_type, obj_grants in get_objects_used_in_view_including_required_grants(
                        session, schema_name, rest_object_name
                    )
                ]
            )

        grants = [
            f"""GRANT {','.join(obj_grants)}
            ON {obj_type if obj_type == "PROCEDURE" or obj_type == "FUNCTION" else ''}
            {quote_identifier(schema_name)}.{quote_identifier(obj_name)}
            TO {quote_identifier(role)}"""
            for obj_name, obj_type, obj_grants in rest_objects
        ]

        # If the object is not a procedure, also add all referenced tables and views
        if (
            rest_object_type != "PROCEDURE"
            and rest_object_type != "FUNCTION"
            and data_mappings is not None
        ):
            for obj in data_mappings:
                for field in obj.get("fields"):
                    if field.get("data_mapping_reference") and (
                        field["data_mapping_reference"].get("unnest") or field["enabled"]
                    ):
                        ref_table = (
                            f'{field["data_mapping_reference"]["reference_mapping"]["referenced_schema"]}'
                            + f'.{field["data_mapping_reference"]["reference_mapping"]["referenced_table"]}'
                        )
                        grants.append(f"""GRANT {','.join(grant_privileges)}
                            ON {ref_table}
                            TO {quote_identifier(role)}""")
    else:
        grants = []

    if explicit_grants is not None:
        grants.extend(get_grant_statements_for_explicit_grants(explicit_grants, role))

    return grants


def get_data_mappings(session, rest_object_id):
    sql = """
        SELECT *
        FROM <metadata>.`data_mapping`
        WHERE rest_object_id = ?
        ORDER BY position
    """

    return core.MrsDbExec(sql).exec(session, [rest_object_id]).items


def get_data_mapping_fields_with_references(session, data_mapping_id, binary_formatter=None):
    sql = """
        SELECT *
        FROM <metadata>.`data_mapping_fields_with_references`
        WHERE data_mapping_id = ?
    """

    return (
        core.MrsDbExec(sql, binary_formatter=binary_formatter)
        .exec(session, [data_mapping_id])
        .items
    )


def get_sdk_service_data(session, service_id, binary_formatter=None):
    sql = """
        SELECT COUNT(*) > 0 AS available
        FROM information_schema.routines
        WHERE routine_name = 'sdk_service_data'
            AND routine_type = 'PROCEDURE'
            AND routine_schema = ?
    """

    if (
        core.MrsDbExec(sql, [core.metadata_schema(session)])
        .exec(session)
        .first["available"]
    ):
        sql = """
            CALL <metadata>.`sdk_service_data`(?)
        """

        row = (
            core.MrsDbExec(sql, binary_formatter=binary_formatter)
            .exec(session, [service_id])
            .first
        )
        # MariaDB's JSON is a LONGTEXT, so the document arrives as text.
        if row is not None and isinstance(row.get("service_res"), str):
            row["service_res"] = json.loads(row["service_res"])

        return row
    else:
        return None
