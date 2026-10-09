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

from .helpers import SchemaCT

schema_create_statement = """CREATE OR REPLACE REST SCHEMA /PhoneBook ON SERVICE /test
    FROM `PhoneBook`
    AUTHENTICATION NOT REQUIRED;

CREATE OR REPLACE REST VIEW /Contacts
    ON SERVICE /test SCHEMA /PhoneBook
    AS `PhoneBook`.`Contacts` CLASS MyServiceAnalogPhoneBookContacts {
        id: id @KEY @SORTABLE,
        fName: f_name,
        lName: l_name,
        number: number,
        email: email
    }
    AUTHENTICATION REQUIRED;"""


def test_add_schema(phone_book, table_contents):
    session = phone_book["session"]
    schemas_table = table_contents("db_schema")

    with SchemaCT(
        session, phone_book["service_id"], "PhoneBook", "/PhoneBook2"
    ) as schema_id:
        assert schemas_table.count == schemas_table.snapshot.count + 1
    assert schemas_table.same_as_snapshot
