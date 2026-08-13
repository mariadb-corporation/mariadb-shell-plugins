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

# To use this script you need to set these environment variables:
#
# MARIADB_SHELL=<path to the mariadb-shell binary>
# MARIADB_SHELL_USER_CONFIG_HOME=<shell user config home to use for the test run>
#
# If not configured, they will be set as follows:
# MARIADB_SHELL to the mariadb-shell found in PATH
# MARIADB_SHELL_USER_CONFIG_HOME to a temporary directory
#

# cSpell:ignore mysqlsh mariadb userhome

import argparse
import os
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


def _create_symlink(target: Path, link_name: Path) -> None:
    if link_name.exists() or link_name.is_symlink():
        link_name.unlink()
    if os.name == "nt":
        subprocess.run(
            f'mklink /J "{link_name}" "{target}"', shell=True, check=True
        )
    else:
        os.symlink(target, link_name)


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

    # The server subprocess launched by the tests loads the plugins from the
    # user config home, so this plugin and the sibling plugins it relies on must
    # be available there: msm_plugin (wrapped by the msm.* tools) and mrs_plugin
    # (registers the REST SQL handler exercised by test_rest_sql.py).
    _create_symlink(plugin_dir, plugins_dir / "mcp_plugin")
    for sibling in ("msm_plugin", "mrs_plugin"):
        sibling_source = source_root / sibling
        if sibling_source.is_dir():
            _create_symlink(sibling_source, plugins_dir / sibling)

    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"
    env["MARIADB_SHELL"] = shell

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

    pattern = f"-k {args.only}" if args.only else ""
    # Install the test dependencies into the shell's Python. Driven off
    # requirements.txt so the versions here honour the pins declared there,
    # notably the MCP SDK major version.
    command = f"{shell} --pym pip install -r {plugin_dir / 'requirements.txt'}"
    print(command)
    completed = subprocess.run(command, shell=True, env=env)
    if completed.returncode != 0:
        print("Failed to install the test dependencies.")
        return completed.returncode

    command = (
        f"{shell} --pym pytest -c {plugin_dir / 'pytest-coverage.ini'} "
        f"--cov={plugin_dir} --cov-append -vv {plugin_dir} {pattern} "
        f"-W ignore::DeprecationWarning"
    )
    print(command)
    completed = subprocess.run(command, shell=True, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
