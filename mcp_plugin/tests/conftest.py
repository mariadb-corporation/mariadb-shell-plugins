# Copyright (c) 2026, MariaDB plc and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

# cSpell:ignore mysqlsh MariaDB

import os
import tempfile

import pytest

import mysqlsh

from mcp_plugin.lib import config
import mcp_plugin.tests.unit.helpers as helpers


@pytest.fixture(scope="session", autouse=True)
def non_interactive_shell():
    """Runs the shell non-interactively for the duration of the test session."""
    mysqlsh.globals.shell.options.set("useWizards", False)
    yield


@pytest.fixture
def stored_connections():
    """Stores the two test connections, restoring prior state afterwards.

    Any connections that existed before the test are backed up (URI and
    password) and restored exactly on teardown, so the test leaves the secret
    store as it found it.

    Yields:
        The list of test connection URIs.
    """
    # Back up the connections that existed prior to the test, then remove them
    # so the test starts from a clean, known set.
    original_connections = {
        uri: config.get_connection_password(uri)
        for uri in config.list_connection_uris()
    }
    for uri in original_connections:
        try:
            config.delete_connection(uri)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    for uri in helpers.TEST_CONNECTION_URIS:
        config.store_connection(uri, helpers.TEST_CONNECTION_PASSWORD)

    yield list(helpers.TEST_CONNECTION_URIS)

    # Restore the original set of connections exactly: drop everything that is
    # currently stored, then re-store the backed-up connections.
    for uri in config.list_connection_uris():
        try:
            config.delete_connection(uri)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
    for uri, password in original_connections.items():
        try:
            config.store_connection(uri, password)
        except Exception:  # noqa: BLE001 - best-effort restore
            pass


@pytest.fixture
def allowed_temp_dir():
    """Provides a temp directory registered as an allowed path.

    The directory (and anything created inside it) and the allowed-paths
    configuration are restored/removed after the test.

    Yields:
        The absolute path of the temporary directory.
    """
    had_settings = config.settings_file_exists()
    original_paths = config.get_allowed_paths()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = os.path.abspath(temp_dir)
        config.set_allowed_paths(original_paths + [temp_dir])
        try:
            yield temp_dir
        finally:
            if had_settings:
                config.set_allowed_paths(original_paths)
            else:
                settings_path = config.get_settings_file_path()
                if os.path.exists(settings_path):
                    os.remove(settings_path)
