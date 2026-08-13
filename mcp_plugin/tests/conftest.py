# Copyright (c) 2026, MariaDB plc.
#
# SPDX-License-Identifier: GPL-2.0-only
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
import shutil
import tempfile
from types import SimpleNamespace

import pytest

import mysqlsh

from mcp_plugin.lib import config
import mcp_plugin.tests.unit.helpers as helpers


def pytest_collection_modifyitems(items):
    """Orders the shared-sandbox lifecycle around the rest of the suite.

    ``test_sandbox_deploy`` runs first (it deploys the shared sandbox) and
    ``test_sandbox_shutdown`` runs last (it tears it down); every other test
    runs in between and uses the deployed sandbox. This uses the native pytest
    collection hook so no ordering plugin is required.
    """
    first, last, middle = [], [], []
    for item in items:
        if item.name == "test_sandbox_deploy":
            first.append(item)
        elif item.name == "test_sandbox_shutdown":
            last.append(item)
        else:
            middle.append(item)
    items[:] = first + middle + last


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


@pytest.fixture
def clean_config():
    """Isolates connection secrets and settings.json for a config/setup test.

    The current connections (URI + password) and allowed-paths settings are
    backed up before the test and restored exactly afterwards, so a test may
    freely add, clear or delete connections and paths.
    """
    original_connections = {
        uri: config.get_connection_password(uri)
        for uri in config.list_connection_uris()
    }
    had_settings = config.settings_file_exists()
    original_paths = config.get_allowed_paths()

    try:
        yield
    finally:
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
        if had_settings:
            config.set_allowed_paths(original_paths)
        else:
            settings_path = config.get_settings_file_path()
            if os.path.exists(settings_path):
                os.remove(settings_path)


@pytest.fixture(scope="session")
def sandbox():
    """Provides a shared sandbox context for the whole test session.

    The context (port, directory, connection URI and password) is created once;
    the sandbox directory is registered as an allowed path so the sandbox and db
    tools accept it. The connection is registered in the secret store by
    sandbox.deploy (via test_sandbox_deploy), not by this fixture.

    The sandbox itself is deployed by ``test_sandbox_deploy`` (which runs first)
    and torn down by ``test_sandbox_shutdown`` (which runs last). This fixture's
    finalizer only performs a best-effort stop/delete as a safety net, then
    removes the connection secret, the allowed-path entry and the directory.

    Yields:
        A namespace with ``port``, ``sandbox_dir``, ``uri``, ``password`` and
        ``instance_dir`` attributes.
    """
    if not helpers.server_binary_available():
        pytest.skip("No MariaDB/MySQL server binary available for sandboxes.")

    sandbox_dir = os.path.abspath(tempfile.mkdtemp(prefix="mcp_sandbox_"))
    port = helpers.find_free_port()
    ctx = SimpleNamespace(
        port=port,
        sandbox_dir=sandbox_dir,
        uri=f"root@127.0.0.1:{port}",
        password="mcp_pytest_root",
        instance_dir=os.path.join(sandbox_dir, str(port)),
        # Set to True by test_sandbox_deploy on success; dependent tests skip
        # when it is False (native replacement for a dependency plugin).
        deployed=False,
    )

    # Register the sandbox directory as an allowed path so the sandbox and db
    # tools accept it. The connection itself is registered by sandbox.deploy
    # (see test_sandbox_deploy) and removed again by sandbox.delete.
    had_settings = config.settings_file_exists()
    original_paths = config.get_allowed_paths()
    config.set_allowed_paths(original_paths + [sandbox_dir])

    try:
        yield ctx
    finally:
        # Safety-net teardown in case test_sandbox_shutdown did not run.
        for tool_name, arguments in (
            (
                "sandbox.stop",
                {
                    "port": port,
                    "sandbox_dir": sandbox_dir,
                    "password": ctx.password,
                },
            ),
            ("sandbox.delete", {"port": port, "sandbox_dir": sandbox_dir}),
        ):
            try:
                helpers.call_tool(["sandbox"], tool_name, arguments)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

        try:
            config.delete_connection(ctx.uri)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

        if had_settings:
            config.set_allowed_paths(original_paths)
        else:
            settings_path = config.get_settings_file_path()
            if os.path.exists(settings_path):
                os.remove(settings_path)

        shutil.rmtree(sandbox_dir, ignore_errors=True)
