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

import dataclasses
import re
from typing import Any, get_origin, get_args, get_type_hints, Mapping, Sequence, List


_camel_pat = re.compile(r'(?<!^)(?=[A-Z])')          # “myField” → “my_Field”
# “my_field” → “myField” (internal use)
_snake_pat = re.compile(r'_[a-z]')


def snake_to_camel(name: str) -> str:
    """my_field → myField"""
    parts = name.split('_')
    return parts[0] + ''.join(p.title() for p in parts[1:])


def camel_to_snake(name: str) -> str:
    """myField → my_field"""
    return _camel_pat.sub('_', name).lower()


def _field_lookup(cls: type) -> Mapping[str, str]:
    """
    Return a dict that maps every possible incoming key (snake or camel)
    to the *real* attribute name defined on ``cls``.
    Example:
        {
            "defaultRoles": "defaultRoles",   # field already camel‑case
            "default_roles": "defaultRoles", # camel ⇢ snake alias
            "defaultRoles": "defaultRoles",  # camel ⇢ camel alias (identity)
        }
    """
    lookup: dict[str, str] = {}
    for f in dataclasses.fields(cls):
        real_name = f.name
        # keep the original spelling (could be snake or camel)
        lookup[real_name] = real_name

        # add the alternative spelling if it is different
        alt = camel_to_snake(
            real_name) if '_' not in real_name else snake_to_camel(real_name)
        if alt != real_name:
            lookup[alt] = real_name
    return lookup


def _instantiate_dataclass(cls: type, data: Mapping[str, Any]) -> Any:
    """
    Build an instance of ``cls`` from ``data``.
    Keys in *data* may be snake_case or camelCase; they are mapped
    to the real attribute names using the lookup table built by ``_field_lookup``.
    Nested dataclasses (or lists thereof) are handled recursively.
    """
    if not isinstance(data, Mapping):
        raise TypeError(
            f'Expected a mapping for {cls.__name__}, got {type(data)}')

    # e.g. {"default_roles": "defaultRoles", ...}
    lookup = _field_lookup(cls)
    hints = get_type_hints(cls)                # field → type

    prepared: dict[str, Any] = {}
    for incoming_key, incoming_val in data.items():
        # Find the actual attribute name the dataclass expects
        real_name = lookup.get(incoming_key)
        if real_name is None:
            # Unknown field – you may decide to raise, ignore or keep it.
            # Here we keep it (will raise later if not in __init__).
            real_name = incoming_key

        expected_type = hints.get(real_name, Any)
        prepared[real_name] = convert_value(expected_type, incoming_val)

    # Finally instantiate the dataclass – any missing non‑optional fields will raise
    return cls(**prepared)


def convert_value(expected_type: Any, value: Any) -> Any:
    """
    Convert *value* to the expected_type.
    Handles:
        • plain dataclasses
        • List[Dataclass] / Sequence[Dataclass]
        • Mapping[str, Dataclass] (rare, but works)
        • primitives – returned unchanged
    """
    origin = get_origin(expected_type)

    # Support for list[DataClass], List[DataClass]
    if origin is list:
        inner_type = get_args(expected_type)[0]
        if dataclasses.is_dataclass(inner_type):
            return [_instantiate_dataclass(inner_type, item) for item in value]
        # not a dataclass → just return the list (maybe primitives)
        return list(value)

    # Support for dict[<type>, DataClass]
    if origin is dict:
        _k_type, _v_type = get_args(expected_type)
        if dataclasses.is_dataclass(_v_type):
            return {_k: _instantiate_dataclass(_v_type, _v) for _k, _v in value.items()}
        return dict(value)

    # Plain dataclass
    if dataclasses.is_dataclass(expected_type):
        return _instantiate_dataclass(expected_type, value)

    # primitive – forward as‑is
    return value


def is_dataclass_convertible(expected_type: Any):
    return dataclasses.is_dataclass(expected_type) \
        or (get_origin(expected_type) is list and
            dataclasses.is_dataclass(get_args(expected_type)[0])) \
        or (get_origin(expected_type) in (dict, Mapping) and
            dataclasses.is_dataclass(get_args(expected_type)[1]))
