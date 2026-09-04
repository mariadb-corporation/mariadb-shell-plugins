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

"""MCP tools driving the MySQL-to-MariaDB migration tooling (``migration``).

The tools here write the tooling's configuration and run its orchestrator. They
are registered only when the tooling is actually installed (see
:func:`register_migration_tools`), so a server on a machine that never ran the
download step does not advertise tools that could not do anything.

Two things are deliberately NOT in the client's hands:

* **Which servers can be reached at all.** A configuration may only name
  accounts that are among the connections ``mcp.setup`` configured, and one
  naming anything else is refused rather than written (see
  :func:`validate_connections`). A client therefore cannot point a migration at
  a server it was never given - not even to have the attempt fail later. The
  check runs again before every migration, so removing a connection stops the
  runs already configured against it.
* **Passwords.** The configuration written by ``migration.set_config`` never
  carries one. Each is read from the shell's secret store at run time, under the
  connection URI the configuration's own host/port/user fields compose. A client
  can therefore run a migration against a configured server without ever being
  able to read, set or exfiltrate its password (see
  :func:`_connection_passwords`).
* **The working directory.** The orchestrator resolves its own package, its
  scripts and the default config path relative to the working directory, so
  every run happens in the install directory (see :func:`_run_orchestrator`).

Every invocation is given a closed stdin. The orchestrator prompts for anything
it is missing - and ``plan`` has no ``--non-interactive`` flag at all - so
without that a tool call would hang until it timed out rather than reporting
what was absent.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver migrationctl yaml

import datetime
import json
import os
import subprocess
import tempfile
from typing import Optional

import mysqlsh

from mcp_plugin.lib import config, general, setup_migration
from mcp_plugin.lib.tool_registrar import tool_registrar

# The tooling's configuration and the template it follows, both relative to the
# install directory. The example is the tooling's own and is never written to.
MIGRATION_CONFIG = os.path.join("config", "migration.yaml")
MIGRATION_CONFIG_EXAMPLE = os.path.join("config", "migration.yaml.example")

# The orchestrator, run as a module out of the install directory.
ORCHESTRATOR_MODULE = "orchestrator.migrationctl"

# How long (in seconds) to let one orchestrator invocation run. A real migration
# can outlast any client's patience, which is why every tool also returns the
# artifacts directory: the run's own report and log can be read from there.
DEFAULT_COMMAND_TIMEOUT = 3600

# How much of the orchestrator's output to hand back. Enough to see what it did
# and why it stopped, bounded so a chatty run cannot flood a client's context.
OUTPUT_TAIL_LIMIT = 8000

# Configuration keys whose value is a password this can supply from the secret
# store, mapped to the (user, host, port) keys naming the connection to read it
# from. These are refused in a configuration and injected at run time instead,
# so no password is ever written to disk by this plugin.
_PASSWORD_SOURCES = {
    "SRC_PASS": ("SRC_USER", "SRC_HOST", "SRC_PORT"),
    "SRC_ADMIN_PASS": ("SRC_ADMIN_USER", "SRC_HOST", "SRC_PORT"),
    "TGT_PASS": ("TGT_USER", "TGT_HOST", "TGT_PORT"),
    "TGT_ADMIN_PASS": ("TGT_ADMIN_USER", "TGT_HOST", "TGT_PORT"),
    # The replication user lives on the source.
    "REPL_PASS": ("REPL_USER", "SRC_HOST", "SRC_PORT"),
}

# The two servers a migration has, and the keys that may name an account on
# each. A side that names a host but no account cannot be checked against the
# configured connections at all, which is why that is refused rather than
# waved through: the whole point is that a host has to be one somebody allowed.
_CONNECTION_SIDES = (
    ("source", "SRC_HOST", ("SRC_ADMIN_USER", "SRC_USER")),
    ("target", "TGT_HOST", ("TGT_ADMIN_USER", "TGT_USER")),
)


def _install_dir() -> str:
    """Returns the install the tools operate on, refusing if there is none."""
    if not setup_migration.is_installed():
        raise mysqlsh.Error(
            "The MySQL-to-MariaDB migration tooling is not installed. Run "
            "'mariadb-shell -- mcp setup' and choose the download step."
        )

    return general.get_migrator_path()


def _config_path() -> str:
    """Returns the absolute path of the tooling's configuration file."""
    return os.path.join(_install_dir(), MIGRATION_CONFIG)


def _render_config(mode: str, env: dict) -> str:
    """Renders the configuration as YAML in the shape the tooling expects.

    Args:
        mode (str): The default execution mode to record.
        env (dict): The environment mapping, values already stringified.

    Returns:
        The file's full text.
    """
    import yaml

    document = {"mode": mode, "env": env}

    # default_flow_style=False for the block mapping the tooling's own example
    # uses, sort_keys=False so a caller's grouping (source, then target, then
    # the mode-specific keys) survives into the file it will have to read.
    return yaml.safe_dump(document, default_flow_style=False, sort_keys=False)


def _stringify(key: str, value) -> str:
    """Returns a configuration value as the string the tooling reads.

    The tooling's environment mapping is strings throughout - ``"1"`` and
    ``"0"`` where another format would use booleans - so numbers and booleans
    are converted rather than refused, and a nested structure is refused
    because there is no string it should silently become.

    Args:
        key (str): The key being set, for the error message.
        value: The value as the client passed it.

    Returns:
        The value as a string.

    Raises:
        mysqlsh.Error: If the value is not a scalar.
    """
    if isinstance(value, bool):
        # The tooling's own flags are "1"/"0", not "True"/"False".
        return "1" if value else "0"
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)

    raise mysqlsh.Error(
        f"The value of '{key}' must be a string, number or boolean, not "
        f"{type(value).__name__}. The tooling reads its configuration as "
        "strings throughout."
    )


def _named_connections(env: dict) -> list:
    """Returns every connection the configuration names.

    A connection is named by a user key together with the host and port of the
    side it lives on, as :data:`_PASSWORD_SOURCES` pairs them up - so
    ``SRC_ADMIN_USER`` at ``SRC_HOST``:``SRC_PORT``, and so on. A pair with no
    user or no host names nothing and is left out.

    Args:
        env (dict): The configuration's environment mapping.

    Returns:
        A list of (password key, user key, URI) tuples.
    """
    named = []

    for password_key, (user_key, host_key, port_key) in _PASSWORD_SOURCES.items():
        user = (env.get(user_key) or "").strip()
        host = (env.get(host_key) or "").strip()
        port = (env.get(port_key) or "").strip()
        if not user or not host:
            continue

        uri = f"{user}@{host}:{port}" if port else f"{user}@{host}"
        named.append((password_key, user_key, uri))

    return named


def validate_connections(env: dict) -> dict:
    """Refuses a configuration naming any server that is not a configured one.

    Every account the configuration names has to be one of the connections
    ``mcp.setup`` configured. This is what keeps a client from pointing a
    migration at a server it was never given: it cannot write such a
    configuration in the first place, so there is no "the password just did not
    resolve" path to fall down later.

    Checked both when a configuration is written and again when a migration is
    run, so that removing a connection with ``mcp.setup`` stops the runs that
    were already configured against it rather than only the new ones.

    Args:
        env (dict): The configuration's environment mapping.

    Returns:
        The {user key: configured URI} pairs that were accepted.

    Raises:
        mysqlsh.Error: If a side names a host but no account, or if any account
            named is not a configured connection.
    """
    configured = config.list_connection_uris()

    unnameable = [
        f"{label} names {host_key} but none of {', '.join(user_keys)}"
        for label, host_key, user_keys in _CONNECTION_SIDES
        if (env.get(host_key) or "").strip()
        and not any((env.get(user_key) or "").strip() for user_key in user_keys)
    ]
    if unnameable:
        raise mysqlsh.Error(
            f"{'; '.join(unnameable)}. A host has to be named together with the "
            "account to reach it by, so that it can be checked against the "
            "connections mcp.setup configured "
            f"({', '.join(configured) or 'none'})."
        )

    accepted = {}
    refused = []
    for _, user_key, uri in _named_connections(env):
        configured_uri = config.resolve_connection_uri(uri)
        if configured_uri is None:
            refused.append(f"{uri} (from {user_key})")
            continue
        accepted[user_key] = configured_uri

    if refused:
        raise mysqlsh.Error(
            f"{'; '.join(refused)} is not a connection configured with "
            "mcp.setup, so a migration cannot be configured against it. "
            f"Configured connections: {', '.join(configured) or 'none'}. Add "
            "one with mcp.setup, or use db.list_connections to see them."
        )

    return accepted


def _connection_passwords(env: dict) -> tuple:
    """Returns the passwords to inject, read from the configured connections.

    For each password the tooling may need, the connection it belongs to is
    composed from the configuration's own fields - ``SRC_ADMIN_USER`` at
    ``SRC_HOST``:``SRC_PORT``, and so on - and resolved against the connections
    ``mcp.setup`` configured. A password is supplied only when that resolves,
    which is what confines a client to servers somebody has already allowed:
    naming a host in the configuration is not enough to get its password.

    Args:
        env (dict): The configuration's environment mapping.

    Returns:
        A (passwords, resolved) tuple: the environment overrides to pass to the
        orchestrator, and the {config key: configured URI} pairs behind them,
        for reporting WHICH connection answered without disclosing anything.
    """
    passwords = {}
    resolved = {}

    for password_key, _, uri in _named_connections(env):
        configured_uri = config.resolve_connection_uri(uri)
        if configured_uri is None:
            # Should not be reachable through the tools, which refuse such a
            # configuration outright (see :func:`validate_connections`), so this
            # is the belt to that braces: no password for an unconfigured
            # connection, whatever put it in the file.
            continue

        passwords[password_key] = config.get_connection_password(configured_uri)
        resolved[password_key] = configured_uri

    return passwords, resolved


def _load_config_env() -> dict:
    """Returns the ``env`` mapping of the configuration on disk.

    Returns:
        The mapping, or an empty dict when the file has none.

    Raises:
        mysqlsh.Error: If the configuration is missing or cannot be parsed.
    """
    import yaml

    path = _config_path()
    if not os.path.isfile(path):
        raise mysqlsh.Error(
            f"No migration configuration at '{path}'. Write one with "
            "migration.set_config first."
        )

    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
    except yaml.YAMLError as error:
        raise mysqlsh.Error(f"Could not parse '{path}': {error}") from error

    env = document.get("env") or {}
    if not isinstance(env, dict):
        raise mysqlsh.Error(f"The 'env' section of '{path}' is not a mapping.")

    return {key: str(value) for key, value in env.items()}


def _artifacts_dir(command: str, mode: str) -> str:
    """Returns a fresh artifacts directory name for one invocation.

    Named after the command, the mode and the moment, so that runs do not
    overwrite one another's report and a client can tell them apart - the same
    shape the tooling's own documentation uses.

    Args:
        command (str): The orchestrator subcommand.
        mode (str): The execution mode.

    Returns:
        A path relative to the install directory.
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    return os.path.join("artifacts", f"{command}_{mode}_{stamp}")


def _read_report(install_dir: str, out_dir: str) -> Optional[dict]:
    """Returns the run's own report, when it wrote one.

    Args:
        install_dir (str): The install the run happened in.
        out_dir (str): The artifacts directory, relative to it.

    Returns:
        The parsed report, or None when there is none to read.
    """
    path = os.path.join(install_dir, out_dir, "report.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        # A run that failed before writing a report, or wrote a partial one.
        return None


def _tail(text: str) -> str:
    """Returns the last :data:`OUTPUT_TAIL_LIMIT` characters of the given text."""
    text = (text or "").strip()
    if len(text) <= OUTPUT_TAIL_LIMIT:
        return text

    return "... (truncated)\n" + text[-OUTPUT_TAIL_LIMIT:]


def _run_orchestrator(command: str, arguments: list, timeout: int) -> dict:
    """Runs one orchestrator subcommand in the install directory.

    The child is given the install directory as its working directory and the
    tooling's virtual environment as its interpreter and PATH - the equivalent
    of sourcing ``.venv/bin/activate`` - so that the orchestrator and anything
    it shells out to resolve ``python3`` inside that environment and never need
    a system Python.

    Args:
        command (str): The subcommand to run.
        arguments (list): Its arguments.
        timeout (int): Seconds to allow before giving up.

    Returns:
        A dict describing the invocation's outcome.

    Raises:
        mysqlsh.Error: If the invocation timed out.
    """
    install_dir = _install_dir()
    env_mapping = _load_config_env()

    # Checked again here, not only when the configuration was written: a
    # connection removed with mcp.setup has to stop the migrations already
    # configured against it, and the file on disk may have been edited by hand
    # since.
    validate_connections(env_mapping)

    passwords, resolved = _connection_passwords(env_mapping)

    venv_dir = os.path.join(install_dir, setup_migration.MIGRATOR_VENV_DIR)
    child_env = dict(os.environ)
    child_env.update(passwords)
    # What `activate` does, without needing a shell to do it.
    child_env["VIRTUAL_ENV"] = venv_dir
    child_env["PATH"] = os.path.join(venv_dir, "bin") + os.pathsep + child_env.get(
        "PATH", ""
    )
    child_env.pop("PYTHONHOME", None)

    general.log_event(
        f"migration.{command} in '{install_dir}' with passwords for "
        f"{sorted(resolved.values()) or 'no configured connection'}"
    )

    try:
        result = subprocess.run(
            [
                setup_migration._venv_python(install_dir),
                "-m", ORCHESTRATOR_MODULE, command,
                *arguments,
            ],
            cwd=install_dir,
            env=child_env,
            # Closed rather than inherited: the orchestrator prompts for what it
            # is missing, and `plan` has no --non-interactive, so an incomplete
            # configuration has to fail loudly instead of blocking the server.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise mysqlsh.Error(
            f"migration.{command} did not finish within {timeout}s and was "
            "stopped. Its artifacts directory holds whatever it had written by "
            "then."
        ) from error

    return {
        "command": command,
        "succeeded": result.returncode == 0,
        "exit_code": result.returncode,
        "install_dir": install_dir,
        # The connections the passwords came from, never the passwords.
        "passwords_from": resolved,
        "stdout": _tail(result.stdout),
        "stderr": _tail(result.stderr),
    }


def write_config(mode: str, env: dict, merge: bool = False) -> dict:
    """Writes the tooling's config/migration.yaml. See ``migration.set_config``.

    Args:
        mode (str): The default execution mode to record.
        env (dict): The tooling's environment mapping.
        merge (bool): Update the given keys and leave the rest of an existing
            configuration alone, instead of replacing the whole file.

    Returns:
        A dict naming the file written, the keys it holds, the example it
        follows, and which passwords resolve from which configured connection.

    Raises:
        mysqlsh.Error: If the arguments are unusable, or a password was given.
    """
    install_dir = _install_dir()
    if not isinstance(env, dict):
        raise mysqlsh.Error("'env' must be a mapping of keys to values.")
    if not mode or not str(mode).strip():
        raise mysqlsh.Error("'mode' must name an execution mode.")

    given = {key: _stringify(key, value) for key, value in env.items()}

    refused = sorted(key for key in given if key in _PASSWORD_SOURCES and given[key])
    if refused:
        raise mysqlsh.Error(
            f"{', '.join(refused)} cannot be set here: passwords are read from "
            "the connections configured with mcp.setup when a migration runs, "
            "and are never written to disk by this plugin. Give the matching "
            "user/host/port fields instead."
        )

    merged = dict(_load_config_env()) if merge else {}
    merged.update(given)

    # Refused before the file is touched: a configuration naming a server
    # nobody allowed must not exist on disk at all, not merely fail to run.
    # Checked on the MERGED result, so a merge cannot assemble a forbidden
    # connection out of two individually harmless calls.
    accepted = validate_connections(merged)

    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Written beside the target and moved into place, so a run reading the
    # configuration never sees a half-written one. 0o600 because this names
    # hosts and accounts, and may carry the app-user default password the
    # tooling supports.
    handle, staging_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".migration.yaml."
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as staged:
            staged.write(_render_config(str(mode).strip(), merged))
        os.chmod(staging_path, 0o600)
        os.replace(staging_path, path)
    except Exception:
        # Never leave the staged file behind to be found later.
        if os.path.exists(staging_path):
            os.remove(staging_path)
        raise

    _, resolved = _connection_passwords(merged)

    return {
        "config_path": path,
        "mode": str(mode).strip(),
        "keys": sorted(merged),
        "example_path": os.path.join(install_dir, MIGRATION_CONFIG_EXAMPLE),
        "connections": accepted,
        "passwords_from": resolved,
    }


def register_migration_tools(server, function_groups=()) -> None:
    """Registers the migration tools, but only where the tooling is installed.

    The tools drive a program that has to be on disk to be driven, so a server
    running where the download step was never taken registers none of them
    rather than advertising four tools whose every call would fail. Installing
    the tooling therefore takes effect on the next server start, not the current
    one.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    if not setup_migration.is_installed():
        general.log_event(
            "migration tools not registered: the MySQL-to-MariaDB migration "
            f"tooling ({general.MIGRATOR_VERSION}) is not installed in "
            f"'{general.get_migrator_root()}'"
        )
        return

    import anyio.to_thread
    from mcp.server.mcpserver import Context

    tool = tool_registrar(server)

    @tool(name="migration.set_config")
    async def set_config(
        ctx: Context,
        mode: str,
        env: dict,
        merge: bool = False,
    ) -> dict:
        """Writes the migration tooling's config/migration.yaml.

        The file follows the tooling's own config/migration.yaml.example, whose
        path is returned so it can be read for the full set of keys and what
        each mode requires.

        Only servers that are already configured as MCP connections may be
        named. Every account the configuration gives - SRC_ADMIN_USER at
        SRC_HOST:SRC_PORT, TGT_ADMIN_USER at TGT_HOST:TGT_PORT, and so on - has
        to match one of the connections mcp.setup configured, and a
        configuration naming anything else is refused rather than written. Use
        db.list_connections to see what is available. A side that names a host
        must also name the account to reach it by, since otherwise there is
        nothing to check.

        Passwords are NOT written here and are refused if given: SRC_PASS,
        SRC_ADMIN_PASS, TGT_PASS, TGT_ADMIN_PASS and REPL_PASS are read from the
        matching configured connection at the moment a migration runs. Set the
        user, host and port fields and the password follows on its own.

        Args:
            mode: The default execution mode (for example one_step, two_step,
                staged, binlog, inplace, replace_slave). The mode a run uses is
                passed per call and overrides this.
            env: The tooling's environment mapping - SRC_HOST, SRC_PORT,
                SRC_ADMIN_USER, SRC_DBS, TGT_HOST and so on. Values are written
                as strings; booleans become the "1"/"0" the tooling reads.
            merge: Update the keys given and leave the rest of an existing
                configuration alone, instead of replacing the whole file.

        Returns:
            A dict naming the file written, the keys it now holds, the example
            it follows, the configured connection each account resolved to, and
            which passwords will be read from which of them when a migration
            runs.
        """
        return await anyio.to_thread.run_sync(
            lambda: write_config(mode, env, merge)
        )

    @tool(name="migration.plan")
    async def plan(
        ctx: Context,
        mode: str,
        out: Optional[str] = None,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> dict:
        """Generates a migration plan from the configuration, executing nothing.

        Args:
            mode: The execution mode to plan for (for example one_step,
                two_step, staged, binlog, inplace, replace_slave).
            out: The artifacts directory, relative to the install directory.
                Defaults to a fresh artifacts/plan_<mode>_<timestamp>.
            timeout: Seconds to allow before the invocation is stopped.

        Returns:
            A dict with the outcome, the artifacts directory and the run's own
            report.json when it wrote one.
        """
        return await anyio.to_thread.run_sync(
            lambda: _invoke("plan", mode, out, timeout, non_interactive=False)
        )

    @tool(name="migration.run")
    async def run(
        ctx: Context,
        mode: str,
        out: Optional[str] = None,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> dict:
        """Executes the migration steps for the configured source and target.

        This CHANGES the target server, and depending on the mode the source as
        well. Run migration.plan first and read its report.

        Args:
            mode: The execution mode to run (for example one_step, two_step,
                staged, binlog, inplace, replace_slave).
            out: The artifacts directory, relative to the install directory.
                Defaults to a fresh artifacts/run_<mode>_<timestamp>. Keep it:
                migration.resume needs the state.json written there.
            timeout: Seconds to allow before the invocation is stopped. A
                migration can take far longer than the default hour; the
                artifacts directory is returned either way, so a run that
                outlasts its timeout can still be followed from its own files
                and picked up with migration.resume.

        Returns:
            A dict with the outcome, the artifacts directory and the run's own
            report.json when it wrote one.
        """
        return await anyio.to_thread.run_sync(
            lambda: _invoke("run", mode, out, timeout, non_interactive=True)
        )

    @tool(name="migration.resume")
    async def resume(
        ctx: Context,
        mode: str,
        out: str,
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> dict:
        """Resumes a failed migration from the state.json of an earlier run.

        Args:
            mode: The execution mode to resume with.
            out: The artifacts directory of the run to resume, relative to the
                install directory - the one holding its state.json, as returned
                by migration.run.
            timeout: Seconds to allow before the invocation is stopped.

        Returns:
            A dict with the outcome, the artifacts directory and the run's own
            report.json when it wrote one.
        """
        if not out or not str(out).strip():
            raise mysqlsh.Error(
                "'out' must name the artifacts directory of the run to resume."
            )

        return await anyio.to_thread.run_sync(
            lambda: _invoke("resume", mode, out, timeout, non_interactive=True)
        )


def _invoke(
    command: str, mode: str, out: Optional[str], timeout: int, non_interactive: bool
) -> dict:
    """Builds one orchestrator invocation's arguments and runs it.

    Args:
        command (str): The subcommand.
        mode (str): The execution mode.
        out (str): The artifacts directory, or None for a fresh one.
        timeout (int): Seconds to allow.
        non_interactive (bool): Whether the subcommand takes
            ``--non-interactive``. ``plan`` does not, which is why every
            invocation also gets a closed stdin.

    Returns:
        The outcome dict, with the artifacts directory and report added.
    """
    if not mode or not str(mode).strip():
        raise mysqlsh.Error("'mode' must name an execution mode.")

    mode = str(mode).strip()
    out_dir = str(out).strip() if out and str(out).strip() else _artifacts_dir(
        command, mode
    )
    if os.path.isabs(out_dir):
        raise mysqlsh.Error(
            f"'out' must be relative to the install directory, not '{out_dir}'."
        )

    arguments = [
        "--config", MIGRATION_CONFIG,
        "--mode", mode,
        "--out", out_dir,
    ]
    if non_interactive:
        arguments.append("--non-interactive")

    outcome = _run_orchestrator(command, arguments, timeout)
    outcome["mode"] = mode
    outcome["out_dir"] = out_dir
    outcome["report"] = _read_report(outcome["install_dir"], out_dir)

    return outcome
