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

import mysqlsh

from mcp_plugin.lib import config, setup, setup_migrator, setup_prompts
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
    uri = "mariadb://cfg_pytest@127.0.0.1:3306"

    config.store_connection(uri, "s3cret")
    assert uri in config.list_connection_uris()
    assert config.get_connection_password(uri) == "s3cret"

    config.delete_connection(uri)
    assert uri not in config.list_connection_uris()

    # A connection stored before the scheme was kept has no scheme in its key.
    # It is REPORTED with the default one filled in, which is the whole of what
    # this change does to an existing configuration - the key is left alone, so
    # the password is still read under it.
    config.store_connection("cfg_pytest@127.0.0.1:3306", "s3cret")
    assert "cfg_pytest@127.0.0.1:3306" in config.list_stored_connection_uris()
    assert uri in config.list_connection_uris()
    assert config.get_connection_password("cfg_pytest@127.0.0.1:3306") == "s3cret"

    config.delete_connection("cfg_pytest@127.0.0.1:3306")


def _empty_both_connection_lists():
    """Empties both connection lists so a test can assert on them exactly.

    The clean_config fixture backs the lists up and restores them afterwards
    but does NOT clear them first, so without this a developer's own configured
    connections turn up in these assertions.
    """
    for kind in config.SUPPORTED_CONNECTION_KINDS:
        for uri in config.list_stored_connection_uris(kind):
            config.delete_connection(uri, kind)


def test_the_two_connection_lists_are_kept_apart(clean_config):
    """One URI can be in both lists, under two passwords, and stays separate.

    The MCP list and the GUI list have different owners - mcp.setup curates the
    first, the VS Code extension the second - so a connection made in one must
    not appear in, or be removed by, an operation on the other. The same server
    in both is the case that would give it away, since only the secret prefix
    tells the two entries apart.
    """
    _empty_both_connection_lists()
    uri = "kind_pytest@127.0.0.1:3306"

    config.store_connection(uri, "mcp-secret")
    config.store_connection(uri, "gui-secret", config.CONNECTION_KIND_GUI)

    reported = config.with_default_scheme(uri)
    assert config.list_connection_uris() == [reported]
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == [reported]

    assert config.get_connection_password(uri) == "mcp-secret"
    assert (
        config.get_connection_password(uri, config.CONNECTION_KIND_GUI)
        == "gui-secret"
    )

    # Deleting one leaves the other exactly as it was.
    config.delete_connection(uri, config.CONNECTION_KIND_GUI)
    assert config.list_connection_uris(config.CONNECTION_KIND_GUI) == []
    assert config.list_connection_uris() == [reported]
    assert config.get_connection_password(uri) == "mcp-secret"


def test_an_unknown_connection_kind_is_refused():
    """A kind that names neither list is an error, never a silent default.

    It arrives straight from a tool argument, and defaulting a misspelling
    would read, write or delete in the other list than the caller meant.
    """
    assert config.normalize_connection_kind(None) == config.CONNECTION_KIND_MCP
    # Spelled as a client might: cased differently, with space around it.
    assert config.normalize_connection_kind(" GUI ") == config.CONNECTION_KIND_GUI

    with pytest.raises(mysqlsh.Error) as refused:
        config.normalize_connection_kind("mysql")

    assert "not a known connection kind" in str(refused.value)


def test_resolving_a_connection_reports_the_list_it_was_found_in(clean_config):
    """find_connection answers with the kind, and searches in the given order.

    The URI alone stops identifying a connection once there are two lists: the
    password is read under the kind, so whatever resolves one has to say which
    list it came out of.
    """
    _empty_both_connection_lists()
    shared = "both_pytest@127.0.0.1:3306"
    gui_only = "gui_pytest@127.0.0.1:3306"

    config.store_connection(shared, "mcp-secret")
    config.store_connection(shared, "gui-secret", config.CONNECTION_KIND_GUI)
    config.store_connection(gui_only, "gui-secret", config.CONNECTION_KIND_GUI)

    both = (config.CONNECTION_KIND_GUI, config.CONNECTION_KIND_MCP)

    # A URI in both lists resolves to the first kind searched rather than being
    # refused as ambiguous - the caller chose the order.
    assert config.find_connection(shared, both) == (
        shared, config.CONNECTION_KIND_GUI
    )
    assert config.find_connection(shared, reversed(both)) == (
        shared, config.CONNECTION_KIND_MCP
    )

    # A URI in one list only is found whichever order is used, and not found at
    # all where that list is not searched.
    assert config.find_connection(gui_only, both) == (
        gui_only, config.CONNECTION_KIND_GUI
    )
    assert config.find_connection(
        gui_only, (config.CONNECTION_KIND_MCP,)
    ) is None

    # The single-list form is the MCP list unless told otherwise, which is what
    # every caller written before the GUI list means.
    assert config.resolve_connection_uri(gui_only) is None
    assert (
        config.resolve_connection_uri(gui_only, config.CONNECTION_KIND_GUI)
        == gui_only
    )
    # And a spelling that only NAMES the connection still resolves, per list.
    assert (
        config.resolve_connection_uri(
            "mariadb://" + gui_only, config.CONNECTION_KIND_GUI
        )
        == gui_only
    )


def test_only_the_mcp_list_is_searched_outside_gui_mode(clean_config):
    """usable_connection_kinds is what keeps the GUI list out of reach.

    The GUI list is written by a server started with --gui and meant for the
    extension. Anywhere else it must not be openable, or a connection the user
    made for their editor would quietly be handed to whatever else drives this
    server.
    """
    _empty_both_connection_lists()
    assert config.usable_connection_kinds() == (config.CONNECTION_KIND_MCP,)

    uri = "reach_pytest@127.0.0.1:3306"
    config.store_connection(uri, "gui-secret", config.CONNECTION_KIND_GUI)

    assert config.find_connection(uri) is None


def test_connection_uris_reduce_to_one_spelling():
    """Every way of writing one connection normalizes to the same URI.

    db.list_connections hands out the stored spelling, but a client writing a
    URI itself leaves the scheme off, leaves out the default port or cases the
    host differently - and would be told a connection it can see listed is not
    configured.
    """
    normalize = config.normalize_connection_uri
    canonical = "mariadb://root@127.0.0.1:3306"

    # The scheme is what a URI without one means, written out, so that both
    # spellings are the one connection.
    assert normalize("root@127.0.0.1:3306") == canonical
    assert normalize(canonical) == canonical
    assert normalize("MariaDB://root@127.0.0.1") == canonical
    # The password is read from the secret store, so one in the URI says nothing
    # about which connection is meant; a trailing slash and padding say nothing
    # at all.
    assert normalize("  root:ignored@127.0.0.1:3306/  ") == canonical
    # Host names are case-insensitive - user names are not.
    assert normalize("root@LOCALHOST") == "mariadb://root@localhost:3306"

    # Kept apart: another server, another user, another protocol, and a URI
    # asking for something the configured connection would not give it. mysql://
    # is among them - it is a synonym of mariadb:// to the shell, but keeping
    # the scheme is what makes +ssh reachable, and folding only SOME schemes
    # together would mean deciding which spelling a listing reports.
    assert normalize("root@127.0.0.1:3307") != canonical
    assert normalize("admin@127.0.0.1:3306") != canonical
    assert normalize("mysql://root@127.0.0.1:3306") != canonical
    assert normalize("mysqlx://root@127.0.0.1:3306") != canonical
    assert normalize("root@127.0.0.1:3306/mysql") != canonical
    assert normalize("root@127.0.0.1:3306?ssl-mode=REQUIRED") != canonical

    # An SSH tunnel is asked for by scheme and nothing else, so the scheme has
    # to survive normalization for one to be configurable at all. The authority
    # of a +ssh URI is the database, so it takes the same default port.
    assert (
        normalize("mariadb+ssh://root@127.0.0.1")
        == "mariadb+ssh://root@127.0.0.1:3306"
    )
    assert normalize("mariadb+ssh://root@127.0.0.1:3306") != canonical

    # A socket is not a host, and a port on one would be nonsense.
    assert normalize("root@%2Ftmp%2Fmysql.sock") == (
        "mariadb://root@%2Ftmp%2Fmysql.sock"
    )

    # Normalizing a normalized URI changes nothing: both the stored URIs and the
    # ones passed in go through this, so it has to be a fixed point.
    for uri in (canonical, "mariadb+ssh://root@127.0.0.1:3306",
                "mysqlx://root@127.0.0.1", "root@%2Ftmp%2Fmysql.sock"):
        assert normalize(normalize(uri)) == normalize(uri)

    # Not a URI at all, so not something that could be opened either.
    for not_a_uri in ("", "   ", "mariadb://", "not a uri", None, 3306):
        assert normalize(not_a_uri) is None


def test_filling_in_the_scheme_leaves_everything_else_alone():
    """with_default_scheme is textual, and only ever adds what is missing.

    It runs on URIs that are already in the form they are meant to keep - a
    stored key on its way to being reported - so it must not re-render them,
    and it has to survive a key this shell can no longer parse.
    """
    with_scheme = config.with_default_scheme

    assert with_scheme("root@127.0.0.1:3306") == "mariadb://root@127.0.0.1:3306"
    assert with_scheme("  root@127.0.0.1  ") == "mariadb://root@127.0.0.1"

    # Already spelled with one, whichever it is, and nothing is re-rendered:
    # no default port, no reordering.
    for uri in (
        "mariadb://root@127.0.0.1",
        "mariadb+ssh://root@127.0.0.1",
        "mysqlx://root@127.0.0.1",
        "MariaDB://root@127.0.0.1",
    ):
        assert with_scheme(uri) == uri

    # Nothing to name a connection with, handed back for the parser to refuse.
    assert with_scheme("") == ""
    assert with_scheme(None) is None


def test_superseding_a_spelling_needs_one_it_can_recognize(clean_config):
    """A URI this shell cannot parse names no connection, so it drops nothing.

    Guessing would be worse: the key it would delete holds a password, and the
    one thing known about it is that it cannot be shown to be the same one.
    """
    _empty_both_connection_lists()
    config.store_connection("drop_pytest@127.0.0.1:3306", "s3cret")

    assert config.drop_superseded_spellings("not a uri") == []
    assert config.list_stored_connection_uris() == ["drop_pytest@127.0.0.1:3306"]


def test_a_connection_stored_without_a_scheme_still_resolves(clean_config):
    """The old spelling keeps working, and configuring it again replaces it.

    Connections configured before MariaDB Shell 26.9.3 are stored with no
    scheme at all, because the shell's parser rejected one. Nothing migrates
    them, so every lookup has to reach them - and storing the new spelling has
    to take the old key with it, or the two would sit side by side and the pair
    would resolve to neither.
    """
    _empty_both_connection_lists()
    old_key = "legacy_pytest@127.0.0.1:3306"
    config.store_connection(old_key, "s3cret")

    # Reported with the scheme, stored without it, and resolving either way.
    assert config.list_stored_connection_uris() == [old_key]
    assert config.list_connection_uris() == ["mariadb://" + old_key]
    assert config.resolve_connection_uri(old_key) == old_key
    assert config.resolve_connection_uri("mariadb://" + old_key) == old_key

    # Configuring it again writes the canonical key and drops the old one.
    canonical = config.normalize_connection_uri(old_key)
    config.store_connection(canonical, "s3cret")
    assert config.drop_superseded_spellings(canonical) == [old_key]

    assert config.list_stored_connection_uris() == [canonical]
    assert config.resolve_connection_uri(old_key) == canonical


def test_a_connection_uri_resolves_to_the_configured_one(clean_config):
    """A URI naming a configured connection resolves to its stored spelling.

    That spelling is the key everything else works with: the password is read
    under it and the session is opened, logged and re-validated on it.
    """
    import mysqlsh

    stored = "mariadb://res_pytest@127.0.0.1:3306"
    config.store_connection(stored, "s3cret")

    assert config.resolve_connection_uri(stored) == stored
    assert config.resolve_connection_uri("res_pytest@127.0.0.1:3306") == stored
    assert config.resolve_connection_uri("res_pytest@127.0.0.1") == stored

    # No configured connection names any of these.
    assert config.resolve_connection_uri("res_pytest@127.0.0.1:3307") is None
    assert config.resolve_connection_uri("other@127.0.0.1:3306") is None
    assert config.resolve_connection_uri(stored + "/mysql") is None
    assert config.resolve_connection_uri("mysqlx://res_pytest@127.0.0.1") is None
    assert config.resolve_connection_uri("not a uri") is None

    # The same connection configured twice, under two spellings: they can hold
    # different passwords, so which one was meant is not for this to guess.
    # Written straight to the store, because the way in through the plugin -
    # storing and then dropping the superseded spellings - is what stops this
    # state from arising in the first place.
    duplicate = "res_pytest@127.0.0.1:3306"
    config.store_connection(duplicate, "another")

    with pytest.raises(mysqlsh.Error) as ambiguous:
        config.resolve_connection_uri("res_pytest@127.0.0.1")

    assert "more than one configured connection" in str(ambiguous.value)

    # Each of the two still resolves to itself, spelled as it is stored - an
    # exact match is never ambiguous.
    assert config.resolve_connection_uri(stored) == stored
    assert config.resolve_connection_uri(duplicate) == duplicate

    # And with the duplicate gone it resolves again: the ambiguity was in the
    # configuration, not in the URI.
    config.delete_connection(duplicate)
    assert config.resolve_connection_uri("res_pytest@127.0.0.1") == stored


# --- lib/setup.py (interactive flows) -------------------------------------


class _FakeShell:
    """Shell stand-in that scripts prompt answers for the setup tests.

    ``prompt`` honours the ``type`` option the way the real shell does, so the
    scripted answers stay in the terse form a person would type ("y", "3") while
    the prompt still hands back what the shell hands back. That matters because
    what the shell returns is NOT what was typed:

    * a ``confirm`` prompt answers with the LABEL of the chosen option,
      ampersand included - ``'&Yes'`` / ``'&No'`` - or the ``defaultValue`` for
      an empty reply;
    * a ``select`` prompt answers with the TEXT of the chosen option, not its
      number, and applies ``defaultValue`` (a 1-BASED index) on an empty reply.

    Both were verified against a real mariadb-shell before this was written; see
    ``shell.help("prompt")``. Everything else is a plain text prompt that
    answers with the string as typed.

    The shell also re-asks until the reply is valid, which is deliberately NOT
    emulated: a test that scripts an invalid answer has a bug in the script, and
    an assertion says so immediately instead of looping.
    """

    def __init__(self, answers):
        self._answers = list(answers)
        self.options = SimpleNamespace(useWizards=True)
        # Every prompt asked, so a test can assert on what was OFFERED. For a
        # select prompt that is the real contract now: the shell renders the
        # numbered list, so the choices reach the user rather than anything the
        # plugin printed itself.
        self.prompts = []

    def select_prompts(self) -> list:
        """Returns the choices of every select prompt asked, in order."""
        return [
            (asked or {}).get("options") or []
            for _, asked in self.prompts
            if (asked or {}).get("type") == "select"
        ]

    def prompt(self, message, options=None):
        assert self._answers, f"unexpected prompt: {message!r}"
        self.prompts.append((message, options))
        answer = self._answers.pop(0)
        options = options or {}
        prompt_type = options.get("type", "text")
        default = options.get("defaultValue")

        if prompt_type == "confirm":
            if answer == "":
                assert default is not None, f"no default to apply: {message!r}"
                return default
            assert answer.lower() in ("y", "yes", "n", "no"), (
                f"{answer!r} is not a valid confirm answer for {message!r}"
            )
            return "&Yes" if answer.lower() in ("y", "yes") else "&No"

        if prompt_type == "select":
            choices = options.get("options") or []
            if answer == "":
                assert default is not None, f"no default to apply: {message!r}"
                return choices[int(default) - 1]
            assert answer.isdigit() and 1 <= int(answer) <= len(choices), (
                f"{answer!r} is not one of the {len(choices)} choices for "
                f"{message!r}"
            )
            return choices[int(answer) - 1]

        return answer

    def parse_uri(self, uri):
        return {"uri": uri}

    def open_session(self, connection_data):
        # A successful (fake) connection; setup only calls close() on it.
        return SimpleNamespace(close=lambda: None)


def _clear_config():
    """Removes all connections and the settings file for a clean start."""
    for uri in config.list_stored_connection_uris():
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
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    assert "mariadb://setup_a@127.0.0.1:3306" in config.list_connection_uris()
    assert (
        config.get_connection_password("mariadb://setup_a@127.0.0.1:3306")
        == "secret"
    )

    # The four yes/no questions are the shell's own CONFIRM prompts, and the
    # password its own password prompt. That is the whole point of asking
    # through the shell: it renders the answers, applies the default and
    # re-asks, none of which is reimplemented here.
    types = [(asked or {}).get("type") for _, asked in fake_shell.prompts]
    assert types.count("confirm") == 4, types
    assert types.count("password") == 1, types
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
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    # Both the added connection and path were removed again.
    assert "mariadb://setup_b@127.0.0.1:3306" not in config.list_connection_uris()
    assert os.path.abspath(path) not in config.get_allowed_paths()

    # The migration tooling entry offers whatever applies to what is installed,
    # rather than a fixed "download" (see tests/unit/test_setup_migrator.py).
    # Compared against the label itself so this holds whatever happens to be
    # installed. Asserted on the CHOICES the select prompt was given, not on
    # printed output: the shell renders the numbered list itself now.
    # The menu is re-offered every round, so the selects interleave: pick them
    # apart by their last choice rather than by position.
    selects = fake_shell.select_prompts()
    menus = [c for c in selects if c and c[-1] == setup.MENU_FINISH_LABEL]
    assert menus, "the management menu was never offered as a select prompt"
    assert menus[0] == [
        "Add a connection",
        "Delete a connection",
        "Add an allowed path",
        "Delete an allowed path",
        setup_migrator.menu_label(),
        setup.MENU_FINISH_LABEL,
    ]

    # The two deletions each offered their one item plus a way out, in the
    # order the menu drove them.
    assert [c for c in selects if c and c[-1] == setup_prompts.CANCEL_LABEL] == [
        [os.path.abspath(path), setup_prompts.CANCEL_LABEL],
        ["mariadb://setup_b@127.0.0.1:3306", setup_prompts.CANCEL_LABEL],
    ]


def test_setup_menu_hides_the_migrator_where_it_is_unsupported(
    clean_config, tmp_path, monkeypatch, capsys
):
    """On Windows the whole migration step is absent from the interactive setup.

    Driven through run_setup rather than through _menu_entries (see
    tests/unit/test_setup_migrator.py) because "not shown" covers the status line the
    menu prints above itself as well as the entry itself - and because the
    scripted answers are what prove the renumbering: "5" has to be Finish, so a
    menu that still had six choices would select the migration entry instead and
    then leave its prompts unanswered, which _FakeShell fails on.
    """
    _clear_config()
    config.set_allowed_paths([])
    monkeypatch.setattr(setup_migrator, "is_supported", lambda: False)

    fake_shell = _FakeShell(["5"])  # menu: finish, which is 5 without the entry
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    # The choices the select prompt was given are the menu now, so the absent
    # entry is asserted there - and this pins that Finish really is the fifth,
    # which the scripted "5" relies on.
    assert fake_shell.select_prompts() == [
        [
            "Add a connection",
            "Delete a connection",
            "Add an allowed path",
            "Delete an allowed path",
            setup.MENU_FINISH_LABEL,
        ]
    ]

    # The status line the menu prints above itself is gated too.
    menu = capsys.readouterr().out
    assert "migration tooling" not in menu.lower()
    assert "Migration tooling" not in menu


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
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    # Stored under the normalized URI rather than what was typed - and the
    # unparsable entry never reached the password prompt, which the scripted
    # answers are what prove: _FakeShell asserts on an unexpected prompt.
    assert config.list_connection_uris() == ["mariadb://setup_c@127.0.0.1:3306"]
    assert (
        config.get_connection_password("mariadb://setup_c@127.0.0.1:3306") == "pw"
    )


def test_setup_requires_interactive_shell(clean_config, monkeypatch):
    """run_setup refuses to run when the shell is non-interactive."""
    import mysqlsh

    monkeypatch.setattr(
        setup_prompts,
        "shell",
        lambda: SimpleNamespace(options=SimpleNamespace(useWizards=False)),
    )

    with pytest.raises(mysqlsh.Error):
        setup.run_setup()
