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

"""Tests for connection/allowed-path configuration and the mcp.setup flow.

Covers lib/config.py (secret-backed connections and settings.json allowed
paths) directly, the db.list_connections tool over stdio, and lib/setup.py's
interactive first-run and menu flows with a scripted fake shell.
"""

# cSpell:ignore mysqlsh MariaDB

import os
from types import SimpleNamespace

import pytest

from mcp_plugin.lib import config, setup
import mcp_plugin.tests.unit.helpers as helpers


# --- db.list_connections over stdio ---------------------------------------


def test_stdio_lists_stored_connections(stored_connections):
    """Storing two connections and reading them back over the stdio server."""
    pytest.importorskip("mcp")

    result = helpers.call_tool(
        function_groups=["db"],
        tool_name="db.list_connections",
    )

    assert result.isError is False

    listed = helpers.tool_payload(result)
    assert isinstance(listed, list)

    for uri in stored_connections:
        assert uri in listed


# --- lib/config.py --------------------------------------------------------


def test_config_allowed_paths(clean_config, tmp_path):
    """Allowed paths persist and gate access by directory containment."""
    allowed = tmp_path / "data"
    allowed.mkdir()

    config.set_allowed_paths([str(allowed)])
    assert config.settings_file_exists()
    assert config.get_allowed_paths() == [str(allowed)]

    # The directory itself and paths inside it are allowed.
    assert config.is_path_allowed(str(allowed)) is True
    assert config.is_path_allowed(str(allowed / "sub" / "file.sql")) is True
    # A sibling outside the allowed directory is not.
    assert config.is_path_allowed(str(tmp_path / "other")) is False

    # With no allowed paths configured, nothing is allowed.
    config.set_allowed_paths([])
    assert config.is_path_allowed(str(allowed)) is False


def test_config_connection_secrets(clean_config):
    """Connections round-trip through the secret store."""
    uri = "cfg_pytest@127.0.0.1:3306"

    config.store_connection(uri, "s3cret")
    assert uri in config.list_connection_uris()
    assert config.get_connection_password(uri) == "s3cret"

    config.delete_connection(uri)
    assert uri not in config.list_connection_uris()


# --- lib/setup.py (interactive flows) -------------------------------------


class _FakeShell:
    """Minimal shell stand-in that scripts prompt answers for setup tests."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.options = SimpleNamespace(useWizards=True)

    def prompt(self, message, options=None):
        assert self._answers, f"unexpected prompt: {message!r}"
        return self._answers.pop(0)

    def parse_uri(self, uri):
        return {"uri": uri}

    def open_session(self, connection_data):
        # A successful (fake) connection; setup only calls close() on it.
        return SimpleNamespace(close=lambda: None)


def _clear_config():
    """Removes all connections and the settings file for a clean start."""
    for uri in config.list_connection_uris():
        config.delete_connection(uri)
    settings_path = config.get_settings_file_path()
    if os.path.exists(settings_path):
        os.remove(settings_path)


def test_setup_first_run(clean_config, tmp_path, monkeypatch):
    """First run (no settings): guided add of a connection and an allowed path."""
    _clear_config()

    answers = [
        "y",                       # Add a connection?
        "setup_a@127.0.0.1:3306",  # connection URI
        "secret",                  # password
        "n",                       # Add another connection? -> stop
        "y",                       # Add an allowed path?
        str(tmp_path),             # path to allow
        "n",                       # Add another path? -> stop
    ]
    # A single shared fake so prompt answers are consumed in sequence across all
    # _shell() calls.
    fake_shell = _FakeShell(answers)
    monkeypatch.setattr(setup, "_shell", lambda: fake_shell)

    setup.run_setup()

    assert "setup_a@127.0.0.1:3306" in config.list_connection_uris()
    assert config.get_connection_password("setup_a@127.0.0.1:3306") == "secret"
    assert os.path.abspath(str(tmp_path)) in config.get_allowed_paths()


def test_setup_menu_add_and_delete(clean_config, tmp_path, monkeypatch):
    """Subsequent run (settings exist): add then delete a connection and path."""
    _clear_config()
    # A settings file makes run_setup use the management menu instead of the
    # guided first-run flow.
    config.set_allowed_paths([])

    path = str(tmp_path)
    answers = [
        "1",                       # menu: add a connection
        "setup_b@127.0.0.1:3306",  # URI
        "pw",                      # password
        "3",                       # menu: add an allowed path
        path,                      # path
        "4",                       # menu: delete an allowed path
        "1",                       # select first path
        "2",                       # menu: delete a connection
        "1",                       # select first connection
        "5",                       # menu: finish
    ]
    fake_shell = _FakeShell(answers)
    monkeypatch.setattr(setup, "_shell", lambda: fake_shell)

    setup.run_setup()

    # Both the added connection and path were removed again.
    assert "setup_b@127.0.0.1:3306" not in config.list_connection_uris()
    assert os.path.abspath(path) not in config.get_allowed_paths()


def test_setup_requires_interactive_shell(clean_config, monkeypatch):
    """run_setup refuses to run when the shell is non-interactive."""
    import mysqlsh

    monkeypatch.setattr(
        setup, "_shell", lambda: SimpleNamespace(options=SimpleNamespace(useWizards=False))
    )

    with pytest.raises(mysqlsh.Error):
        setup.run_setup()
