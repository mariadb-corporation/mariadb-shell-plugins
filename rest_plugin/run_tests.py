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

# cSpell:ignore mysqlsh mariadb userhome

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
    # Anything else (a test file, say) is handed to pytest as given.
    args, pytest_arguments = parser.parse_known_args()

    shell = _resolve_shell(args.shell)

    plugin_dir = Path(__file__).resolve().parent  # .../rest_plugin
    source_root = plugin_dir.parent  # repo root containing the plugin folders

    assert (plugin_dir / "run_tests.py").exists(), (
        "Please run this script inside the rest_plugin directory."
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
    _create_symlink(plugin_dir, plugins_dir / "rest_plugin")
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
    pytest_options.extend(pytest_arguments)
    # A test file or directory given on the command line replaces the whole
    # plugin as the thing to run.
    test_paths = [argument for argument in pytest_arguments if not argument.startswith("-")]
    tests = " ".join(test_paths) if test_paths else str(plugin_dir)

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
