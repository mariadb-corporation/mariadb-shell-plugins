# Copyright (c) 2026, MariaDB plc.
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

Also covers how a connection URI is compared: a connection is looked up by its
URI, and the spelling a client sends is not the one it was configured under, so
the spellings that name the same connection have to reduce to one - while the
ones that ask for more than the configured connection gives must not.
"""

# cSpell:ignore mysqlsh MariaDB mysqlx

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

    assert result.is_error is False

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


def test_connection_uris_reduce_to_one_spelling():
    """Every way of writing one connection normalizes to the same URI.

    db.list_connections hands out the stored spelling, but a client writing a
    URI itself puts a scheme in front of it, leaves out the default port or
    cases the host differently - and would be told a connection it can see
    listed is not configured.
    """
    normalize = config.normalize_connection_uri
    bare = "root@127.0.0.1:3306"

    # mariadb:// is not a scheme the shell's own parser even accepts, which is
    # why it has to be taken off before the URI is parsed.
    assert normalize(bare) == bare
    assert normalize("mariadb://" + bare) == bare
    assert normalize("mysql://" + bare) == bare
    assert normalize("MariaDB://root@127.0.0.1") == bare
    # The password is read from the secret store, so one in the URI says nothing
    # about which connection is meant; a trailing slash and padding say nothing
    # at all.
    assert normalize("  root:ignored@127.0.0.1:3306/  ") == bare
    # Host names are case-insensitive - user names are not.
    assert normalize("root@LOCALHOST") == "root@localhost:3306"

    # Kept apart: another server, another user, another protocol, and a URI
    # asking for something the configured connection would not give it.
    assert normalize("root@127.0.0.1:3307") != bare
    assert normalize("admin@127.0.0.1:3306") != bare
    assert normalize("mysqlx://" + bare) != bare
    assert normalize(bare + "/mysql") != bare
    assert normalize(bare + "?ssl-mode=REQUIRED") != bare

    # Normalizing a normalized URI changes nothing: both the stored URIs and the
    # ones passed in go through this, so it has to be a fixed point.
    assert normalize(normalize("mariadb://root@127.0.0.1")) == bare

    # Not a URI at all, so not something that could be opened either.
    for not_a_uri in ("", "   ", "mariadb://", "not a uri", None, 3306):
        assert normalize(not_a_uri) is None


def test_a_connection_uri_resolves_to_the_configured_one(clean_config):
    """A URI naming a configured connection resolves to its stored spelling.

    That spelling is the key everything else works with: the password is read
    under it and the session is opened, logged and re-validated on it.
    """
    import mysqlsh

    stored = "res_pytest@127.0.0.1:3306"
    config.store_connection(stored, "s3cret")

    assert config.resolve_connection_uri(stored) == stored
    assert config.resolve_connection_uri("mariadb://" + stored) == stored
    assert config.resolve_connection_uri("res_pytest@127.0.0.1") == stored

    # No configured connection names any of these.
    assert config.resolve_connection_uri("res_pytest@127.0.0.1:3307") is None
    assert config.resolve_connection_uri("other@127.0.0.1:3306") is None
    assert config.resolve_connection_uri(stored + "/mysql") is None
    assert config.resolve_connection_uri("not a uri") is None

    # The same connection configured twice, under two spellings: they can hold
    # different passwords, so which one was meant is not for this to guess.
    duplicate = "mysql://" + stored
    config.store_connection(duplicate, "another")

    with pytest.raises(mysqlsh.Error) as ambiguous:
        config.resolve_connection_uri("mariadb://" + stored)

    assert "more than one configured connection" in str(ambiguous.value)

    # Each of the two still resolves to itself, spelled as it is stored - an
    # exact match is never ambiguous.
    assert config.resolve_connection_uri(stored) == stored
    assert config.resolve_connection_uri(duplicate) == duplicate

    # And with the duplicate gone it resolves again: the ambiguity was in the
    # configuration, not in the URI.
    config.delete_connection(duplicate)
    assert config.resolve_connection_uri("mariadb://" + stored) == stored


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


def test_setup_menu_add_and_delete(clean_config, tmp_path, monkeypatch, capsys):
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
        "6",                       # menu: finish
    ]
    fake_shell = _FakeShell(answers)
    monkeypatch.setattr(setup, "_shell", lambda: fake_shell)

    setup.run_setup()

    # Both the added connection and path were removed again.
    assert "setup_b@127.0.0.1:3306" not in config.list_connection_uris()
    assert os.path.abspath(path) not in config.get_allowed_paths()

    # The migration tooling entry offers whatever applies to what is installed,
    # rather than a fixed "download" (see tests/unit/test_migrator.py). Compared
    # against the label itself so this holds whatever happens to be installed.
    menu = capsys.readouterr().out
    assert f"5. {setup._migrator_menu_label()}" in menu


def test_setup_stores_a_connection_under_one_spelling(clean_config, monkeypatch):
    """What is typed is stored normalized, and only if it is a URI at all.

    The stored URI is the key a connection is looked up under, so one spelling
    per connection is what keeps the same connection from being configured
    twice - the one case db.connect cannot resolve for itself.
    """
    _clear_config()
    # A settings file makes run_setup use the management menu.
    config.set_allowed_paths([])

    answers = [
        "1",                                # menu: add a connection
        "not a uri",                        # refused, and no password asked for
        "1",                                # menu: add a connection
        "  mariadb://setup_c@127.0.0.1  ",  # the same connection, spelled out
        "pw",                               # password
        "6",                                # menu: finish
    ]
    fake_shell = _FakeShell(answers)
    monkeypatch.setattr(setup, "_shell", lambda: fake_shell)

    setup.run_setup()

    # Stored under the normalized URI rather than what was typed - and the
    # unparsable entry never reached the password prompt, which the scripted
    # answers are what prove: _FakeShell asserts on an unexpected prompt.
    assert config.list_connection_uris() == ["setup_c@127.0.0.1:3306"]
    assert config.get_connection_password("setup_c@127.0.0.1:3306") == "pw"


def test_setup_requires_interactive_shell(clean_config, monkeypatch):
    """run_setup refuses to run when the shell is non-interactive."""
    import mysqlsh

    monkeypatch.setattr(
        setup, "_shell", lambda: SimpleNamespace(options=SimpleNamespace(useWizards=False))
    )

    with pytest.raises(mysqlsh.Error):
        setup.run_setup()
