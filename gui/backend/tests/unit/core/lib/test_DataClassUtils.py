# Copyright (c) 2026, Oracle and/or its affiliates.
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

from gui_plugin.core.lib.DataClassUtils import is_dataclass_convertible, convert_value
from dataclasses import dataclass
from typing import List, Sequence, Mapping, Optional


@dataclass
class Nested:
    intId: int


@dataclass
class Sample:
    int_param: int
    string_param: str
    list_param: list[int]
    camelCaseListParam: list[int]
    nestedListParam: list[Nested]

@dataclass
class OptSample:
    optionalParam: Optional[Nested]


def test_is_dataclass_convertible():
    assert is_dataclass_convertible(Sample)
    assert is_dataclass_convertible(list[Sample])
    assert is_dataclass_convertible(List[Sample])
    assert is_dataclass_convertible(dict[str, Sample])
    assert not is_dataclass_convertible(dict[str, int])
    assert not is_dataclass_convertible(dict)
    assert not is_dataclass_convertible(int)
    assert not is_dataclass_convertible(list)
    assert not is_dataclass_convertible(list[int])
    assert not is_dataclass_convertible(str)


def test_convert_simple_value():
    result: Nested = convert_value(Nested, {"int_id": 5})
    assert isinstance(result, Nested)
    assert result.intId == 5

    result = convert_value(Nested, {"intId": 8})
    assert isinstance(result, Nested)
    assert result.intId == 8


def test_convert_simple_value_list():
    result: list[Nested] = convert_value(
        list[Nested], [{"int_id": 5}, {"intId": 8}])
    assert len(result) == 2
    assert isinstance(result[0], Nested)
    assert result[0].intId == 5
    assert isinstance(result[1], Nested)
    assert result[1].intId == 8


def test_convert_simple_value_dict():
    result: dict[str, Nested] = convert_value(
        dict[str, Nested], {"one": {"int_id": 5}, "two": {"intId": 8}})
    assert len(result) == 2
    assert isinstance(result["one"], Nested)
    assert result["one"].intId == 5
    assert isinstance(result["two"], Nested)
    assert result["two"].intId == 8


def test_convert_complex_value():
    result: Sample = convert_value(Sample, {"int_param": 1, "string_param": "param1", "list_param": [
                                   1, 2, 3], "camelCaseListParam": [4, 5, 6], "nestedListParam": [{"intId": 8}]})

    assert isinstance(result, Sample)
    assert result.int_param == 1
    assert result.string_param == "param1"
    assert result.list_param == [1, 2, 3]
    assert result.camelCaseListParam == [4, 5, 6]
    assert len(result.nestedListParam) == 1
    assert isinstance(result.nestedListParam[0], Nested)
    assert result.nestedListParam[0].intId == 8

    # Tests all with wrong casing
    result: Sample = convert_value(Sample, {"intParam": 1, "stringParam": "param1", "listParam": [
                                   1, 2, 3], "camel_case_list_param": [4, 5, 6], "nested_list_param": [{"int_id": 8}]})
    assert isinstance(result, Sample)
    assert result.int_param == 1
    assert result.string_param == "param1"
    assert result.list_param == [1, 2, 3]
    assert result.camelCaseListParam == [4, 5, 6]
    assert len(result.nestedListParam) == 1
    assert isinstance(result.nestedListParam[0], Nested)
    assert result.nestedListParam[0].intId == 8


def test_convert_optional_dataclass_value():
    result = convert_value(OptSample, {"optional_param": {"int_id":42}})
    assert isinstance(result, OptSample)
    assert isinstance(result.optionalParam, Nested)
    assert result.optionalParam.intId == 42

    result = convert_value(OptSample, {"optional_param": None})
    assert isinstance(result, OptSample)
    assert result.optionalParam is None
