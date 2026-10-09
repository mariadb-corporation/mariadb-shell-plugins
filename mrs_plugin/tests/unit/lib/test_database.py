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

import json

from mrs_plugin.lib import database


def test_nested_documents_are_decoded():
    """As sdk_service_data returns them on MariaDB: strings in arrays."""
    field = '{"name": "id", "enabled": \\u0001, "no_check": \\u0000}'
    mapping = json.dumps({"name": "Product", "fields": [field]})
    service = json.dumps({"id": "x", "rest_schemas": [mapping], "options": None})

    assert database._json_document(service) == {
        "id": "x",
        "rest_schemas": [
            {
                "name": "Product",
                "fields": [{"name": "id", "enabled": True, "no_check": False}],
            }
        ],
        "options": None,
    }


def test_raw_bit_bytes_become_booleans():
    assert database._json_document('{"a": \x01, "b": \x00}') == {"a": True, "b": False}


def test_text_that_only_looks_like_json_is_kept():
    assert database._json_document({"comment": "[draft]"}) == {"comment": "[draft]"}


def test_the_sdk_of_a_service_has_its_objects(phone_book):
    from mrs_plugin import services

    code = services.get_sdk_service_classes(
        session=phone_book["session"],
        service_id=phone_book["service_id"],
        service_url="https://localhost:8443/test",
    )

    assert "fName" in code
