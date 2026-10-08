# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
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
# The suite deploys its own MariaDB sandbox (see tests/conftest.py), so a
# mariadbd binary has to be on the PATH; no running server is needed.

# cSpell:ignore mysqlsh mariadb userhome mdupgrade

import argparse
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


def _create_symlink(target: Path, link_name: Path) -> None:
    if link_name.exists() or link_name.is_symlink():
        link_name.unlink()
    if os.name == "nt":
        subprocess.run(
            f'mklink /J "{link_name}" "{target}"', shell=True, check=True
        )
    else:
        os.symlink(target, link_name)


def _print_shell_log(user_home: Path) -> None:
    """Prints the shell's log of the run, the place the plugin's errors go."""
    log_path = user_home / "mariadb-shell.log"
    if not log_path.exists():
        return
    print("----------------------------------------")
    print("MariaDB Shell log")
    print("----------------------------------------")
    with open(log_path, encoding="utf-8", errors="replace") as log:
        for line in log:
            print(line.rstrip())


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
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show the tests' output and run the shell with --verbose",
    )
    parser.add_argument(
        "-M",
        "--shell-options",
        default=None,
        help="Additional options to pass to the MariaDB Shell",
    )
    parser.add_argument(
        "-P",
        "--pytest",
        default=None,
        help="Additional options to pass to pytest",
    )
    parser.add_argument(
        "--mdupgrade",
        action="store_true",
        default=False,
        help=(
            "Also run the metadata upgrade test. It is skipped otherwise: it "
            "deploys a second sandbox and installs every released metadata "
            "version, which is slow."
        ),
    )
    # Anything else (a test file, say) is handed to pytest as given.
    args, pytest_arguments = parser.parse_known_args()

    shell = _resolve_shell(args.shell)

    plugin_dir = Path(__file__).resolve().parent  # .../mrs_plugin
    source_root = plugin_dir.parent  # repo root containing the plugin folders

    assert (plugin_dir / "run_tests.py").exists(), (
        "Please run this script inside the mrs_plugin directory."
    )

    user_home = Path(
        args.userhome
        or os.path.join(
            tempfile.mkdtemp(prefix="mrs_dot_mariadb_shell_"), "dot_mariadb_shell"
        )
    )
    plugins_dir = user_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # The shell loads the plugins from the user config home, so this plugin and
    # msm_plugin, which deploys the MRS metadata schema, must be available
    # there.
    _create_symlink(plugin_dir, plugins_dir / "mrs_plugin")
    msm_source = source_root / "msm_plugin"
    if msm_source.is_dir():
        _create_symlink(msm_source, plugins_dir / "msm_plugin")

    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"
    env["MARIADB_SHELL"] = shell
    # The sandbox the suite deploys is a throw-away test server: skipping the
    # syncs makes its DDL, which the suite does a lot of, much faster.
    env.setdefault("MARIADB_SANDBOX_NO_SYNC", "1")

    shell_options = args.shell_options or ""
    pytest_options = [args.pytest or ""]
    if args.verbose:
        shell_options += " --verbose"
        pytest_options.append("-sv")
    if args.only:
        # Quoted, so a pattern with "or" / "and" stays one argument.
        pytest_options.append(f"-k {shlex.quote(args.only)}")
    if args.mdupgrade:
        pytest_options.append("--mdupgrade")
    pytest_options.extend(pytest_arguments)
    # A test file or directory given on the command line replaces the whole
    # plugin as the thing to run.
    test_paths = [argument for argument in pytest_arguments if not argument.startswith("-")]
    tests = " ".join(test_paths) if test_paths else str(plugin_dir)

    # Install the test dependencies into the shell's Python. Driven off
    # requirements.txt so the versions here honour the pins declared there.
    command = f"{shell} --pym pip install -r {plugin_dir / 'requirements.txt'}"
    print(command)
    completed = subprocess.run(command, shell=True, env=env)
    if completed.returncode != 0:
        print("Failed to install the test dependencies.")
        return completed.returncode

    command = (
        f"{shell} {shell_options} --pym pytest "
        f"-c {plugin_dir / 'pytest-coverage.ini'} "
        f"--cov={plugin_dir} --cov-append -vv {tests} "
        f"{' '.join(option for option in pytest_options if option)} "
        f"-W ignore::DeprecationWarning"
    )
    print(command)
    # The tests resolve relative paths (the content sets, the grammar test's
    # files) from the plugin directory.
    completed = subprocess.run(command, shell=True, env=env, cwd=plugin_dir)
    if completed.returncode != 0:
        _print_shell_log(user_home)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
