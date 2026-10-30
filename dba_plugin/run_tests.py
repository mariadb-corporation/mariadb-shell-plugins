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

# Runs the tests of the dba plugin and of every dba vendor plugin next to it.
#
# MARIADB_SHELL=<path to the mariadb-shell binary>, else the one on the PATH.
# MARIADB_SHELL_USER_CONFIG_HOME=<shell user config home>, else a temporary
# one. Either way the run keeps its secrets there, with the shell's plaintext
# credential helper, never in the OS secret store - the tests store and
# delete API keys.
#
# --live also runs the tests against the vendors' real APIs, for each vendor
# whose DBA_TEST_<VENDOR>_API_KEY environment variable is set.

# cSpell:ignore mysqlsh mariadb userhome

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path


def _create_symlink(target: Path, link_name: Path) -> None:
    if link_name.exists() or link_name.is_symlink():
        link_name.unlink()
    if os.name == "nt":
        subprocess.run(f'mklink /J "{link_name}" "{target}"', shell=True, check=True)
    else:
        os.symlink(target, link_name)


def _isolate_secret_store(user_home: Path, shell: str) -> None:
    """Keeps the run's secrets in its own config home, where the shell has
    the plaintext helper (see mcp_plugin/run_tests.py)."""
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s", "--shell", type=Path,
        default=os.environ.get(
            "MARIADB_SHELL",
            shutil.which("mariadb-shell.exe") if os.name == "nt" else shutil.which("mariadb-shell"),
        ),
        help="Path to MariaDB Shell binary",
    )
    parser.add_argument(
        "-u", "--userhome", default=os.environ.get("MARIADB_SHELL_USER_CONFIG_HOME"),
        help="Shell user config home to use",
    )
    parser.add_argument("-k", "--only", default=None, help="Only run tests matching this pattern")
    parser.add_argument(
        "--vendors", default=None,
        help="Comma-separated vendor plugins to test, e.g. hetzner; all by default.",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Also run the tests against the vendors' real APIs.",
    )
    args = parser.parse_args()

    assert args.shell, "Could not find the MariaDB Shell binary. Set MARIADB_SHELL or pass --shell."
    shell = str(args.shell)

    plugin_dir = Path(__file__).resolve().parent
    source_root = plugin_dir.parent
    vendor_dirs = sorted(
        path for path in source_root.glob("dba_vendor_*_plugin") if (path / "init.py").exists()
    )
    if args.vendors is not None:
        wanted = {f"dba_vendor_{name.strip()}_plugin" for name in args.vendors.split(",") if name.strip()}
        vendor_dirs = [path for path in vendor_dirs if path.name in wanted]

    user_home = Path(
        args.userhome
        or os.path.join(tempfile.mkdtemp(prefix="dba_dot_mariadb_shell_"), "dot_mariadb_shell")
    )
    plugins_dir = user_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    _isolate_secret_store(user_home, shell)

    for directory in [plugin_dir, *vendor_dirs]:
        _create_symlink(directory, plugins_dir / directory.name)

    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"

    command = f"{shell} --pym pip install -r {plugin_dir / 'requirements.txt'}"
    print(command)
    if subprocess.run(command, shell=True, env=env).returncode != 0:
        print("Failed to install the test dependencies.")
        return 1

    # The plugins are tested through their symlinks, which is where the shell
    # loads them from - so the modules the tests import are the loaded ones.
    test_dirs = " ".join(
        shlex.quote(str(plugins_dir / directory.name)) for directory in [plugin_dir, *vendor_dirs]
    )
    covered = " ".join(
        f"--cov={shlex.quote(str(plugins_dir / directory.name))}"
        for directory in [plugin_dir, *vendor_dirs]
    )
    pattern = f"-k {shlex.quote(args.only)}" if args.only else ""
    live = "--live" if args.live else ""
    command = (
        f"{shell} --pym pytest -c {plugin_dir / 'pytest-coverage.ini'} -p dba_plugin.tests.fixtures "
        f"--rootdir={plugins_dir} {covered} -vv {test_dirs} {pattern} {live} "
        f"-W ignore::DeprecationWarning"
    )
    print(command)
    return subprocess.run(command, shell=True, env=env, cwd=plugin_dir).returncode


if __name__ == "__main__":
    raise SystemExit(main())
