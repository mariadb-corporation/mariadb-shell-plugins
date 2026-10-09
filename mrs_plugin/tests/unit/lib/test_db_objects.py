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

import pytest
from ..helpers import DbObjectCT, get_default_db_object_init


@pytest.mark.usefixtures("phone_book")
def test_add_db_object(phone_book, table_contents):
    db_object_table = table_contents("db_object")
    session = phone_book["session"]
    schema_id = phone_book["schema_id"]
    db_object_init = get_default_db_object_init(session, schema_id)

    with DbObjectCT(session, **db_object_init) as db_object_id:
        assert db_object_table.get("id", db_object_id) == {
            "auth_stored_procedure": None,
            "auto_detect_media_type": 1,
            "comments": "Object that will be removed",
            "crud_operations": ["CREATE", "READ", "UPDATE", "DELETE"],
            "db_schema_id": schema_id,
            "details": None,
            "enabled": 1,
            "format": "FEED",
            "id": db_object_id,
            "items_per_page": 10,
            "media_type": "application/json",
            "name": "ContactBasicInfo",
            "object_type": "VIEW",
            "options": {"aaa": "val aaa", "bbb": "val bbb"},
            "metadata": None,
            "request_path": "/view_contact_basic_info",
            "requires_auth": 0,
            "internal": 0,
        }

    assert db_object_table.same_as_snapshot
