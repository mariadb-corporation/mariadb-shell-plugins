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

import json
import os
import shutil
import socket

import mysqlsh
from mrs_plugin import lib


def add_test_schema(session, service_id, schema_name, request_path=None, **kwargs):
    """Adds a REST schema the way the former mrs.add.schema() did."""
    with lib.core.MrsDbTransaction(session):
        return lib.schemas.add_schema(
            session=session,
            schema_name=schema_name,
            service_id=lib.core.id_to_uuid(service_id, "service_id"),
            request_path=request_path or f"/{schema_name}",
            requires_auth=kwargs.get("requires_auth"),
            enabled=kwargs.get("enabled", 1),
            items_per_page=kwargs.get("items_per_page"),
            comments=kwargs.get("comments"),
            options=kwargs.get("options"),
            metadata=kwargs.get("metadata"),
        )


def add_test_rest_object(session, **kwargs):
    """Adds a REST object with its grants the way the former
    mrs.add.dbObject() did."""
    kwargs.pop("session", None)
    with lib.core.MrsDbTransaction(session):
        rest_object_id, grants = lib.rest_objects.add_rest_object(
            session=session,
            schema_id=lib.core.id_to_uuid(kwargs["schema_id"], "schema_id"),
            rest_object_name=kwargs["rest_object_name"],
            request_path=kwargs["request_path"],
            enabled=kwargs.get("enabled", True),
            rest_object_type=kwargs["rest_object_type"],
            items_per_page=kwargs.get("items_per_page"),
            requires_auth=kwargs.get("requires_auth"),
            row_user_ownership_enforced=kwargs.get("row_user_ownership_enforced"),
            row_user_ownership_column=kwargs.get("row_user_ownership_column"),
            crud_operation_format=kwargs.get("crud_operation_format"),
            comments=kwargs.get("comments") or "",
            media_type=kwargs.get("media_type"),
            auto_detect_media_type=kwargs.get("auto_detect_media_type", True),
            auth_stored_procedure=kwargs.get("auth_stored_procedure"),
            options=kwargs.get("options"),
            metadata=kwargs.get("metadata"),
            data_mappings=kwargs.get("data_mappings"),
        )
        for grant in grants:
            lib.core.MrsDbExec(grant).exec(session)
    return rest_object_id


def get_default_rest_object_init(
    session,
    schema_id,
    name=None,
    request_path=None,
    rest_object_type=None,
    object_options=None,
):
    data_mapping_id = lib.core.get_sequence_id(session)
    return {
        "schema_id": schema_id,
        "rest_object_name": name or "ContactBasicInfo",
        "rest_object_type": rest_object_type or "VIEW",
        "request_path": request_path or "/view_contact_basic_info",
        "crud_operation_format": "FEED",
        "requires_auth": False,
        "items_per_page": 10,
        "comments": "Object that will be removed",
        "enabled": True,
        "media_type": "application/json",
        "auto_detect_media_type": True,
        "auth_stored_procedure": None,
        "options": {"aaa": "val aaa", "bbb": "val bbb"},
        "metadata": None,
        "data_mappings": [
            {
                "id": data_mapping_id,
                "name": "MyServicePhoneBookContactsWithEmail",
                "position": 0,
                "kind": "RESULT",
                "comments": "Comment for object",
                "options": object_options
                or {
                    "dataMappingViewInsert": True,
                    "dataMappingViewUpdate": True,
                    "dataMappingViewDelete": True,
                },
                "sdk_options": {
                    "option1": "value 1",
                    "option2": "value 2",
                },
                "fields": [
                    {
                        "id": lib.core.get_sequence_id(session),
                        "data_mapping_id": data_mapping_id,
                        "name": "id",
                        "position": 1,
                        "db_column": {
                            "comment": "",
                            "datatype": "int",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": True,
                            "is_unique": False,
                            "name": "id",
                            "not_null": True,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": False,
                        "no_check": False,
                        "no_update": False,
                        "options": None,
                    }
                ],
            }
        ],
    }


def rest_path(path):
    """A request path quoted for REST SQL."""
    return lib.core.quote_ident(path)


def drop_rest_rest_object(session, rest_object_id):
    """Drops a REST object with REST SQL, which also revokes its grants."""
    row = session.run_sql(
        lib.core.metadata_sql(
            session,
            """SELECT o.request_path, o.object_type, s.request_path, se.url_context_root
        FROM <metadata>.rest_object o
            JOIN <metadata>.rest_schema s ON s.id = o.rest_schema_id
            JOIN <metadata>.service se ON se.id = s.service_id
        WHERE o.id = ?""",
        ),
        [rest_object_id],
    ).fetch_one()
    if row:
        kind = "VIEW" if row[1] in ("TABLE", "VIEW") else row[1]
        session.run_sql(
            f"DROP REST {kind} {rest_path(row[0])} "
            f"FROM SERVICE {rest_path(row[3])} SCHEMA {rest_path(row[2])}"
        )


def drop_rest_schema(session, schema_id):
    row = session.run_sql(
        lib.core.metadata_sql(
            session,
            """SELECT s.request_path, se.url_context_root
        FROM <metadata>.rest_schema s
            JOIN <metadata>.service se ON se.id = s.service_id
        WHERE s.id = ?""",
        ),
        [schema_id],
    ).fetch_one()
    if row:
        session.run_sql(
            f"DROP REST SCHEMA {rest_path(row[0])} FROM SERVICE {rest_path(row[1])}"
        )


class SchemaCT(object):
    def __init__(self, session, service_id, schema_name, request_path, **kwargs):
        self._session = session
        self._schema_id = add_test_schema(
            session,
            service_id=service_id,
            schema_name=schema_name,
            request_path=request_path,
            requires_auth=False,
            items_per_page=25,
            **kwargs,
        )

    def __enter__(self):
        return self._schema_id

    def __exit__(self, type, value, traceback):
        drop_rest_schema(self._session, self._schema_id)

    @property
    def id(self):
        return self._schema_id


class ServiceCT(object):
    def __init__(self, session, url_context_root, **kwargs):
        self._session = session
        self._path = url_context_root
        comments = kwargs.get("comments")
        session.run_sql(
            f"CREATE REST SERVICE {rest_path(url_context_root)}"
            + (f" COMMENT {lib.core.squote_str(comments)}" if comments else "")
        )
        service = lib.services.get_service(session, url_context_root=url_context_root)
        assert service is not None
        self._service_id = service["id"]

    def __enter__(self):
        return self._service_id

    def __exit__(self, type, value, traceback):
        self._session.run_sql(f"DROP REST SERVICE IF EXISTS {rest_path(self._path)}")

    @property
    def id(self):
        return self._service_id


class RestObjectCT:
    def __init__(self, session, **kwargs) -> None:
        self._session = session
        self._rest_object_id = add_test_rest_object(session, **kwargs)

    def __enter__(self):
        return self._rest_object_id

    def __exit__(self, type, value, traceback):
        drop_rest_rest_object(self._session, self._rest_object_id)


def create_mrs_phonebook_schema(session, service_context_root, schema_name, temp_dir):
    """Creates the REST service, schema, content set and REST object of a
    phone book database for the tests."""
    service = lib.services.get_service(session, url_context_root=service_context_root)
    if not service:
        session.run_sql(
            f"CREATE REST SERVICE {rest_path(service_context_root)} COMMENT 'Test service'"
        )
        service = lib.services.get_service(
            session, url_context_root=service_context_root
        )
    assert service is not None, f"Unable to add the {service_context_root} service"

    schema_id = add_test_schema(
        session,
        service["id"],
        schema_name,
        requires_auth=False,
        items_per_page=20,
        comments="test schema",
    )

    content_set = lib.content_sets.get_content_set(
        session, service_id=service["id"], request_path="/test_content_set"
    )
    if not content_set:
        session.run_sql(
            f"CREATE REST CONTENT SET /test_content_set "
            f"ON SERVICE {rest_path(service_context_root)} "
            "COMMENT 'Content Set' OPTIONS {} AUTHENTICATION NOT REQUIRED"
        )
        for request_path, data in (
            ("/readme.txt", b"Line '1'\nLine \"2\"\nLine \\3\\"),
            ("/somebinaryfile.bin", b"\0\1\2\3\4\5\6\7"),
        ):
            session.run_sql(
                lib.content_sets.content_file_statement(
                    request_path,
                    service_context_root,
                    "/test_content_set",
                    data,
                    {},
                )
            )
        content_set = lib.content_sets.get_content_set(
            session, service_id=service["id"], request_path="/test_content_set"
        )

    object_key = lib.core.convert_id_to_string(lib.core.get_sequence_id(session))
    rest_object = {
        "rest_object_name": "Contacts",
        "rest_object_type": "TABLE",
        "schema_id": schema_id,
        "auto_add_schema": False,
        "request_path": "/Contacts",
        "enabled": True,
        "crud_operations": ["READ"],
        "crud_operation_format": "FEED",
        "requires_auth": True,
        "row_user_ownership_enforced": False,
        "comments": "",
        "auto_detect_media_type": False,
        "auth_stored_procedure": "",
        "options": None,
        "data_mappings": [
            {
                "id": object_key,
                "rest_object_id": "",
                "name": "MyServiceAnalogPhoneBookContacts",
                "position": 0,
                "kind": "RESULT",
                "fields": [
                    {
                        "id": lib.core.convert_id_to_string(
                            lib.core.get_sequence_id(session)
                        ),
                        "data_mapping_id": object_key,
                        "name": "id",
                        "position": 1,
                        "db_column": {
                            "comment": "",
                            "datatype": "int",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": True,
                            "is_unique": False,
                            "name": "id",
                            "not_null": True,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": True,
                        "no_check": False,
                        "no_update": False,
                    },
                    {
                        "id": lib.core.convert_id_to_string(
                            lib.core.get_sequence_id(session)
                        ),
                        "data_mapping_id": object_key,
                        "name": "fName",
                        "position": 2,
                        "db_column": {
                            "comment": "",
                            "datatype": "varchar(45)",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": False,
                            "is_unique": False,
                            "name": "f_name",
                            "not_null": False,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": False,
                        "no_check": False,
                        "no_update": False,
                    },
                    {
                        "id": lib.core.convert_id_to_string(
                            lib.core.get_sequence_id(session)
                        ),
                        "data_mapping_id": object_key,
                        "name": "lName",
                        "position": 3,
                        "db_column": {
                            "comment": "",
                            "datatype": "varchar(45)",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": False,
                            "is_unique": False,
                            "name": "l_name",
                            "not_null": False,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": False,
                        "no_check": False,
                        "no_update": False,
                    },
                    {
                        "id": lib.core.convert_id_to_string(
                            lib.core.get_sequence_id(session)
                        ),
                        "data_mapping_id": object_key,
                        "name": "number",
                        "position": 4,
                        "db_column": {
                            "comment": "",
                            "datatype": "varchar(20)",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": False,
                            "is_unique": False,
                            "name": "number",
                            "not_null": False,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": False,
                        "no_check": False,
                        "no_update": False,
                    },
                    {
                        "id": lib.core.convert_id_to_string(
                            lib.core.get_sequence_id(session)
                        ),
                        "data_mapping_id": object_key,
                        "name": "email",
                        "position": 5,
                        "db_column": {
                            "comment": "",
                            "datatype": "varchar(45)",
                            "id_generation": None,
                            "is_generated": False,
                            "is_primary": False,
                            "is_unique": False,
                            "name": "email",
                            "not_null": False,
                            "srid": None,
                        },
                        "enabled": True,
                        "allow_filtering": True,
                        "allow_sorting": False,
                        "no_check": False,
                        "no_update": False,
                    },
                ],
            }
        ],
    }

    rest_object_id = add_test_rest_object(session, **rest_object)
    assert rest_object_id is not None

    return {
        "session": session,
        "service_id": service["id"],
        "schema_id": schema_id,
        "rest_object_id": rest_object_id,
        "content_set_id": content_set["id"],
        "temp_dir": temp_dir.name,
    }


class TableContents(object):
    class TableSnapshot(object):
        def __init__(self, snapshot):
            self._data = snapshot

        def __getitem__(self, key):
            return self._data[key]

        def __len__(self):
            return len(self._data)

        def __str__(self) -> str:
            return str(self._data)

        def __eq__(self, value) -> bool:
            if isinstance(value, dict):
                value = [value]
            return self._data == value

        @property
        def count(self):
            return len(self._data)

        @property
        def items(self):
            return self._data

        def get(self, column_name, value):
            for item in self._data:
                if item.get(column_name) == value:
                    return item
            return None

        def exists(self, column_name, value):
            return self.get(column_name, value) is not None

    def __init__(self, session, table_name, take_snapshot=True):
        self._session = session
        self._table_name = table_name
        self._snapshot = None
        self._diff = set()

        if take_snapshot:
            self.take_snapshot()

    @property
    def items(self):
        return lib.core.select(table=self._table_name).exec(self._session).items

    @property
    def count(self):
        return (
            lib.core.select(table=self._table_name, cols="COUNT(*) as CNT")
            .exec(self._session)
            .first["CNT"]
        )

    def get(self, column_name, value):
        return (
            lib.core.select(table=self._table_name, where=[f"{column_name}=?"])
            .exec(self._session, [value])
            .first
        )

    def filter(self, column_name, value):
        return (
            lib.core.select(table=self._table_name, where=f"{column_name}=?")
            .exec(self._session, [value])
            .items
        )

    def exists(self, column_name, value):
        return self.get(column_name, value) is not None

    def take_snapshot(self):
        self._snapshot = TableContents.TableSnapshot(
            lib.core.select(table=self._table_name).exec(self._session).items
        )

    @property
    def snapshot(self) -> TableSnapshot:
        assert self._snapshot is not None
        return self._snapshot

    @staticmethod
    def _canonical(rows):
        # A snapshot compares the *contents* of a table, and the queries backing
        # it carry no ORDER BY, so the server is free to hand the same rows back
        # in a different sequence. That is not hypothetical for the
        # INFORMATION_SCHEMA privilege views: a GRANT/REVOKE cycle re-buckets the
        # in-memory grant tables and transposes unrelated rows. Compare as a
        # multiset so row order never decides the result.
        return sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)

    @property
    def same_as_snapshot(self):
        assert self._snapshot is not None

        current = lib.core.select(table=self._table_name).exec(self._session).items
        return self._canonical(current) == self._canonical(self._snapshot.items)

    def assert_same(self):
        assert self._snapshot is not None

        assert self.same_as_snapshot, self.diff_text

    @property
    def diff_text(self):
        assert self._snapshot is not None

        current = lib.core.select(table=self._table_name).exec(self._session).items
        expected = self._snapshot.items

        added = self._multiset_difference(current, expected)
        removed = self._multiset_difference(expected, current)

        if not added and not removed:
            return ""

        text = ""
        if added:
            text += f"\nUnexpected rows ({len(added)}):"
            for row in added:
                text += f"\n  + {row}"
        if removed:
            text += f"\nMissing rows ({len(removed)}):"
            for row in removed:
                text += f"\n  - {row}"
        return text

    @staticmethod
    def _multiset_difference(left, right):
        """Rows in `left` that `right` does not account for, duplicates included."""
        remaining = list(right)
        difference = []
        for row in left:
            if row in remaining:
                remaining.remove(row)
            else:
                difference.append(row)
        return difference


class QueryResults(object):
    def __init__(self, query_fn):
        self.query_fn = query_fn

        self.orig_results = None
        self.new_results = None

    def __enter__(self):
        self.orig_results = [r for r in iter(self.query_fn().fetch_one_object, None)]
        return self

    def __exit__(self, type, value, traceback):
        pass

    def run(self, rerun=True):
        if self.new_results is None or rerun:
            self.new_results = [r for r in iter(self.query_fn().fetch_one_object, None)]

    def expect_unchanged(self):
        self.run(rerun=True)
        assert self.orig_results == self.new_results

    def expect_rows(self, rows):
        self.run(rerun=True)
        assert self.new_results == rows

    def expect_added(self, added_rows):
        self.run(rerun=True)
        orig_results = self.orig_results.copy()
        for r in self.new_results:
            if r in orig_results:
                orig_results.remove(r)
            else:
                assert (
                    r in added_rows
                ), f"unexpected new row:{r}\nold={self.orig_results}\nnew={self.new_results}"
                added_rows.remove(r)
        assert (
            added_rows == []
        ), f"{added_rows} not added as expected\nold={self.orig_results}\nnew={self.new_results}"

    def expect_removed(self, removed_rows):
        self.run(rerun=True)
        new_results = self.new_results.copy()
        for r in self.orig_results:
            if r in new_results:
                new_results.remove(r)
            else:
                assert (
                    r in removed_rows
                ), f"old={self.orig_results}\nnew={self.new_results}"
                removed_rows.remove(r)
        assert removed_rows == [], f"{removed_rows} not removed as expected"


def create_test_db(session, schema_name):
    session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`;")
    session.run_sql(
        f"CREATE SCHEMA IF NOT EXISTS `{schema_name}` DEFAULT CHARACTER SET utf8;"
    )

    session.run_sql(f"USE `{schema_name}`;")
    session.run_sql(f"""CREATE TABLE IF NOT EXISTS `{schema_name}`.`Contacts` (
                        `id` INT NOT NULL,
                        `f_name` VARCHAR(45) NULL,
                        `l_name` VARCHAR(45) NULL,
                        `number` VARCHAR(20) NULL,
                        `email` VARCHAR(45) NULL,
                        PRIMARY KEY (`id`))
                        ENGINE = InnoDB;""")
    session.run_sql(f"""CREATE TABLE IF NOT EXISTS `{schema_name}`.`Addresses` (
                        `id` INT NOT NULL,
                        `address_line` VARCHAR(256) NULL,
                        `city` VARCHAR(128) NULL,
                        PRIMARY KEY (`id`))
                        ENGINE = InnoDB;""")
    session.run_sql("""CREATE PROCEDURE `GetAllContacts` ()
                        BEGIN
                            SELECT * FROM Contacts;
                        END""")
    session.run_sql("""CREATE OR REPLACE VIEW `ContactsWithEmail` AS
                        SELECT * FROM `Contacts`
                        WHERE `email` is not NULL;""")
    session.run_sql("""CREATE OR REPLACE VIEW `ContactBasicInfo` AS
                        SELECT f_name, l_name, number FROM `Contacts`
                        WHERE `email` is not NULL;""")
    session.run_sql(
        f"""CREATE TABLE IF NOT EXISTS `{schema_name}`.`Notes` (
                        `id` INT NOT NULL,
                        `contact_id` INT NOT NULL,
                        `title` VARCHAR(50) NOT NULL,
                        `content` VARCHAR(255),
                        PRIMARY KEY (`id`),
                        FOREIGN KEY (`contact_id`) REFERENCES `{schema_name}`.`Contacts`(`id`));"""
    )
    session.run_sql(
        """CREATE FUNCTION `format_name` (first_name VARCHAR(45), last_name VARCHAR(45))
                        RETURNS VARCHAR(91) DETERMINISTIC
                        RETURN CONCAT(first_name, ' ', last_name);"""
    )
    session.run_sql("""CREATE OR REPLACE SQL SECURITY INVOKER VIEW `ContactNotes` AS
                        SELECT `format_name`(t1.`f_name`, t1.`l_name`), t1.`number`, t2.`title`, t2.`content` FROM `Contacts` t1
                        JOIN `Notes` t2 ON t1.`id` = t2.`contact_id`;""")


def get_rest_object_privileges(session, schema_name, rest_object_name):
    # 1. Fetch table privileges
    raw_table_grants = lib.core.MrsDbExec(f"""
        SELECT PRIVILEGE_TYPE
        FROM INFORMATION_SCHEMA.TABLE_PRIVILEGES
        WHERE TABLE_SCHEMA = '{schema_name}'
            AND TABLE_NAME = '{rest_object_name}'
        """).exec(session).items

    table_grants = [g["PRIVILEGE_TYPE"].upper() for g in raw_table_grants]

    # 2. Fetch procedure/routine privileges
    raw_proc_grants = lib.core.MrsDbExec("""
        SELECT PROC_PRIV
        FROM mysql.procs_priv
        WHERE LOWER(Db) = LOWER(?)
            AND LOWER(Routine_name) = LOWER(?)
        """).exec(session, [schema_name, rest_object_name]).items

    proc_grants = []
    for row in raw_proc_grants:
        priv = row.get("PROC_PRIV")
        if not priv:
            continue

        # Handle string ('Execute,Alter Routine') or set/list ({'Execute', 'Alter Routine'})
        if isinstance(priv, str):
            proc_grants.extend(
                [p.strip().upper() for p in priv.split(",") if p.strip()]
            )
        elif isinstance(priv, (set, list, tuple)):
            proc_grants.extend([p.strip().upper() for p in priv])

    # 3. Combine and deduplicate preserving insertion order
    all_grants = table_grants + proc_grants

    # dict.fromkeys removes duplicates while keeping original list order
    return list(dict.fromkeys(all_grants))


def find_free_port() -> int:
    """Returns a currently-free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_binary_available() -> bool:
    """Returns whether a MariaDB/MySQL server binary is on the PATH.

    The sandbox functions look the server up there, so this says whether a
    sandbox can be deployed at all.
    """
    return bool(shutil.which("mariadbd") or shutil.which("mysqld"))


def get_connection_data(instance: int = 0):
    """Returns the root connection data of one of the run's sandboxes.

    Instance 0 is the sandbox the ``init_mrs`` fixture deploys for the whole
    session; the fixture publishes its port and password through MYSQL_PORT
    and MYSQL_PASSWORD before anything connects. A further instance (the
    metadata upgrade test deploys one of its own) gets a free port unless
    MYSQL_PORT<instance> names one.

    Args:
        instance (int): Which sandbox of the run, 0 being the session's.

    Returns:
        A dict with user, host, port (as a string) and password.
    """
    port_variable = "MYSQL_PORT" if instance == 0 else f"MYSQL_PORT{instance}"
    return {
        "user": os.environ.get("MYSQL_USER", "root"),
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "port": os.environ.get(port_variable) or str(find_free_port()),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }


def create_shell_session(instance=0) -> mysqlsh.globals.session:
    connection_data = get_connection_data(instance)
    shell = mysqlsh.globals.shell

    return shell.connect(
        f"{connection_data['user']}:{connection_data['password']}@{connection_data['host']}:{connection_data['port']}"
    )


def string_replace(text: str, replacers: dict) -> str:
    for key, value in replacers.items():
        text = text.replace(key, value)
    return text
