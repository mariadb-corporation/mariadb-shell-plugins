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

"""Installs mariadb-restd with the rest-daemon repository's install script.

The same scheme as the MariaDB Shell's install: ``install.sh`` (or
``install.ps1`` on Windows) from the repository's main branch downloads the
release ``MARIADB_RESTD_TAG`` names, or the newest one for this platform (a
prerelease only when there is no stable one), and unpacks it into a folder per version under
:func:`mcp_plugin.lib.restd.install_prefix`, linking it from ``~/.local/bin``.

The script runs with its own stdin and its output captured: it must not read
the MCP server's stdio, which is the protocol channel.
"""

# cSpell:ignore restd MariaDB

import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

from mcp_plugin.lib.tool_registrar import tool_error

INSTALL_SCRIPT_URL_POSIX = (
    "https://github.com/mariadb-corporation/rest-daemon/raw/main/install.sh"
)
INSTALL_SCRIPT_URL_WINDOWS = (
    "https://github.com/mariadb-corporation/rest-daemon/raw/main/install.ps1"
)

# A release is tens of megabytes.
DOWNLOAD_TIMEOUT = 60
INSTALL_TIMEOUT = 900

# How the install scripts start the message they end on when they fail, as
# the shell's do.
_INSTALLER_ERROR = re.compile(r"^\s*install\.(sh|ps1):\s*")

_TAIL_LINES = 10


def _script_url() -> str:
    return INSTALL_SCRIPT_URL_WINDOWS if os.name == "nt" else INSTALL_SCRIPT_URL_POSIX


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as error:
        raise tool_error(
            f"The mariadb-restd install script could not be downloaded from "
            f"{url}: {error}. Install mariadb-restd and put it on the PATH, "
            "or pass restd_path."
        ) from error


def _failure(output: str) -> str:
    """The part of a failed install's output worth an error message."""
    lines = [line for line in output.splitlines() if line.strip()]
    own = [line for line in lines if _INSTALLER_ERROR.match(line)]
    return "\n".join(own[-3:] if own else lines[-_TAIL_LINES:])


def _run(command: list, env: dict) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise tool_error(
            f"The mariadb-restd install did not finish within "
            f"{INSTALL_TIMEOUT} seconds."
        ) from error
    except OSError as error:
        raise tool_error(
            f"The mariadb-restd install script could not be run: {error}"
        ) from error


def _output(completed: subprocess.CompletedProcess) -> str:
    return "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )


def install(tag: str = None) -> str:
    """Installs a mariadb-restd release.

    Prereleases are taken as the MariaDB Shell's launchers take them: a stable
    release is preferred, and a prerelease installed only when there is no
    stable one. ``MARIADB_RESTD_PRERELEASE`` decides it instead: truthy goes
    straight for a prerelease, ``0``/``false``/``no``/``off`` refuses one.

    Args:
        tag (str): The release to install, for example 'v26.10.1', with one
            run of the script; the script still comes from main, since no
            release carries its own yet. The newest one when None.

    Returns:
        str: What the install script printed.

    Raises:
        ToolError: If the script cannot be downloaded, or fails.
    """
    url = _script_url()
    script = _download(url)

    env = dict(os.environ)
    # The scripts read any non-empty value as "take prereleases", so it is
    # decided here, and set again only for a run that takes one.
    choice = env.pop("MARIADB_RESTD_PRERELEASE", "").strip().lower()
    if tag:
        env["MARIADB_RESTD_TAG"] = tag

    with tempfile.TemporaryDirectory() as temp_dir:
        if os.name == "nt":
            file_name = "install.ps1"
            runner = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]
        else:
            file_name = "install.sh"
            runner = ["/bin/bash"]
        path = os.path.join(temp_dir, file_name)
        with open(path, "wb") as file:
            file.write(script)
        stable = (runner + [path], env)
        # As the shell's launchers ask for one: --pre-release to install.sh,
        # MARIADB_RESTD_PRERELEASE=1 to install.ps1, which the .cmd launcher
        # reaches through the environment.
        if os.name == "nt":
            prerelease = (runner + [path], dict(env, MARIADB_RESTD_PRERELEASE="1"))
        else:
            prerelease = (runner + [path, "--pre-release"], env)

        # A pinned tag already names the release.
        if tag or choice in ("0", "false", "no", "off"):
            attempts = [stable]
        elif choice:
            attempts = [prerelease]
        else:
            # Without it the scripts take /releases/latest, which skips
            # prereleases, so while every release is one the first run fails.
            attempts = [stable, prerelease]

        outputs = []
        for command, run_env in attempts:
            completed = _run(command, run_env)
            outputs.append(_output(completed))
            if completed.returncode == 0:
                return "\n".join(part for part in outputs if part)

    raise tool_error(
        f"The mariadb-restd install failed (exit code "
        f"{completed.returncode}).\n{_failure(outputs[-1])}"
    )
