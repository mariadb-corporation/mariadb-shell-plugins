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

"""Tests for the non-interactive mcp.setup (lib/setup_cli.py).

Every item the interactive menu offers has a command-line option, so these
cover the option surface: what each action does, which combinations are refused
before anything is touched, and where a password may come from.

Tests that really add or delete a connection or a path use the clean_config
fixture, which backs the current configuration up and restores it afterwards -
the shell's secret store is shared with the developer's own connections and is
NOT isolated by MARIADB_SHELL_USER_CONFIG_HOME.
"""

# cSpell:ignore mysqlsh MariaDB

import io
import json
import os
from types import SimpleNamespace

import pytest

import mysqlsh

from mcp_plugin.lib import config, general, setup, setup_cli, setup_migrator
from mcp_plugin.lib import setup_prompts as prompts


@pytest.fixture
def stored_connection(clean_config, monkeypatch):
    """Makes storing a connection succeed without opening a real session.

    Yields:
        The list every verified URI is appended to.
    """
    verified = []
    monkeypatch.setattr(
        setup_cli, "verify_connection", lambda uri, password: verified.append(uri)
    )
    yield verified


def _wizard_shell(answers=()):
    """Returns a shell stand-in that can prompt, scripted with answers."""
    remaining = list(answers)

    def prompt(message, options=None):
        assert remaining, f"unexpected prompt: {message!r}"
        return remaining.pop(0)

    return SimpleNamespace(options=SimpleNamespace(useWizards=True), prompt=prompt)


# --- Option names and parsing ----------------------------------------------


def test_option_names_are_reported_as_the_help_spells_them():
    """The help lists only camelCase, so a message must not say anything else."""
    assert setup_cli._cli_name("add_connection") == "--addConnection"
    assert setup_cli._cli_name("password_env") == "--passwordEnv"
    assert setup_cli._cli_name("non_interactive") == "--nonInteractive"
    assert setup_cli._cli_name("json") == "--json"


def test_lists_are_taken_comma_separated_or_as_a_list():
    """The command line passes a list as one comma-separated string."""
    assert setup_cli._as_list("a,b") == ["a", "b"]
    assert setup_cli._as_list(" a , b ,, ") == ["a", "b"]
    assert setup_cli._as_list(["a", " b "]) == ["a", "b"]
    assert setup_cli._as_list(None) == []
    assert setup_cli._as_list("") == []


def test_any_option_at_all_switches_off_the_walkthrough():
    """A misspelled option must be refused, not answered with a walkthrough."""
    assert setup_cli.has_options({}) is False
    assert setup_cli.has_options({"show": True}) is True
    # Unrecognized too: it has to reach the refusal rather than look like
    # "no options were given".
    assert setup_cli.has_options({"addPathz": "/tmp"}) is True


def test_an_unknown_option_is_refused_and_the_known_ones_listed():
    """The Python API accepts any keyword, so the refusal happens here."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"addPathz": "/tmp"})

    message = str(error.value)
    assert "Unknown option(s): addPathz" in message
    assert "--addPaths" in message


# --- Combinations refused before anything is touched -----------------------


def test_show_cannot_be_combined_with_an_action():
    """--show reports; it does not also change things."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"show": True, "add_paths": "/tmp"})

    assert "--show only reports the configuration" in str(error.value)
    assert "--addPaths" in str(error.value)


def test_json_only_applies_to_show():
    """Nothing else produces machine-readable output."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"json": True, "add_paths": "/tmp"})

    assert "--json only applies to --show" in str(error.value)


def test_exactly_one_password_source_is_allowed():
    """Which password was meant is not something to guess at."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({
            "add_connection": "u@h:3306", "password": "x", "password_env": "Y",
        })

    message = str(error.value)
    assert "Give exactly one of" in message
    assert "--password" in message and "--passwordEnv" in message


def test_the_password_options_only_apply_to_adding_a_connection():
    """Passed with anything else they would silently do nothing."""
    for option in ("password", "password_env", "password_stdin", "no_verify"):
        with pytest.raises(mysqlsh.Error) as error:
            setup_cli.apply({"add_paths": "/tmp", option: "x"})

        assert "only applies to --addConnection" in str(error.value)


def test_options_that_ask_for_nothing_are_refused():
    """--nonInteractive alone would otherwise fall through to the walkthrough."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"non_interactive": True})

    assert "Nothing to do" in str(error.value)


# --- Where the password comes from -----------------------------------------


def test_the_password_can_come_from_the_command_line(stored_connection):
    """--password works, and is the one the docstring discourages."""
    setup_cli.apply({"add_connection": "cli_a@127.0.0.1:3306", "password": "pw-a"})

    assert config.get_connection_password("cli_a@127.0.0.1:3306") == "pw-a"


def test_the_password_can_come_from_a_named_environment_variable(
    stored_connection, monkeypatch
):
    """The option takes the variable's NAME, so no secret is in the command."""
    monkeypatch.setenv("MCP_TEST_PW", "pw-b")

    setup_cli.apply({
        "add_connection": "cli_b@127.0.0.1:3306", "password_env": "MCP_TEST_PW",
    })

    assert config.get_connection_password("cli_b@127.0.0.1:3306") == "pw-b"


def test_an_unset_environment_variable_is_an_error(stored_connection, monkeypatch):
    """Naming a variable that is not there must not store an empty password."""
    monkeypatch.delenv("MCP_TEST_MISSING", raising=False)

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({
            "add_connection": "cli_c@127.0.0.1:3306",
            "password_env": "MCP_TEST_MISSING",
        })

    assert "--passwordEnv names 'MCP_TEST_MISSING'" in str(error.value)
    assert "cli_c@127.0.0.1:3306" not in config.list_connection_uris()


def test_an_empty_environment_variable_is_an_empty_password(
    stored_connection, monkeypatch
):
    """An empty password is a password; only an unset variable is an error."""
    monkeypatch.setenv("MCP_TEST_EMPTY", "")

    setup_cli.apply({
        "add_connection": "cli_d@127.0.0.1:3306", "password_env": "MCP_TEST_EMPTY",
    })

    assert config.get_connection_password("cli_d@127.0.0.1:3306") == ""


def test_the_password_can_come_from_stdin(stored_connection, monkeypatch):
    """Piped in, so a secret manager can hand it over without a file."""
    monkeypatch.setattr(setup_cli.sys, "stdin", io.StringIO("pw-e\nignored\n"))

    setup_cli.apply({
        "add_connection": "cli_e@127.0.0.1:3306", "password_stdin": True,
    })

    # The first line only: a password has no line break in it.
    assert config.get_connection_password("cli_e@127.0.0.1:3306") == "pw-e"


def test_reading_the_password_from_a_terminal_is_refused(monkeypatch):
    """It would wait for input that nobody knows to type."""
    monkeypatch.setattr(
        setup_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True)
    )

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({
            "add_connection": "u@127.0.0.1:3306", "password_stdin": True,
        })

    assert "stdin is a terminal" in str(error.value)


def test_with_no_source_the_password_is_prompted_for(stored_connection, monkeypatch):
    """A person at a terminal is still asked, exactly as the menu asks."""
    monkeypatch.setattr(prompts, "shell", lambda: _wizard_shell(["pw-f"]))

    setup_cli.apply({"add_connection": "cli_f@127.0.0.1:3306"})

    assert config.get_connection_password("cli_f@127.0.0.1:3306") == "pw-f"


def test_non_interactive_turns_a_missing_password_into_an_error(monkeypatch):
    """An automated run has to fail rather than wait."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({
            "add_connection": "u@127.0.0.1:3306", "non_interactive": True,
        })

    message = str(error.value)
    assert "--nonInteractive forbids asking" in message
    assert "--passwordStdin" in message


def test_a_session_that_cannot_prompt_says_so_instead_of_failing_oddly(monkeypatch):
    """Without wizards there is no way to ask, so the options are named."""
    monkeypatch.setattr(
        prompts, "shell",
        lambda: SimpleNamespace(options=SimpleNamespace(useWizards=False)),
    )

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_connection": "u@127.0.0.1:3306"})

    assert "cannot prompt for one" in str(error.value)


# --- Connections -----------------------------------------------------------


def test_a_uri_carrying_a_password_is_refused(stored_connection):
    """Normalization strips it, so accepting it would store no password at all."""
    before = config.list_connection_uris()

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_connection": "mariadb://cli_p:pw@127.0.0.1:3306"})

    message = str(error.value)
    assert "carries a password" in message
    assert "--passwordStdin" in message
    assert config.list_connection_uris() == before


def test_an_unparsable_uri_is_refused(stored_connection):
    """What names no connection cannot be stored as one."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_connection": "not a uri", "password": "x"})

    assert "is not a valid connection URI" in str(error.value)


def test_the_uri_is_stored_normalized(stored_connection):
    """One spelling per connection, as the interactive path also stores it."""
    setup_cli.apply({
        "add_connection": "  mariadb://cli_g@127.0.0.1  ", "password": "pw",
    })

    configured = config.list_connection_uris()
    assert "cli_g@127.0.0.1:3306" in configured
    # The spelling that was passed is NOT what it is stored under.
    assert "  mariadb://cli_g@127.0.0.1  " not in configured
    assert "mariadb://cli_g@127.0.0.1" not in configured


def test_the_connection_is_verified_before_being_stored(stored_connection):
    """The same check the menu makes, and it is what --noVerify skips."""
    setup_cli.apply({"add_connection": "cli_h@127.0.0.1:3306", "password": "pw"})

    assert stored_connection == ["cli_h@127.0.0.1:3306"]


def test_a_connection_that_cannot_be_opened_is_not_stored(clean_config, monkeypatch):
    """And the way to store it anyway is named."""
    def failing_verify(uri, password):
        raise RuntimeError("Access denied for user")

    monkeypatch.setattr(setup_cli, "verify_connection", failing_verify)

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_connection": "cli_i@127.0.0.1:3306", "password": "pw"})

    message = str(error.value)
    assert "Access denied for user" in message
    assert "--noVerify" in message
    assert "cli_i@127.0.0.1:3306" not in config.list_connection_uris()


def test_no_verify_stores_without_opening_a_session(clean_config, monkeypatch):
    """For a server that is not up yet, which a provisioning script may hit."""
    def must_not_verify(uri, password):
        raise AssertionError("the connection was verified despite --noVerify")

    monkeypatch.setattr(setup_cli, "verify_connection", must_not_verify)

    setup_cli.apply({
        "add_connection": "cli_j@127.0.0.1:3306", "password": "pw", "no_verify": True,
    })

    assert config.get_connection_password("cli_j@127.0.0.1:3306") == "pw"


def test_adding_a_configured_connection_updates_its_password(
    stored_connection, capsys
):
    """A provisioning script has to be safe to run twice."""
    setup_cli.apply({"add_connection": "cli_k@127.0.0.1:3306", "password": "first"})
    capsys.readouterr()

    setup_cli.apply({"add_connection": "cli_k@127.0.0.1:3306", "password": "second"})

    assert config.get_connection_password("cli_k@127.0.0.1:3306") == "second"
    assert config.list_connection_uris().count("cli_k@127.0.0.1:3306") == 1
    # Said, rather than looking like a fresh one.
    assert "updated" in capsys.readouterr().out


def test_connections_are_deleted_by_uri_in_any_spelling(stored_connection):
    """The menu picks by number; the command line has to name them."""
    setup_cli.apply({"add_connection": "cli_l@127.0.0.1:3306", "password": "pw"})

    setup_cli.apply({"delete_connections": "mariadb://cli_l@127.0.0.1"})

    assert "cli_l@127.0.0.1:3306" not in config.list_connection_uris()


def test_deleting_a_connection_that_is_not_configured_is_an_error(stored_connection):
    """With what IS configured named, since the caller clearly expected it."""
    setup_cli.apply({"add_connection": "cli_m@127.0.0.1:3306", "password": "pw"})

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"delete_connections": "nobody@nowhere:3306"})

    message = str(error.value)
    assert "is not a configured connection" in message
    assert "cli_m@127.0.0.1:3306" in message


# --- Allowed paths ---------------------------------------------------------


def test_paths_are_added_and_deleted_by_value(clean_config, tmp_path):
    """Comma-separated, absolute, and expanded the same way the menu does it."""
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    setup_cli.apply({"add_paths": f"{first},{second}"})

    allowed = config.get_allowed_paths()
    assert str(first) in allowed and str(second) in allowed

    setup_cli.apply({"delete_paths": str(first)})

    allowed = config.get_allowed_paths()
    assert str(first) not in allowed and str(second) in allowed


def test_a_path_that_does_not_exist_is_refused(clean_config, tmp_path):
    """The menu says so and carries on; a script has to fail."""
    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_paths": str(tmp_path / "not-there")})

    assert "is not an existing directory" in str(error.value)


def test_adding_an_allowed_path_twice_is_not_an_error(clean_config, tmp_path, capsys):
    """Safe to run twice, and it says it did nothing."""
    setup_cli.apply({"add_paths": str(tmp_path)})
    capsys.readouterr()

    setup_cli.apply({"add_paths": str(tmp_path)})

    assert "was already allowed" in capsys.readouterr().out
    assert config.get_allowed_paths().count(str(tmp_path)) == 1


def test_deleting_a_path_that_is_not_allowed_is_an_error(clean_config, tmp_path):
    """With the allowed ones named."""
    setup_cli.apply({"add_paths": str(tmp_path)})

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"delete_paths": "/nowhere-in-particular"})

    message = str(error.value)
    assert "is not an allowed path" in message
    assert str(tmp_path) in message


# --- The migration tooling -------------------------------------------------


def test_the_migrator_is_installed_in_three_steps(clean_config, monkeypatch):
    """Download, provision, wrap - the same three the menu drives."""
    calls = []
    monkeypatch.setattr(setup_migrator, "is_supported", lambda: True)
    monkeypatch.setattr(
        setup_migrator, "download", lambda: calls.append("download") or "/dir"
    )
    monkeypatch.setattr(
        setup_migrator, "provision",
        lambda target: calls.append(("provision", target)) or "/dir/.venv",
    )
    monkeypatch.setattr(
        setup_migrator, "install_wrapper",
        lambda target: calls.append(("wrapper", target)) or "/bin/mariadb-migrator",
    )

    setup_cli.apply({"install_migrator": True})

    assert calls == ["download", ("provision", "/dir"), ("wrapper", "/dir")]


def test_installing_the_tooling_where_it_does_not_run_is_refused(
    clean_config, monkeypatch
):
    """Windows gets the same answer here as it gets from the menu: nothing."""
    monkeypatch.setattr(setup_migrator, "is_supported", lambda: False)
    monkeypatch.setattr(
        setup_migrator, "download",
        lambda: (_ for _ in ()).throw(AssertionError("downloaded anyway")),
    )

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"install_migrator": True})

    assert "does not run on this platform" in str(error.value)


def test_removing_the_tooling_reports_what_went(clean_config, monkeypatch):
    """One step, and it takes the wrapper with it (see setup_migrator)."""
    monkeypatch.setattr(setup_migrator, "remove", lambda: "/root")

    setup_cli.apply({"remove_migrator": True})


def test_remove_and_install_together_is_the_reinstall_idiom(clean_config, monkeypatch):
    """Removal runs first, so the pair reinstalls rather than contradicting."""
    calls = []
    monkeypatch.setattr(setup_migrator, "is_supported", lambda: True)
    monkeypatch.setattr(setup_migrator, "remove", lambda: calls.append("remove") or "/root")
    monkeypatch.setattr(setup_migrator, "download", lambda: calls.append("download") or "/dir")
    monkeypatch.setattr(setup_migrator, "provision", lambda target: "/dir/.venv")
    monkeypatch.setattr(setup_migrator, "install_wrapper", lambda target: "/bin/m")

    setup_cli.apply({"install_migrator": True, "remove_migrator": True})

    assert calls == ["remove", "download"]


# --- Order, reporting and dispatch -----------------------------------------


def test_deletions_happen_before_additions(stored_connection):
    """So deleting and re-adding the same connection in one call adds it."""
    setup_cli.apply({"add_connection": "cli_n@127.0.0.1:3306", "password": "old"})

    setup_cli.apply({
        "delete_connections": "cli_n@127.0.0.1:3306",
        "add_connection": "cli_n@127.0.0.1:3306",
        "password": "new",
    })

    assert config.get_connection_password("cli_n@127.0.0.1:3306") == "new"


def test_a_failure_leaves_what_already_succeeded_in_place(clean_config, tmp_path):
    """A script has to be able to tell how far it got."""
    setup_cli.apply({"add_paths": str(tmp_path)})
    assert str(tmp_path) in config.get_allowed_paths()

    with pytest.raises(mysqlsh.Error):
        setup_cli.apply({
            "add_paths": str(tmp_path / "not-there"),
            "delete_paths": str(tmp_path),
        })

    # The deletion ran first and stands; the addition is what failed.
    assert str(tmp_path) not in config.get_allowed_paths()


def test_a_settings_file_is_left_behind_so_the_next_run_uses_the_menu(
    clean_config, tmp_path
):
    """Same thing the first-run walkthrough ensures at its end."""
    settings_path = config.get_settings_file_path()
    if os.path.exists(settings_path):
        os.remove(settings_path)

    setup_cli.apply({"add_paths": str(tmp_path)})

    assert config.settings_file_exists()


def test_run_setup_dispatches_on_whether_options_were_given(monkeypatch):
    """No options is the walkthrough; any option is the declarative path."""
    applied = []
    monkeypatch.setattr(setup_cli, "apply", lambda options: applied.append(options))

    setup.run_setup(show=True)
    assert applied == [{"show": True}]

    # Without options and without wizards it is the walkthrough that refuses,
    # and it now points at the options as the other way in.
    monkeypatch.setattr(
        prompts, "shell",
        lambda: SimpleNamespace(options=SimpleNamespace(useWizards=False)),
    )
    with pytest.raises(mysqlsh.Error) as error:
        setup.run_setup()

    assert "must be run from an interactive shell session" in str(error.value)
    assert "mcp setup --help" in str(error.value)


# --- Reporting the configuration -------------------------------------------


def test_show_reports_everything_the_menu_shows(clean_config, tmp_path, capsys):
    """Connections, allowed paths and the state of the migration tooling."""
    setup_cli.apply({"add_paths": str(tmp_path)})
    capsys.readouterr()

    setup_cli.apply({"show": True})

    output = capsys.readouterr().out
    assert "Configured connections:" in output
    assert "Allowed paths:" in output
    assert str(tmp_path) in output
    assert "Migration tooling:" in output
    assert general.MIGRATOR_VERSION in output


def test_show_with_json_prints_one_parsable_document(clean_config, tmp_path, capsys):
    """Nothing else on stdout, so a script can read all of it."""
    setup_cli.apply({"add_paths": str(tmp_path)})
    capsys.readouterr()

    setup_cli.apply({"show": True, "json": True})

    reported = json.loads(capsys.readouterr().out)

    assert str(tmp_path) in reported["allowed_paths"]
    assert reported["allowed_paths"] == config.get_allowed_paths()
    assert reported["connections"] == config.list_connection_uris()
    tooling = reported["migrator"]
    assert tooling["configured_release"] == general.MIGRATOR_VERSION
    assert tooling["supported"] is setup_migrator.is_supported()
    assert tooling["installed_releases"] == setup_migrator.installed_versions()
    assert tooling["wrapper_path"] == setup_migrator.wrapper_path()


def test_show_on_a_platform_without_the_tooling_says_so(
    clean_config, monkeypatch, capsys
):
    """No install path to report where the tooling cannot run."""
    monkeypatch.setattr(setup_migrator, "is_supported", lambda: False)

    setup_cli.apply({"show": True})

    assert "not supported on this platform" in capsys.readouterr().out


def test_show_says_none_rather_than_printing_an_empty_list(
    clean_config, monkeypatch, capsys
):
    """An empty section has to read as empty, not as a missing heading."""
    monkeypatch.setattr(config, "list_connection_uris", lambda: [])
    monkeypatch.setattr(config, "get_allowed_paths", lambda: [])

    setup_cli.apply({"show": True})

    output = capsys.readouterr().out
    assert "Configured connections:\n  (none)" in output
    assert "Allowed paths:\n  (none)" in output


def test_a_uri_that_parses_but_cannot_be_put_back_together_is_refused(
    stored_connection, monkeypatch
):
    """The second guard: parsable enough to inspect, not enough to store.

    normalize_connection_uri returns None for anything the shell cannot
    re-render, which is a different failure from not parsing at all - and the
    only one that reaches the check after the password inspection.
    """
    monkeypatch.setattr(config, "normalize_connection_uri", lambda uri: None)

    with pytest.raises(mysqlsh.Error) as error:
        setup_cli.apply({"add_connection": "cli_q@127.0.0.1:3306", "password": "pw"})

    assert "is not a valid connection URI" in str(error.value)
