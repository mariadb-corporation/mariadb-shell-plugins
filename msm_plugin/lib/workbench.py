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

"""MySQL Workbench models (.mwb): read, and turned into MSM SQL and diagrams.

A .mwb file is a zip archive. ``document.mwb.xml`` holds the model as GRT
XML: every object a ``<value type="object" struct-name=... id=...>`` with
its members keyed by ``key``, lists and dicts as values of their own, and a
reference to another object as a ``<link>`` holding its id. ``@db/data.db``
is an SQLite database with a table per model table that has rows to insert,
named by the table's id, its columns named by the columns' ids. Scripts
attached to the model are in ``@scripts/`` or ``@sqlscripts/``, the pictures of
its image figures in ``@images/``.

This module has no file writes and no shell imports: it reads a model and
writes the CREATE statements of one of its schemas, MariaDB SQL, grouped the
way an MSM development script keeps them, and its EER diagrams as
msm-diagram/1 documents.
"""

import base64
import os
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Optional

from msm_plugin.lib import diagrams
from msm_plugin.lib import statements as statements_lib

DOCUMENT = "document.mwb.xml"
DATA = "@db/data.db"
# Where the archive keeps the scripts attached to the model (either folder,
# by the Workbench version) and the pictures of its image figures.
SCRIPTS = ("@scripts/", "@sqlscripts/")
IMAGES = "@images/"
# An image figure's picture goes into the diagram document as a data URL
# (every msm call parses the document again, and the VIEW is deployed), so a
# larger one, or one past what a diagram's pictures may take together, is
# reported instead of embedded.
MAX_IMAGE_SIZE = 512 * 1024
MAX_DIAGRAM_IMAGES_SIZE = 4 * 1024 * 1024
_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".svg": "image/svg+xml", ".bmp": "image/bmp", ".webp": "image/webp",
}

# A model larger than this is not read (the MRS model's XML is about 1 MB).
_MAX_DOCUMENT_SIZE = 256 * 1024 * 1024

_SIMPLE_TYPE_PREFIX = "com.mysql.rdbms.mysql.datatype."
# Types whose values are written unquoted in an INSERT.
_NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "MEDIUMINT", "INT", "INTEGER", "BIGINT", "DECIMAL",
    "NUMERIC", "FLOAT", "DOUBLE", "REAL", "BIT", "BOOL", "BOOLEAN",
}
# Workbench marks a cell value that is an SQL expression with this prefix.
_FUNCTION_PREFIX = "\\func "
_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\Z")


# -----------------------------------------------------------------------------
# Reading the GRT XML


class _Link(str):
    """The id of an object another one refers to."""


def _parse_value(element, objects: dict):
    if element.tag == "link":
        return _Link((element.text or "").strip())
    kind = element.get("type")
    if kind == "object":
        value = {"_struct": element.get("struct-name"), "_id": element.get("id")}
        if value["_id"]:
            objects[value["_id"]] = value
        for child in element:
            value[child.get("key")] = _parse_value(child, objects)
        return value
    if kind == "list":
        return [_parse_value(child, objects) for child in element]
    if kind == "dict":
        return {child.get("key"): _parse_value(child, objects) for child in element}
    text = element.text or ""
    if kind == "int":
        return int(text or 0)
    if kind == "real":
        return float(text or 0)
    return text


class _Objects:
    """The objects of a model by their id, so a link can be followed."""

    def __init__(self, objects: dict):
        self._objects = objects

    def get(self, value) -> Optional[dict]:
        """The object a member refers to: a link followed, an object as it is."""
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value:
            return None
        found = self._objects.get(value)
        if found is None:
            bare = value.strip("{}")
            found = self._objects.get(bare) or self._objects.get("{" + bare + "}")
        return found


def _follow_list_links(value, objects: _Objects) -> None:
    """Puts the object every list member links to in the member's place.

    GRT writes an object in full where it first occurs and as a link
    everywhere else, its keys in alphabetical order: a routine in a routine
    group is an object under routineGroups and a link in its schema's
    routines. The code reads lists as objects, so the links are followed
    once, here; one to nothing is dropped. An object put in place is walked
    where it is owned, not again.
    """
    if isinstance(value, dict):
        for member in value.values():
            _follow_list_links(member, objects)
    elif isinstance(value, list):
        kept = []
        for item in value:
            if isinstance(item, _Link):
                item = objects.get(item)
                if item is None:
                    continue
            else:
                _follow_list_links(item, objects)
            kept.append(item)
        value[:] = kept


@dataclass
class Model:
    """A Workbench model as read from its file.

    Attributes:
        document: The workbench.Document object.
        objects: Its objects by id.
        data: The rows to insert, by table id: {"columns": [column ids],
            "rows": [[value or None]]}.
        scripts: The names of the scripts attached to the model.
        images: The size of each picture under @images/ by its archive name;
            the import reads one from path when a figure shows it.
        path: The model file.
    """

    document: dict
    objects: _Objects
    data: dict = field(default_factory=dict)
    scripts: list = field(default_factory=list)
    images: dict = field(default_factory=dict)
    path: str = ""

    @property
    def physical_model(self) -> dict:
        models = self.document.get("physicalModels") or []
        if not models:
            raise ValueError("The Workbench model has no physical model.")
        return models[0]

    @property
    def catalog(self) -> dict:
        return self.physical_model["catalog"]

    def schemas(self) -> list:
        return self.catalog.get("schemata", [])

    def schema(self, name: str = None) -> dict:
        """The schema of that name, or the model's only one.

        Raises:
            ValueError: For a name the model does not have, or for none when it
                has several.
        """
        schemas = self.schemas()
        names = [schema.get("name") for schema in schemas]
        if name is not None:
            for schema in schemas:
                if schema.get("name", "").lower() == name.lower():
                    return schema
            raise ValueError(
                f"The Workbench model has no schema `{name}`, it has "
                + ", ".join(f"`{n}`" for n in names) + "."
            )
        if len(schemas) != 1:
            raise ValueError(
                "The Workbench model has "
                + (f"the schemas {', '.join(f'`{n}`' for n in names)}" if schemas else "no schema")
                + "; name the one to import."
            )
        return schemas[0]


def _read_data(archive: zipfile.ZipFile) -> dict:
    """The rows of @db/data.db by table id; a model without the file has none."""
    if DATA not in archive.namelist():
        return {}
    data = {}
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "data.db")
        with archive.open(DATA) as source, open(path, "wb") as target:
            target.write(source.read())
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            for table in tables:
                if not table.startswith("{"):
                    continue
                quoted = '"' + table.replace('"', '""') + '"'
                cursor = connection.execute(f"SELECT * FROM {quoted}")
                columns = [description[0] for description in cursor.description]
                rows = [list(row) for row in cursor.fetchall()]
                if rows:
                    data[table] = {"columns": columns, "rows": rows}
        finally:
            connection.close()
    return data


def read_model(path: str) -> Model:
    """Reads a Workbench model file.

    Raises:
        ValueError: For a file that is not a Workbench model.
    """
    # Absolute: diagram_documents opens the file again for the images.
    path = os.path.abspath(os.path.expanduser(path))
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ValueError(f"`{path}` is not a MySQL Workbench model (.mwb) file: {e}")
    with archive:
        try:
            info = archive.getinfo(DOCUMENT)
        except KeyError:
            raise ValueError(f"`{path}` is not a MySQL Workbench model: it has no {DOCUMENT}.")
        if info.file_size > _MAX_DOCUMENT_SIZE:
            raise ValueError(f"The model's {DOCUMENT} is larger than msm reads.")
        try:
            root = ElementTree.fromstring(archive.read(DOCUMENT))
        except ElementTree.ParseError as e:
            raise ValueError(f"The model's {DOCUMENT} could not be read: {e}")
        if root.get("document_type") != "MySQL Workbench Model" or len(root) == 0:
            raise ValueError(f"`{path}` does not hold a MySQL Workbench model.")
        objects = {}
        document = _parse_value(root[0], objects)
        objects = _Objects(objects)
        _follow_list_links(document, objects)
        data = _read_data(archive)
        scripts, images = [], {}
        for info in archive.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            if name.startswith(SCRIPTS):
                scripts.append(name.split("/", 1)[1])
            elif name.startswith(IMAGES):
                images[name] = info.file_size
    return Model(document, objects, data, sorted(scripts), images, path)


# -----------------------------------------------------------------------------
# SQL


quote_identifier = statements_lib.quote_identifier


def _simple_type_name(model: Model, link) -> str:
    """The SQL name of a simple type: TIMESTAMP for ...datatype.timestamp_f."""
    simple = model.objects.get(link)
    name = simple.get("name") if simple else str(link or "")
    if name.startswith(_SIMPLE_TYPE_PREFIX):
        name = name[len(_SIMPLE_TYPE_PREFIX):]
    if name.endswith("_f"):
        name = name[:-2]
    return name.upper()


def column_type(model: Model, column: dict) -> str:
    """A column's type as SQL, as Workbench forward engineering writes it.

    A user-defined type is replaced by its SQL definition (its name where it
    has none).
    """
    user_type = model.objects.get(column.get("userType"))
    if user_type is not None:
        definition = (user_type.get("sqlDefinition") or user_type.get("name") or "").strip()
        flags = (user_type.get("flags") or "").strip()
        return f"{definition} {flags}".strip()

    name = _simple_type_name(model, column.get("simpleType"))
    length = column.get("length", -1)
    precision = column.get("precision", -1)
    scale = column.get("scale", -1)
    params = (column.get("datatypeExplicitParams") or "").strip()
    if params:
        text = name + params
    elif isinstance(length, int) and length >= 0:
        text = f"{name}({length})"
    elif isinstance(precision, int) and precision >= 0:
        text = f"{name}({precision}, {scale})" if scale >= 0 else f"{name}({precision})"
    else:
        text = name
    flags = [flag for flag in column.get("flags", []) if flag]
    return " ".join([text] + flags)


def _column_sql(model: Model, column: dict) -> str:
    parts = [quote_identifier(column["name"]), column_type(model, column)]
    if column.get("characterSetName"):
        parts.append(f"CHARACTER SET {column['characterSetName']}")
    if column.get("collationName"):
        parts.append(f"COLLATE {column['collationName']}")
    if column.get("generated"):
        storage = (column.get("generatedStorage") or "VIRTUAL").upper()
        parts.append(f"GENERATED ALWAYS AS ({column.get('expression', '')}) {storage}")
    else:
        parts.append("NOT NULL" if column.get("isNotNull") else "NULL")
        if column.get("defaultValueIsNull"):
            parts.append("DEFAULT NULL")
        elif (column.get("defaultValue") or "") != "":
            parts.append(f"DEFAULT {column['defaultValue']}")
        if column.get("autoIncrement"):
            parts.append("AUTO_INCREMENT")
    if column.get("comment"):
        parts.append(f"COMMENT {diagrams.sql_string_literal(column['comment'])}")
    return " ".join(parts)


def _index_sql(model: Model, index: dict) -> str:
    kind = (index.get("indexType") or "INDEX").upper()
    primary = kind == "PRIMARY" or bool(index.get("isPrimary"))
    parts = []
    for item in index.get("columns", []):
        column = model.objects.get(item.get("referencedColumn"))
        if column is not None:
            part = quote_identifier(column["name"])
        elif item.get("expression"):
            part = f"({item['expression']})"
        else:
            continue
        if item.get("columnLength", 0) > 0:
            part += f"({item['columnLength']})"
        if item.get("descend"):
            part += " DESC"
        elif not primary:
            part += " ASC"
        parts.append(part)
    columns = "(" + ", ".join(parts) + ")"
    if primary:
        text = f"PRIMARY KEY {columns}"
    else:
        prefix = {"UNIQUE": "UNIQUE INDEX", "FULLTEXT": "FULLTEXT INDEX",
                  "SPATIAL": "SPATIAL INDEX"}.get(kind, "INDEX")
        text = f"{prefix} {quote_identifier(index['name'])} {columns}"
    if index.get("algorithm"):
        text += f" USING {index['algorithm']}"
    if index.get("comment"):
        text += f" COMMENT {diagrams.sql_string_literal(index['comment'])}"
    if index.get("visible", 1) == 0:
        text += " IGNORED"
    return text


def _rule(rule: str) -> str:
    return (rule or "").strip().upper()


def foreign_key_sql(model: Model, foreign_key: dict) -> str:
    """A foreign key as the CONSTRAINT clause of a CREATE or ALTER TABLE."""
    columns = [model.objects.get(c) for c in foreign_key.get("columns", [])]
    referenced = [model.objects.get(c) for c in foreign_key.get("referencedColumns", [])]
    table = model.objects.get(foreign_key.get("referencedTable"))
    text = (
        f"CONSTRAINT {quote_identifier(foreign_key['name'])}\n"
        f"    FOREIGN KEY ({', '.join(quote_identifier(c['name']) for c in columns if c)})\n"
        f"    REFERENCES {quote_identifier(table['name'])} "
        f"({', '.join(quote_identifier(c['name']) for c in referenced if c)})"
    )
    for clause, rule in (("ON DELETE", foreign_key.get("deleteRule")),
                         ("ON UPDATE", foreign_key.get("updateRule"))):
        rule = _rule(rule)
        if rule:
            text += f"\n    {clause} {rule}"
    return text


def _check_sql(check: dict) -> str:
    name = check.get("name")
    expression = check.get("expression") or check.get("sqlDefinition") or ""
    prefix = f"CONSTRAINT {quote_identifier(name)} " if name else ""
    return f"{prefix}CHECK ({expression})"


def _table_options(table: dict) -> str:
    options = []
    if table.get("tableEngine"):
        options.append(f"ENGINE = {table['tableEngine']}")
    if table.get("defaultCharacterSetName"):
        options.append(f"DEFAULT CHARACTER SET = {table['defaultCharacterSetName']}")
    if table.get("defaultCollationName"):
        options.append(f"COLLATE = {table['defaultCollationName']}")
    if table.get("rowFormat"):
        options.append(f"ROW_FORMAT = {table['rowFormat']}")
    if table.get("comment"):
        options.append(f"COMMENT = {diagrams.sql_string_literal(table['comment'])}")
    return "\n".join(options)


def _banner(kind: str, name: str) -> str:
    rule = "-- " + "-" * 77
    return f"{rule}\n-- {kind} `{name}`\n{rule}\n"


def table_sql(model: Model, table: dict, foreign_keys: list) -> str:
    """The CREATE TABLE statement of a table, with these of its foreign keys."""
    members = [_column_sql(model, column) for column in table.get("columns", [])]
    members += [_index_sql(model, index) for index in table.get("indices", [])]
    members += [foreign_key_sql(model, foreign_key) for foreign_key in foreign_keys]
    members += [_check_sql(check) for check in table.get("checks", []) or []]
    for column in table.get("columns", []):
        members += [_check_sql(check) for check in column.get("checks", []) or []]
    body = ",\n".join("  " + member.replace("\n", "\n  ") for member in members)
    options = _table_options(table)
    return (
        _banner("TABLE", table["name"])
        + f"CREATE TABLE IF NOT EXISTS {quote_identifier(table['name'])} (\n{body}\n)"
        + (f"\n{options}" if options else "")
        + ";"
    )


def _ordered_tables(model: Model, tables: list) -> tuple:
    """The tables, every one after the tables its foreign keys refer to.

    Returns:
        The tables in order, and the foreign keys that close a cycle, as
        (table, foreign key) pairs, to be added once all tables exist.
    """
    ids = {table["_id"] for table in tables}
    placed, order, later = set(), [], []
    visiting = set()

    def visit(table):
        if table["_id"] in placed:
            return
        visiting.add(table["_id"])
        for foreign_key in table.get("foreignKeys", []):
            parent = model.objects.get(foreign_key.get("referencedTable"))
            if parent is None or parent["_id"] not in ids or parent["_id"] == table["_id"]:
                continue
            if parent["_id"] in visiting:
                later.append((table, foreign_key))
            else:
                visit(parent)
        visiting.discard(table["_id"])
        placed.add(table["_id"])
        order.append(table)

    for table in tables:
        visit(table)
    return order, later


def _value_sql(value, numeric: bool) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    if text.startswith(_FUNCTION_PREFIX):
        return text[len(_FUNCTION_PREFIX):]
    if numeric and _NUMBER.match(text):
        return text
    return diagrams.sql_string_literal(text)


def inserts_sql(model: Model, table: dict) -> Optional[str]:
    """The INSERT of a table's rows in the model, or None without rows."""
    data = model.data.get(table["_id"])
    if not data:
        return None
    columns = {column["_id"]: column for column in table.get("columns", [])}
    picked = [
        (index, columns[column_id])
        for index, column_id in enumerate(data["columns"])
        if column_id in columns
    ]
    if not picked:
        return None
    numeric = [
        column_type(model, column).split("(")[0].split(" ")[0].upper() in _NUMERIC_TYPES
        for _, column in picked
    ]
    rows = [
        "(" + ", ".join(
            _value_sql(row[index], is_numeric) for (index, _), is_numeric in zip(picked, numeric)
        ) + ")"
        for row in data["rows"]
    ]
    return (
        f"INSERT INTO {quote_identifier(table['name'])} "
        f"({', '.join(quote_identifier(column['name']) for _, column in picked)})\n"
        "VALUES " + ",\n    ".join(rows) + ";"
    )


def _idempotent(kind: str, name: str, sql: str) -> str:
    """A CREATE statement of section 150, safe to run where the object exists."""
    statement = statements_lib.strip_statement(sql)
    if kind in ("VIEW", "EVENT"):
        statement = re.sub(
            r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?", "CREATE OR REPLACE ", statement,
            count=1, flags=re.IGNORECASE,
        )
        return f"{statement}%%"
    return f"DROP {kind} IF EXISTS {quote_identifier(name)}%%\n{statement}%%"


def _block(statements: list, title: str) -> Optional[str]:
    """Statements ending with %%, in a DELIMITER block of their own."""
    if not statements:
        return None
    return f"-- {title}\n\nDELIMITER %%\n\n" + "\n\n".join(statements) + "\n\nDELIMITER ;"


def _object_name(sql: str, fallback: str) -> str:
    match = re.search(
        r"\b(?:VIEW|PROCEDURE|FUNCTION|TRIGGER|EVENT)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:`(?:[^`]|``)+`\s*\.\s*|\w+\s*\.\s*)?(`(?:[^`]|``)+`|\w+)",
        statements_lib.strip_statement(sql), re.IGNORECASE,
    )
    if not match:
        return fallback
    name = match.group(1)
    return name[1:-1].replace("``", "`") if name.startswith("`") else name


def _privilege_target(model: Model, privilege: dict, schema_name: str) -> str:
    """What a GRANT is on: an object of the schema, or * for all of it (the
    script runs after USE of the schema, whatever name a deployment gives it,
    so `<schema>.*` of the model's schema is * as well)."""
    target = model.objects.get(privilege.get("databaseObject"))
    if target is not None and target.get("name"):
        return quote_identifier(target["name"])
    name = privilege.get("databaseObjectName") or ""
    if name in ("**.*", "*.*", "*", ""):
        return "*"
    if name.endswith(".*") and name[:-2].strip("`").lower() == schema_name.lower():
        return "*"
    return name if "." in name else quote_identifier(name)


@dataclass
class Imported:
    """The SQL of a schema, grouped as an MSM development script keeps it.

    Each member is the SQL of one part, or None where it has none.
    """

    tables: Optional[str] = None
    table_additions: Optional[str] = None
    inserts: Optional[str] = None
    views: Optional[str] = None
    routines: Optional[str] = None
    triggers: Optional[str] = None
    events: Optional[str] = None
    roles: Optional[str] = None
    counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def schema_sql(model: Model, schema: dict) -> Imported:
    """Writes the CREATE statements of a schema of the model.

    Tables go in an order that lets each one refer to the tables before it;
    a foreign key that closes a cycle is added by an ALTER TABLE after all of
    them. Views, routines, triggers and events are written to be run again
    (CREATE OR REPLACE, or DROP ... IF EXISTS first), in DELIMITER %% blocks
    as MSM Section 150 has them. Roles get CREATE ROLE IF NOT EXISTS and
    their GRANTs on the schema's objects.
    """
    result = Imported()
    tables = [table for table in schema.get("tables", []) if not table.get("isStub")]
    table_ids = {table["_id"] for table in tables}
    order, later = _ordered_tables(model, tables)
    later_ids = {id(foreign_key) for _, foreign_key in later}

    statements = []
    for table in order:
        inline = []
        for foreign_key in table.get("foreignKeys", []):
            parent = model.objects.get(foreign_key.get("referencedTable"))
            if parent is None:
                result.warnings.append(
                    f"The foreign key `{foreign_key.get('name')}` of `{table['name']}` refers "
                    "to a table the model does not have; it was left out."
                )
            elif parent["_id"] not in table_ids:
                # A stub, or a table of another schema: the script creates
                # one schema, and a REFERENCES to a table it does not have
                # could not be deployed.
                result.warnings.append(
                    f"The foreign key `{foreign_key.get('name')}` of `{table['name']}` refers "
                    f"to `{parent.get('name')}`, which is not a table of the schema; it was "
                    "left out."
                )
            elif id(foreign_key) not in later_ids:
                inline.append(foreign_key)
        if table.get("partitionType"):
            result.warnings.append(
                f"The partitioning of `{table['name']}` was not imported."
            )
        statements.append(table_sql(model, table, inline))
    result.tables = "\n\n".join(statements) or None
    if later:
        result.table_additions = "\n\n".join(
            f"ALTER TABLE {quote_identifier(table['name'])}\n  ADD "
            + foreign_key_sql(model, foreign_key).replace("\n", "\n  ") + ";"
            for table, foreign_key in later
        )

    inserts = [sql for sql in (inserts_sql(model, table) for table in order) if sql]
    result.inserts = "\n\n".join(inserts) or None
    stale = [table_id for table_id in model.data if table_id not in table_ids]
    if stale and len(model.schemas()) == 1:
        result.warnings.append(
            f"{len(stale)} table(s) of rows in the model's data belong to no table of the "
            "model; they were left out."
        )

    grouped = {"VIEW": [], "PROCEDURE": [], "TRIGGER": [], "EVENT": []}

    def add(sql: str, fallback_kind: str, fallback_name: str):
        if not (sql or "").strip():
            result.warnings.append(f"The {fallback_kind.lower()} `{fallback_name}` has no SQL.")
            return
        kind = statements_lib.statement_kind(sql)
        if kind is None:
            result.warnings.append(
                f"The {fallback_kind.lower()} `{fallback_name}` is not a CREATE statement "
                "msm can place; it was left out."
            )
            return
        name = _object_name(sql, fallback_name)
        grouped["PROCEDURE" if kind == "FUNCTION" else kind].append(
            _banner(kind, name) + _idempotent(kind, name, sql)
        )

    for view in schema.get("views", []):
        add(view.get("sqlDefinition"), "VIEW", view.get("name", ""))
    for routine in schema.get("routines", []):
        add(routine.get("sqlDefinition"), "ROUTINE", routine.get("name", ""))
    for table in order:
        for trigger in table.get("triggers", []):
            add(trigger.get("sqlDefinition"), "TRIGGER", trigger.get("name", ""))
    for event in schema.get("events", []):
        add(event.get("sqlDefinition"), "EVENT", event.get("name", ""))

    result.views = _block(grouped["VIEW"], "VIEWs")
    result.routines = _block(grouped["PROCEDURE"], "PROCEDUREs and FUNCTIONs")
    result.triggers = _block(grouped["TRIGGER"], "TRIGGERs")
    result.events = _block(grouped["EVENT"], "EVENTs")

    roles = model.catalog.get("roles", [])
    role_statements = []
    for role in roles:
        lines = [f"CREATE ROLE IF NOT EXISTS {quote_identifier(role['name'])};"]
        for privilege in role.get("privileges", []):
            names = [p for p in privilege.get("privileges", []) if p]
            if not names:
                continue
            target = _privilege_target(model, privilege, schema.get("name", ""))
            lines.append(
                f"GRANT {', '.join(names)} ON {target} TO {quote_identifier(role['name'])};"
            )
        for child in role.get("childRoles", []):
            child_role = model.objects.get(child)
            if child_role is not None:
                lines.append(
                    f"GRANT {quote_identifier(role['name'])} TO "
                    f"{quote_identifier(child_role['name'])};"
                )
        role_statements.append(_banner("ROLE", role["name"]) + "\n".join(lines))
    result.roles = "\n\n".join(role_statements) or None

    result.counts = {
        "tables": len(order),
        "views": len(grouped["VIEW"]),
        "routines": len(grouped["PROCEDURE"]),
        "triggers": len(grouped["TRIGGER"]),
        "events": len(grouped["EVENT"]),
        "roles": len(roles),
        "inserted_rows": sum(len(model.data[t["_id"]]["rows"]) for t in order if t["_id"] in model.data),
    }
    return result


# -----------------------------------------------------------------------------
# Diagrams


def _figure_base(figure: dict, kind: str, name: str, color: str) -> dict:
    """What every figure keeps: its id, type, name, place, size and colour,
    and the flags Workbench has. The caller adds the layer."""
    result = {
        "id": figure["_id"],
        "type": kind,
        "name": name,
        "left": float(figure.get("left", 0)),
        "top": float(figure.get("top", 0)),
        "width": float(figure.get("width", 0)),
        "height": float(figure.get("height", 0)),
        "color": figure.get("color") or color,
    }
    for key in ("expanded", "columnsExpanded", "indicesExpanded", "foreignKeysExpanded",
                "triggersExpanded", "manualSizing", "keepAspectRatio"):
        if key in figure:
            result[key] = bool(figure[key])
    return result


def _image_data_url(name: str, data: bytes) -> str:
    extension = os.path.splitext(name)[1].lower()
    mime = _IMAGE_TYPES.get(extension, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _convert_figure(model: Model, figure: dict, schema_ids: dict, archive, budget: int):
    """A Workbench figure as an msm-diagram/1 figure (Workbench's struct
    names are msm's types); None for one msm does not keep: another
    schema's object or an unknown kind; or the reason, a str, for an image
    that is not embedded.

    Args:
        archive: The model file, open, for the images; None without any.
        budget: What the diagram's pictures may still take, in bytes.
    """
    struct = (figure.get("_struct") or "").rsplit(".", 1)[-1]
    kind = diagrams.FIGURE_KINDS.get(struct)
    if kind is not None and not kind.many:
        item = model.objects.get(figure.get(kind.key))
        if item is None or item["_id"] not in schema_ids[kind.objects]:
            return None
        result = _figure_base(figure, struct, item["name"], kind.color)
        result[kind.key] = item["name"]
        return result
    if kind is not None:
        group = model.objects.get(figure.get("routineGroup"))
        if group is None:
            return None
        routines = [
            routine["name"]
            for routine in (model.objects.get(r) for r in group.get("routines", []))
            if routine is not None and routine["_id"] in schema_ids[kind.objects]
        ]
        if not routines:
            return None
        result = _figure_base(figure, struct, group.get("name") or diagrams.ROUTINE_GROUP_NAME,
                              kind.color)
        result[kind.key] = routines
        return result
    if struct == diagrams.NOTE_FIGURE:
        result = _figure_base(figure, struct, figure.get("name") or "",
                              diagrams.NOTE_FIGURE_COLOR)
        result["text"] = figure.get("text") or ""
        for key in ("font", "textColor"):
            if figure.get(key):
                result[key] = figure[key]
        return result
    if struct == diagrams.IMAGE_FIGURE:
        file_name = figure.get("filename") or ""
        size = model.images.get(file_name)
        if size is None or archive is None:
            return "the model file does not hold it."
        if size > MAX_IMAGE_SIZE:
            return f"it is larger than {MAX_IMAGE_SIZE // 1024} KB."
        if size > budget:
            return (
                f"the diagram's pictures would be larger than "
                f"{MAX_DIAGRAM_IMAGES_SIZE // (1024 * 1024)} MB together."
            )
        result = _figure_base(figure, struct, figure.get("name") or "", "")
        result["image"] = _image_data_url(file_name, archive.read(file_name))
        return result
    return None


def diagram_documents(model: Model, schema: dict) -> tuple:
    """The model's EER diagrams as msm-diagram/1 documents.

    Table, view and routine group figures of the schema's objects are kept,
    with notes and images (each picture embedded as a data URL); other
    schemas' objects and other kinds are left out. Figures on a layer keep
    positions relative to it, as Workbench has them.

    Returns:
        The documents, and warnings on what was left out.
    """
    schema_ids = {
        kind: {item["_id"] for item in schema.get(kind, [])}
        for kind in dict.fromkeys(each.objects for each in diagrams.FIGURE_KINDS.values())
    }
    # The model file, open while the figures' pictures are read.
    opened = nullcontext()
    if model.images and model.path:
        try:
            opened = zipfile.ZipFile(model.path)
        except (OSError, zipfile.BadZipFile) as e:
            raise ValueError(f"`{model.path}` cannot be read for the diagrams' images: {e}")
    with opened as archive:
        return _diagram_documents(model, schema_ids, archive)


def _diagram_documents(model: Model, schema_ids: dict, archive) -> tuple:
    documents, warnings = [], []
    for diagram in model.physical_model.get("diagrams", []):
        diagram_name = diagram.get("name") or "Diagram"
        root_layer = model.objects.get(diagram.get("rootLayer"))
        figures, skipped = [], 0
        budget = MAX_DIAGRAM_IMAGES_SIZE
        for figure in diagram.get("figures", []):
            figure = model.objects.get(figure)
            if figure is None:
                continue
            converted = _convert_figure(model, figure, schema_ids, archive, budget)
            if isinstance(converted, str):
                warnings.append(
                    f"The image `{figure.get('filename')}` of the diagram `{diagram_name}` "
                    f"was left out: {converted}"
                )
                continue
            if converted is None:
                skipped += 1
                continue
            if converted["type"] == diagrams.IMAGE_FIGURE:
                budget -= model.images[figure["filename"]]
            layer = model.objects.get(figure.get("layer"))
            if layer is not None and (root_layer is None or layer["_id"] != root_layer["_id"]):
                converted["layer"] = layer["_id"]
            figures.append(converted)
        figure_ids = {figure["id"] for figure in figures}

        layers = []
        for layer in diagram.get("layers", []):
            layer = model.objects.get(layer)
            if layer is None:
                continue
            layers.append({
                "id": layer["_id"],
                "name": layer.get("name", ""),
                "left": float(layer.get("left", 0)),
                "top": float(layer.get("top", 0)),
                "width": float(layer.get("width", 0)),
                "height": float(layer.get("height", 0)),
                "color": layer.get("color") or "#F0F1FE",
                "description": layer.get("description", ""),
                "figures": [
                    f["_id"] for f in (model.objects.get(x) for x in layer.get("figures", []))
                    if f is not None and f["_id"] in figure_ids
                ],
            })

        connections = []
        for connection in diagram.get("connections", []):
            connection = model.objects.get(connection)
            if connection is None:
                continue
            foreign_key = model.objects.get(connection.get("foreignKey"))
            start = model.objects.get(connection.get("startFigure"))
            end = model.objects.get(connection.get("endFigure"))
            if foreign_key is None or start is None or end is None \
                    or start["_id"] not in figure_ids or end["_id"] not in figure_ids:
                continue
            owner = model.objects.get(foreign_key.get("owner"))
            connections.append({
                "id": connection["_id"],
                "foreignKey": foreign_key["name"],
                "table": owner["name"] if owner else "",
                "startFigure": start["_id"],
                "endFigure": end["_id"],
                "visible": bool(connection.get("visible", 1)),
                "caption": connection.get("caption", ""),
                "middleSegmentOffset": float(connection.get("middleSegmentOffset", 0)),
                "drawSplit": bool(connection.get("drawSplit", 0)),
            })

        document = {
            "format": diagrams.DIAGRAM_FORMAT,
            "name": diagram_name,
            "width": float(diagram.get("width", 0)),
            "height": float(diagram.get("height", 0)),
            "zoom": float(diagram.get("zoom", 1)),
            "layers": layers,
            "figures": figures,
            "connections": connections,
        }
        if diagram.get("description"):
            document["description"] = diagram["description"]
        if skipped:
            warnings.append(
                f"{skipped} figure(s) of the diagram `{document['name']}` that show no table, "
                "view or routine of the schema were left out."
            )
        documents.append(document)

    # Names have to be unique: they are the rows' names.
    used = {diagrams.SCHEMA_ROW_NAME}
    for document in documents:
        name = document["name"]
        number = 1
        while document["name"] in used:
            number += 1
            document["name"] = f"{name} ({number})"
        used.add(document["name"])
    return documents, warnings
