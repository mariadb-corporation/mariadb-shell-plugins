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


from mrs_plugin import lib

from .helpers import TableContents, SchemaCT, DbObjectCT, get_default_db_object_init

db_object_create_statement = """CREATE OR REPLACE REST VIEW /Contacts
    ON SERVICE /test SCHEMA /PhoneBook
    AS `PhoneBook`.`Contacts` CLASS MyServiceAnalogPhoneBookContacts {
        id: id @KEY @SORTABLE,
        fName: f_name,
        lName: l_name,
        number: number,
        email: email
    }
    AUTHENTICATION REQUIRED;"""


def crud_operations(session, db_object_id):
    row = session.run_sql(
        lib.core.metadata_sql(
            session, "SELECT crud_operations FROM <metadata>.db_object WHERE id = ?"
        ),
        [db_object_id],
    ).fetch_one()
    return row[0].split(",") if row and row[0] else []


def test_set_crud_operations(phone_book, table_contents):
    session = phone_book["session"]
    db_object_table: TableContents = table_contents("db_object")
    db_object = get_default_db_object_init(session, phone_book["schema_id"])

    db_object["objects"][0]["options"] = {
        "dataMappingViewInsert": True,
        "dataMappingViewUpdate": True,
        "dataMappingViewDelete": True,
    }

    with DbObjectCT(session, **db_object) as db_object_id:
        result = crud_operations(session, db_object_id)

        assert result == ["CREATE", "READ", "UPDATE", "DELETE"]

    db_object["objects"][0]["options"] = {
        "dataMappingViewInsert": False,
        "dataMappingViewUpdate": True,
        "dataMappingViewDelete": True,
    }

    with DbObjectCT(session, **db_object) as db_object_id:
        result = crud_operations(session, db_object_id)

        assert result == ["READ", "UPDATE", "DELETE"]

    db_object["objects"][0]["options"] = {
        "dataMappingViewInsert": False,
        "dataMappingViewUpdate": False,
        "dataMappingViewDelete": True,
    }

    with DbObjectCT(session, **db_object) as db_object_id:
        result = crud_operations(session, db_object_id)

        assert result == ["READ", "DELETE"]

    db_object["objects"][0]["options"] = {
        "dataMappingViewInsert": False,
        "dataMappingViewUpdate": False,
        "dataMappingViewDelete": False,
    }

    with DbObjectCT(session, **db_object) as db_object_id:
        result = crud_operations(session, db_object_id)

        assert result == ["READ"]

    db_object["objects"][0]["fields"][0]["options"] = {
        "dataMappingViewInsert": True,
        "dataMappingViewUpdate": True,
        "dataMappingViewDelete": True,
    }

    with DbObjectCT(session, **db_object) as db_object_id:
        result = crud_operations(session, db_object_id)

        assert result == ["READ", "UPDATE"]


def test_special_schemas(phone_book, mobile_phone_book, table_contents):
    session = phone_book["session"]
    information_schema_grants: TableContents = table_contents(
        "INFORMATION_SCHEMA.TABLE_PRIVILEGES"
    )

    with SchemaCT(
        session, phone_book["service_id"], "information_schema", "/information_schema"
    ) as schema_id:

        db_object_init = get_default_db_object_init(
            session, schema_id, "CHARACTER_SETS", "/character_sets"
        )

        with DbObjectCT(session, **db_object_init) as db_object_id:
            assert information_schema_grants.same_as_snapshot

    with SchemaCT(
        session, phone_book["service_id"], "performance_schema", "/performance_schema"
    ) as schema_id:

        db_object_init = get_default_db_object_init(
            session, schema_id, "accounts", "/accounts" "TABLE"
        )

        with DbObjectCT(session, **db_object_init) as db_object_id:
            assert not information_schema_grants.same_as_snapshot

            filtered = information_schema_grants.filter(
                "TABLE_SCHEMA", "performance_schema"
            )
            assert len(filtered) == 2
            filtered.sort(key=lambda a: a["TABLE_NAME"])

            row = filtered[0]
            assert row["TABLE_NAME"] == "accounts"
            assert row["GRANTEE"] == f"'{lib.core.metadata_role(session, 'user')}'@''"
            assert row["PRIVILEGE_TYPE"] == "SELECT"

            row = filtered[1]
            assert row["TABLE_NAME"] == "accounts"
            assert (
                row["GRANTEE"]
                == f"'{lib.core.metadata_role(session, 'data_provider')}'@''"
            )
            assert row["PRIVILEGE_TYPE"] == "SELECT"

    assert information_schema_grants.same_as_snapshot
