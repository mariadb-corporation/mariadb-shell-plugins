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

from mrs_plugin.lib import core, schemas, database
import json


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


def query_db_objects(
    session,
    db_object_id=None,
    schema_id=None,
    request_path=None,
    db_object_name=None,
    include_enable_state=None,
    object_types=None,
):

    # Build SQL based on which input has been provided

    sql = """
        SELECT o.id, o.db_schema_id, o.name, o.request_path,
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
        FROM mysql_rest_service_metadata.db_object o
            LEFT OUTER JOIN mysql_rest_service_metadata.db_schema sc
                ON sc.id = o.db_schema_id
            LEFT OUTER JOIN mysql_rest_service_metadata.service se
                ON se.id = sc.service_id
            LEFT JOIN mysql_rest_service_metadata.url_host h
                ON se.url_host_id = h.id
            LEFT OUTER JOIN (
                SELECT new_row_id AS id, MAX(changed_at) as changed_at
                FROM mysql_rest_service_metadata.audit_log
                WHERE table_name = 'db_object'
                GROUP BY new_row_id) al
            ON al.id = o.id
        """

    params = []
    wheres = []
    if db_object_id is not None:
        wheres.append("o.id = ?")
        params.append(db_object_id)
    else:
        if schema_id is not None:
            wheres.append("o.db_schema_id = ?")
            params.append(schema_id)
        if request_path is not None:
            wheres.append("o.request_path = ?")
            params.append(request_path)
        if db_object_name is not None:
            wheres.append("o.name = ?")
            params.append(db_object_name)
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


def add_db_object(
    session,
    schema_id,
    db_object_name,
    request_path,
    db_object_type,
    enabled,
    items_per_page,
    requires_auth,
    crud_operation_format,
    comments,
    media_type,
    auto_detect_media_type,
    auth_stored_procedure,
    options,
    objects,
    metadata=None,
    internal=False,
    db_object_id=None,
    reuse_ids=False,
    row_user_ownership_enforced=None,
    row_user_ownership_column=None,
):
    if not isinstance(db_object_name, str):
        raise Exception("Invalid object name.")

    if db_object_type not in ["TABLE", "VIEW", "PROCEDURE", "FUNCTION", "SCRIPT"]:
        raise ValueError(
            "Invalid db_object_type. Only valid types are TABLE, VIEW, PROCEDURE and FUNCTION."
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

    if db_object_id is None:
        db_object_id = core.get_sequence_id(session)

    crud_operations = calculate_crud_operations(
        db_object_type=db_object_type, objects=objects, options=options
    )

    values = {
        "id": db_object_id,
        "db_schema_id": schema_id,
        "name": db_object_name,
        "request_path": request_path,
        "object_type": db_object_type,
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

    # Remove row_user_ownership_enforced and row_user_ownership_column from db_object values as they are now
    # passed in object and object_reference directly
    values.pop("row_user_ownership_enforced", None)
    values.pop("row_user_ownership_column", None)

    # Update object.row_ownership_field_id when the old parameters are still used
    if row_user_ownership_enforced and row_user_ownership_column:
        for obj in objects:
            fields = obj.get("fields", [])
            for field in fields:
                db_column = field.get("db_column", None)
                if db_column is not None:
                    if db_column.get("name") == row_user_ownership_column:
                        obj["row_ownership_field_id"] = field.get("id")

    core.insert(table="db_object", values=values).exec(session)

    set_objects(session, db_object_id, objects)

    if db_object_type == "PROCEDURE" or db_object_type == "FUNCTION":
        grant_privileges = ["EXECUTE"]
    else:
        grant_privileges = map_crud_operations(crud_operations)

    if not grant_privileges:
        raise ValueError("No valid CRUD Operation specified")

    # Ensure that the explicit grants lookup with get does not fail
    if options is None:
        options = {}

    if db_object_type == "SCRIPT":
        return db_object_id, database.get_grant_statements_for_explicit_grants(
            options.get("grants", None)
        )
    else:
        return db_object_id, database.get_grant_statements(
            session=session,
            schema_name=schema["name"],
            db_object_name=db_object_name,
            grant_privileges=grant_privileges,
            objects=objects,
            db_object_type=db_object_type,
            explicit_grants=options.get("grants", None),
            disable_automatic_grants=options.get("disableAutomaticGrants", False),
        )


def get_objects(session, db_object_id):
    return database.get_objects(session, db_object_id)


def get_object_fields_with_references(session, object_id, binary_formatter=None):
    return database.get_object_fields_with_references(
        session, object_id, binary_formatter=binary_formatter
    )


def set_objects(session, db_object_id, objects):
    if objects is None:
        objects = []

    sql = "DELETE FROM mysql_rest_service_metadata.object WHERE db_object_id = ?"
    core.MrsDbExec(sql).exec(
        session, [core.id_to_uuid(db_object_id, "db_object_id")]
    ).items

    for obj in objects:
        set_object_fields_with_references(session, db_object_id, obj)


def set_object_fields_with_references(session, db_object_id, obj):
    values = {
        "id": core.id_to_uuid(obj.get("id"), "object.id"),
        "db_object_id": core.id_to_uuid(db_object_id, "db_object_id"),
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

    core.insert(table="object", values=values).exec(session)

    fields = obj.get("fields", [])

    # Insert object_references first
    inserted_object_references_ids = []
    for field in fields:
        obj_ref = field.get("object_reference")

        if obj_ref is not None and (
            not (obj_ref.get("id") in inserted_object_references_ids)
        ):
            inserted_object_references_ids.append(obj_ref.get("id"))

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

            core.insert(table="object_reference", values=values).exec(session)

    # Then insert object_fields
    inserted_field_ids = []
    for field in fields:
        obj_ref = field.get("object_reference")

        if not (field.get("id") in inserted_field_ids):
            inserted_field_ids.append(field.get("id"))

            values = {
                "id": core.id_to_uuid(field.get("id"), "field.id"),
                "object_id": core.id_to_uuid(field.get("object_id"), "field.object_id"),
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

            core.insert(table="object_field", values=values).exec(session)


def calculate_crud_operations(db_object_type, objects=None, options=None):
    if db_object_type == "SCRIPT":
        return ["CREATE", "READ", "UPDATE"]
    if db_object_type == "PROCEDURE" or db_object_type == "FUNCTION":
        if options is not None and options.get("mysqlTask", None) is not None:
            return ["CREATE", "READ", "UPDATE", "DELETE"]
        else:
            return ["CREATE"]

    if objects is None:
        return ["READ"]

    if len(objects) == 0:
        raise Exception("No object result definition present.")

    obj = objects[0]
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
