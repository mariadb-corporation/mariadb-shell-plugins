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

# The Python plugin's own writers of REST schemas, REST objects and their data
# mappings and grants. The REST SQL of the shell's mrs module creates these now,
# so the plugin no longer ships them; the tests keep them to build their
# fixtures. Building those fixtures with REST SQL instead is a next step (see
# the plugin's project context).

import json

from mrs_plugin.lib import core, schemas


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


def add_schema(
    session,
    schema_name,
    service_id: bytes = None,
    request_path=None,
    requires_auth=None,
    enabled=1,
    items_per_page=None,
    comments=None,
    options=None,
    metadata=None,
    schema_type="DATABASE_SCHEMA",
    internal=False,
    schema_id=None,
):
    """Add a schema to the given MRS service

    Args:
        schema_name (str): The name of the schema to add
        service_id: The id of the service the schema should be added to
        request_path (str): The request_path
        requires_auth (bool): Whether authentication is required to access
            the schema
        enabled (int): The enabled state
        items_per_page (int): The number of items returned per page
        comments (str): Comments for the schema
        options (dict): The options for the schema
        metadata (dict): Metadata of the schema
        schema_type (str): Either "DATABASE_SCHEMA" or "SCRIPT_MODULE"
        session (object): The database session to use.

    Returns:
        The id of the inserted schema
    """
    if schema_type == "DATABASE_SCHEMA":
        # If a schema name has been provided, check if that schema exists
        row = get_schema(session, schema_name)

        if row is None:
            raise ValueError(
                f"The given database schema name '{schema_name}' does not exists."
            )

        schema_name = row["SCHEMA_NAME"]
    elif schema_name is None:
        raise ValueError(f"No schema name given.")

    # Get request_path and default it to '/'
    if request_path is None:
        request_path = "/" + schema_name

    core.Validations.request_path(request_path)

    # Get requires_auth
    if requires_auth is None:
        requires_auth = False

    # Get items_per_page
    if items_per_page is None:
        items_per_page = 25

    # Get comments
    if comments is None:
        comments = ""

    if options is None:
        options = ""

    if schema_id is None:
        schema_id = core.get_sequence_id(session)
    values = {
        "id": schema_id,
        "service_id": service_id,
        "name": schema_name,
        "request_path": request_path,
        "requires_auth": int(requires_auth),
        "enabled": enabled,
        "items_per_page": items_per_page,
        "comments": comments,
        "options": core.convert_json(options) if options else None,
        "metadata": core.convert_json(metadata) if metadata else None,
        "schema_type": schema_type,
        "internal": int(internal),
    }

    core.insert(table="rest_schema", values=values).exec(session)

    return schema_id


def map_crud_operations(crud_operations):
    grant_privileges = []
    for crud_operation in crud_operations:
        if crud_operation == "CREATE" or crud_operation == "1":
            grant_privileges.append("INSERT")
        elif crud_operation == "READ" or crud_operation == "2":
            grant_privileges.append("SELECT")
        elif crud_operation == "UPDATE" or crud_operation == "3":
            grant_privileges.append("UPDATE")
        elif crud_operation == "DELETE" or crud_operation == "4":
            grant_privileges.append("DELETE")
        else:
            raise ValueError(
                f"The given CRUD operation {crud_operation} " "does not exist."
            )
    return grant_privileges


def add_rest_object(
    session,
    schema_id,
    rest_object_name,
    request_path,
    rest_object_type,
    enabled,
    items_per_page,
    requires_auth,
    crud_operation_format,
    comments,
    media_type,
    auto_detect_media_type,
    auth_stored_procedure,
    options,
    data_mappings,
    metadata=None,
    internal=False,
    rest_object_id=None,
    reuse_ids=False,
    row_user_ownership_enforced=None,
    row_user_ownership_column=None,
):
    if not isinstance(rest_object_name, str):
        raise Exception("Invalid object name.")

    if rest_object_type not in ["TABLE", "VIEW", "PROCEDURE", "FUNCTION", "SCRIPT"]:
        raise ValueError(
            "Invalid rest_object_type. Only valid types are TABLE, VIEW, PROCEDURE and FUNCTION."
        )

    if not crud_operation_format:
        raise ValueError("No CRUD operation format specified." "Operation cancelled.")

    if row_user_ownership_enforced is None:
        row_user_ownership_enforced = False

    if auto_detect_media_type is None:
        auto_detect_media_type = False

    if not comments:
        comments = ""

    schema = schemas.get_schema(
        session=session, schema_id=schema_id, auto_select_single=True
    )

    if rest_object_id is None:
        rest_object_id = core.get_sequence_id(session)

    crud_operations = calculate_crud_operations(
        rest_object_type=rest_object_type, data_mappings=data_mappings
    )

    values = {
        "id": rest_object_id,
        "rest_schema_id": schema_id,
        "name": rest_object_name,
        "request_path": request_path,
        "object_type": rest_object_type,
        "enabled": enabled,
        "items_per_page": items_per_page,
        "requires_auth": int(requires_auth),
        "row_user_ownership_enforced": int(row_user_ownership_enforced),
        "row_user_ownership_column": row_user_ownership_column,
        "crud_operations": crud_operations,
        "format": crud_operation_format,
        "comments": comments,
        "media_type": media_type,
        "metadata": metadata,
        "auto_detect_media_type": int(auto_detect_media_type),
        "auth_stored_procedure": auth_stored_procedure,
        "options": options,
        "internal": internal,
    }

    # Remove row_user_ownership_enforced and row_user_ownership_column from rest_object values as they are now
    # passed in object and data_mapping_reference directly
    values.pop("row_user_ownership_enforced", None)
    values.pop("row_user_ownership_column", None)

    # Update object.row_ownership_field_id when the old parameters are still used
    if row_user_ownership_enforced and row_user_ownership_column:
        for obj in data_mappings:
            fields = obj.get("fields", [])
            for field in fields:
                db_column = field.get("db_column", None)
                if db_column is not None:
                    if db_column.get("name") == row_user_ownership_column:
                        obj["row_ownership_field_id"] = field.get("id")

    core.insert(table="rest_object", values=values).exec(session)

    set_data_mappings(session, rest_object_id, data_mappings)

    if rest_object_type == "PROCEDURE" or rest_object_type == "FUNCTION":
        grant_privileges = ["EXECUTE"]
    else:
        grant_privileges = map_crud_operations(crud_operations)

    if not grant_privileges:
        raise ValueError("No valid CRUD Operation specified")

    # Ensure that the explicit grants lookup with get does not fail
    if options is None:
        options = {}

    if rest_object_type == "SCRIPT":
        return rest_object_id, get_grant_statements_for_explicit_grants(
            options.get("grants", None), core.metadata_role(session, "data_provider")
        )
    else:
        return rest_object_id, get_grant_statements(
            session=session,
            schema_name=schema["name"],
            rest_object_name=rest_object_name,
            grant_privileges=grant_privileges,
            data_mappings=data_mappings,
            rest_object_type=rest_object_type,
            explicit_grants=options.get("grants", None),
            disable_automatic_grants=options.get("disableAutomaticGrants", False),
        )


def set_data_mappings(session, rest_object_id, data_mappings):
    if data_mappings is None:
        data_mappings = []

    sql = "DELETE FROM <metadata>.data_mapping WHERE rest_object_id = ?"
    core.MrsDbExec(sql).exec(
        session, [core.id_to_uuid(rest_object_id, "rest_object_id")]
    ).items

    for obj in data_mappings:
        set_data_mapping_fields_with_references(session, rest_object_id, obj)


def set_data_mapping_fields_with_references(session, rest_object_id, obj):
    values = {
        "id": core.id_to_uuid(obj.get("id"), "object.id"),
        "rest_object_id": core.id_to_uuid(rest_object_id, "rest_object_id"),
        "name": obj.get("name"),
        "kind": obj.get("kind", "RESULT"),
        "position": obj.get("position"),
        "sdk_options": core.convert_dict_to_json_string(obj.get("sdk_options")),
        "comments": obj.get("comments"),
    }

    options = obj.get("options", None)
    # To be backwards compatible, duplicate the options using the old key names
    if options is not None:
        options["duality_view_insert"] = options.get("dataMappingViewInsert", None)
        options["duality_view_update"] = options.get("dataMappingViewUpdate", None)
        options["duality_view_delete"] = options.get("dataMappingViewDelete", None)
        if options.get("dataMappingViewNoCheck", None) is not None:
            options["duality_view_no_check"] = options.get(
                "dataMappingViewNoCheck", None
            )
    values["options"] = options
    row_ownership_field_id = obj.get("row_ownership_field_id", None)
    if row_ownership_field_id is not None:
        values["row_ownership_field_id"] = core.id_to_uuid(
            row_ownership_field_id, "row_ownership_field_id"
        )

    core.insert(table="data_mapping", values=values).exec(session)

    fields = obj.get("fields", [])

    # Insert data_mapping_references first
    inserted_data_mapping_references_ids = []
    for field in fields:
        obj_ref = field.get("data_mapping_reference")

        if obj_ref is not None and (
            not (obj_ref.get("id") in inserted_data_mapping_references_ids)
        ):
            inserted_data_mapping_references_ids.append(obj_ref.get("id"))

            # make sure to covert the sub Dict with dict()
            ref_map = obj_ref.get("reference_mapping")
            ref_map_json = None
            if ref_map is not None:
                ref_map = dict(ref_map)

                # Convert column_mapping, which is a list of dict
                converted_col_mapping = []
                for cm in ref_map["column_mapping"]:
                    cm_dict = dict(cm)
                    # Check if column_mapping already follows new format
                    if "base" in cm_dict and "ref" in cm_dict:
                        converted_col_mapping.append(cm_dict)
                    else:
                        # If not, convert to new format that uses "base" and "ref" keys
                        for key in cm_dict.keys():
                            converted_col_mapping.append(
                                {"base": key, "ref": cm_dict.get(key)}
                            )

                ref_map["column_mapping"] = converted_col_mapping
                ref_map_json = json.dumps(ref_map)

            if not ref_map_json:
                raise Exception(
                    f'reference_mapping not defined for field {field.get("name")}'
                )

            values = {
                "id": core.id_to_uuid(obj_ref.get("id"), "objectReference.id"),
                "reduce_to_value_of_field_id": core.id_to_uuid(
                    obj_ref.get("reduce_to_value_of_field_id"),
                    "objectReference.reduce_to_value_of_field_id",
                    True,
                ),
                "reference_mapping": ref_map_json,
                "unnest": obj_ref.get("unnest"),
                "sdk_options": core.convert_dict_to_json_string(
                    obj_ref.get("sdk_options")
                ),
                "comments": obj_ref.get("comments"),
            }
            options = obj.get("options", None)
            # To be backwards compatible, duplicate the options using the old key names
            if options is not None:
                options["duality_view_insert"] = options.get(
                    "dataMappingViewInsert", None
                )
                options["duality_view_update"] = options.get(
                    "dataMappingViewUpdate", None
                )
                options["duality_view_delete"] = options.get(
                    "dataMappingViewDelete", None
                )
                if options.get("dataMappingViewNoCheck", None) is not None:
                    options["duality_view_no_check"] = options.get(
                        "dataMappingViewNoCheck", None
                    )
            values["options"] = options
            row_ownership_field_id = obj_ref.get("row_ownership_field_id", None)
            if row_ownership_field_id is not None:
                values["row_ownership_field_id"] = core.id_to_uuid(
                    row_ownership_field_id, "objectReference.row_ownership_field_id"
                )

            core.insert(table="data_mapping_reference", values=values).exec(session)

    # Then insert data_mapping_fields
    inserted_field_ids = []
    for field in fields:
        obj_ref = field.get("data_mapping_reference")

        if not (field.get("id") in inserted_field_ids):
            inserted_field_ids.append(field.get("id"))

            values = {
                "id": core.id_to_uuid(field.get("id"), "field.id"),
                "data_mapping_id": core.id_to_uuid(field.get("data_mapping_id"), "field.data_mapping_id"),
                "parent_reference_id": core.id_to_uuid(
                    field.get("parent_reference_id"), "field.parent_reference_id", True
                ),
                "represents_reference_id": core.id_to_uuid(
                    field.get("represents_reference_id"),
                    "field.represents_reference_id",
                    True,
                ),
                "name": field.get("name"),
                "position": field.get("position"),
                "db_column": core.convert_dict_to_json_string(field.get("db_column")),
                "enabled": field.get("enabled"),
                "allow_filtering": field.get("allow_filtering"),
                "allow_sorting": field.get("allow_sorting", 0),
                "no_check": field.get("no_check"),
                "no_update": field.get("no_update"),
                "sdk_options": core.convert_dict_to_json_string(
                    field.get("sdk_options")
                ),
                "comments": field.get("comments"),
            }

            values["options"] = field.get("options", None)
            values["json_schema"] = core.convert_dict_to_json_string(
                field.get("json_schema", None)
            )

            core.insert(table="data_mapping_field", values=values).exec(session)


def calculate_crud_operations(rest_object_type, data_mappings=None):
    if rest_object_type == "SCRIPT":
        return ["CREATE", "READ", "UPDATE"]
    if rest_object_type == "PROCEDURE" or rest_object_type == "FUNCTION":
        return ["CREATE"]

    if data_mappings is None:
        return ["READ"]

    if len(data_mappings) == 0:
        raise Exception("No object result definition present.")

    obj = data_mappings[0]
    options = obj.get("options", {})
    if options is None:
        options = {}
    crudOps = ["READ"]

    if options.get("dataMappingViewInsert", False) is True:
        crudOps.append("CREATE")
    if options.get("dataMappingViewUpdate", False) is True:
        crudOps.append("UPDATE")
    if options.get("dataMappingViewDelete", False) is True:
        crudOps.append("DELETE")

    # Loop over all fields and check if an object reference has a CRUD operation set. If so, the mrsObject
    # needs to be updatable as well
    if "UPDATE" not in crudOps:
        for field in obj.get("fields"):
            options = field.get("options", None)
            if options is not None and len(crudOps) < 4:
                if "UPDATE" not in crudOps and (
                    options.get("dataMappingViewInsert", False) is True
                    or options.get("dataMappingViewUpdate", False) is True
                    or options.get("dataMappingViewDelete", False) is True
                ):
                    crudOps.append("UPDATE")

    return crudOps
