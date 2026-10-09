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

import pytest
import tempfile
import json
import mysqlsh

from lib.core import MrsDbSession, MrsDbExec
from ...content_sets import *
from .helpers import (
    ServiceCT,
    ContentSetCT,
    get_default_content_set_init,
    TableContents,
    string_replace,
)


@pytest.mark.skip(
    reason="This test requires the project to be built in order to load the content set"
)
def test_auth_app_grant_options(phone_book, table_contents):
    session = phone_book["session"]

    with ServiceCT(session, "/myService2") as service_id:
        session.run_sql("""
            CREATE OR REPLACE REST CONTENT SET /mrsScriptsContent ON SERVICE /myService2
            FROM './examples/mrs_scripts/' LOAD SCRIPTS""")

        res = (
            MrsDbExec("SHOW GRANTS FOR 'mysql_rest_service_data_provider'")
            .exec(session)
            .items
        )

        grants = [
            "GRANT SELECT ON `mysql_rest_service_metadata`.`mrs_user` TO `mysql_rest_service_data_provider`@`%`",
            "GRANT SELECT ON `mysql_rest_service_metadata`.`msm_schema_version` TO `mysql_rest_service_data_provider`@`%`",
            "GRANT SELECT ON `mysql_rest_service_metadata`.`mrs_user_schema_version` TO `mysql_rest_service_data_provider`@`%`",
        ]
        found = 0
        for row in res:
            key = next(iter(row))
            if row[key] in grants:
                found += 1

        assert found == 3
