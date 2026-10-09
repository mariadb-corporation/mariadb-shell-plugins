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

from ... import lib
from .helpers import AuthAppCT, UserCT, get_default_auth_app_init, get_default_user_init

import os

InitialAuthAppIds = []


def test_verify_auth_vendors(phone_book, table_contents):
    auth_vendor_table = table_contents("auth_vendor")

    assert auth_vendor_table.count == 5
    assert auth_vendor_table.items == [
        {
            "comments": "Built-in user management of MRS",
            "enabled": 1,
            "id": lib.core.id_to_uuid("0x30000000000000000000000000000000", ""),
            "name": "MRS",
            "validation_url": None,
            "options": None,
        },
        {
            "comments": "Provides basic authentication via MySQL Server accounts",
            "enabled": 1,
            "id": lib.core.id_to_uuid("0x31000000000000000000000000000000", ""),
            "name": "MySQL Internal",
            "validation_url": None,
            "options": None,
        },
        {
            "comments": "Uses the Facebook Login OAuth2 service",
            "enabled": 1,
            "id": lib.core.id_to_uuid("0x32000000000000000000000000000000", ""),
            "name": "Facebook",
            "validation_url": None,
            "options": None,
        },
        {
            "comments": "Uses the Google OAuth2 service",
            "enabled": 1,
            "id": lib.core.id_to_uuid("0x34000000000000000000000000000000", ""),
            "name": "Google",
            "validation_url": None,
            "options": None,
        },
        {
            "id": lib.core.id_to_uuid("0x35000000000000000000000000000000", ""),
            "name": "OCI OAuth2",
            "validation_url": None,
            "enabled": 1,
            "comments": "Uses the OCI OAuth2 service",
            "options": None,
        },
    ]
