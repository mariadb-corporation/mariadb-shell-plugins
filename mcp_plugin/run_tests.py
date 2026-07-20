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

# To use this script you need to set these environment variables:
#
# MYSQLSH=<path to mysqlsh/mariadb-shell binary>
# MYSQLSH_USER_CONFIG_HOME=<shell user config home to use for the test run>
#
# If not configured, they will be set as follows:
# MYSQLSH to the mysqlsh (or mariadb-shell) found in PATH
# MYSQLSH_USER_CONFIG_HOME to a temporary directory

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
        or os.environ.get("MYSQLSH")
        or shutil.which("mariadb-shell")
        or shutil.which("mysqlsh")
    )
    assert shell is not None, (
        "Could not find the MySQL/MariaDB Shell binary. Set MYSQLSH or pass "
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
    parser.add_argument("-s", "--shell", default=None, help="Path to the shell binary")
    parser.add_argument(
        "-u",
        "--userhome",
        default=os.environ.get("MYSQLSH_USER_CONFIG_HOME"),
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
        or os.path.join(tempfile.mkdtemp(prefix="mcp_dot_mysqlsh_"), "dot_mysqlsh")
    )
    plugins_dir = user_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # The server subprocess launched by the tests loads the plugins from the
    # user config home, so both this plugin and the msm plugin it wraps must be
    # available there.
    _create_symlink(plugin_dir, plugins_dir / "mcp_plugin")
    msm_source = source_root / "msm_plugin"
    if msm_source.is_dir():
        _create_symlink(msm_source, plugins_dir / "msm_plugin")

    env = os.environ.copy()
    env["MYSQLSH_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MYSQLSH_TERM_COLOR_MODE"] = "nocolor"
    env["MYSQLSH"] = shell

    pattern = f"-k {args.only}" if args.only else ""
    command = (
        f"{shell} --pym pip install pytest pytest-cov mcp"
    )
    print(command)
    completed = subprocess.run(command, shell=True, env=env)

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
