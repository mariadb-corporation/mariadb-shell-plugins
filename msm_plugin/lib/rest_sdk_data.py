# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The SDK data of a REST service a schema project defines, without a server.

The REST metadata procedure sdk_service_data returns a service as a document
(service -> rest_schemas -> rest_objects -> data_mappings -> fields), which
rest_plugin's SDK generator fills its templates from. A schema project has no
metadata: its Section 180 holds the REST statements. This module builds the
same document from what mariadb-lsp's outline says about them (features
rest_object_details and rest_settings) and from the schema snapshot's
tables, views and routines, as MariaDB Shell's MRS DDL executor would write
the metadata when it runs the statements
(mysqlshdk/libs/mrs/ddl_executor_rest_objects.cc, Mapping_builder) and as
the metadata procedure table_columns_with_references describes a table.

No REST SQL is read here: every statement is the outline's (the user's
rule). A column's type is the outline's column_type (feature column_types),
the server's form of it; a binary without that feature gives the type as
written, and the caller says so.

An object whose statement MariaDB Shell would refuse (a column the table
does not have, a reference without a foreign key) is left out with a
warning, as it would not exist on a server either. So is one on a database
object the project does not define.

No shell imports: the tests run it on outlines alone.
"""

import uuid
from typing import Optional

from msm_plugin.lib.diagrams import same_name

# What the outline must tell for the SDK.
SDK_OUTLINE_FEATURES = frozenset(("rest_object_details", "rest_settings"))
# The server's form of the column types (lsp-server, agreed 2026-11-23).
COLUMN_TYPES_FEATURE = "column_types"

# The ids of the document: made up, as a project has no metadata, and the
# same for the same project and service, so a dump made again gives the
# same files.
_ID_NAMESPACE = uuid.UUID("6f1b4c2e-2d5a-4c63-9a3e-5b7d1f0c8e42")

class Refused(ValueError):
    """MariaDB Shell would refuse the statement of a REST object."""


# -----------------------------------------------------------------------------
# Names, as the shell makes them (metadata_rest_objects.cc)


def _ascii_lower(c: str) -> str:
    return chr(ord(c) + 32) if "A" <= c <= "Z" else c


def _ascii_upper(c: str) -> str:
    return chr(ord(c) - 32) if "a" <= c <= "z" else c


def _ascii_alnum(c: str) -> bool:
    return "a" <= c <= "z" or "A" <= c <= "Z" or "0" <= c <= "9"


def name_to_camel_case(name: str) -> str:
    """A column's or parameter's name as a field name: words of ASCII
    letters and digits (and any non-ASCII letter), the first lower case, the
    others capitalized; a word without lower-case letters is folded first."""
    def in_word(c: str) -> bool:
        return _ascii_alnum(c) or ord(c) >= 0x80

    result = ""
    i = 0
    while i < len(name):
        if not in_word(name[i]):
            i += 1
            continue
        end = i
        while end < len(name) and in_word(name[end]):
            end += 1
        word = name[i:end]
        fold = not any("a" <= c <= "z" for c in word)
        first = result == ""
        for j, c in enumerate(word):
            ch = _ascii_lower(c) if fold else c
            if j == 0:
                ch = _ascii_lower(ch) if first else _ascii_upper(ch)
            result += ch
        i = end
    return result


def field_name(db_name: str) -> str:
    name = name_to_camel_case(db_name)
    return name if name else "field"


def path_to_pascal_case(path: str) -> str:
    """A REST path as a class name: /svc/shop/order_items -> SvcShopOrderItems."""
    if path.startswith("/"):
        path = path[1:]
    camel = ""
    for index, part in enumerate(path.replace("/", "_").split("_")):
        if index == 0:
            camel += part
        elif part:
            camel += _ascii_upper(part[0]) + part[1:]
    result = "".join(c for c in camel if _ascii_alnum(c))
    return _ascii_upper(result[0]) + result[1:] if result else result


# -----------------------------------------------------------------------------
# The database objects of the snapshot


class Catalog:
    """The tables, views and routines of the project's schema, as
    table_columns_with_references and the routine lookups of the shell
    describe them."""

    def __init__(self, document: dict, schema_name: str, column_types: bool):
        self.schema_name = schema_name
        self.column_types = column_types
        self.tables = {t["name"].lower(): t for t in document.get("tables", []) if t.get("name")}
        self.views = {v["name"].lower(): v for v in document.get("views", []) if v.get("name")}
        self.routines = {
            (r.get("kind"), r["name"].lower()): r
            for r in document.get("routines", [])
            if r.get("name")
        }
        # Per table, worked out once: the PRI columns, and the foreign keys
        # of other tables that reference it
        self._primaries = {key: self._primary(t) for key, t in self.tables.items()}
        self._incoming = {}
        for child in self.tables.values():
            for foreign_key in child.get("foreign_keys", []):
                references = foreign_key.get("references") or {}
                if self.in_schema(references.get("schema")) and references.get("table"):
                    self._incoming.setdefault(references["table"].lower(), []).append(
                        (child, foreign_key)
                    )

    def in_schema(self, schema: Optional[str]) -> bool:
        return schema is None or same_name(schema, self.schema_name)

    def object_type(self, schema: Optional[str], name: str) -> Optional[str]:
        """TABLE, VIEW or None, as the shell's database_object_type."""
        if not self.in_schema(schema):
            return None
        if name.lower() in self.tables:
            return "TABLE"
        if name.lower() in self.views:
            return "VIEW"
        return None

    def routine(self, kind: str, schema: Optional[str], name: str) -> Optional[dict]:
        if not self.in_schema(schema):
            return None
        return self.routines.get((kind, name.lower()))

    def _type(self, member: dict, typed_member: str, written_member: str) -> Optional[str]:
        if self.column_types and member.get(typed_member):
            return member[typed_member]
        written = member.get(written_member)
        return written.lower() if isinstance(written, str) else None

    def parameter_type(self, parameter: dict) -> Optional[str]:
        return self._type(parameter, "column_type", "type")

    def return_type(self, routine: dict) -> Optional[str]:
        return self._type(routine, "returns_column_type", "returns")

    # -- Tables ---------------------------------------------------------------

    @staticmethod
    def _primary(table: dict) -> list:
        """The columns COLUMN_KEY shows as PRI: the primary key, else the
        first unique key whose columns are all NOT NULL, which the server
        takes as the primary key."""
        indexes = table.get("indexes", [])
        for index in indexes:
            if index.get("kind") == "primary":
                return [c.lower() for c in index.get("columns", [])]
        nullable = {c["name"].lower(): c.get("nullable", True) for c in table.get("columns", [])}
        for index in indexes:
            columns = [c.lower() for c in index.get("columns", [])]
            if index.get("kind") == "unique" and columns and not any(
                nullable.get(c, True) for c in columns
            ):
                return columns
        return []

    @staticmethod
    def _unique(table: dict, primary: list) -> set:
        """The columns COLUMN_KEY shows as UNI: alone in a unique key."""
        unique = {
            index["columns"][0].lower()
            for index in table.get("indexes", [])
            if index.get("kind") == "unique" and len(index.get("columns", [])) == 1
        }
        for column in table.get("columns", []):
            # SERIAL is BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE
            if same_name(column.get("type"), "SERIAL"):
                unique.add(column["name"].lower())
        return unique - set(primary)

    def _db_column(self, column: dict, primary: list, unique: set) -> dict:
        name = column["name"]
        is_primary = name.lower() in primary
        nullable = column.get("nullable", True) and not is_primary
        datatype = self._type(column, "column_type", "type")
        id_generation = None
        if column.get("auto_increment"):
            id_generation = "auto_inc"
        elif is_primary and datatype == "binary(16)":
            id_generation = "rev_uuid"
        default = column.get("default")
        if (
            default is None and nullable and column.get("generated") is None
            and not column.get("auto_increment")
        ):
            # MariaDB shows a nullable column without a default as NULL
            default = "NULL"
        return {
            "name": name,
            "datatype": datatype,
            "not_null": not nullable,
            "is_primary": is_primary,
            "is_unique": name.lower() in unique,
            "is_generated": column.get("generated") is not None,
            "id_generation": id_generation,
            "comment": column.get("comment") or "",
            "srid": None,
            "column_default": default,
            "charset": column.get("charset"),
            "collation": column.get("collation"),
        }

    def _pk_ids(self, table: dict) -> list:
        """The PRI columns of a table as schema.table.column."""
        return [
            f"{self.schema_name}.{table['name']}.{column}".lower()
            for column in self._primaries[table["name"].lower()]
        ]

    def _pk_ref_ids(self, child: dict) -> list:
        """The PRI columns of a table, each as the column it references where
        a foreign key has it, as the procedure's PK_REF."""
        ids = []
        for column in self._primaries[child["name"].lower()]:
            referenced = None
            for foreign_key in child.get("foreign_keys", []):
                columns = [c.lower() for c in foreign_key.get("columns", [])]
                if column in columns:
                    references = foreign_key.get("references") or {}
                    ref_columns = references.get("columns") or []
                    position = columns.index(column)
                    if position < len(ref_columns):
                        candidate = (
                            f"{references.get('schema') or self.schema_name}."
                            f"{references.get('table')}.{ref_columns[position]}"
                        ).lower()
                        referenced = max(referenced or candidate, candidate)
            ids.append(referenced or f"{self.schema_name}.{child['name']}.{column}".lower())
        return ids

    def table_columns(self, schema: Optional[str], name: str) -> Optional[list]:
        """The columns and references of a table or view, each {position,
        name, db_column, reference_mapping}, ordered by position; None for an
        object the project does not have. A view's columns are only those
        its statement lists (no types)."""
        if not self.in_schema(schema):
            return None
        table = self.tables.get(name.lower())
        if table is None:
            view = self.views.get(name.lower())
            if view is None:
                return None
            return [
                {
                    "position": i + 1, "name": column,
                    "db_column": {"name": column, "datatype": None, "not_null": False,
                                  "is_primary": False, "is_unique": False,
                                  "is_generated": False, "id_generation": None},
                    "reference_mapping": None,
                }
                for i, column in enumerate(view.get("columns") or [])
            ]

        columns = table.get("columns", [])
        ordinal = {c["name"].lower(): i + 1 for i, c in enumerate(columns)}
        primary = self._primaries[name.lower()]
        unique = self._unique(table, primary)
        rows = [
            {"position": ordinal[c["name"].lower()], "name": c["name"],
             "db_column": self._db_column(c, primary, unique), "reference_mapping": None}
            for c in columns
        ]

        # n:1, the references from the table to others
        for foreign_key in table.get("foreign_keys", []):
            references = foreign_key.get("references") or {}
            fk_columns = foreign_key.get("columns", [])
            ref_columns = references.get("columns") or []
            if not fk_columns or not all(c.lower() in ordinal for c in fk_columns):
                continue
            rows.append({
                "position": max(ordinal[c.lower()] for c in fk_columns) + 100,
                "name": references.get("table"),
                "db_column": None,
                "reference_mapping": {
                    "kind": "n:1",
                    "constraint": f"{self.schema_name}.{foreign_key.get('name')}",
                    "to_many": False,
                    "referenced_schema": references.get("schema") or self.schema_name,
                    "referenced_table": references.get("table"),
                    "column_mapping": [
                        {"base": base, "ref": ref} for base, ref in zip(fk_columns, ref_columns)
                    ],
                },
            })

        # 1:1 and 1:n, the references from other tables to it. The procedure
        # joins both tables' primary keys: without one there is none.
        pk_table = set(self._pk_ids(table))
        for child, foreign_key in self._incoming.get(name.lower(), []) if pk_table else []:
            child_ordinal = {c["name"].lower(): i + 1 for i, c in enumerate(child.get("columns", []))}
            fk_columns = foreign_key.get("columns", [])
            ref_columns = (foreign_key.get("references") or {}).get("columns") or []
            pk_ref = self._pk_ref_ids(child)
            if not pk_ref or not fk_columns:
                continue
            if not all(c.lower() in child_ordinal for c in fk_columns):
                continue
            one_to_one = set(pk_ref) <= pk_table
            rows.append({
                "position": max(child_ordinal[c.lower()] for c in fk_columns) + 1000,
                "name": child["name"],
                "db_column": None,
                "reference_mapping": {
                    "kind": "1:1" if one_to_one else "1:n",
                    "constraint": f"{self.schema_name}.{foreign_key.get('name')}",
                    "to_many": not one_to_one,
                    "referenced_schema": self.schema_name,
                    "referenced_table": child["name"],
                    "column_mapping": [
                        {"base": base, "ref": ref} for base, ref in zip(ref_columns, fk_columns)
                    ],
                },
            })

        rows.sort(key=lambda row: row["position"])
        return rows


# -----------------------------------------------------------------------------
# The data mapping, as the shell's Mapping_builder builds it


def _flags(options: list) -> set:
    return {option.upper() for option in options or [] if isinstance(option, str)}


def mapping_options(flags: set) -> dict:
    """The CRUD options of a data mapping or reference, as the shell's
    Crud_annotations make them: the negated form wins."""
    return {
        "dataMappingViewInsert": "INSERT" in flags and "NOINSERT" not in flags,
        "dataMappingViewUpdate": "UPDATE" in flags and "NOUPDATE" not in flags,
        "dataMappingViewDelete": "DELETE" in flags and "NODELETE" not in flags,
        "dataMappingViewNoCheck": "NOCHECK" in flags and "CHECK" not in flags,
    }


class _Builder:
    def __init__(self, catalog: Catalog, schema_name: str, name: str, new_id):
        self.catalog = catalog
        self.schema_name = schema_name
        self.name = name
        self.new_id = new_id

    def _object(self, name: Optional[str], kind: str, position: int) -> dict:
        return {"id": self.new_id(), "name": name or "", "kind": kind, "position": position,
                "row_ownership_field_id": None, "options": None, "sdk_options": None,
                "fields": []}

    def _field(self, obj: dict, parent_reference_id: Optional[str]) -> dict:
        return {"id": self.new_id(), "data_mapping_id": obj["id"],
                "parent_reference_id": parent_reference_id, "name": "", "position": 0,
                "db_column": None, "enabled": False, "allow_filtering": True,
                "allow_sorting": False, "no_check": False, "no_update": False,
                "json_schema": None, "options": None, "sdk_options": None,
                "reference": None, "candidate": None}

    def _no_such_column(self, column: str):
        raise Refused(
            f"The column `{column}` does not exist on `{self.schema_name}`.`{self.name}`."
        )

    # -- Objects --------------------------------------------------------------

    def view_object(self, class_name, crud: list, mapping: Optional[list]) -> dict:
        obj = self._object(class_name, "RESULT", 0)
        obj["options"] = mapping_options(_flags(crud))
        known = self._add_table_fields(obj, None, self.schema_name, self.name, mapping is None)
        if mapping is not None:
            # A database view's columns are not known (lsp's view_columns is
            # to come): take the mapping's as given, as with FORCE.
            self._apply_mapping(obj, mapping, None, force=not known)
        self._make_names_unique(obj, mapping is not None)
        return obj

    def parameters_object(self, routine: Optional[dict], parameters: Optional[dict],
                          force: bool) -> dict:
        obj = self._object(parameters.get("name") if parameters else None, "PARAMETERS", 0)
        for position, parameter in enumerate((routine or {}).get("parameters", []), start=1):
            mode = (parameter.get("mode") or "IN").upper()
            field = self._field(obj, None)
            field["name"] = field_name(parameter.get("name") or "")
            field["position"] = position
            field["enabled"] = parameters is None
            field["db_column"] = {
                "name": parameter.get("name"), "in": "IN" in mode, "out": "OUT" in mode,
                "datatype": self.catalog.parameter_type(parameter), "not_null": False,
                "is_generated": False, "is_primary": False, "is_unique": False,
                "charset": None, "collation": None,
            }
            obj["fields"].append(field)
        if parameters is not None:
            self._apply_mapping(obj, parameters.get("fields") or [], None, force)
        self._make_names_unique(obj, parameters is not None)
        return obj

    def procedure_result(self, result: dict, position: int) -> dict:
        obj = self._object(result.get("name"), "RESULT", position)
        for pair in result.get("fields") or []:
            if pair.get("reference") is not None:
                raise Refused(
                    f"The column `{pair.get('name')}` of a RESULT cannot hold a nested object."
                )
            field = self._field(obj, None)
            field["position"] = len(obj["fields"])
            field["db_column"] = {"name": pair.get("column"),
                                  "datatype": self._datatype_of(pair)}
            self._apply_annotations(obj, field, pair)
            obj["fields"].append(field)
        return obj

    def function_result(self, routine: Optional[dict], result: Optional[dict]) -> dict:
        obj = self._object(result.get("name") if result else None, "RESULT", 1)
        field = self._field(obj, None)
        field["name"] = "result"
        field["enabled"] = True
        field["db_column"] = {
            "name": "result",
            "datatype": self.catalog.return_type(routine) if routine else None,
            "not_null": False, "is_generated": False, "is_primary": False, "is_unique": False,
        }
        for pair in (result or {}).get("fields") or []:
            if pair.get("column") != "result" or pair.get("reference") is not None:
                self._no_such_column(pair.get("column") or pair.get("name"))
            self._apply_annotations(obj, field, pair)
        obj["fields"].append(field)
        return obj

    def routine_objects(self, kind: str, routine: Optional[dict], parameters, results: list,
                        force: bool) -> list:
        objects = [self.parameters_object(routine, parameters, force)]
        if kind == "function":
            objects.append(self.function_result(routine, results[0] if results else None))
        else:
            for result in results:
                objects.append(self.procedure_result(result, len(objects)))
        return objects

    # -- Fields ---------------------------------------------------------------

    @staticmethod
    def _datatype_of(pair: dict) -> str:
        return pair["datatype"].lower() if pair.get("datatype") else "varchar(255)"

    def _add_table_fields(self, obj: dict, parent_reference_id, schema_name: str, table: str,
                          enable_columns: bool) -> bool:
        """Adds a table's columns and references, disabled unless asked;
        whether the columns are known (a database view's are not)."""
        columns = self.catalog.table_columns(schema_name, table)
        if columns is None:
            raise Refused(
                f"The table or view `{schema_name}`.`{table}` is not in the project."
            )
        for column in columns:
            field = self._field(obj, parent_reference_id)
            field["name"] = field_name(column["name"] or "")
            field["position"] = column["position"]
            field["enabled"] = enable_columns and column["reference_mapping"] is None
            field["db_column"] = column["db_column"]
            field["candidate"] = column["reference_mapping"]
            obj["fields"].append(field)
        return bool(columns)

    def _apply_mapping(self, obj: dict, mapping: list, parent_reference_id, force: bool):
        for pair in mapping:
            if pair.get("reference") is None:
                self._apply_column(obj, pair, parent_reference_id, force)
            else:
                self._apply_reference(obj, pair, parent_reference_id)

    def _apply_column(self, obj: dict, pair: dict, parent_reference_id, force: bool):
        column = pair.get("column")
        field = next(
            (f for f in obj["fields"]
             if f["parent_reference_id"] == parent_reference_id and f["db_column"]
             and f["db_column"].get("name") == column),
            None,
        )
        if field is None:
            if not force:
                self._no_such_column(column)
            field = self._field(obj, parent_reference_id)
            field["position"] = len(obj["fields"])
            flags = _flags(pair.get("options"))
            field["db_column"] = {
                "name": column, "datatype": self._datatype_of(pair),
                "in": bool(flags & {"IN", "INOUT"}), "out": bool(flags & {"OUT", "INOUT"}),
            }
            obj["fields"].append(field)
        self._apply_annotations(obj, field, pair)

    def _apply_annotations(self, obj: dict, field: dict, pair: dict):
        flags = _flags(pair.get("options"))
        field["name"] = pair.get("name")
        field["enabled"] = True
        if "NOCHECK" in flags:
            field["no_check"] = True
        if "SORTABLE" in flags:
            field["allow_sorting"] = True
        if "NOFILTERING" in flags:
            field["allow_filtering"] = False
        if "ROWOWNERSHIP" in flags:
            obj["row_ownership_field_id"] = field["id"]
        if field["db_column"] is not None:
            field["db_column"] = dict(field["db_column"])
            if "KEY" in flags:
                field["db_column"]["is_primary"] = True
            if pair.get("datatype"):
                field["db_column"]["datatype"] = pair["datatype"].lower()
        if "NOUPDATE" in flags:
            field["no_update"] = True
        if pair.get("json_schema") is not None:
            field["json_schema"] = pair["json_schema"]

    def _apply_reference(self, obj: dict, pair: dict, parent_reference_id):
        reference = pair["reference"]
        schema_name = reference.get("schema") or self.schema_name
        table = reference.get("table")
        field = next(
            (f for f in obj["fields"]
             if f["parent_reference_id"] == parent_reference_id and f["candidate"]
             and f["reference"] is None
             and same_name(f["candidate"].get("referenced_schema"), schema_name)
             and same_name(f["candidate"].get("referenced_table"), table)),
            None,
        )
        if field is None:
            raise Refused(
                f"The table `{schema_name}`.`{table}` has no reference to "
                f"`{self.schema_name}`.`{self.name}`."
            )
        flags = _flags(pair.get("options"))
        ref = {
            "id": self.new_id(),
            "reduce_to_value_of_field_id": None,
            "row_ownership_field_id": None,
            "reference_mapping": field["candidate"],
            "unnest": "UNNEST" in flags,
            "options": mapping_options(flags),
            "sdk_options": None,
            "comments": None,
        }
        to_many = ref["reference_mapping"].get("kind") == "1:n"
        field["name"] = pair.get("name")
        field["enabled"] = True
        field["candidate"] = None
        field["reference"] = ref

        self._add_table_fields(obj, ref["id"], schema_name, table, False)
        self._apply_mapping(obj, reference.get("fields") or [], ref["id"], False)
        if ref["unnest"] and to_many:
            self._reduce_to_single_column(obj, ref)

    def _reduce_to_single_column(self, obj: dict, ref: dict):
        references = {ref["id"]}
        added = True
        while added:
            added = False
            for field in obj["fields"]:
                if (field["reference"] and field["parent_reference_id"] in references
                        and field["reference"]["id"] not in references):
                    references.add(field["reference"]["id"])
                    added = True
        reduce_to = None
        for field in obj["fields"]:
            if (field["parent_reference_id"] not in references or not field["enabled"]
                    or field["reference"]):
                continue
            if reduce_to is not None:
                raise Refused(
                    f"Only one column `{reduce_to['name']}` must be defined for a N:1 unnest "
                    f"operation. The column `{field['name']}` needs to be removed."
                )
            reduce_to = field
        if reduce_to is None:
            raise Refused("At least one column must be defined for a N:1 unnest operation.")
        ref["reduce_to_value_of_field_id"] = reduce_to["id"]

    @staticmethod
    def _make_names_unique(obj: dict, mapped: bool):
        def named(field):
            return mapped and field["enabled"]

        taken = {(f["parent_reference_id"], f["name"]) for f in obj["fields"] if named(f)}
        for field in obj["fields"]:
            if named(field):
                continue
            name = field["name"]
            i = 2
            while (field["parent_reference_id"], name) in taken:
                name = f"{field['name']}{i}"
                i += 1
            taken.add((field["parent_reference_id"], name))
            field["name"] = name


def assign_object_names(objects: list, full_path: str, is_routine: bool, used: set):
    """Names the objects the statement left unnamed after the object's full
    path, as the shell's assign_object_names; used holds the names (upper
    case) of the REST schema's objects so far."""
    assigned = []
    for i, obj in enumerate(objects):
        if obj["name"]:
            used.add(obj["name"].upper())
            continue
        name = path_to_pascal_case(full_path)
        postfix = 2
        if is_routine:
            if obj["kind"] == "PARAMETERS":
                name += "Params"
            if i > 1:
                postfix = max(postfix, i)
                name = path_to_pascal_case(full_path + str(postfix))
        while name in assigned or name.upper() in used:
            name = path_to_pascal_case(full_path + str(postfix))
            postfix += 1
        obj["name"] = name
        assigned.append(name)
        used.add(name.upper())


def calculate_crud_operations(object_type: str, objects: list) -> list:
    if object_type in ("PROCEDURE", "FUNCTION"):
        return ["CREATE"]
    obj = objects[0]
    options = obj.get("options") or {}
    crud = ["READ"]
    if options.get("dataMappingViewInsert"):
        crud.append("CREATE")
    if options.get("dataMappingViewUpdate"):
        crud.append("UPDATE")
    if options.get("dataMappingViewDelete"):
        crud.append("DELETE")
    if "UPDATE" not in crud:
        for field in obj["fields"]:
            ref_options = (field["reference"] or {}).get("options") or {}
            if (ref_options.get("dataMappingViewInsert")
                    or ref_options.get("dataMappingViewUpdate")
                    or ref_options.get("dataMappingViewDelete")):
                crud.append("UPDATE")
                break
    return crud


def _fields_document(obj: dict) -> list:
    """The fields as data_mapping_fields_with_references lists them: the
    top level, then each level below a reference, with lev and the
    reference document."""
    by_reference = {}
    for field in obj["fields"]:
        by_reference.setdefault(field["parent_reference_id"], []).append(field)
    rows = []
    level = [(field, 1) for field in by_reference.get(None, [])]
    while level:
        below = []
        for field, lev in level:
            ref = field["reference"]
            rows.append({
                "caption": "  " * (lev - 1) + "- " + field["name"],
                "lev": lev,
                "position": field["position"],
                "id": field["id"],
                "represents_reference_id": ref["id"] if ref else None,
                "parent_reference_id": field["parent_reference_id"],
                "data_mapping_id": field["data_mapping_id"],
                "name": field["name"],
                "db_column": field["db_column"],
                "enabled": field["enabled"],
                "allow_filtering": field["allow_filtering"],
                "allow_sorting": field["allow_sorting"],
                "no_check": field["no_check"],
                "no_update": field["no_update"],
                "options": field["options"],
                "sdk_options": field["sdk_options"],
                "data_mapping_reference": None if ref is None else {
                    key: value for key, value in ref.items() if key != "id"
                },
            })
            if ref:
                below.extend((child, lev + 1) for child in by_reference.get(ref["id"], []))
        level = below
    return rows


def _enabled(value) -> bool:
    """A REST item is in the SDK when ENABLED (the default); DISABLED and
    PRIVATE ones are not, as sdk_service_data takes enabled = 1 only."""
    if value is None or value is True:
        return True
    return isinstance(value, str) and value.upper() == "ENABLED"


def service_document(service: dict, catalog: Catalog, seed: str) -> tuple:
    """The service document sdk_service_data returns, for a REST service of
    the outline.

    Args:
        service: The outline's service (rest.services[]).
        catalog: The project's database objects.
        seed: What makes the made-up ids of this project and service.

    Returns:
        (service_res, warnings).
    """
    counter = [0]

    def new_id() -> str:
        counter[0] += 1
        return str(uuid.uuid5(_ID_NAMESPACE, f"{seed}\n{counter[0]}"))

    warnings = []
    service_path = service.get("path") or ""
    url_context_root = service.get("request_path") or service_path.partition("@")[2] or service_path
    document = {
        "id": new_id(),
        "url_context_root": url_context_root,
        "name": url_context_root.lstrip("/"),
        "enabled": True,
        "published": bool(service.get("published")),
        "options": service.get("options"),
        "auth_path": service.get("auth_path") or "/authentication",
        "auth_completed_url_validation": service.get("auth_validation"),
        "rest_schemas": [],
    }
    for schema in service.get("schemas", []):
        if not _enabled(schema.get("enabled")):
            continue
        schema_doc = {
            "id": new_id(),
            "name": schema.get("schema"),
            "schema_type": "DATABASE_SCHEMA",
            "request_path": schema.get("path"),
            "requires_auth": bool(schema.get("requires_auth")),
            "internal": False,
            "options": schema.get("options"),
            "rest_objects": [],
        }
        used_names = set()
        for item in schema.get("objects", []):
            if not _enabled(item.get("enabled")):
                continue
            full_path = f"{service_path}{schema.get('path')}{item.get('path')}"
            try:
                schema_doc["rest_objects"].append(
                    _object_document(item, schema, catalog, new_id, used_names, full_path,
                                     warnings)
                )
            except Refused as e:
                warnings.append(f"REST object {full_path} is left out: {e}")
        document["rest_schemas"].append(schema_doc)
    return document, warnings


def _object_document(item: dict, schema: dict, catalog: Catalog, new_id, used_names: set,
                     full_path: str, warnings: list) -> dict:
    kind = item.get("kind")
    db_schema = item.get("object_schema") or schema.get("schema")
    name = item.get("object_name")
    if db_schema is None or not catalog.in_schema(db_schema):
        raise Refused(
            f"its database object `{db_schema}`.`{name}` is not in the project's schema "
            f"`{catalog.schema_name}`."
        )
    builder = _Builder(catalog, db_schema, name, new_id)
    if kind == "view":
        object_type = catalog.object_type(db_schema, name)
        if object_type is None:
            raise Refused(f"The table or view `{db_schema}`.`{name}` does not exist.")
        if object_type == "VIEW":
            warnings.append(
                f"REST object {full_path}: the columns of the view `{name}` are not known "
                "without a server; its fields are typed by @DATATYPE, else as text."
            )
        objects = [builder.view_object(item.get("class"), item.get("crud"), item.get("fields"))]
    else:
        object_type = kind.upper()
        routine = catalog.routine(kind, db_schema, name)
        if routine is None and not item.get("force"):
            raise Refused(f"The {kind} `{db_schema}`.`{name}` does not exist.")
        objects = builder.routine_objects(
            kind, routine, item.get("parameters"), item.get("results") or [],
            bool(item.get("force")),
        )
    assign_object_names(objects, full_path, kind != "view", used_names)

    rest_object_id = new_id()
    return {
        "id": rest_object_id,
        "name": name,
        "request_path": item.get("path"),
        "internal": False,
        "object_type": object_type,
        "crud_operations": ",".join(calculate_crud_operations(object_type, objects)),
        "format": (item.get("format") or "FEED").upper(),
        "requires_auth": True if item.get("requires_auth") is None else bool(item["requires_auth"]),
        "options": item.get("options"),
        "data_mappings": [
            {
                "id": obj["id"],
                "rest_object_id": rest_object_id,
                "name": obj["name"],
                "kind": obj["kind"],
                "position": obj["position"],
                "row_ownership_field_id": obj["row_ownership_field_id"],
                "options": obj["options"],
                "sdk_options": obj["sdk_options"],
                "fields": _fields_document(obj),
            }
            for obj in objects
        ],
    }
