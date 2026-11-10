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

# To use this script you need to set these environment variables:
#
# MARIADB_SHELL=<path to the mariadb-shell binary>
# MARIADB_SHELL_USER_CONFIG_HOME=<shell user config home to use for the test run>
#
# If not configured, they will be set as follows:
# MARIADB_SHELL to the mariadb-shell found in PATH
# MARIADB_SHELL_USER_CONFIG_HOME to a temporary directory
#
# MARIADB_SHELL_OPTIONS (or --shell-options) holds extra options for every
# shell the run starts, for example --log-level=debug3.
#
# Either way the run keeps its secrets in that home, with the shell's
# plaintext credential helper, never in the OS secret store: the tests store,
# clear and restore connections, and on the OS store those are the
# developer's own.
#

# cSpell:ignore mysqlsh mariadb userhome

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


def _resolve_shell(explicit):
    shell = (
        explicit
        or os.environ.get("MARIADB_SHELL")
        or shutil.which("mariadb-shell")
    )
    assert shell is not None, (
        "Could not find the MariaDB Shell binary. Set MARIADB_SHELL or pass "
        "--shell."
    )
    return str(shell)


def _isolate_secret_store(user_home: Path, shell: str) -> None:
    """Keeps the run's secrets in its own config home.

    The shell's secret store is the OS one by default - the macOS keychain,
    the Windows credential manager - which a config home does not change. The
    tests store, clear and restore connections through it, so a run against
    it works on the developer's own connections: a restore that lost their
    folders filed every one of them at the top level. The plaintext helper
    keeps the secrets in ``.mariadb-secret-store-plaintext.json`` under the
    config home instead, where every process of the run - this one, and the
    MCP servers the tests start - reads the same ones, and nothing outside
    the run sees them.

    Not every build ships the plaintext helper - the CI shell does not - and
    setting a helper the shell lacks fails every test that stores a
    connection. Such a shell keeps its default store, which on a CI runner
    holds nobody's connections.

    Args:
        user_home (Path): The run's shell user config home.
        shell (str): The shell binary, whose directory holds the helpers.

    Returns:
        None
    """
    helper = Path(shell).resolve().parent / (
        "mariadb-secret-store-plaintext.exe" if os.name == "nt"
        else "mariadb-secret-store-plaintext"
    )
    if not helper.exists():
        print(f"No {helper.name} next to the shell: the run uses the shell's "
              "default secret store.")
        return

    options_path = user_home / "options.json"
    options = {}
    if options_path.exists():
        with open(options_path, encoding="utf-8") as options_file:
            options = json.load(options_file)
    options["credentialStore.helper"] = "plaintext"
    with open(options_path, "w", encoding="utf-8") as options_file:
        json.dump(options, options_file, indent=4)


def _create_symlink(target: Path, link_name: Path) -> None:
    if link_name.exists() or link_name.is_symlink():
        link_name.unlink()
    if os.name == "nt":
        subprocess.run(
            f'mklink /J "{link_name}" "{target}"', shell=True, check=True
        )
    else:
        os.symlink(target, link_name)


def _install_test_dependencies(shell: str, requirements: Path, target: Path, env: dict) -> int:
    """Installs the test dependencies into a folder of their own.

    The shell ships what its plugins need at run time (mcp, pydantic, ...);
    the tests only add their tools (pytest, pytest-cov, ...). Those are never
    installed into the shell's own site-packages: resolving their
    dependencies there lets pip replace a package the shell bundles, and one
    that no longer matches the rest breaks the shell (pydantic_core swapped
    under pydantic). They go into target instead, which the caller puts on
    PYTHONPATH for the test run.

    pip is held to the versions the shell has, and every package the shell
    already has is removed from target again, so the tests always run
    against the shell's own packages.

    Args:
        shell: The shell command to run pip with.
        requirements: The plugin's requirements.txt.
        target: The folder to install into; it is emptied first.
        env: The environment to run pip in.

    Returns:
        pip's exit code, 0 on success.
    """
    import csv
    import re

    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)

    listed = subprocess.run(
        f"{shell} --pym pip list --format=freeze",
        shell=True, env=env, capture_output=True, text=True,
    )
    if listed.returncode != 0:
        print(listed.stdout + listed.stderr)
        print("Failed to list the packages of the shell's Python.")
        return listed.returncode
    installed = sorted(
        line.strip() for line in listed.stdout.splitlines()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==\S+", line.strip())
    )
    constraints = target.parent / f"{target.name}-constraints.txt"
    constraints.write_text("\n".join(installed) + "\n")

    command = (
        f"{shell} --pym pip install --disable-pip-version-check "
        f"--target {shlex.quote(str(target))} -r {shlex.quote(str(requirements))} "
        f"-c {shlex.quote(str(constraints))}"
    )
    print(command)
    completed = subprocess.run(command, shell=True, env=env)
    if completed.returncode != 0:
        print("Failed to install the test dependencies.")
        return completed.returncode

    def key(name: str) -> str:
        return re.sub(r"[-_.]+", "_", name).lower()

    have = {key(line.split("==", 1)[0]): line.split("==", 1)[1] for line in installed}
    for info in sorted(target.glob("*.dist-info")):
        name, _, version = info.name[: -len(".dist-info")].rpartition("-")
        if have.get(key(name)) != version:
            continue
        record = info / "RECORD"
        rows = csv.reader(record.read_text().splitlines()) if record.is_file() else []
        for row in rows:
            path = (target / row[0]).resolve() if row else None
            if path is not None and path.is_relative_to(target.resolve()) and path.is_file():
                path.unlink()
        shutil.rmtree(info, ignore_errors=True)
    # The folders the removed files leave empty, deepest first
    for folder in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        if folder.name == "__pycache__":
            shutil.rmtree(folder, ignore_errors=True)
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
    remaining = sorted(info.name for info in target.glob("*.dist-info"))
    print("Test dependencies the shell does not have: " + (", ".join(remaining) or "none"))
    return 0


def _with_pythonpath(env: dict, folder: Path) -> None:
    """Puts folder in front of the PYTHONPATH of env."""
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{folder}{os.pathsep}{existing}" if existing else str(folder)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--shell",
        required=False,
        type=Path,
        default=os.environ.get(
            "MARIADB_SHELL",
            shutil.which("mariadb-shell.exe") if os.name == "nt" else shutil.which("mariadb-shell"),
        ),
        help="Path to MariaDB Shell binary",
    )
    parser.add_argument(
        "-u",
        "--userhome",
        default=os.environ.get("MARIADB_SHELL_USER_CONFIG_HOME"),
        help="Shell user config home to use",
    )
    parser.add_argument(
        "-k", "--only", default=None, help="Only run tests matching this pattern"
    )
    parser.add_argument(
        "-M",
        "--shell-options",
        default=os.environ.get("MARIADB_SHELL_OPTIONS", ""),
        help=(
            "Extra options for every shell the run starts, the MCP servers "
            "the tests launch included, e.g. --log-level=debug3"
        ),
    )
    parser.add_argument(
        "-e",
        "--e2e",
        action="store_true",
        default=False,
        help=(
            "Also run the end-to-end tests. They are skipped otherwise: each "
            "deploys its own source and target servers and installs the "
            "migration tooling, so they are not part of a standard run."
        ),
    )
    parser.add_argument(
        "--keycloak",
        action="store_true",
        default=False,
        help=(
            "Also run the tests against a real Keycloak realm, configured by the "
            "KEYCLOAK_* environment variables (see tests/unit/test_oauth.py)."
        ),
    )
    args = parser.parse_args()

    shell = _resolve_shell(args.shell)

    plugin_dir = Path(__file__).resolve().parent  # .../mcp_plugin
    source_root = plugin_dir.parent  # repo root containing the plugin folders

    assert (plugin_dir / "run_tests.py").exists(), (
        "Please run this script inside the mcp_plugin directory."
    )

    user_home = Path(
        args.userhome
        or os.path.join(
            tempfile.mkdtemp(prefix="mcp_dot_mariadb_shell_"), "dot_mariadb_shell"
        )
    )
    plugins_dir = user_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    _isolate_secret_store(user_home, shell)

    # The server subprocess launched by the tests loads the plugins from the
    # user config home, so this plugin and the sibling plugins it relies on must
    # be available there: msm_plugin (wrapped by the msm.* tools), rest_plugin
    # (registers the REST SQL handler exercised by test_rest_sql.py) and
    # the dba_plugin group, its core and vendor plugins (wrapped by the dba.*
    # tools).
    _create_symlink(plugin_dir, plugins_dir / "mcp_plugin")
    for sibling in ("msm_plugin", "rest_plugin", "dba_plugin"):
        sibling_source = source_root / sibling
        if sibling_source.is_dir():
            _create_symlink(sibling_source, plugins_dir / sibling)

    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"
    env["MARIADB_SHELL"] = shell
    # Read by the tests' helpers for every shell they start.
    env["MARIADB_SHELL_OPTIONS"] = args.shell_options
    shell = f"{shell} {args.shell_options}".rstrip()

    # Enable coverage of the MCP server stdio subprocess: put the coverage
    # bootstrap (a sitecustomize) on the subprocess PYTHONPATH and tell the
    # test harness (via MCP_COVERAGE_RC) which coverage config the subprocess
    # should start with. COVERAGE_PROCESS_START itself is only set on the
    # subprocess (in helpers), so the pytest process' pytest-cov is unaffected.
    cov_bootstrap = plugin_dir / "tests" / "_cov"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{cov_bootstrap}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(cov_bootstrap)
    )
    env["MCP_COVERAGE_RC"] = str(plugin_dir / ".coveragerc")

    pattern = f"-k {shlex.quote(args.only)}" if args.only else ""
    e2e = "--e2e" if args.e2e else ""
    keycloak = "--keycloak" if args.keycloak else ""
    # The MCP SDK comes with the shell, not from requirements.txt. The plugin
    # is written against its 2.x API (mcp.server.mcpserver), so a shell that
    # bundles another major version is reported here, not as a failure of
    # every test.
    code = "import importlib.metadata as m; print(m.version('mcp'))"
    found = subprocess.run(
        f"{shell} --py -e {shlex.quote(code)}",
        shell=True, env=env, capture_output=True, text=True,
    )
    lines = [line.strip() for line in found.stdout.splitlines() if line.strip()]
    sdk_version = lines[-1] if found.returncode == 0 and lines else None
    if sdk_version is None or not sdk_version.startswith("2."):
        print(found.stdout + found.stderr)
        print(f"The shell bundles MCP SDK {sdk_version or '(none)'}; the plugin needs 2.x.")
        return 1

    # The test tools go into a folder of their own, never into the shell's
    # site-packages: see _install_test_dependencies.
    test_deps = Path(tempfile.mkdtemp(prefix="test_python_deps_")) / "site"
    returncode = _install_test_dependencies(
        shell, plugin_dir / "requirements.txt", test_deps, env
    )
    if returncode != 0:
        return returncode
    _with_pythonpath(env, test_deps)

    command = (
        f"{shell} --pym pytest -c {plugin_dir / 'pytest-coverage.ini'} "
        f"--cov={plugin_dir} --cov-append -vv {plugin_dir} {pattern} {e2e} {keycloak} "
        f"-W ignore::DeprecationWarning"
    )
    print(command)
    completed = subprocess.run(command, shell=True, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
