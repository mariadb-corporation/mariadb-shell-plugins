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

# Runs the tests of the dba plugin: dba_core and every vendor plugin of the
# dba_plugin plugin group.
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
    group_dir = plugin_dir.parent
    vendor_dirs = sorted(
        path for path in group_dir.glob("vendor_*") if (path / "init.py").exists()
    )
    if args.vendors is not None:
        wanted = {f"vendor_{name.strip()}" for name in args.vendors.split(",") if name.strip()}
        vendor_dirs = [path for path in vendor_dirs if path.name in wanted]

    user_home = Path(
        args.userhome
        or os.path.join(tempfile.mkdtemp(prefix="dba_dot_mariadb_shell_"), "dot_mariadb_shell")
    )
    plugins_dir = user_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    _isolate_secret_store(user_home, shell)

    # The group is a real folder holding links to the members tested, so that
    # --vendors leaves the others out of the shell, and its package file.
    group_link = plugins_dir / group_dir.name
    if group_link.is_symlink():
        group_link.unlink()
    group_link.mkdir(exist_ok=True)
    shutil.copyfile(group_dir / "__init__.py", group_link / "__init__.py")
    for directory in [plugin_dir, *vendor_dirs]:
        _create_symlink(directory, group_link / directory.name)

    env = os.environ.copy()
    env["MARIADB_SHELL_USER_CONFIG_HOME"] = user_home.as_posix()
    env["MARIADB_SHELL_TERM_COLOR_MODE"] = "nocolor"

    # The test tools go into a folder of their own, never into the shell's
    # site-packages: see _install_test_dependencies.
    test_deps = Path(tempfile.mkdtemp(prefix="test_python_deps_")) / "site"
    if _install_test_dependencies(shell, plugin_dir / "requirements.txt", test_deps, env) != 0:
        return 1
    _with_pythonpath(env, test_deps)

    # The plugins are tested through their symlinks, which is where the shell
    # loads them from - so the modules the tests import are the loaded ones.
    test_dirs = " ".join(
        shlex.quote(str(group_link / directory.name)) for directory in [plugin_dir, *vendor_dirs]
    )
    covered = " ".join(
        f"--cov={shlex.quote(str(group_link / directory.name))}"
        for directory in [plugin_dir, *vendor_dirs]
    )
    pattern = f"-k {shlex.quote(args.only)}" if args.only else ""
    live = "--live" if args.live else ""
    command = (
        f"{shell} --pym pytest -c {plugin_dir / 'pytest-coverage.ini'} -p dba_plugin.dba_core.tests.fixtures "
        f"--rootdir={plugins_dir} {covered} -vv {test_dirs} {pattern} {live} "
        f"-W ignore::DeprecationWarning"
    )
    print(command)
    return subprocess.run(command, shell=True, env=env, cwd=plugin_dir).returncode


if __name__ == "__main__":
    raise SystemExit(main())
