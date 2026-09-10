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

"""Getting a MariaDB server of a requested version for a sandbox to run.

The sandbox tools deploy an instance with whatever ``mariadbd`` is on the PATH,
which is one version on any given machine. This module is what lets a client ask
for a *particular* version instead: it knows which versions are published, which
are already on this machine, and how to fetch one that is neither.

Three places are searched, in this order, and the order is the whole design:

1. **The PATH.** A server that is already installed system-wide is used as it
   is. Downloading a second copy of a version the machine already has would cost
   a few hundred megabytes to end up in the same place.
2. **The download directory** (:func:`mcp_plugin.lib.general.get_sandbox_server_path`).
   One directory per version, the version as its name, so versions sit side by
   side and nothing has to be uninstalled to try another.
3. **The published index** (:data:`INDEX_FILE_NAME`, shipped beside this
   module). The version is downloaded, its SHA-256 checked against the index,
   extracted into 2., and only then used.

The index is a static file rather than something fetched at run time. What a
given plugin version can install is then a property of that plugin version -
reproducible, reviewable in a diff, and unaffected by what a remote index says
today - and looking up a version costs no network at all. Publishing a new
server version means shipping a new plugin.

**The checksum is not decoration.** Everything here ends in running a downloaded
executable as a database server. The index pins a SHA-256 per package, the
download is rejected if it does not match, and a rejected download leaves
nothing behind.
"""

# cSpell:ignore mysqlsh MariaDB mariadbd mysqld sandboxes xattr LOCALAPPDATA
# cSpell:ignore glibc

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from typing import NamedTuple, Optional

import mysqlsh

from mcp_plugin.lib import general

# The published index of downloadable server packages, shipped beside this
# module. See the module docstring for why it is a file and not a URL.
INDEX_FILE_NAME = "sandbox_server_versions.json"

# The index layout this module understands. A file declaring anything else is
# refused rather than read optimistically: the failure is then one clear message
# about a plugin that is too old for its own data file, instead of a KeyError
# from somewhere in the middle of a download.
SUPPORTED_INDEX_VERSION = 1

# Seconds any single socket operation of a download may block for. This is NOT
# a budget for the whole transfer - the body is streamed, and a server package
# is a few hundred megabytes - it is how long a stalled connection is tolerated
# before the download is given up on.
DOWNLOAD_TIMEOUT = 120

# Prefix for the directories an install works in. Dot-prefixed so that a run
# interrupted half way leaves something that is visibly not a version, and so
# that :func:`installed_versions` can tell the two apart without a second
# record of what is real.
WORK_PREFIX = "."

# A version as a client may ask for it: 'major.minor' or 'major.minor.patch'.
# Anything else is refused up front, so a typo is answered by a message naming
# the two accepted shapes rather than by an empty list of matches.
_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")

# How a server binary reports its own version: 'mariadbd  Ver 12.3.2-MariaDB-log
# for osx10.21 on arm64'. Only the three numbers after 'Ver' are read; the
# suffix carries the build flavour, which says nothing about the version.
_BINARY_VERSION_PATTERN = re.compile(r"\bVer\s+(\d+)\.(\d+)\.(\d+)")

# Seconds to wait for a server binary to answer '--version'. Generous for what
# is a process start and a printed line, because the alternative to waiting is
# concluding a perfectly good server is not there.
_VERSION_TIMEOUT = 30

# The server binary, in the order it is looked for. MySQL's name is accepted
# because the shell's sandbox supports MySQL instances too and the sandbox tools
# do not care which vendor a binary is - but only MariaDB packages are
# published, so a 'mysqld' only ever comes from the PATH or from a directory
# somebody filled by hand.
_SERVER_BINARY_NAMES = ("mariadbd", "mysqld")

# Where inside an installation the binary is. A published package puts it in
# 'bin'; the empty string covers a directory somebody pointed at the bin folder
# itself. Anything else is found by walking the tree (see :func:`find_server_binary`).
_BINARY_SUBDIRECTORIES = ("bin", "")


class ResolvedServer(NamedTuple):
    """Which server a requested version resolved to, and where it came from.

    Attributes:
        version (str): The full ``major.minor.patch`` version that will run,
            which for a ``major.minor`` request is the patch release actually
            found - not what was asked for.
        mariadbd_path (str): The binary to deploy with, or None when the version
            is the one on the PATH and the shell should find it itself.
        source (str): One of :data:`SOURCE_PATH`, :data:`SOURCE_INSTALLED` or
            :data:`SOURCE_DOWNLOADED`.
    """

    version: str
    mariadbd_path: Optional[str]
    source: str


# The three places a server is found, in search order. Reported back to the
# client because "11.8.9 is running" and "11.8.9 was just downloaded and is
# running" are different answers to the same request, and only the second one
# explains where the minutes went.
SOURCE_PATH = "path"
SOURCE_INSTALLED = "installed"
SOURCE_DOWNLOADED = "downloaded"


def index_path() -> str:
    """Returns the path of the published index shipped with this plugin.

    Returns:
        The absolute path of :data:`INDEX_FILE_NAME`.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), INDEX_FILE_NAME)


def load_index() -> dict:
    """Reads and validates the published index.

    Read on every call rather than cached: it is a small local file, the reads
    are rare (a tool call, not a request loop), and a cache would have to be
    invalidated by a plugin upgrade that replaced the file underneath a running
    server.

    Returns:
        The parsed index.

    Raises:
        mysqlsh.Error: The file is missing, is not valid JSON, or declares an
            index version this plugin does not understand.
    """
    path = index_path()
    try:
        with open(path, "r", encoding="utf-8") as index_file:
            index = json.load(index_file)
    except OSError as exc:
        raise mysqlsh.Error(
            f"The list of downloadable MariaDB server versions ({path}) could "
            f"not be read: {exc}"
        ) from exc
    except ValueError as exc:
        raise mysqlsh.Error(
            f"The list of downloadable MariaDB server versions ({path}) is not "
            f"valid JSON: {exc}"
        ) from exc

    declared = index.get("sandboxServerIndexVersion")
    if declared != SUPPORTED_INDEX_VERSION:
        raise mysqlsh.Error(
            f"The list of downloadable MariaDB server versions ({path}) is "
            f"version {declared!r}, which this plugin does not understand (it "
            f"reads version {SUPPORTED_INDEX_VERSION}). Update the plugin."
        )

    return index


def platform_key() -> Optional[str]:
    """Returns the key the index names packages for this machine under.

    The index uses the operating system and architecture names Node.js reports
    (``darwin-arm64``, ``linux-x64``, ``win32-x64`` and so on) because it is
    shared with tooling that speaks those. Python spells both differently and
    spells the architecture differently again per platform - ``arm64`` on macOS
    against ``aarch64`` on Linux, ``AMD64`` on Windows against ``x86_64``
    everywhere else - so every spelling is mapped here rather than at each call.

    Returns:
        The platform key, or None when no packages could exist for this machine
        (a platform or an architecture the index has no name for).
    """
    system = {"darwin": "darwin", "linux": "linux", "win32": "win32"}.get(sys.platform)
    architecture = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
        "x64": "x64",
    }.get(platform.machine().lower())

    if system is None or architecture is None:
        return None

    return f"{system}-{architecture}"


def require_platform_key() -> str:
    """Returns :func:`platform_key`, refusing a machine nothing is published for.

    Returns:
        The platform key.

    Raises:
        mysqlsh.Error: No server packages are published for this machine.
    """
    key = platform_key()
    if key is None:
        raise mysqlsh.Error(
            "No MariaDB server packages are published for this platform "
            f"({sys.platform}/{platform.machine()}). Install a server manually "
            "and leave the version unset to deploy with the one on the PATH."
        )

    return key


def _series_versions(series: dict) -> dict:
    """Returns one index entry's packages, keyed by full version.

    The index nests a series' releases as a list of single-key objects
    (``"patches": [{"9": [...]}]``) so that the file stays diff-friendly as
    releases are added. That shape is awkward to look anything up in, so it is
    flattened once, here, and nowhere else has to know about it.

    Args:
        series (dict): One ``serverVersions`` entry.

    Returns:
        A dict of ``major.minor.patch`` to the list of that release's packages.
    """
    major = series.get("major")
    minor = series.get("minor")
    flattened = {}
    for patch_group in series.get("patches", []):
        for patch, packages in patch_group.items():
            flattened[f"{major}.{minor}.{patch}"] = packages

    return flattened


def _package_of(packages, key: str) -> Optional[dict]:
    """Returns the package built for the given platform key, if there is one."""
    for package in packages:
        if package.get("os") == key:
            return package

    return None


def available_versions(series: str = None) -> list:
    """Returns the server versions that can be downloaded for this machine.

    Only versions with a package built for this platform are listed: a release
    published for Linux alone is not something this machine can be given, and
    offering it would only produce a failure one call later.

    Args:
        series (str): A ``major.minor`` series to list every patch release of.
            Omit it to list one version per series - the latest patch release of
            each, which is what the index's ``latestPatch`` names.

    Returns:
        The versions as ``major.minor.patch`` strings, sorted oldest first.

    Raises:
        mysqlsh.Error: No packages are published for this machine, ``series`` is
            not a ``major.minor`` version, or it names a series the index does
            not have.
    """
    key = require_platform_key()
    index = load_index()

    if series is None:
        latest = []
        for entry in index.get("serverVersions", []):
            version = f"{entry.get('major')}.{entry.get('minor')}.{entry.get('latestPatch')}"
            packages = _series_versions(entry).get(version, [])
            if _package_of(packages, key) is not None:
                latest.append(version)

        return sorted(latest, key=_sort_key)

    major, minor, patch = parse_version(series)
    if patch is not None:
        raise mysqlsh.Error(
            f"'{series}' is a full version. To list the patch releases of a "
            f"series, pass its major.minor version ('{major}.{minor}')."
        )

    entry = _series_entry(index, major, minor)
    if entry is None:
        raise mysqlsh.Error(
            f"MariaDB {major}.{minor} is not among the downloadable versions. "
            f"Available: {', '.join(available_versions()) or 'none'}."
        )

    return sorted(
        (
            version
            for version, packages in _series_versions(entry).items()
            if _package_of(packages, key) is not None
        ),
        key=_sort_key,
    )


def _series_entry(index: dict, major: int, minor: int) -> Optional[dict]:
    """Returns the index entry for one major.minor series, if it has one."""
    for entry in index.get("serverVersions", []):
        if str(entry.get("major")) == str(major) and str(entry.get("minor")) == str(minor):
            return entry

    return None


def _sort_key(version: str) -> tuple:
    """Returns a version's numeric sort key, so 11.8.10 sorts after 11.8.9."""
    return tuple(int(part) for part in version.split("."))


def parse_version(version: str) -> tuple:
    """Parses a requested version into its numbers.

    Args:
        version (str): ``major.minor`` or ``major.minor.patch``, optionally with
            a leading ``v`` - a version copied off a release page carries one,
            and refusing it would be a riddle rather than a rule.

    Returns:
        A ``(major, minor, patch)`` tuple of ints, ``patch`` being None when
        only a series was given.

    Raises:
        mysqlsh.Error: The version is not one of those two shapes.
    """
    text = (version or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]

    match = _VERSION_PATTERN.match(text)
    if match is None:
        raise mysqlsh.Error(
            f"'{version}' is not a MariaDB server version. Give it as "
            "'major.minor' (for example '11.8', which uses the latest patch "
            "release) or as 'major.minor.patch' (for example '11.8.9')."
        )

    major, minor, patch = match.groups()
    return int(major), int(minor), None if patch is None else int(patch)


def matches(version: str, major: int, minor: int, patch: Optional[int]) -> bool:
    """Returns whether a full version satisfies a request.

    A ``major.minor`` request is satisfied by any patch release of that series;
    a full request only by that exact release.

    Args:
        version (str): A full ``major.minor.patch`` version.
        major (int): The requested major version.
        minor (int): The requested minor version.
        patch (int): The requested patch release, or None for any.

    Returns:
        True when the version satisfies the request.
    """
    try:
        parts = _sort_key(version)
    except ValueError:
        # Not a version at all - a stray directory under the server root.
        return False

    if len(parts) != 3 or parts[0] != major or parts[1] != minor:
        return False

    return patch is None or parts[2] == patch


def find_server_binary(install_dir: str) -> Optional[str]:
    """Returns the server binary inside an installation, if there is one.

    Args:
        install_dir (str): The directory to look in.

    Returns:
        The absolute path of the binary, or None when the directory holds none.
    """
    suffix = ".exe" if os.name == "nt" else ""
    names = [name + suffix for name in _SERVER_BINARY_NAMES]

    for subdirectory in _BINARY_SUBDIRECTORIES:
        for name in names:
            candidate = os.path.join(install_dir, subdirectory, name)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

    # A layout the published packages do not have, but a directory filled by
    # hand might. Worth a walk rather than a refusal: the alternative is telling
    # somebody their perfectly good installation is empty.
    for current_dir, _, files in os.walk(install_dir):
        for name in names:
            if name in files:
                return os.path.abspath(os.path.join(current_dir, name))

    return None


def installed_versions() -> list:
    """Returns the server versions installed under the server root.

    Read off the directory names, which are the only record of what a copy is
    (see :func:`mcp_plugin.lib.general.get_sandbox_server_path`). A directory an
    install is working in is dot-prefixed and is not a version, and a directory
    with no server binary in it is a leftover rather than an installation.

    Returns:
        The installed versions, sorted oldest first; empty when none are.
    """
    root = general.get_sandbox_server_root()
    try:
        entries = os.listdir(root)
    except OSError:
        # The root does not exist, so nothing is installed.
        return []

    found = []
    for entry in entries:
        if entry.startswith(WORK_PREFIX) or not _VERSION_PATTERN.match(entry):
            continue
        if find_server_binary(os.path.join(root, entry)) is not None:
            found.append(entry)

    return sorted(found, key=_sort_key)


def path_server_version() -> Optional[str]:
    """Returns the version of the server binary on the PATH, if there is one.

    Returns:
        The version as ``major.minor.patch``, or None when no server binary is
        on the PATH or it does not report a version this understands.
    """
    for name in _SERVER_BINARY_NAMES:
        binary = shutil.which(name)
        if binary is None:
            continue

        try:
            reported = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
                check=False,
            ).stdout
        except OSError:
            # On the PATH but not runnable - the same as not being there.
            continue
        except subprocess.TimeoutExpired:
            continue

        match = _BINARY_VERSION_PATTERN.search(reported or "")
        if match is not None:
            return ".".join(match.groups())

    return None


def _download_package(url: str, archive_path: str, expected_sha256: str) -> None:
    """Downloads a package and verifies its checksum before letting it be used.

    The digest is computed as the body streams past, so the package is never
    read twice and never held in memory. A package whose digest does not match
    the index is deleted rather than kept: what it actually is cannot be known,
    and leaving it on disk invites a later run from reaching for it.

    Args:
        url (str): The package URL from the index.
        archive_path (str): The file to write the package to.
        expected_sha256 (str): The digest the index pins for this package.

    Returns:
        None

    Raises:
        mysqlsh.Error: The digest of what arrived is not the pinned one.
    """
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        with open(archive_path, "wb") as archive_file:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                archive_file.write(block)

    actual = digest.hexdigest()
    if actual.lower() != (expected_sha256 or "").lower():
        try:
            os.remove(archive_path)
        except OSError:
            pass
        raise mysqlsh.Error(
            f"The MariaDB server package downloaded from {url} does not match "
            f"the checksum published for it (expected {expected_sha256}, got "
            f"{actual}). It was discarded and nothing was installed."
        )


def _extract_package(archive_path: str, unpack_dir: str) -> str:
    """Extracts a downloaded package and returns the directory to install.

    Extracted with tarfile's ``data`` filter, which is what refuses an entry
    naming an absolute path, a ``..`` component or a link pointing out of the
    tree - a package is remote input and an archive is perfectly capable of
    asking to be written somewhere else. The filter also strips setuid and
    setgid bits while leaving the owner's execute bit alone, so the server
    binary comes out runnable and comes out unprivileged.

    A package wraps everything in one directory named after the build
    (``mariadb-11.8.9-macos26-arm-64bit-sandbox/``). That name is a property of
    the build and not of the version, so it is dropped and the directory *below*
    it becomes the installation - which is what makes ``<version>/bin/mariadbd``
    true of every platform's package.

    Args:
        archive_path (str): The downloaded package.
        unpack_dir (str): An empty directory to unpack into.

    Returns:
        The directory to move into place as the installation.
    """
    os.makedirs(unpack_dir, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        archive.extractall(path=unpack_dir, filter="data")

    entries = os.listdir(unpack_dir)
    if len(entries) == 1:
        wrapped = os.path.join(unpack_dir, entries[0])
        if os.path.isdir(wrapped):
            return wrapped

    return unpack_dir


def clear_quarantine_flags(target_dir: str) -> None:
    """Clears the extended attributes that stop a downloaded binary from running.

    macOS only, and best-effort. Files that arrive over the network can carry
    ``com.apple.quarantine``, which makes Gatekeeper refuse to execute them with
    an error that names none of this - so ``xattr -cr`` is run over the finished
    installation before it is ever used. Nothing needs doing on Linux, and on
    Windows the mark of the web rides on an alternate data stream that a Python
    extraction never creates.

    A failure here is reported and not raised. It is not certain the attributes
    were there at all, the installation is otherwise complete, and refusing to
    deploy over it would turn a maybe-problem into a definite one.

    Args:
        target_dir (str): The installation to clear.

    Returns:
        None
    """
    if sys.platform != "darwin":
        return

    xattr = shutil.which("xattr")
    if xattr is None:
        general.log_event(
            "xattr was not found, so the quarantine attributes of the "
            f"downloaded server in {target_dir} were left as they are. If it "
            "refuses to start, run: xattr -cr " + target_dir
        )
        return

    result = subprocess.run(
        [xattr, "-cr", target_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        general.log_event(
            f"Clearing the extended attributes of {target_dir} failed "
            f"({(result.stderr or '').strip()}). If the server refuses to "
            "start, run: xattr -cr " + target_dir
        )


def install(version: str) -> str:
    """Downloads and installs one published server version.

    The new copy is built in a staging directory next to the target and only
    swapped in once it is complete and has been checked, so a download that
    fails part way through - or a package that turns out not to contain a server
    at all - leaves whatever was installed exactly as it was.

    Args:
        version (str): The full ``major.minor.patch`` version to install.

    Returns:
        The directory the version was installed in.

    Raises:
        mysqlsh.Error: No package is published for this version and platform,
            the download does not match its checksum, or what was extracted
            holds no server binary.
    """
    key = require_platform_key()
    index = load_index()
    major, minor, patch = parse_version(version)
    if patch is None:
        raise mysqlsh.Error(f"'{version}' is a series, not a version to install.")

    entry = _series_entry(index, major, minor)
    packages = _series_versions(entry).get(version, []) if entry is not None else []
    package = _package_of(packages, key)
    if package is None:
        raise mysqlsh.Error(
            f"MariaDB {version} is not published for {key}. Available for this "
            f"platform: {', '.join(available_versions()) or 'none'}."
        )

    root = general.get_sandbox_server_root()
    target_dir = general.get_sandbox_server_path(version)
    staging_dir = os.path.join(root, WORK_PREFIX + version + ".new")
    previous_dir = os.path.join(root, WORK_PREFIX + version + ".old")

    # The root is this function's to create: nothing else has a reason to.
    os.makedirs(root, exist_ok=True)

    # Anything left behind by an interrupted earlier run.
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(previous_dir, ignore_errors=True)

    general.log_event(
        f"downloading MariaDB {version} for {key} from {package.get('url')}"
    )

    # Downloaded and unpacked inside the server root rather than in the system
    # temp directory: it is on the same filesystem as the target, so the
    # finished copy is moved into place instead of copied across devices - and a
    # few hundred megabytes do not land on a temp filesystem that may be a
    # ramdisk or far too small.
    with tempfile.TemporaryDirectory(
        dir=root, prefix=WORK_PREFIX + "download-"
    ) as work_dir:
        archive_path = os.path.join(work_dir, "server.tar.gz")
        try:
            _download_package(
                package.get("url"), archive_path, package.get("sha256sum")
            )
            extracted = _extract_package(archive_path, os.path.join(work_dir, "unpack"))
            if find_server_binary(extracted) is None:
                raise mysqlsh.Error(
                    f"The MariaDB {version} package contains no server binary "
                    f"({' or '.join(_SERVER_BINARY_NAMES)}). Nothing was installed."
                )
            # Moved out of the temp directory before it is cleaned up; the swap
            # below then only renames within the root.
            os.rename(extracted, staging_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    clear_quarantine_flags(staging_dir)

    had_previous = os.path.isdir(target_dir)
    if had_previous:
        os.rename(target_dir, previous_dir)
    try:
        os.rename(staging_dir, target_dir)
    except OSError:
        # Put the copy that was working back before reporting the failure.
        if had_previous:
            os.rename(previous_dir, target_dir)
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    shutil.rmtree(previous_dir, ignore_errors=True)

    general.log_event(f"installed MariaDB {version} in {target_dir}")

    return target_dir


def resolve(version: str) -> ResolvedServer:
    """Finds a server of the requested version, downloading it if it takes that.

    The three places are searched in the order the module docstring gives.
    Within the installed copies and within the published index a ``major.minor``
    request takes the newest patch release, so asking for a series gets the best
    of what is there rather than the first that happened to be listed.

    Args:
        version (str): ``major.minor`` or ``major.minor.patch``.

    Returns:
        The :class:`ResolvedServer` naming the version, the binary to deploy
        with (None when it is the one on the PATH) and where it came from.

    Raises:
        mysqlsh.Error: The version is malformed, or it is neither on this
            machine nor published for this platform.
    """
    major, minor, patch = parse_version(version)

    # 1. Already installed system-wide.
    on_path = path_server_version()
    if on_path is not None and matches(on_path, major, minor, patch):
        return ResolvedServer(on_path, None, SOURCE_PATH)

    # 2. Downloaded by an earlier call. Newest matching patch release wins.
    matching = [
        installed
        for installed in installed_versions()
        if matches(installed, major, minor, patch)
    ]
    if matching:
        found = matching[-1]
        install_dir = general.get_sandbox_server_path(found)
        return ResolvedServer(
            found, find_server_binary(install_dir), SOURCE_INSTALLED
        )

    # 3. Published, so fetch it.
    if patch is not None:
        wanted = f"{major}.{minor}.{patch}"
    else:
        series = available_versions(f"{major}.{minor}")
        if not series:
            raise mysqlsh.Error(
                f"MariaDB {major}.{minor} is not published for "
                f"{require_platform_key()}."
            )
        wanted = series[-1]

    install_dir = install(wanted)
    binary = find_server_binary(install_dir)
    if binary is None:
        # install() already checked this on the staging copy, so reaching here
        # means the installation was disturbed between the check and the swap.
        raise mysqlsh.Error(
            f"The installed MariaDB {wanted} in {install_dir} holds no server "
            "binary."
        )

    return ResolvedServer(wanted, binary, SOURCE_DOWNLOADED)
