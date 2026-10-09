# Copyright (c) 2021, 2025, Oracle and/or its affiliates.
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
from ...lib.core import *
from ...lib.services import *
from ...lib.content_sets import *
from ...lib.schemas import *


def test_get_interactive_default():
    interactive_default = get_interactive_default()
    assert interactive_default is not None
    assert isinstance(interactive_default, bool)


def test_get_current_session():
    current_session = get_current_session()
    assert current_session is not None


def test_id_to_uuid():
    context = "my_context"
    ids = ["", "1234", "/myService"]

    for id in ids:
        with pytest.raises(
            RuntimeError, match=f"Invalid id format '{id}' for '{context}'."
        ):
            core.id_to_uuid(id, context, False)

    id = "0x1234"
    with pytest.raises(RuntimeError, match=f"The '{context}' has an invalid size."):
        core.id_to_uuid(id, context, False)


def test_convert_path_to_camel_case():
    camel_case_name = convert_path_to_camel_case(path="/foo_bar")
    assert camel_case_name == "fooBar"

    camel_case_name = convert_path_to_camel_case(path="/Foo_Bar")
    assert camel_case_name == "FooBar"

    camel_case_name = convert_path_to_camel_case(path="/foo")
    assert camel_case_name == "foo"

    # forcing lowerCamelCase
    camel_case_name = convert_path_to_camel_case(path="/Foo_Bar", lower=True)
    assert camel_case_name == "fooBar"

    camel_case_name = convert_path_to_camel_case(path="/Foo_bar", lower=True)
    assert camel_case_name == "fooBar"

    # handling special characters
    camel_case_name = convert_path_to_camel_case(path="foo(bar)")
    assert camel_case_name == "foobar"

    camel_case_name = convert_path_to_camel_case(
        path="foo(bar)", allowed_special_characters={"(", ")"}
    )
    assert camel_case_name == "foo(bar)"
