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
``install.ps1`` on Windows) downloads the release ``MARIADB_RESTD_TAG`` names,
or the newest one for this platform (a prerelease only when there is no stable
one), and unpacks it into a folder per version under
:func:`mcp_plugin.lib.restd.install_prefix`, linking it from ``~/.local/bin``.

A pinned release is installed with the script at its own tag, as the VS Code
extension installs the shell (its ``installScriptUrl``), so the script is the
one released with the daemon it installs; the newest release with the script
on main, as the shell's ai-plugins launchers do.

The script runs with its own stdin and its output captured: it must not read
the MCP server's stdio, which is the protocol channel.
"""

# cSpell:ignore restd MariaDB

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Optional

from mcp_plugin.lib.tool_registrar import tool_error

# Followed by the git ref - a release's tag, or main - and the script's name.
INSTALL_SCRIPT_BASE_URL = "https://github.com/mariadb-corporation/rest-daemon/raw/"

# A release is tens of megabytes.
DOWNLOAD_TIMEOUT = 60
INSTALL_TIMEOUT = 900

# What the install scripts print, read as the extension reads the shell's
# (its installer.ts): `==> ` marks progress, `install.sh: ` / `install.ps1: `
# starts the message they die with, and curl names its own failures.
_PROGRESS = re.compile(r"^\s*==>\s*(.+?)\s*$")
_INSTALLER_ERROR = re.compile(r"^\s*install\.(sh|ps1):\s*")
_CURL_ERROR = re.compile(r"^\s*curl:\s*\(\d+\)")
_GENERIC_ERROR = re.compile(
    r"\b(error|failed|could not|cannot|unable|denied|not found)\b", re.IGNORECASE
)
# A bare curl progress bar: nothing a log reader needs.
_PROGRESS_BAR = re.compile(r"[#O=\-\s]*(\d+(\.\d+)?%)?")
# What the log shows of the environment the script runs with.
_LOGGED_ENV = ("MARIADB_RESTD_TAG", "MARIADB_RESTD_PRERELEASE", "MARIADB_RESTD_SHA256SUMS")

# Taking turns, as the ai-plugins launchers take turns installing the shell
# (their step 4): this lock, in the install prefix, and install.sh's own.
TURN_LOCK = ".mcp-plugin.lock"
INSTALL_LOCK = ".install.lock"
# MARIADB_RESTD_LOCK_WAIT overrides it, as MARIADB_SHELL_LOCK_WAIT does the
# launchers'.
DEFAULT_LOCK_WAIT = 180


def script_url(tag: str = None) -> str:
    """Where the install script is: at the release's tag, else on main.

    Args:
        tag (str): The release, such as 'v26.10.1'; None for the newest.

    Returns:
        The URL of ``install.ps1`` on Windows, ``install.sh`` elsewhere.
    """
    name = "install.ps1" if os.name == "nt" else "install.sh"
    return f"{INSTALL_SCRIPT_BASE_URL}{tag or 'main'}/{name}"


def _download(url: str, pinned: bool) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as error:
        # A pinned install is the VS Code extension's, whose user passes no
        # restd_path: the URL and the cause are what helps there, as in the
        # extension's own error for the shell.
        advice = (
            "" if pinned
            else " Install mariadb-restd and put it on the PATH, or pass restd_path."
        )
        raise tool_error(
            f"The mariadb-restd installer could not be downloaded from "
            f"{url}: {error}.{advice}"
        ) from error


def clean_line(line: str) -> Optional[str]:
    """One line of installer output, reduced to what is worth keeping.

    As the extension's ``cleanInstallerLine``: curl draws its progress bar by
    rewriting one line with carriage returns, so only the last redraw is what
    a terminal would show, and a bare bar is dropped.

    Returns:
        The line to keep, or None if there is nothing in it.
    """
    shown = line.split("\r")[-1].strip()
    if not shown or _PROGRESS_BAR.fullmatch(shown):
        return None
    return shown


def summarize_failure(lines: list) -> Optional[str]:
    """The reason a failed installer run gave, as the extension's
    ``summarizeInstallerFailure`` picks it.

    The scripts end on one message of their own, which can run over a line
    or two of indented text; curl's error, if there was one, comes above it.

    Args:
        lines (list): What the installer printed that was not a progress
            marker, in order.

    Returns:
        A one line reason, or None if the output holds none.
    """
    curl = next(
        (line.strip() for line in reversed(lines) if _CURL_ERROR.match(line)), None
    )
    start = next(
        (i for i in range(len(lines) - 1, -1, -1) if _INSTALLER_ERROR.match(lines[i])),
        None,
    )
    if start is not None:
        message = _INSTALLER_ERROR.sub("", lines[start], count=1)
        # A message is wrapped onto indented lines until its sentence ends.
        for line in lines[start + 1:start + 3]:
            if re.search(r"[.?!:]$", message.strip()) or not re.match(r"\s", line):
                break
            message += f" {line.strip()}"
        message = " ".join(message.split())
        return f"{message} ({curl})" if curl else message

    return curl or next(
        (line.strip() for line in reversed(lines) if _GENERIC_ERROR.search(line)), None
    )


def _format_command(command: list, env: dict) -> str:
    """A command for the log, with the variables that pick the release."""
    shown = [f"{name}={env[name]}" for name in _LOGGED_ENV if env.get(name)]
    return " ".join(
        json.dumps(part) if re.search(r"\s", part) else part
        for part in shown + command
    )


def _text(output) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _read_output(output: str, log: list) -> list:
    """Logs what the installer printed, as the extension logs the shell's.

    Lines are split as the extension's LineReader splits them: at newlines,
    one trailing carriage return dropped, any other one a curl redraw.

    Returns:
        What it printed that was not a progress marker, for the reason it
        gives should it fail.
    """
    diagnostics = []
    for raw in output.split("\n"):
        raw = raw[:-1] if raw.endswith("\r") else raw
        line = clean_line(raw)
        if line is None:
            continue
        log.append(f"  installer: {line}")
        if not _PROGRESS.match(line):
            diagnostics.append(raw.split("\r")[-1])
    return diagnostics


def _run(command: list, env: dict, on_start=None) -> subprocess.CompletedProcess:
    """Runs the script with stdout and stderr in one stream, in order.

    Args:
        on_start (callable): Given the script's PID once it runs, for the
            lock it installs under.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    if on_start is not None:
        on_start(process.pid)
    try:
        output, _ = process.communicate(timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, INSTALL_TIMEOUT, output=output)
    return subprocess.CompletedProcess(command, process.returncode, output, None)


def _lock_wait(log) -> int:
    raw = os.environ.get("MARIADB_RESTD_LOCK_WAIT", "").strip()
    if not raw:
        return DEFAULT_LOCK_WAIT
    if raw.isdigit():
        return int(raw)
    log(
        f"MARIADB_RESTD_LOCK_WAIT is not a number of seconds: {raw}; "
        f"using {DEFAULT_LOCK_WAIT}"
    )
    return DEFAULT_LOCK_WAIT


def _lock_busy(path: str) -> bool:
    """Whether a lock folder belongs to a live install, as the launchers'
    lock_busy: one of the PIDs in its files runs, or it has none yet and is
    under a minute old (its owner is between creating it and writing one)."""
    from mcp_plugin.lib import restd

    if not os.path.isdir(path):
        return False
    pids = []
    try:
        for name in os.listdir(path):
            try:
                with open(os.path.join(path, name), encoding="utf-8") as file:
                    pids += [int(pid) for pid in re.findall(r"\d+", file.read())]
            except OSError:
                continue
    except OSError:
        return False
    if any(restd._is_alive(pid) for pid in pids):
        return True
    if pids:
        return False
    try:
        return time.time() - os.path.getmtime(path) < 60
    except OSError:
        return False


class InstallTurn:
    """An install's turn, taken as the ai-plugins launchers take theirs.

    Installers running at once into one prefix can each unpack the same
    package, so installs queue on a lock folder in the install prefix
    (TURN_LOCK) holding the PIDs of this server and of the installer it runs,
    and wait for an install.sh already running there (its INSTALL_LOCK) - one
    the VS Code extension or another agent's server started. The lock is stale
    once none of its PIDs runs. A server that stops waiting leaves the lock to
    its installer, which carries on, so the next one waits for that install
    instead of starting a second.

    POSIX only, as the launchers: on Windows install.ps1's own mutex
    serialises installs, and the .cmd launcher adds no lock either.
    """

    def __init__(self, prefix: str, log):
        self.prefix = prefix
        self.lock = os.path.join(prefix, TURN_LOCK)
        self.install_lock = os.path.join(prefix, INSTALL_LOCK)
        self.log = log
        self.held = False
        self.installer_pid = None

    def take(self, sleep=time.sleep) -> bool:
        """Waits until no other install runs, then holds the lock.

        Returns:
            False when MARIADB_RESTD_LOCK_WAIT ran out first; True otherwise,
            also where there is nothing to lock (Windows, or a prefix that
            cannot be made).
        """
        if os.name == "nt":
            return True
        wait = _lock_wait(self.log)
        started = time.monotonic()
        next_note = 0
        try:
            os.makedirs(self.prefix, exist_ok=True)
        except OSError:
            pass
        while True:
            if _lock_busy(self.install_lock):
                who = f"an install already running ({self.install_lock})"
            else:
                try:
                    os.mkdir(self.lock)
                except FileExistsError:
                    if _lock_busy(self.lock):
                        who = f"another install of mariadb-restd (lock {self.lock})"
                    else:
                        # Renamed, not removed, so only one waiter clears it.
                        self.log(f"clearing a lock left by an install that is gone: {self.lock}")
                        stale = f"{self.lock}.stale.{os.getpid()}"
                        try:
                            os.rename(self.lock, stale)
                            shutil.rmtree(stale, ignore_errors=True)
                        except OSError:
                            pass
                        continue
                except OSError:
                    self.log(f"cannot create the lock {self.lock}; installing without it")
                    return True
                else:
                    with open(os.path.join(self.lock, "pid"), "w", encoding="utf-8") as file:
                        file.write(f"{os.getpid()}\n")
                    self.held = True
                    return True
            waited = int(time.monotonic() - started)
            if waited >= wait:
                self.log(f"gave up after {waited}s waiting for {who}")
                return False
            if waited >= next_note:
                self.log(
                    f"waiting for {who} to finish (up to {wait}s, MARIADB_RESTD_LOCK_WAIT)"
                    if next_note == 0 else f"still waiting for {who} ({waited}s)"
                )
                next_note = waited + 10
            sleep(1)

    def add_installer(self, pid: int) -> None:
        """Puts the installer's PID into the lock, so it outlives this server."""
        self.installer_pid = pid
        if self.held:
            try:
                with open(os.path.join(self.lock, "pid"), "a", encoding="utf-8") as file:
                    file.write(f"{pid}\n")
            except OSError:
                pass

    def release(self) -> None:
        """Gives the lock back, unless its installer still runs."""
        from mcp_plugin.lib import restd

        if not self.held:
            return
        self.held = False
        if self.installer_pid is None or not restd._is_alive(self.installer_pid):
            shutil.rmtree(self.lock, ignore_errors=True)
            return
        # Left to the installer alone: unlike a launcher, which exec's the
        # server, this server goes on running, and its PID would keep the
        # lock busy for as long as it does.
        try:
            with open(os.path.join(self.lock, "pid"), "w", encoding="utf-8") as file:
                file.write(f"{self.installer_pid}\n")
        except OSError:
            pass


def install(tag: str = None, on_start=None) -> str:
    """Installs a mariadb-restd release.

    Prereleases are taken as the MariaDB Shell's launchers take them: a stable
    release is preferred, and a prerelease installed only when there is no
    stable one. ``MARIADB_RESTD_PRERELEASE`` decides it instead: truthy goes
    straight for a prerelease, ``0``/``false``/``no``/``off`` refuses one.

    Args:
        tag (str): The release to install, for example 'v26.10.1', with one
            run of the script from that tag. The newest one when None, with
            the script from main.
        on_start (callable): Given each installer run's PID as it starts.

    Returns:
        str: The install's log, as the extension logs the shell's: the
        script's URL, each run's command, every line it printed but curl's
        progress bars, and its exit code and duration.

    Raises:
        ToolError: If the script cannot be downloaded, or fails: with the
        reason it gave, as the extension words the shell's, and the log.
    """
    url = script_url(tag)
    name = f"mariadb-restd {tag.lstrip('vV')}" if tag else "the newest mariadb-restd release"
    script = _download(url, bool(tag))

    env = dict(os.environ)
    # The scripts read any non-empty value as "take prereleases", so it is
    # decided here, and set again only for a run that takes one.
    choice = env.pop("MARIADB_RESTD_PRERELEASE", "").strip().lower()
    # A committed copy of a release's SHA256SUMS (the Hermes launchers export
    # one, as they do the shell's) belongs to the tag it was set with: kept
    # only for that tag, since the scripts refuse one without a tag and a
    # different release's would fail every checksum.
    if not tag or os.environ.get("MARIADB_RESTD_TAG", "").strip() != tag:
        env.pop("MARIADB_RESTD_SHA256SUMS", None)
    if tag:
        env["MARIADB_RESTD_TAG"] = tag

    with tempfile.TemporaryDirectory() as temp_dir:
        if os.name == "nt":
            file_name = "install.ps1"
            # RemoteSigned, as the extension runs the shell's (its
            # buildInstallCommand): it lets a local script run, and a file
            # written here carries no mark of having come from the internet.
            # Bypass would do no more, and is what malware asks for.
            runner = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "RemoteSigned",
                "-File",
            ]
        else:
            file_name = "install.sh"
            # A POSIX sh script (`#!/bin/sh`), run as the extension runs the
            # shell's.
            runner = ["/bin/sh"]
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

        log = [f"Installing {name} with {url}."]
        for command, run_env in attempts:
            log.append(f"Running the installer: {_format_command(command, run_env)}")
            started = time.monotonic()
            try:
                completed = _run(command, run_env, on_start)
            except subprocess.TimeoutExpired as error:
                _read_output(_text(error.stdout), log)
                log.append(f"The installer was stopped after {INSTALL_TIMEOUT}s.")
                raise tool_error(
                    f"{name} could not be installed: the installer did not "
                    f"finish within {INSTALL_TIMEOUT} seconds.\n\n" + "\n".join(log)
                ) from error
            except OSError as error:
                raise tool_error(
                    f"The mariadb-restd installer could not be run "
                    f"({command[0]}): {error}"
                ) from error
            # stderr is merged into stdout; a test's stand-in may still set it.
            output = "\n".join(
                part for part in (
                    _text(completed.stdout), _text(getattr(completed, "stderr", None))
                ) if part
            )
            diagnostics = _read_output(output, log)
            seconds = time.monotonic() - started
            log.append(
                f"The installer exited with code {completed.returncode} "
                f"after {seconds:.1f}s."
            )
            if completed.returncode == 0:
                return "\n".join(log)

    reason = summarize_failure(diagnostics)
    code = completed.returncode
    raise tool_error(
        f"{name} could not be installed: "
        + (
            f"{reason} (the installer exited with code {code})."
            if reason else f"the installer exited with code {code}."
        )
        + "\n\n" + "\n".join(log)
    )
