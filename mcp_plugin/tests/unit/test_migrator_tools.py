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

"""Tests for the migration MCP tools (lib/migrator_functions.py).

Covers writing the tooling's configuration, resolving the passwords a run needs
from the configured connections, and the shape of the orchestrator invocation.
The orchestrator itself is never run: what matters here is that it is invoked in
the right directory, with the right interpreter, the right arguments and a
closed stdin - and that no password ever comes back out.
"""

# cSpell:ignore mysqlsh MariaDB migrationctl yaml

import asyncio
import json
import os
import subprocess

import pytest

import mysqlsh
import yaml

from mcp_plugin.lib import config, general, migrator_functions, setup_migrator


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """Builds a stand-in migration tooling install and points the paths at it.

    Everything the tools touch is here: the install directory itself, its
    config/ folder, the example the configuration follows and a stub for the
    venv interpreter. Nothing real is run, so the interpreter only has to exist.

    Yields:
        The absolute path of the stand-in install directory.
    """
    data_home = tmp_path / "data_home"
    monkeypatch.setattr(general, "get_data_home", lambda: str(data_home))

    install_dir = tmp_path / "data_home" / general.MIGRATOR_DIR_NAME / general.MIGRATOR_VERSION
    (install_dir / "config").mkdir(parents=True)
    (install_dir / "config" / "migration.yaml.example").write_text(
        'mode: "one_step"\nenv:\n  SRC_HOST: "source-db-host"\n', encoding="utf-8"
    )
    venv_bin = install_dir / setup_migrator.MIGRATOR_VENV_DIR / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").write_text("#!/bin/sh\n", encoding="utf-8")

    yield str(install_dir)


@pytest.fixture
def configured_connections(monkeypatch):
    """Stands in for the connections mcp.setup configured.

    Patched rather than really stored: the real store is the developer's own
    secret store, and these tests would otherwise write into it.

    Yields:
        The {uri: password} mapping the tools will see as configured.
    """
    connections = {
        "admin@source-host:3306": "src-secret",
        "admin@target-host:3306": "tgt-secret",
        "repl@source-host:3306": "repl-secret",
    }

    def fake_resolve(uri):
        return uri if uri in connections else None

    monkeypatch.setattr(config, "resolve_connection_uri", fake_resolve)
    monkeypatch.setattr(config, "get_connection_password", lambda uri: connections[uri])
    # Also the list the refusals name: without this a test would read - and
    # print - whatever the developer happens to have configured.
    monkeypatch.setattr(config, "list_connection_uris", lambda: sorted(connections))
    yield connections


_SOURCE_AND_TARGET = {
    "SRC_HOST": "source-host",
    "SRC_PORT": "3306",
    "SRC_ADMIN_USER": "admin",
    "SRC_DBS": "sakila",
    "TGT_HOST": "target-host",
    "TGT_PORT": "3306",
    "TGT_ADMIN_USER": "admin",
}


# --- Registration ----------------------------------------------------------


class _FakeServer:
    """Records the tool names registered on it."""

    def __init__(self):
        self.registered = []

    def tool(self, *args, **kwargs):
        self.registered.append(kwargs.get("name"))
        return lambda func: func


def test_no_tools_are_registered_without_an_install(tmp_path, monkeypatch, capsys):
    """A server where the tooling was never downloaded advertises none of them."""
    monkeypatch.setattr(general, "get_data_home", lambda: str(tmp_path / "empty"))
    server = _FakeServer()

    migrator_functions.register_migrator_tools(server)

    assert server.registered == []
    # Said out loud, so a client missing the tools can find out why.
    assert "not registered" in capsys.readouterr().err


def test_all_four_tools_are_registered_with_an_install(fake_install):
    """With the tooling installed, the four migrator tools are registered."""
    server = _FakeServer()

    migrator_functions.register_migrator_tools(server)

    assert server.registered == [
        "migrator.set_config",
        "migrator.plan",
        "migrator.run",
        "migrator.resume",
    ]


# --- Writing the configuration ---------------------------------------------


def test_the_configuration_is_written_in_the_shape_the_tooling_reads(
    fake_install, configured_connections
):
    """A top-level mode and an env mapping of strings, as the example has it."""
    result = migrator_functions.write_config("two_step", dict(_SOURCE_AND_TARGET))

    path = os.path.join(fake_install, "config", "migration.yaml")
    assert result["config_path"] == path
    assert result["mode"] == "two_step"
    assert result["example_path"].endswith("config/migration.yaml.example")

    document = yaml.safe_load(open(path, encoding="utf-8"))
    assert document["mode"] == "two_step"
    assert document["env"]["SRC_HOST"] == "source-host"
    assert document["env"]["SRC_DBS"] == "sakila"
    # Strings throughout, which is what the orchestrator reads.
    assert all(isinstance(value, str) for value in document["env"].values())

    # Readable by its owner only: it names hosts and accounts.
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    # Nothing staged left behind.
    assert sorted(os.listdir(os.path.dirname(path))) == [
        "migration.yaml", "migration.yaml.example"
    ]


def test_numbers_and_booleans_become_the_strings_the_tooling_expects(
    fake_install, configured_connections
):
    """The tooling's flags are "1"/"0", so a boolean must not become "True"."""
    migrator_functions.write_config(
        "one_step",
        {**_SOURCE_AND_TARGET, "SRC_PORT": 3306, "MIGRATE_APP_USERS": True,
         "ANALYZE_TARGET": False, "STAGED_PHASE": None},
    )

    document = yaml.safe_load(
        open(os.path.join(fake_install, "config", "migration.yaml"), encoding="utf-8")
    )
    assert document["env"]["SRC_PORT"] == "3306"
    assert document["env"]["MIGRATE_APP_USERS"] == "1"
    assert document["env"]["ANALYZE_TARGET"] == "0"
    assert document["env"]["STAGED_PHASE"] == ""


def test_a_nested_value_is_refused_rather_than_silently_flattened(fake_install):
    """There is no string a list should quietly become."""
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", {"SRC_DBS": ["a", "b"]})

    assert "must be a string, number or boolean" in str(error.value)


def test_passwords_are_refused_in_the_configuration(fake_install):
    """Every password the store can supply is rejected, and named."""
    for key in ("SRC_PASS", "SRC_ADMIN_PASS", "TGT_PASS", "TGT_ADMIN_PASS", "REPL_PASS"):
        with pytest.raises(mysqlsh.Error) as error:
            migrator_functions.write_config(
                "one_step", {**_SOURCE_AND_TARGET, key: "hunter2"}
            )

        assert key in str(error.value)
        assert "never written to disk" in str(error.value)

    # Nothing was written by any of those attempts.
    assert not os.path.exists(os.path.join(fake_install, "config", "migration.yaml"))


def test_an_empty_password_field_is_allowed(fake_install, configured_connections):
    """The tooling's own example carries empty password keys; only values bite."""
    migrator_functions.write_config(
        "one_step", {**_SOURCE_AND_TARGET, "SRC_ADMIN_PASS": ""}
    )

    document = yaml.safe_load(
        open(os.path.join(fake_install, "config", "migration.yaml"), encoding="utf-8")
    )
    assert document["env"]["SRC_ADMIN_PASS"] == ""


def test_merging_keeps_the_keys_it_was_not_given(fake_install, configured_connections):
    """merge=True updates what is named and leaves the rest alone."""
    migrator_functions.write_config("one_step", dict(_SOURCE_AND_TARGET))

    migrator_functions.write_config("two_step", {"SRC_DBS": "world"}, merge=True)

    document = yaml.safe_load(
        open(os.path.join(fake_install, "config", "migration.yaml"), encoding="utf-8")
    )
    assert document["mode"] == "two_step"
    assert document["env"]["SRC_DBS"] == "world"
    # Untouched.
    assert document["env"]["SRC_HOST"] == "source-host"
    assert document["env"]["TGT_ADMIN_USER"] == "admin"

    # Without merge the file is replaced outright.
    migrator_functions.write_config("one_step", {"SRC_DBS": "only"})
    document = yaml.safe_load(
        open(os.path.join(fake_install, "config", "migration.yaml"), encoding="utf-8")
    )
    assert list(document["env"]) == ["SRC_DBS"]


def test_a_mode_is_required(fake_install):
    """The tooling keys everything off the mode, so it cannot be blank."""
    for bad_mode in ("", "   ", None):
        with pytest.raises(mysqlsh.Error) as error:
            migrator_functions.write_config(bad_mode, dict(_SOURCE_AND_TARGET))
        assert "must name an execution mode" in str(error.value)


def test_the_tools_refuse_to_work_without_an_install(tmp_path, monkeypatch):
    """Every entry point says the tooling is missing rather than failing oddly."""
    monkeypatch.setattr(general, "get_data_home", lambda: str(tmp_path / "empty"))

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", {})

    assert "is not installed" in str(error.value)


# --- Passwords: only from the configured connections -----------------------


def test_passwords_come_from_the_configured_connections(
    fake_install, configured_connections
):
    """The URI is composed from the config's own fields and must be configured."""
    passwords, resolved = migrator_functions._connection_passwords({
        **_SOURCE_AND_TARGET, "REPL_USER": "repl",
    })

    assert passwords == {
        "SRC_ADMIN_PASS": "src-secret",
        "TGT_ADMIN_PASS": "tgt-secret",
        "REPL_PASS": "repl-secret",
    }
    # What is reported back is which connection answered, not the secret.
    assert resolved == {
        "SRC_ADMIN_PASS": "admin@source-host:3306",
        "TGT_ADMIN_PASS": "admin@target-host:3306",
        "REPL_PASS": "repl@source-host:3306",
    }


def test_an_unconfigured_host_yields_no_password(fake_install, configured_connections):
    """Naming a host in the configuration must not be enough to get its password.

    This is the whole of the confinement: a client can write any host it likes
    into the configuration, but only a connection somebody already configured
    with mcp.setup has a password to hand over.
    """
    passwords, resolved = migrator_functions._connection_passwords({
        **_SOURCE_AND_TARGET, "SRC_HOST": "some-host-nobody-allowed",
    })

    assert "SRC_ADMIN_PASS" not in passwords
    assert "SRC_ADMIN_PASS" not in resolved
    # The target was configured, so that one still resolves.
    assert passwords == {"TGT_ADMIN_PASS": "tgt-secret"}


def test_a_password_needs_both_a_user_and_a_host(fake_install, configured_connections):
    """Half a connection names nothing, so nothing is looked up."""
    passwords, _ = migrator_functions._connection_passwords(
        {"SRC_HOST": "source-host", "SRC_PORT": "3306"}
    )
    assert passwords == {}

    passwords, _ = migrator_functions._connection_passwords(
        {"SRC_ADMIN_USER": "admin", "SRC_PORT": "3306"}
    )
    assert passwords == {}


# --- Running the orchestrator ----------------------------------------------


@pytest.fixture
def recorded_run(monkeypatch):
    """Captures the orchestrator invocation instead of performing it.

    Yields:
        A list that each call appends its (command, kwargs) to.
    """
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="RUN: PASS\n", stderr="")

    monkeypatch.setattr(migrator_functions.subprocess, "run", fake_run)
    yield calls


def _write_valid_config():
    """Writes a configuration the invocation tests can run against."""
    return migrator_functions.write_config(
        "two_step", {**_SOURCE_AND_TARGET, "REPL_USER": "repl"}
    )


def test_the_invocation_runs_in_the_install_with_the_venv_interpreter(
    fake_install, configured_connections, recorded_run
):
    """cwd, interpreter and PATH are the equivalent of activating the venv."""
    _write_valid_config()

    outcome = migrator_functions._invoke("run", "two_step", None, 900, True)

    command, kwargs = recorded_run[0]
    assert command[0] == setup_migrator._venv_python(fake_install)
    assert command[1:4] == ["-m", "orchestrator.migrationctl", "run"]
    assert kwargs["cwd"] == fake_install
    # stdin closed: the orchestrator prompts for what it lacks, and plan has no
    # --non-interactive at all, so an incomplete config must fail not hang.
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 900

    child_env = kwargs["env"]
    venv_dir = os.path.join(fake_install, setup_migrator.MIGRATOR_VENV_DIR)
    assert child_env["VIRTUAL_ENV"] == venv_dir
    assert child_env["PATH"].startswith(os.path.join(venv_dir, "bin") + os.pathsep)
    assert "PYTHONHOME" not in child_env
    # The passwords reach the orchestrator through the environment ...
    assert child_env["SRC_ADMIN_PASS"] == "src-secret"
    assert child_env["TGT_ADMIN_PASS"] == "tgt-secret"

    # ... and none of them comes back out.
    serialized = json.dumps(outcome)
    for secret in configured_connections.values():
        assert secret not in serialized
    assert outcome["passwords_from"]["SRC_ADMIN_PASS"] == "admin@source-host:3306"
    assert outcome["succeeded"] is True
    assert outcome["exit_code"] == 0
    assert outcome["stdout"] == "RUN: PASS"


def test_only_the_subcommands_that_take_non_interactive_are_given_it(
    fake_install, configured_connections, recorded_run
):
    """plan has no such flag, so passing it would break the invocation."""
    _write_valid_config()

    migrator_functions._invoke("plan", "one_step", None, 60, False)
    assert "--non-interactive" not in recorded_run[-1][0]

    migrator_functions._invoke("run", "one_step", None, 60, True)
    assert "--non-interactive" in recorded_run[-1][0]

    migrator_functions._invoke("resume", "one_step", "artifacts/earlier", 60, True)
    assert "--non-interactive" in recorded_run[-1][0]


def test_the_artifacts_directory_defaults_to_a_fresh_stamped_one(
    fake_install, configured_connections, recorded_run
):
    """Named after command, mode and moment so runs do not overwrite each other."""
    _write_valid_config()

    outcome = migrator_functions._invoke("run", "two_step", None, 60, True)

    assert outcome["out_dir"].startswith(os.path.join("artifacts", "run_two_step_"))
    command = recorded_run[-1][0]
    assert command[command.index("--out") + 1] == outcome["out_dir"]
    assert command[command.index("--config") + 1] == os.path.join(
        "config", "migration.yaml"
    )
    assert command[command.index("--mode") + 1] == "two_step"

    # A directory that was asked for is used as it is.
    outcome = migrator_functions._invoke("run", "two_step", "artifacts/mine", 60, True)
    assert outcome["out_dir"] == "artifacts/mine"


def test_an_absolute_artifacts_directory_is_refused(
    fake_install, configured_connections, recorded_run
):
    """Everything is relative to the install; an absolute path would escape it."""
    _write_valid_config()

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._invoke("run", "two_step", "/tmp/elsewhere", 60, True)

    assert "must be relative to the install directory" in str(error.value)
    assert recorded_run == []


def test_a_missing_configuration_is_reported_before_anything_runs(
    fake_install, configured_connections, recorded_run
):
    """There is nothing to run without a configuration, and it says which one."""
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._invoke("plan", "one_step", None, 60, False)

    assert "No migration configuration at" in str(error.value)
    assert "migrator.set_config" in str(error.value)
    assert recorded_run == []


def test_the_runs_own_report_is_returned_when_it_wrote_one(
    fake_install, configured_connections, recorded_run
):
    """The report is the useful answer, so it is read back and handed over."""
    _write_valid_config()
    out_dir = os.path.join("artifacts", "run_here")
    os.makedirs(os.path.join(fake_install, out_dir))
    with open(
        os.path.join(fake_install, out_dir, "report.json"), "w", encoding="utf-8"
    ) as report:
        json.dump({"success": True, "steps": ["dump", "load"]}, report)

    outcome = migrator_functions._invoke("run", "two_step", out_dir, 60, True)

    assert outcome["report"] == {"success": True, "steps": ["dump", "load"]}

    # A run that wrote none, or a partial one, is not an error here.
    outcome = migrator_functions._invoke("run", "two_step", "artifacts/none", 60, True)
    assert outcome["report"] is None


def test_a_failing_invocation_reports_the_exit_code_and_output(
    fake_install, configured_connections, monkeypatch
):
    """A non-zero exit is an outcome to report, not an exception to raise."""
    _write_valid_config()

    def failing_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 2, stdout="RUN: FAIL\n", stderr="gate failed: binlog_format"
        )

    monkeypatch.setattr(migrator_functions.subprocess, "run", failing_run)

    outcome = migrator_functions._invoke("run", "two_step", None, 60, True)

    assert outcome["succeeded"] is False
    assert outcome["exit_code"] == 2
    assert "gate failed: binlog_format" in outcome["stderr"]


def test_a_timeout_says_where_the_artifacts_are(
    fake_install, configured_connections, monkeypatch
):
    """A migration can outlast any timeout, so the way to follow it is given."""
    _write_valid_config()

    def timing_out_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(migrator_functions.subprocess, "run", timing_out_run)

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._invoke("run", "two_step", None, 5, True)

    assert "did not finish within 5s" in str(error.value)
    assert "artifacts directory" in str(error.value)


def test_long_output_is_truncated_from_the_front(fake_install):
    """The tail is what says how a run ended, so that is the half kept."""
    assert migrator_functions._tail("short") == "short"

    long_output = "x" * (migrator_functions.OUTPUT_TAIL_LIMIT + 500) + "THE-END"
    tail = migrator_functions._tail(long_output)

    assert tail.startswith("... (truncated)")
    assert tail.endswith("THE-END")
    assert len(tail) < len(long_output)


# --- The registered tools themselves ---------------------------------------


class _CapturingServer:
    """Keeps the registered tool callables so they can be awaited directly.

    Cheaper than driving a real server over stdio for this, and it still goes
    through the real tool_registrar wrapper, which is what a client's call would
    reach.
    """

    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[kwargs.get("name")] = func
            return func

        return decorator


def test_the_set_config_tool_writes_and_reports(
    fake_install, configured_connections
):
    """The registered tool is callable and returns what it wrote.

    Driven with asyncio.run rather than an async test, which is how the rest of
    this suite runs coroutines - there is no async pytest plugin here.
    """
    server = _CapturingServer()
    migrator_functions.register_migrator_tools(server)

    result = asyncio.run(server.tools["migrator.set_config"](
        None, "two_step", {**_SOURCE_AND_TARGET, "REPL_USER": "repl"}
    ))

    assert result["mode"] == "two_step"
    assert "SRC_HOST" in result["keys"]
    assert result["passwords_from"]["SRC_ADMIN_PASS"] == "admin@source-host:3306"
    assert os.path.isfile(os.path.join(fake_install, "config", "migration.yaml"))


def test_the_plan_run_and_resume_tools_reach_the_orchestrator(
    fake_install, configured_connections, recorded_run
):
    """Each tool invokes its own subcommand, with resume's directory required."""
    _write_valid_config()
    server = _CapturingServer()
    migrator_functions.register_migrator_tools(server)

    asyncio.run(server.tools["migrator.plan"](None, "one_step"))
    assert recorded_run[-1][0][3] == "plan"

    asyncio.run(server.tools["migrator.run"](None, "two_step"))
    assert recorded_run[-1][0][3] == "run"

    asyncio.run(
        server.tools["migrator.resume"](None, "two_step", "artifacts/earlier")
    )
    command = recorded_run[-1][0]
    assert command[3] == "resume"
    assert command[command.index("--out") + 1] == "artifacts/earlier"

    # resume without the directory of the run to resume has nothing to go on.
    # Raised as ToolError rather than mysqlsh.Error: a call that goes through
    # the registered wrapper is re-raised so the client sees the tool's own
    # message instead of a generic one (see lib/tool_registrar.py).
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as error:
        asyncio.run(server.tools["migrator.resume"](None, "two_step", "  "))
    assert "must name the artifacts directory" in str(error.value)


def test_a_blank_mode_is_refused_by_the_run_tools(
    fake_install, configured_connections, recorded_run
):
    """The mode drives everything the orchestrator does, so it cannot be blank."""
    _write_valid_config()
    server = _CapturingServer()
    migrator_functions.register_migrator_tools(server)

    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as error:
        asyncio.run(server.tools["migrator.run"](None, "   "))

    assert "must name an execution mode" in str(error.value)
    assert recorded_run == []


# --- Reading a configuration back ------------------------------------------


def test_an_unparsable_configuration_is_reported_not_swallowed(fake_install):
    """A hand-edited file that is not YAML says so, naming the file."""
    path = os.path.join(fake_install, "config", "migration.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("env: [this: is not, valid: yaml\n")

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._load_config_env()

    assert "Could not parse" in str(error.value)
    assert path in str(error.value)


def test_an_env_section_that_is_not_a_mapping_is_refused(fake_install):
    """The tooling reads env as a mapping, so a list is not usable."""
    path = os.path.join(fake_install, "config", "migration.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("mode: one_step\nenv:\n  - not\n  - a mapping\n")

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._load_config_env()

    assert "is not a mapping" in str(error.value)


def test_a_configuration_without_an_env_section_reads_as_empty(fake_install):
    """Valid YAML with nothing in env is empty, not an error."""
    path = os.path.join(fake_install, "config", "migration.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("mode: one_step\n")

    assert migrator_functions._load_config_env() == {}


def test_env_must_be_a_mapping(fake_install):
    """The argument is refused before anything is written."""
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", ["SRC_HOST=x"])

    assert "must be a mapping" in str(error.value)


def test_a_failed_write_leaves_no_staged_file_behind(
    fake_install, configured_connections, monkeypatch
):
    """A partial write must not be findable later, nor replace the real file."""
    real_render = migrator_functions._render_config

    def failing_render(mode, env):
        raise OSError("disk full")

    monkeypatch.setattr(migrator_functions, "_render_config", failing_render)

    with pytest.raises(OSError):
        migrator_functions.write_config("one_step", dict(_SOURCE_AND_TARGET))

    monkeypatch.setattr(migrator_functions, "_render_config", real_render)
    # Only the tooling's own example is there; no .migration.yaml.* leftovers.
    assert os.listdir(os.path.join(fake_install, "config")) == [
        "migration.yaml.example"
    ]


# --- Only configured servers may be named ----------------------------------


def test_a_configuration_naming_an_unconfigured_server_is_refused(
    fake_install, configured_connections
):
    """The refusal is the point: such a configuration never reaches disk.

    Previously an unconfigured host was merely left without a password, so the
    configuration was written and failed later. Now it cannot be written at all,
    which is what keeps a client from pointing a migration at a server it was
    never given.
    """
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", {
            **_SOURCE_AND_TARGET, "SRC_HOST": "a-server-nobody-allowed",
        })

    message = str(error.value)
    assert "admin@a-server-nobody-allowed:3306" in message
    assert "SRC_ADMIN_USER" in message
    assert "not a connection configured with mcp.setup" in message
    # The way forward is named, with what IS available.
    assert "admin@source-host:3306" in message
    assert "db.list_connections" in message

    assert not os.path.exists(os.path.join(fake_install, "config", "migration.yaml"))


def test_every_account_named_is_checked_not_just_the_source(
    fake_install, configured_connections
):
    """Target and replication accounts are confined the same way."""
    for key, value in (
        ("TGT_HOST", "another-server-nobody-allowed"),
        ("TGT_ADMIN_USER", "someone-else"),
        ("SRC_ADMIN_USER", "someone-else"),
        ("REPL_USER", "not-the-repl-user"),
    ):
        with pytest.raises(mysqlsh.Error) as error:
            migrator_functions.write_config(
                "binlog", {**_SOURCE_AND_TARGET, "REPL_USER": "repl", key: value}
            )
        assert "not a connection configured" in str(error.value)


def test_a_host_named_without_an_account_is_refused(
    fake_install, configured_connections
):
    """A host with no account cannot be checked, so it is not allowed through.

    This is the hole the check would otherwise have: name a forbidden host, omit
    the user, and nothing composes a URI to test it against.
    """
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", {
            "SRC_HOST": "a-server-nobody-allowed", "SRC_PORT": "3306",
        })

    message = str(error.value)
    assert "source names SRC_HOST but none of SRC_ADMIN_USER, SRC_USER" in message
    assert "has to be named together with the account" in message

    # And the same for the target side.
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config("one_step", {
            "SRC_HOST": "source-host", "SRC_PORT": "3306",
            "SRC_ADMIN_USER": "admin", "TGT_HOST": "target-host",
        })

    assert "target names TGT_HOST but none of TGT_ADMIN_USER, TGT_USER" in str(
        error.value
    )


def test_a_side_that_is_not_named_at_all_is_not_demanded(
    fake_install, configured_connections
):
    """inplace and dump_only migrations have no target, so none is required."""
    result = migrator_functions.write_config("inplace", {
        "SRC_HOST": "source-host", "SRC_PORT": "3306", "SRC_ADMIN_USER": "admin",
    })

    assert result["connections"] == {"SRC_ADMIN_USER": "admin@source-host:3306"}


def test_the_accepted_connections_are_reported(fake_install, configured_connections):
    """What was accepted, and as which configured connection, comes back."""
    result = migrator_functions.write_config(
        "binlog", {**_SOURCE_AND_TARGET, "REPL_USER": "repl"}
    )

    assert result["connections"] == {
        "SRC_ADMIN_USER": "admin@source-host:3306",
        "TGT_ADMIN_USER": "admin@target-host:3306",
        "REPL_USER": "repl@source-host:3306",
    }


def test_a_merge_cannot_assemble_a_forbidden_connection(
    fake_install, configured_connections
):
    """The check is on the merged result, not on the keys of one call.

    Otherwise two individually harmless calls could add up to a configuration
    pointing somewhere neither of them named on its own.
    """
    migrator_functions.write_config("one_step", dict(_SOURCE_AND_TARGET))

    # Changing only the host, with the user already on file, still has to fail.
    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions.write_config(
            "one_step", {"SRC_HOST": "a-server-nobody-allowed"}, merge=True
        )

    assert "not a connection configured" in str(error.value)
    # The configuration on disk is the one that was there before.
    document = yaml.safe_load(
        open(os.path.join(fake_install, "config", "migration.yaml"), encoding="utf-8")
    )
    assert document["env"]["SRC_HOST"] == "source-host"


def test_revoking_a_connection_stops_a_run_already_configured(
    fake_install, configured_connections, recorded_run, monkeypatch
):
    """The check runs again before a migration, not only when it was written.

    A configuration written while a connection existed must stop working when
    that connection is removed with mcp.setup - otherwise revoking access would
    only affect new configurations.
    """
    _write_valid_config()
    # It runs while the connections stand.
    migrator_functions._invoke("run", "two_step", None, 60, True)
    assert len(recorded_run) == 1

    # The source connection is removed, as mcp.setup would remove it.
    configured_connections.pop("admin@source-host:3306")

    with pytest.raises(mysqlsh.Error) as error:
        migrator_functions._invoke("run", "two_step", None, 60, True)

    assert "not a connection configured" in str(error.value)
    # Nothing further was launched.
    assert len(recorded_run) == 1
