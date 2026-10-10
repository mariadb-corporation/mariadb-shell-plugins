# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from mrs_plugin.lib import database


def test_the_sdk_data_is_a_nested_document(phone_book):
    """MariaDB's JSON is text; every level below it is a document too."""
    data = database.get_sdk_service_data(
        phone_book["session"], phone_book["service_id"]
    )["service_res"]

    schema = data["rest_schemas"][0]
    rest_object = schema["rest_objects"][0]
    field = rest_object["data_mappings"][0]["fields"][0]
    assert isinstance(schema, dict) and isinstance(field, dict)
    assert field["enabled"] in (True, False)


def test_the_sdk_of_a_service_has_its_objects(phone_book):
    from mrs_plugin import services

    code = services.get_sdk_service_classes(
        session=phone_book["session"],
        service_id=phone_book["service_id"],
        service_url="https://localhost:8443/test",
    )

    assert "fName" in code
