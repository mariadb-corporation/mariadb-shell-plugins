# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms,
# as designated in a particular file or component or in included
# license documentation.  The authors of MySQL hereby grant you an
# additional permission to link the program and your derivative works
# with the separately licensed software that they have either included
# with the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import sqlite3
from pathlib import Path

from gui_plugin.core.ShellGuiWebSocketHandler import command_matches_privileges

BACKEND_ROOT = Path(__file__).parents[3]
SCHEMA_DIR = BACKEND_ROOT / "gui_plugin" / "core" / "db_schema"

SINGLE_SERVER_PRIVILEGES = [
    {
        "access_pattern": (
            r"gui\.users\.(get_gui_module_list|list_profiles|get_profile|"
            r"add_profile|get_default_profile|set_default_profile|"
            r"set_web_session_profile)"
        ),
    },
    {
        "access_pattern": (
            r"^(?!(?:gui\.(?:shell|users)\b))(?:(gui|mrs|mds|msm))"
            r"\.[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?$"
        ),
    },
]


def test_command_matches_when_later_privilege_matches():
    privileges = [
        {"access_pattern": r"gui\.modules\.\w*"},
        {"access_pattern": r"gui\.users\.(get_profile|list_profiles)"},
    ]

    assert command_matches_privileges("gui.users.get_profile", privileges)


def test_command_does_not_match_when_no_privilege_matches():
    privileges = [
        {"access_pattern": r"gui\.modules\.\w*"},
        {"access_pattern": r"gui\.users\.(get_profile|list_profiles)"},
    ]

    assert not command_matches_privileges("gui.users.list_users", privileges)


def test_full_access_privilege_matches_any_command():
    privileges = [
        {"access_pattern": r".*"},
    ]

    assert command_matches_privileges("gui.users.list_users", privileges)


def test_command_requires_full_pattern_match():
    privileges = [
        {"access_pattern": r"gui\.users\.(get_profile|list_profiles)"},
    ]

    assert not command_matches_privileges("gui.users.get_profile_extra", privileges)


def test_single_server_user_can_execute_allowed_gui_users_subset():
    assert command_matches_privileges("gui.users.get_profile", SINGLE_SERVER_PRIVILEGES)


def test_single_server_user_cannot_execute_unlisted_gui_users_commands():
    assert not command_matches_privileges(
        "gui.users.list_users", SINGLE_SERVER_PRIVILEGES
    )


def test_single_server_user_keeps_non_users_single_server_access():
    assert command_matches_privileges("mrs.foo.bar", SINGLE_SERVER_PRIVILEGES)


def test_single_server_user_cannot_execute_gui_shell_commands():
    assert not command_matches_privileges("gui.shell.execute", SINGLE_SERVER_PRIVILEGES)


def test_fresh_sqlite_schema_single_server_privileges_match_commands(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    db = sqlite3.connect(tmp_path / "gui_backend.sqlite3")
    try:
        db.executescript((SCHEMA_DIR / "mysqlsh_gui_backend.sqlite.sql").read_text())
        privileges = [
            {"access_pattern": row[0]} for row in db.execute("""SELECT p.access_pattern
                FROM privilege p
                    INNER JOIN role_has_privilege r_p
                        ON p.id = r_p.privilege_id
                WHERE r_p.role_id = 4 AND p.privilege_type_id = 1""")
        ]

        assert command_matches_privileges("gui.users.get_profile", privileges)
        assert command_matches_privileges("mrs.foo.bar", privileges)
        assert not command_matches_privileges("gui.users.list_users", privileges)
        assert not command_matches_privileges("gui.shell.execute", privileges)
    finally:
        db.close()


def test_sqlite_migration_updates_single_server_privileges(tmp_path):
    db = sqlite3.connect(tmp_path / "gui_backend.sqlite3")
    try:
        db.executescript("""
            CREATE TABLE privilege (
                id INTEGER NOT NULL,
                privilege_type_id INTEGER NOT NULL,
                name TEXT,
                access_pattern TEXT
            );
            CREATE TABLE role_has_privilege (
                role_id INTEGER NOT NULL,
                privilege_id INTEGER NOT NULL
            );
            CREATE VIEW schema_version (major, minor, patch)
            AS SELECT 0, 0, 23;
            INSERT INTO privilege VALUES
                (5, 1, 'Access to selected gui.users functions', 'old'),
                (6, 1, 'Limited access for Single Server Mode', 'old');
            INSERT INTO role_has_privilege VALUES (4, 6);
        """)
        db.executescript(
            (SCHEMA_DIR / "mysqlsh_gui_backend_0.0.23_to_0.0.24.sqlite.sql").read_text()
        )
        privileges = [
            {"access_pattern": row[0]} for row in db.execute("""SELECT p.access_pattern
                FROM privilege p
                    INNER JOIN role_has_privilege r_p
                        ON p.id = r_p.privilege_id
                WHERE r_p.role_id = 4 AND p.privilege_type_id = 1""")
        ]

        assert db.execute(
            "SELECT major, minor, patch FROM schema_version"
        ).fetchone() == (0, 0, 24)
        assert command_matches_privileges("gui.users.get_profile", privileges)
        assert command_matches_privileges("mrs.foo.bar", privileges)
        assert not command_matches_privileges("gui.users.list_users", privileges)
    finally:
        db.close()
