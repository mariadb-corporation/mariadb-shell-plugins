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

PREFIXED = "acme_mariadb_rest_service_eu"
ROLES = ["admin", "schema_admin", "dev", "user", "meta_provider", "data_provider"]


def test_metadata_schema_with_prefix_and_postfix(phone_book):
    session = phone_book["session"]
    default = lib.core.metadata_schema(session)
    assert default == "mariadb_rest_service"
    assert lib.core.metadata_role(session, "data_provider") == (
        "mariadb_rest_service_data_provider"
    )

    try:
        session.run_sql(f"CONFIGURE REST METADATA SCHEMA {PREFIXED}")
        lib.core.forget_metadata_schema(session)
        assert lib.core.metadata_schema(session) == PREFIXED
        for role in ROLES:
            name = lib.core.metadata_role(session, role)
            assert name == f"acme_mariadb_rest_service_{role}_eu"
            assert (
                session.run_sql(
                    "SELECT COUNT(*) FROM mysql.user WHERE user = ? AND is_role = 'Y'",
                    [name],
                ).fetch_one()[0]
                == 1
            )
        # The plugin's SQL goes to the prefixed schema
        assert (
            lib.core.MrsDbExec("SELECT COUNT(*) AS n FROM <metadata>.`service`")
            .exec(session)
            .first["n"]
            == 0
        )
    finally:
        session.run_sql("USE REST METADATA SCHEMA mariadb_rest_service")
        session.run_sql(f"DROP SCHEMA IF EXISTS {PREFIXED}")
        for role in ROLES:
            session.run_sql(f"DROP ROLE IF EXISTS acme_mariadb_rest_service_{role}_eu")
        lib.core.forget_metadata_schema(session)

    assert lib.core.metadata_schema(session) == default
    assert len(session.run_sql("SHOW REST SERVICES").fetch_all()) > 0
