# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""REST paths as the server compares them.

Shared by the REST endpoint removal (lib/rest_removal.py) and the REST
object figures of the diagrams (lib/diagrams.py); no shell imports.
"""

_QUOTES = ("`", "'", '"')


def path_key(path: str) -> str:
    """A REST path as the server compares it: unquoted, case ignored."""
    if len(path) >= 2 and path[0] == path[-1] and path[0] in _QUOTES:
        path = path[1:-1]
    return path.lower()


def object_key(service: str, schema: str, rest_object: str) -> tuple:
    """A REST object by its service, REST schema and own path, compared as
    the server compares them."""
    return path_key(service), path_key(schema), path_key(rest_object)
