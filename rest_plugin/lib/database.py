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

"""
Module that deals with the "real" database schema instead of the MRS objects
"""

import json

from rest_plugin.lib import core


def get_data_mappings(session, rest_object_id):
    sql = """
        SELECT *
        FROM <metadata>.`data_mapping`
        WHERE rest_object_id = ?
        ORDER BY position
    """

    return core.MrsDbExec(sql).exec(session, [rest_object_id]).items


def get_data_mapping_fields_with_references(session, data_mapping_id, binary_formatter=None):
    sql = """
        SELECT *
        FROM <metadata>.`data_mapping_fields_with_references`
        WHERE data_mapping_id = ?
    """

    return (
        core.MrsDbExec(sql, binary_formatter=binary_formatter)
        .exec(session, [data_mapping_id])
        .items
    )


def get_sdk_service_data(session, service_id, binary_formatter=None):
    sql = """
        SELECT COUNT(*) > 0 AS available
        FROM information_schema.routines
        WHERE routine_name = 'sdk_service_data'
            AND routine_type = 'PROCEDURE'
            AND routine_schema = ?
    """

    if (
        core.MrsDbExec(sql, [core.metadata_schema(session)])
        .exec(session)
        .first["available"]
    ):
        sql = """
            CALL <metadata>.`sdk_service_data`(?)
        """

        row = (
            core.MrsDbExec(sql, binary_formatter=binary_formatter)
            .exec(session, [service_id])
            .first
        )
        # MariaDB's JSON is a LONGTEXT, so the document arrives as text.
        if row is not None and isinstance(row.get("service_res"), str):
            row["service_res"] = json.loads(row["service_res"])

        return row
    else:
        return None
