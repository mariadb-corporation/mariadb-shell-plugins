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

"""The MariaDB SQL language server's binary, mariadb-lsp, as msm runs it.

msm reads the schema objects of a project's development script through the
binary's ``outline`` command: that is what the schema snapshot of the
diagrams (MSM Section 160) is made from. No shell import at module scope, so
the module can be tested without a shell.
"""

import json
import os
import shutil
import subprocess
from typing import Optional

LSP_BINARY_NAME = "mariadb-lsp"

# How long the binary may take: --version, and an outline of a large script.
_VERSION_TIMEOUT = 10
_OUTLINE_TIMEOUT = 120


class LspError(ValueError):
    """The binary is missing, or did not answer as it should."""


def _binary_name() -> str:
    return LSP_BINARY_NAME + (".exe" if os.name == "nt" else "")


def _shell_folder() -> Optional[str]:
    """The folder of the shell msm runs in, where its package installs the binary."""
    try:
        import mysqlsh
    except ImportError:
        return None
    own = getattr(mysqlsh, "executable", None)
    return os.path.dirname(own) if own else None


def _candidates(lsp_path: Optional[str]) -> list:
    """The places the binary is looked for, in order."""
    if lsp_path:
        if os.path.isdir(lsp_path):
            return [os.path.join(lsp_path, _binary_name())]
        return [lsp_path]

    found = []
    shell_folder = _shell_folder()
    if shell_folder:
        found.append(os.path.join(shell_folder, _binary_name()))
    on_path = shutil.which(_binary_name())
    if on_path:
        found.append(on_path)
    found.append(os.path.join(os.path.expanduser("~"), ".local", "bin", _binary_name()))
    return found


def find_lsp(lsp_path: Optional[str] = None) -> Optional[str]:
    """Returns the mariadb-lsp binary, or None where there is none.

    Looked for as mcp_plugin's sql tools and the VS Code extension do: the
    given path (the binary or its folder), else next to the shell, on PATH,
    then in ~/.local/bin. A candidate counts only if ``--version`` answers
    ``mariadb-lsp <version>``.

    Args:
        lsp_path: The binary or its folder, to take instead of searching.

    Returns:
        The binary's path, or None.
    """
    for candidate in _candidates(lsp_path):
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            continue
        try:
            answer = subprocess.run(
                [candidate, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if answer.returncode == 0 and answer.stdout.startswith(LSP_BINARY_NAME + " "):
            return candidate
    return None


def require_lsp(lsp_path: Optional[str] = None) -> str:
    """Returns the mariadb-lsp binary, or raises where there is none."""
    binary = find_lsp(lsp_path)
    if binary is None:
        where = f" at `{lsp_path}`" if lsp_path else ""
        raise LspError(
            f"The MariaDB SQL language server's binary `{LSP_BINARY_NAME}` could not "
            f"be found{where}. It is installed with the MariaDB Shell and needed to "
            "read the schema objects of the development script."
        )
    return binary


def outline(binary: str, sql: str, source_dir: str, sql_mode: str = None) -> dict:
    """Outlines SQL text with the binary.

    The binary expands the SOURCE directives itself, relative to source_dir,
    so the positions of an included object are in its own file.

    Args:
        binary: The binary, from find_lsp().
        sql: The SQL text.
        source_dir: The folder relative SOURCE paths are taken from.
        sql_mode: The sql_mode at the start, comma-separated.

    Returns:
        The outline, as the binary writes it (docs/outline-format.md of the
        lsp-server).
    """
    command = [binary, "outline", "--source-dir", source_dir]
    if sql_mode:
        command += ["--sql-mode", sql_mode]
    command.append("-")
    try:
        answer = subprocess.run(
            command,
            input=sql,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_OUTLINE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise LspError(f"`{LSP_BINARY_NAME} outline` did not finish in {_OUTLINE_TIMEOUT} s.")
    except OSError as e:
        raise LspError(f"`{LSP_BINARY_NAME}` could not be run: {e}")
    if answer.returncode != 0:
        raise LspError(
            f"`{LSP_BINARY_NAME} outline` failed (exit {answer.returncode}): "
            f"{answer.stderr.strip()}"
        )
    try:
        return json.loads(answer.stdout)
    except json.JSONDecodeError as e:
        raise LspError(f"`{LSP_BINARY_NAME} outline` wrote no JSON: {e}")
