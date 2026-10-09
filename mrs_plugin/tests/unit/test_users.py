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

import pytest

from ... import lib
from .helpers import UserCT, get_default_user_init, TableContents

import hashlib
import hmac
import base64


def test_user_sql(phone_book):
    session = phone_book["session"]

    import json

    with pytest.raises(Exception, match='Invalid REST user "boss"@"MRS Auth App"\n'):
        session.run_sql(
            'ALTER REST USER "boss"@"MRS Auth App" IDENTIFIED BY "somepassword";'
        )

    with pytest.raises(Exception, match="The password must not be empty.\n"):
        session.run_sql('ALTER REST USER "boss"@"MRS Auth App" IDENTIFIED BY "";')

    with pytest.raises(Exception, match='Invalid REST user "boss"@"MRS Auth App"\n'):
        session.run_sql(
            'ALTER REST USER "boss"@"MRS Auth App" IDENTIFIED BY "SomePassword";'
        )

    with pytest.raises(Exception, match='Invalid REST user "boss"@"MRS Auth App"\n'):
        session.run_sql(
            'ALTER REST USER "boss"@"MRS Auth App" IDENTIFIED BY "SomePassword!";'
        )

    session.run_sql(
        """CREATE REST USER "boss"@"MRS Auth App" IDENTIFIED BY "MySQLR0cks!" ACCOUNT LOCK OPTIONS {
                                          "email": "boss@example.com",
                                          "vendor_user_id": "vendor",
                                          "mapped_user_id": "vendorboss123",
                                          "custom":"custom value"
                    } APP OPTIONS {"myoption": 12345};"""
    )
    user = lib.users.get_user(
        session=session,
        user_name="boss",
        auth_app_name="MRS Auth App",
        service_id=phone_book["service_id"],
    )
    assert user is not None
    assert user["email"] == "boss@example.com"
    assert not user["login_permitted"], "locked"
    assert user["vendor_user_id"] == "vendor"
    assert user["mapped_user_id"] == "vendorboss123"
    assert user["options"] == {"custom": "custom value"}
    assert json.dumps(user["app_options"]) == '{"myoption": 12345}'

    session.run_sql(
        'ALTER REST USER "boss"@"MRS Auth App" IDENTIFIED BY "MySQLR0cks!";'
    )
    user = lib.users.get_user(
        session=session,
        user_name="boss",
        auth_app_name="MRS Auth App",
        service_id=phone_book["service_id"],
    )
    assert user is not None
    assert user["email"] == "boss@example.com"
    assert not user["login_permitted"], "locked"
    assert user["vendor_user_id"] == "vendor"
    assert user["mapped_user_id"] == "vendorboss123"
    assert json.dumps(user["app_options"]) == '{"myoption": 12345}'

    session.run_sql("""ALTER REST USER "boss"@"MRS Auth App" OPTIONS {
                    "custom2": "Custom Value2",
                    "email": "boss@example2.com",
                    "vendor_user_id": "vendor2",
                    "mapped_user_id": "vendor123"
                    } APP OPTIONS {
                    "anything": [32]
                    };""")
    user = lib.users.get_user(
        session=session,
        user_name="boss",
        auth_app_name="MRS Auth App",
        service_id=phone_book["service_id"],
    )
    assert user is not None
    assert user["email"] == "boss@example2.com"
    assert not user["login_permitted"], "locked"
    assert user["vendor_user_id"] == "vendor2"
    assert user["mapped_user_id"] == "vendor123"
    assert json.dumps(user["app_options"]) == '{"anything": [32]}'

    res = session.run_sql('show create rest user "boss"@"MRS Auth App";')
    ddl = res.fetch_one()[0]
    assert """CREATE OR REPLACE REST USER `boss`@`MRS Auth App`
    ACCOUNT LOCK
    IDENTIFIED BY '[Stored Password]'
    OPTIONS {
        "custom2": "Custom Value2",
        "email": "boss@example2.com",
        "vendor_user_id": "vendor2",
        "mapped_user_id": "vendor123"
    }
    APP OPTIONS {
        "anything": [
            32
        ]
    };""" == ddl

    session.run_sql('ALTER REST USER "boss"@"MRS Auth App" ACCOUNT UNLOCK;')
    user = lib.users.get_user(
        session=session,
        user_name="boss",
        auth_app_name="MRS Auth App",
        service_id=phone_book["service_id"],
    )
    assert user is not None
    assert user["login_permitted"], "locked"


def test_user_sql_service(phone_book):
    session = phone_book["session"]

    script = [
        "CREATE REST SERVICE /one",
        "CREATE REST SERVICE /two",
        "CREATE REST AUTH APP \"app1\" VENDOR MySQL COMMENT 'svc1'",
        "CREATE REST AUTH APP \"app2\" VENDOR MySQL COMMENT 'svc2'",
        'CREATE REST USER "usr"@"app1" OPTIONS {"email": "one@site.com"}',
        'CREATE REST USER "usr"@"app2" OPTIONS {"email": "two@site.com"}',
    ]
    for sql in script:
        try:
            session.run_sql(sql)
        except:
            print(sql)
            raise

    id_one = lib.services.get_service(session=session, url_context_root="/one")["id"]
    id_two = lib.services.get_service(session=session, url_context_root="/two")["id"]

    user = lib.users.get_user(
        session=session, user_name="usr", auth_app_name="app1", service_id=id_one
    )
    assert user["email"] == "one@site.com"
    user = lib.users.get_user(
        session=session, user_name="usr", auth_app_name="app2", service_id=id_two
    )
    assert user["email"] == "two@site.com"

    session.run_sql('ALTER REST USER "usr"@"app1" OPTIONS {"email": "ONE@site.com"}')
    session.run_sql('ALTER REST USER "usr"@"app2" OPTIONS {"email": "TWO@site.com"}')
    user = lib.users.get_user(
        session=session, user_name="usr", auth_app_name="app1", service_id=id_one
    )
    assert user["email"] == "ONE@site.com"
    user = lib.users.get_user(
        session=session, user_name="usr", auth_app_name="app2", service_id=id_two
    )
    assert user["email"] == "TWO@site.com"

    session.run_sql("DROP REST SERVICE /one")
    session.run_sql("DROP REST SERVICE /two")
