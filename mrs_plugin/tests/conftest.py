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

# cSpell:ignore mysqlsh mariadb mdupgrade

import os
import shutil
import tempfile

import pytest

import mysqlsh
from mysqlsh.globals import sandbox

import mrs_plugin.tests.unit.helpers as helpers

PHONE_BOOKS = {}

# The root password of the sandbox the suite deploys, unless MYSQL_PASSWORD
# names another.
SANDBOX_PASSWORD = "mrs_pytest_root"


def pytest_addoption(parser):
    parser.addoption(
        "--mdupgrade",
        action="store_true",
        dest="mdupgrade",
        default=False,
        help="enable metadata upgrade tests (slow)",
    )


@pytest.fixture(scope="session", autouse=True)
def non_interactive_shell():
    """Runs the shell non-interactively for the duration of the test session."""
    mysqlsh.globals.shell.options.set("useWizards", False)
    yield


def _remove_sandbox(port: int, sandbox_dir: str) -> None:
    """Stops and deletes a sandbox, then removes its directory.

    Every step is best-effort: a sandbox that never started, or one the test
    already stopped, must not keep the next step from running.
    """
    for operation in (sandbox.stop, sandbox.kill, sandbox.delete):
        try:
            operation(port, {"sandboxDir": sandbox_dir})
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
    shutil.rmtree(sandbox_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def init_mrs():
    """Deploys the session's sandbox and configures MRS on it.

    The sandbox listens on a free port (MYSQL_PORT names one instead) under a
    temporary directory, with SSL off so the run does not depend on openssl.
    Its port and password are published through MYSQL_PORT and MYSQL_PASSWORD,
    which is where the helpers read the connection from, so every test and
    every later sandbox of the run agrees on them.

    The sandbox is stopped and deleted afterwards - also when the setup fails
    half-way, so a broken run leaves no server running on its port.

    Yields:
        The shell session connected to the sandbox.
    """
    if not helpers.server_binary_available():
        pytest.exit(
            "No mariadbd or mysqld binary on the PATH: the suite deploys a "
            "sandbox and cannot run without one.",
            returncode=1,
        )

    port = int(os.environ.get("MYSQL_PORT") or helpers.find_free_port())
    os.environ["MYSQL_PORT"] = str(port)
    os.environ.setdefault("MYSQL_PASSWORD", SANDBOX_PASSWORD)
    connection_data = helpers.get_connection_data()

    sandbox_dir = tempfile.mkdtemp(prefix="mrs_sandbox_")
    session = None
    temp_dirs = []
    try:
        sandbox.deploy(
            port,
            {
                "password": connection_data["password"],
                "sandboxDir": sandbox_dir,
                "ssl": False,
            },
        )

        session = helpers.create_shell_session()
        assert session is not None

        phone_book_dbs = ["PhoneBook", "MobilePhoneBook", "AnalogPhoneBook"]

        # ONLY_FULL_GROUP_BY keeps the plugin's queries valid on MariaDB,
        # which unlike MySQL 8 does not accept columns that merely depend on a
        # GROUP BY key; MSM deployment scripts run the REST DDL with this mode
        # set.
        session.run_sql("set sql_mode='ONLY_FULL_GROUP_BY'")

        helpers.create_test_db(session, "EmptyPhoneBook")
        for db in phone_book_dbs:
            helpers.create_test_db(session, db)

        # The shell's mrs module deploys the metadata schema
        session.run_sql("CONFIGURE REST METADATA")

        for db in phone_book_dbs:
            temp_dir = tempfile.TemporaryDirectory()
            temp_dirs.append(temp_dir)
            PHONE_BOOKS[db] = helpers.create_mrs_phonebook_schema(
                session, "/test", db, temp_dir
            )

        yield session
    finally:
        for temp_dir in temp_dirs:
            temp_dir.cleanup()

        if session is not None:
            session.close()

        _remove_sandbox(port, sandbox_dir)


@pytest.fixture(scope="session")
def phone_book(init_mrs):
    yield PHONE_BOOKS["PhoneBook"]


@pytest.fixture(scope="session")
def mobile_phone_book(init_mrs):
    yield PHONE_BOOKS["MobilePhoneBook"]


@pytest.fixture(scope="session")
def analog_phone_book(init_mrs):
    yield PHONE_BOOKS["AnalogPhoneBook"]


@pytest.fixture(scope="session")
def table_contents(phone_book):
    def create_table_content_object(
        table_name, schema=None, take_snapshot=True
    ) -> helpers.TableContents:
        schema = schema or phone_book
        return helpers.TableContents(schema["session"], table_name, take_snapshot)

    yield create_table_content_object
