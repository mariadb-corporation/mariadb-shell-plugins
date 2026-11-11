# Copyright (c) 2025, 2026, Oracle and/or its affiliates.
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

import os
import mysqlsh


def get_connection_data():
    return {
        "user": os.environ.get("MYSQL_USER", "root"),
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "port": os.environ.get("MYSQL_PORT", "3388"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }


def create_shell_session() -> mysqlsh.globals.session:
    connection_data = get_connection_data()
    shell = mysqlsh.globals.shell

    return shell.connect(
        f"{connection_data['user']}:{connection_data['password']}@{connection_data['host']}:{connection_data['port']}"
    )


def query(session, sql: str) -> list:
    """The rows of a query, run as msm runs its SQL."""
    from msm_plugin import lib as msm_lib

    return msm_lib.core.MsmDbExec(sql).exec(session).items


def set_project_setting(project_path: str, key: str, value) -> None:
    """Sets one setting of a project's msm.project.json."""
    import json

    settings_file = os.path.join(project_path, "msm.project.json")
    with open(settings_file) as f:
        settings = json.load(f)
    settings[key] = value
    with open(settings_file, "w") as f:
        json.dump(settings, f)


def find_test_lsp(capable) -> "str | None":
    """The mariadb-lsp binary a test runs with, where it is capable:
    MSM_TEST_LSP (the binary or its folder), else the binaries msm would
    find; None where none is.

    Args:
        capable: Tells whether a binary does what the test needs.
    """
    from msm_plugin.lib import lsp

    given = os.environ.get("MSM_TEST_LSP")
    for candidate in [given] if given else lsp._candidates(None):
        binary = lsp.find_lsp(candidate)
        if binary is not None and capable(binary):
            return binary
    return None
