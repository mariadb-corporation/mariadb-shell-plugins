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
# MARIADB_SHELL=<path to mariadb-shell binary>
# MARIADB_SHELL_USER_CONFIG_HOME=<shell user config home>
# MARIADB_SHELL_PLUGIN_SOURCE_DIR=<source code path to the plugins>
#
# If not configured, they will be set as follows:
# MARIADB_SHELL to the mariadb-shell in PATH
# MARIADB_SHELL_USER_CONFIG_HOME to /tmp/dot_mariadb_shell
# MARIADB_SHELL_PLUGIN_SOURCE_DIR to ../../
import shutil
import shlex
import os
import tempfile
import subprocess
from pathlib import Path
import argparse
from contextlib import contextmanager

import signal
import sqlite3
import zipfile


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


def signal_handler(sig, frame):
    print(f"1) Ctrl+C! captured: {sig}")


if os.name == "nt":
    signal.signal(signal.SIGINT, signal_handler)


@contextmanager
def pushd(new_path):
    current = os.getcwd()
    os.chdir(new_path.as_posix())
    yield
    os.chdir(current)


def create_symlink(target: Path, link_name: Path, is_dir):
    if os.name == "nt":
        p = subprocess.run(f'mklink /J "{link_name}" "{target}"', shell=True)
        print(p.stdout)
        p.check_returncode()
    else:
        os.symlink(target, link_name)


arg_parser = argparse.ArgumentParser()

arg_parser.add_argument(
    "-d",
    "--debug",
    required=False,
    choices=["TESTS", "BACKEND"],
    default=os.environ.get("ATTACH_DEBUGGER", None),
    help="Attach debugger to TESTS and/or BACKEND",
)
arg_parser.add_argument(
    "-p", "--portable", required=False, type=Path, help="The path to the portable code"
)
arg_parser.add_argument(
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
arg_parser.add_argument("-v", "--verbose", required=False, help="Enable verbose mode")
arg_parser.add_argument(
    "-u",
    "--userhome",
    required=False,
    type=Path,
    default=os.environ.get("MARIADB_SHELL_USER_CONFIG_HOME", None),
    help="Path to the user config home",
)
arg_parser.add_argument(
    "-k",
    "--only",
    required=False,
    type=str,
    default=None,
    help="Run only the tests that apply to the pattern",
)

try:
    args = arg_parser.parse_args()
except argparse.ArgumentError as e:
    print(str(e))

print(args)

# check if we're running in the backend directory
assert Path(
    os.path.join(os.getcwd(), "run_tests.py")
).exists(), "Please run this script inside the backend directory."


assert (
    args.shell is not None
), "Could not find the MariaDB Shell binary. Please specify it using the --shell parameter or the MARIADB_SHELL environment variable."


class MyPaths:
    def __init__(self, debug_mode, portable_path, shell_path, userhome_path: Path):
        class MyPathsBase:
            pass

        self.source = MyPathsBase()
        self.runtime = MyPathsBase()
        self.runtime.plugins = MyPathsBase()
        self.runtime.plugin_data = MyPathsBase()

        self.shell = shell_path

        if debug_mode:
            self.runtime.root = Path(
                os.path.join(tempfile.gettempdir(), "backend_debug")
            )
            shutil.rmtree(self.runtime.root, ignore_errors=True)
        elif userhome_path is None:
            self.runtime.root = Path(
                os.path.join(tempfile.TemporaryDirectory().name, "dot_mariadb_shell")
            )
        else:
            self.runtime.root = Path(userhome_path)

        self.runtime.plugins.root = Path(os.path.join(self.runtime.root, "plugins"))
        self.runtime.plugins.gui_plugin = Path(
            os.path.join(self.runtime.plugins.root, "gui_plugin")
        )
        self.runtime.plugins.msm_plugin = Path(
            os.path.join(self.runtime.plugins.root, "msm_plugin")
        )

        self.runtime.plugin_data.root = Path(
            os.path.join(self.runtime.root, "plugin_data")
        )
        self.runtime.plugin_data.gui_plugin = Path(
            os.path.join(self.runtime.plugin_data.root, "gui_plugin")
        )

        self.source.root = Path(os.path.abspath(os.path.join(Path().cwd(), "..")))
        self.source.plugin = Path(os.path.join(self.source.root, "msm_plugin"))

        self.source.pytest_config = Path(
            os.path.join(
                self.source.plugin,
                (
                    "pytest-coverage.ini"
                    if debug_mode is None
                    else self.source.plugin / "pytest.ini"
                ),
            )
        )

        if portable_path is None:
            self.source.code = self.source.plugin
        else:
            self.source.code = portable_path

    def verify(self):
        assert self.runtime.root.is_dir()
        assert self.runtime.plugins.root.is_dir()
        assert self.runtime.plugins.gui_plugin.is_dir()
        assert self.source.webroot.is_dir()
        assert self.source.pytest_config.is_file()


if args.portable is not None and zipfile.is_zipfile(args.portable):
    # Unzip the portable zip
    unzip_path = os.path.dirname(args.portable)
    with zipfile.ZipFile(args.portable, "r") as zip_ref:
        zip_ref.extractall(unzip_path)

    # Update the portable argument with the final path
    args.portable = os.path.join(unzip_path, "gui_plugin")

paths = MyPaths(args.debug, args.portable, args.shell, args.userhome)

# remove the shell home dir
if args.debug is not None and paths.runtime.root.exists():
    shutil.rmtree(paths.runtime.root, ignore_errors=True)

# create the shell home dir
if not paths.runtime.root.is_dir():
    paths.runtime.root.mkdir(parents=True)

# create .mariadb-shell/plugins
if not paths.runtime.plugins.root.is_dir():
    paths.runtime.plugins.root.mkdir(parents=True)

# remove link to .mariadb-shell/plugins/msm_plugin
if paths.runtime.plugins.msm_plugin.exists():
    paths.runtime.plugins.msm_plugin.unlink()

# Create source code symlink into the runtime plugin dir (.mariadb-shell/plugins/msm_plugin)
create_symlink(paths.source.code, paths.runtime.plugins.msm_plugin, is_dir=True)

LOGS = ""
PATTERN = ""
# Enables verbose execution
if args.verbose is not None or args.debug is not None:
    LOGS = "-sv"

if args.only is not None:
    PATTERN = f"-k {args.only}"

with pushd(paths.source.plugin):
    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = paths.runtime.root.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"
    env["COV_CORE_DATAFILE"] = ".coverage.eager"

    if args.debug is not None:
        env["ATTACH_DEBUGGER"] = args.debug

    # The test tools go into a folder of their own, never into the shell's
    # site-packages: see _install_test_dependencies.
    test_deps = Path(tempfile.mkdtemp(prefix="test_python_deps_")) / "site"
    returncode = _install_test_dependencies(
        paths.shell, paths.source.plugin / "requirements.txt", test_deps, env
    )
    if returncode != 0:
        exit(returncode)
    _with_pythonpath(env, test_deps)

    command = f"{paths.shell} --pym pytest --cov={paths.source.code} --cov-append -vv -c {paths.source.pytest_config} {LOGS} {paths.source.plugin} {PATTERN} -W ignore::DeprecationWarning"
    print(command)
    shell = subprocess.run(command, shell=True, env=env)

if not shell.returncode == 0:
    print("----------------------------------------")
    print("MARIADB SHELL log")
    print("----------------------------------------")
    with open(os.path.join(paths.runtime.root / "mariadb-shell.log")) as f:
        for line in f.readlines():
            print(line.strip())

exit(shell.returncode)
