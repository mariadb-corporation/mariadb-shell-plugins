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

"""Tests for finding, downloading and installing a MariaDB server for a sandbox.

Covers lib/sandbox_servers.py and the two path helpers it installs through
(``get_sandbox_server_root`` / ``get_sandbox_server_path`` in lib/general.py).

**Nothing here reaches the network.** ``urlopen`` is replaced with a stand-in
serving a package built locally, and the index is replaced with one whose
checksums are of that package - so the tests neither depend on what a given
release contains nor on it still being downloadable. The package is given the
shape a real one has, which was read off the published
``mariadb-11.8.9-macos26-arm-64bit-sandbox.tar.gz``: everything below one
top-level directory named after the build, with the server in its ``bin``
directory and the executable bit set.

The one test that really downloads and really deploys is marked ``e2e`` and runs
only with ``--e2e`` (``run_tests.py --e2e``).
"""

# cSpell:ignore mysqlsh MariaDB mariadbd mysqld xattr LOCALAPPDATA aarch64

import asyncio
import hashlib
import io
import os
import stat
import tarfile
from types import SimpleNamespace

import pytest

import mysqlsh

from mcp_plugin.lib import general, sandbox_functions, sandbox_servers
import mcp_plugin.tests.unit.helpers as helpers

# The version the built-in-memory package pretends to be. Deliberately NOT one
# of the versions the shipped index carries: a test that passed only because the
# real index happens to list the same number would be proving nothing.
FAKE_VERSION = "10.6.1"
FAKE_SERIES = "10.6"

# A second release of the same series, so "give me 10.6" has something to choose
# between - and 10 sorts after 9 only if the sort is numeric, which is the point
# of the two patch numbers being what they are.
FAKE_OLDER_VERSION = "10.6.0"


def _package_bytes(entries, root_name="mariadb-10.6.1-linux-sandbox"):
    """Builds a .tar.gz shaped like a published server package.

    Args:
        entries: Tuples of the path below the package's top-level directory,
            its contents and its unix mode.
        root_name: The top-level directory the package wraps everything in.
            An empty string writes the entries at the root instead, which is
            the shape whose wrapper must NOT be stripped.

    Returns:
        The archive as bytes.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, contents, mode in entries:
            payload = contents.encode()
            info = tarfile.TarInfo(f"{root_name}/{name}" if root_name else name)
            info.size = len(payload)
            info.mode = mode
            archive.addfile(info, io.BytesIO(payload))

    return buffer.getvalue()


# What a package holds, reduced to the parts the install actually looks at: the
# server binary in bin/, marked executable, plus a couple of the data files that
# have to survive the extraction alongside it.
_PACKAGE_ENTRIES = (
    ("bin/mariadbd", "#!/bin/sh\necho ' Ver 10.6.1-MariaDB for linux'\n", 0o755),
    ("bin/mariadb", "#!/bin/sh\n", 0o755),
    ("share/mariadb_system_tables.sql", "SELECT 1;\n", 0o644),
)


@pytest.fixture
def platform_key():
    """The platform key of the machine the suite is running on.

    Everything the index answers is filtered by it, so a stub index has to name
    it. A machine no packages could exist for cannot run these tests at all.
    """
    key = sandbox_servers.platform_key()
    if key is None:
        pytest.skip("no MariaDB server packages are published for this platform")

    return key


@pytest.fixture
def server_root(tmp_path, monkeypatch):
    """Points the server root at a temp directory for the test.

    Both helpers are replaced, not just the root: the real
    ``get_sandbox_server_path`` derives its answer from the real root, so
    redirecting one without the other would install into a temp directory and
    then look for it in the user's home. The two real helpers are tested on
    their own in ``test_server_root_*``.

    Yields:
        The absolute path of the stand-in server root.
    """
    root = tmp_path / "servers"
    monkeypatch.setattr(general, "get_sandbox_server_root", lambda: str(root))
    monkeypatch.setattr(
        general,
        "get_sandbox_server_path",
        lambda version: os.path.join(str(root), version),
    )
    yield str(root)


def _stub_index(monkeypatch, platform_key, packages):
    """Replaces the shipped index with one describing the given packages.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        platform_key: The key to publish the packages under.
        packages: A dict of full version to the package's bytes. The checksum
            in the index is computed from those bytes, so a test that wants a
            mismatch corrupts what the download serves, not what the index says.

    Returns:
        The index dict that was installed.
    """
    by_series = {}
    for version, payload in packages.items():
        major, minor, patch = version.split(".")
        series = by_series.setdefault(
            (major, minor),
            {"major": int(major), "minor": int(minor), "latestPatch": int(patch),
             "patches": []},
        )
        series["latestPatch"] = max(series["latestPatch"], int(patch))
        series["patches"].append(
            {
                patch: [
                    {
                        "os": platform_key,
                        "url": f"https://example.invalid/mariadb-{version}.tar.gz",
                        "sha256sum": hashlib.sha256(payload).hexdigest(),
                    }
                ]
            }
        )

    index = {
        "sandboxServerIndexVersion": sandbox_servers.SUPPORTED_INDEX_VERSION,
        "serverVersions": list(by_series.values()),
    }
    monkeypatch.setattr(sandbox_servers, "load_index", lambda: index)
    return index


class _FakeResponse:
    """The little of a urlopen response ``_download_package`` reads."""

    def __init__(self, payload):
        self._stream = io.BytesIO(payload)

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _stub_download(monkeypatch, payloads):
    """Serves the given bytes for a download instead of reaching the network.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        payloads: A dict of URL to the bytes to serve. A URL that is not in it
            raises, so a test cannot accidentally pass by fetching nothing.

    Returns:
        The list every requested URL is appended to.
    """
    requested = []

    def fake_urlopen(url, timeout=None):
        requested.append(url)
        if url not in payloads:
            raise AssertionError(f"unexpected download of {url}")
        return _FakeResponse(payloads[url])

    monkeypatch.setattr(sandbox_servers.urllib.request, "urlopen", fake_urlopen)
    return requested


def _url_of(version):
    """Returns the URL ``_stub_index`` publishes a version under."""
    return f"https://example.invalid/mariadb-{version}.tar.gz"


# --------------------------------------------------------------------------
# The shipped index
# --------------------------------------------------------------------------


def test_shipped_index_is_readable_and_current():
    """The index shipped with the plugin parses and declares a version we read."""
    index = sandbox_servers.load_index()

    assert index["sandboxServerIndexVersion"] == sandbox_servers.SUPPORTED_INDEX_VERSION
    assert index["serverVersions"], "the shipped index lists no server versions"


def test_shipped_index_publishes_every_platform_key():
    """Every package in the shipped index is named for a platform key we map to.

    A package published under a key :func:`platform_key` never produces is a
    package nobody can install - and the mismatch would show up as "not
    published for your platform" on the machine it was built for.
    """
    known = {
        f"{system}-{architecture}"
        for system in ("darwin", "linux", "win32")
        for architecture in ("arm64", "x64")
    }

    for series in sandbox_servers.load_index()["serverVersions"]:
        for version, packages in sandbox_servers._series_versions(series).items():
            for package in packages:
                assert package["os"] in known, f"{version}: unknown os {package['os']}"
                assert package["url"].endswith(".tar.gz")
                assert len(package["sha256sum"]) == 64


def test_shipped_index_latest_patch_is_the_newest_one():
    """``latestPatch`` names a release the series actually carries, the newest."""
    for series in sandbox_servers.load_index()["serverVersions"]:
        versions = sandbox_servers._series_versions(series)
        latest = f"{series['major']}.{series['minor']}.{series['latestPatch']}"

        assert latest in versions
        assert latest == sorted(versions, key=sandbox_servers._sort_key)[-1]


def test_load_index_refuses_an_index_version_it_does_not_read(tmp_path, monkeypatch):
    """An index from a newer plugin is refused with one clear message."""
    index_file = tmp_path / "sandbox_server_versions.json"
    index_file.write_text('{"sandboxServerIndexVersion": 99, "serverVersions": []}')
    monkeypatch.setattr(sandbox_servers, "index_path", lambda: str(index_file))

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.load_index()

    assert "does not understand" in str(failure.value)


def test_load_index_reports_a_missing_file(tmp_path, monkeypatch):
    """A missing index is reported, not raised as a bare OSError."""
    monkeypatch.setattr(
        sandbox_servers, "index_path", lambda: str(tmp_path / "gone.json")
    )

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.load_index()

    assert "could not be read" in str(failure.value)


def test_load_index_reports_invalid_json(tmp_path, monkeypatch):
    """A corrupted index is reported as such."""
    index_file = tmp_path / "sandbox_server_versions.json"
    index_file.write_text("{not json")
    monkeypatch.setattr(sandbox_servers, "index_path", lambda: str(index_file))

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.load_index()

    assert "not valid JSON" in str(failure.value)


# --------------------------------------------------------------------------
# Platform keys
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system, machine, expected",
    [
        ("darwin", "arm64", "darwin-arm64"),
        ("darwin", "x86_64", "darwin-x64"),
        ("linux", "aarch64", "linux-arm64"),
        ("linux", "x86_64", "linux-x64"),
        ("win32", "ARM64", "win32-arm64"),
        ("win32", "AMD64", "win32-x64"),
        # Every platform the index has no name for, and every architecture.
        ("freebsd13", "x86_64", None),
        ("linux", "s390x", None),
    ],
)
def test_platform_key_maps_every_spelling(monkeypatch, system, machine, expected):
    """Python's platform and architecture names map onto the index's keys."""
    monkeypatch.setattr(sandbox_servers.sys, "platform", system)
    monkeypatch.setattr(sandbox_servers.platform, "machine", lambda: machine)

    assert sandbox_servers.platform_key() == expected


def test_require_platform_key_refuses_a_platform_with_no_packages(monkeypatch):
    """A machine nothing is published for is told so, and told what to do."""
    monkeypatch.setattr(sandbox_servers, "platform_key", lambda: None)

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.require_platform_key()

    assert "on the PATH" in str(failure.value)


# --------------------------------------------------------------------------
# Listing what is available
# --------------------------------------------------------------------------


def test_available_versions_lists_the_latest_patch_of_each_series(
    monkeypatch, platform_key
):
    """With no series, one version per series: its newest patch release."""
    _stub_index(
        monkeypatch,
        platform_key,
        {
            FAKE_OLDER_VERSION: b"old",
            FAKE_VERSION: b"new",
            "11.4.2": b"other",
        },
    )

    assert sandbox_servers.available_versions() == [FAKE_VERSION, "11.4.2"]


def test_available_versions_lists_every_patch_of_one_series(monkeypatch, platform_key):
    """With a series, every patch release of it, oldest first."""
    _stub_index(
        monkeypatch,
        platform_key,
        {FAKE_VERSION: b"new", FAKE_OLDER_VERSION: b"old", "11.4.2": b"other"},
    )

    assert sandbox_servers.available_versions(FAKE_SERIES) == [
        FAKE_OLDER_VERSION,
        FAKE_VERSION,
    ]


def test_available_versions_skips_a_release_not_built_for_this_platform(
    monkeypatch, platform_key
):
    """A version published for another platform is not offered to this one."""
    index = _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"new"})
    for series in index["serverVersions"]:
        for patch_group in series["patches"]:
            for packages in patch_group.values():
                packages[0]["os"] = "solaris-sparc"

    assert sandbox_servers.available_versions() == []
    assert sandbox_servers.available_versions(FAKE_SERIES) == []


def test_available_versions_refuses_a_full_version_as_a_series(monkeypatch, platform_key):
    """Asked to list the patches "of 10.6.1", it says what to pass instead."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"new"})

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.available_versions(FAKE_VERSION)

    assert f"'{FAKE_SERIES}'" in str(failure.value)


def test_available_versions_refuses_an_unknown_series(monkeypatch, platform_key):
    """An unknown series is refused with the list of the known ones."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"new"})

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.available_versions("99.9")

    assert FAKE_VERSION in str(failure.value)


# --------------------------------------------------------------------------
# Parsing and matching versions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("11.8", (11, 8, None)),
        ("11.8.9", (11, 8, 9)),
        ("v11.8.9", (11, 8, 9)),
        ("V11.8", (11, 8, None)),
        ("  11.8.9  ", (11, 8, 9)),
        ("11.8.10", (11, 8, 10)),
    ],
)
def test_parse_version_accepts_both_shapes(text, expected):
    """A series, a full version, and either with the leading v of a release tag."""
    assert sandbox_servers.parse_version(text) == expected


@pytest.mark.parametrize("text", ["11", "", None, "abc", "11.8.9.1", "11.x", "-1.2"])
def test_parse_version_refuses_anything_else(text):
    """Anything that is not one of the two shapes is refused by name."""
    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.parse_version(text)

    assert "major.minor" in str(failure.value)


@pytest.mark.parametrize(
    "version, request_parts, expected",
    [
        ("11.8.9", (11, 8, None), True),
        ("11.8.9", (11, 8, 9), True),
        ("11.8.9", (11, 8, 3), False),
        ("11.8.9", (11, 4, None), False),
        ("12.3.3", (11, 8, None), False),
        # Not a version at all - a stray directory under the server root.
        ("nightly", (11, 8, None), False),
        ("11.8", (11, 8, None), False),
    ],
)
def test_matches_treats_a_series_as_any_patch(version, request_parts, expected):
    """A series request takes any patch release; a full one only its own."""
    assert sandbox_servers.matches(version, *request_parts) is expected


# --------------------------------------------------------------------------
# What is already on the machine
# --------------------------------------------------------------------------


def _make_install(root, version, binary="bin/mariadbd"):
    """Creates a stand-in installation with a runnable server binary in it."""
    path = os.path.join(root, version)
    target = os.path.join(path, binary)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n")
    os.chmod(target, 0o755)
    return path


def test_find_server_binary_looks_in_bin_first(tmp_path):
    """The published layout: the binary sits in the installation's bin/."""
    install_dir = _make_install(str(tmp_path), FAKE_VERSION)

    found = sandbox_servers.find_server_binary(install_dir)

    assert found == os.path.join(install_dir, "bin", "mariadbd")


def test_find_server_binary_accepts_a_bin_directory_itself(tmp_path):
    """A directory that IS the bin folder is a valid installation to point at."""
    install_dir = _make_install(str(tmp_path), FAKE_VERSION)

    found = sandbox_servers.find_server_binary(os.path.join(install_dir, "bin"))

    assert found == os.path.join(install_dir, "bin", "mariadbd")


def test_find_server_binary_walks_an_unusual_layout(tmp_path):
    """A layout the packages do not have is searched rather than refused."""
    install_dir = _make_install(str(tmp_path), FAKE_VERSION, "usr/local/sbin/mariadbd")

    found = sandbox_servers.find_server_binary(install_dir)

    assert found.endswith(os.path.join("usr", "local", "sbin", "mariadbd"))


def test_find_server_binary_returns_none_for_a_directory_without_one(tmp_path):
    """A directory holding no server binary is not an installation."""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert sandbox_servers.find_server_binary(str(empty)) is None


def test_installed_versions_reads_the_directory_names(server_root):
    """Installed versions are the version-named directories holding a binary."""
    _make_install(server_root, "11.8.9")
    _make_install(server_root, "11.8.10")
    _make_install(server_root, "12.3.3")

    # 11.8.10 after 11.8.9: the sort is numeric, not lexicographic.
    assert sandbox_servers.installed_versions() == ["11.8.9", "11.8.10", "12.3.3"]


def test_installed_versions_ignores_leftovers(server_root):
    """A work directory, a non-version and an empty directory are not installs."""
    _make_install(server_root, "11.8.9")
    _make_install(server_root, sandbox_servers.WORK_PREFIX + "11.8.9.new")
    _make_install(server_root, "nightly")
    os.makedirs(os.path.join(server_root, "12.3.3"))

    assert sandbox_servers.installed_versions() == ["11.8.9"]


def test_installed_versions_is_empty_without_a_root(server_root):
    """No root means nothing has ever been downloaded."""
    assert not os.path.exists(server_root)
    assert sandbox_servers.installed_versions() == []


def test_path_server_version_reads_the_reported_version(monkeypatch):
    """The three numbers after 'Ver' are the version; the build suffix is not."""
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        sandbox_servers.subprocess,
        "run",
        lambda *a, **kw: _completed_process(
            "/usr/bin/mariadbd  Ver 12.3.2-MariaDB-log for osx10.21 on arm64\n"
        ),
    )

    assert sandbox_servers.path_server_version() == "12.3.2"


def _completed_process(stdout):
    """A stand-in for the CompletedProcess ``path_server_version`` reads."""
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


def test_path_server_version_is_none_without_a_server(monkeypatch):
    """No server binary on the PATH is not an error, it is an absence."""
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: None)

    assert sandbox_servers.path_server_version() is None


def test_path_server_version_is_none_when_the_output_is_unreadable(monkeypatch):
    """A binary that answers something else is treated as no answer at all."""
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        sandbox_servers.subprocess, "run", lambda *a, **kw: _completed_process("nope\n")
    )

    assert sandbox_servers.path_server_version() is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError("not executable"),
        # A different exception class entirely, and one the module has to name
        # on its own: subprocess.TimeoutExpired is not an OSError.
        __import__("subprocess").TimeoutExpired(cmd=["mariadbd"], timeout=1),
    ],
)
def test_path_server_version_survives_a_binary_that_will_not_run(monkeypatch, failure):
    """A binary on the PATH that cannot be run is the same as not being there."""
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: "/usr/bin/" + name)

    def explode(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sandbox_servers.subprocess, "run", explode)

    assert sandbox_servers.path_server_version() is None


# --------------------------------------------------------------------------
# Installing
# --------------------------------------------------------------------------


def test_install_extracts_the_package_and_strips_its_wrapper(
    monkeypatch, platform_key, server_root
):
    """The build's wrapping directory goes, so <version>/bin/mariadbd is true."""
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    requested = _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    target = sandbox_servers.install(FAKE_VERSION)

    assert requested == [_url_of(FAKE_VERSION)]
    assert target == os.path.join(server_root, FAKE_VERSION)
    binary = os.path.join(target, "bin", "mariadbd")
    assert os.path.isfile(binary)
    # The executable bit survives the extraction, or nothing can be deployed.
    assert os.stat(binary).st_mode & stat.S_IXUSR
    # And the rest of the package came along with it.
    assert os.path.isfile(
        os.path.join(target, "share", "mariadb_system_tables.sql")
    )
    # Nothing is left in the root but the finished installation.
    assert os.listdir(server_root) == [FAKE_VERSION]


def test_install_keeps_a_package_that_is_not_wrapped(
    monkeypatch, platform_key, server_root
):
    """Only ONE wrapping directory is ever stripped, and only if there is one."""
    payload = _package_bytes(_PACKAGE_ENTRIES, root_name="")
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    target = sandbox_servers.install(FAKE_VERSION)

    assert os.path.isfile(os.path.join(target, "bin", "mariadbd"))


def test_install_rejects_a_package_whose_checksum_does_not_match(
    monkeypatch, platform_key, server_root
):
    """A package that is not what the index pinned is discarded, not installed."""
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    # The index pins the checksum of `payload`; the download serves something
    # else, which is exactly the case the checksum exists for.
    _stub_download(
        monkeypatch,
        {_url_of(FAKE_VERSION): _package_bytes(_PACKAGE_ENTRIES + (("extra", "x", 0o644),))},
    )

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.install(FAKE_VERSION)

    assert "checksum" in str(failure.value)
    assert not os.path.exists(os.path.join(server_root, FAKE_VERSION))


def test_install_rejects_a_package_with_no_server_in_it(
    monkeypatch, platform_key, server_root
):
    """A package that extracts to no server binary installs nothing."""
    payload = _package_bytes((("share/readme.txt", "hello\n", 0o644),))
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.install(FAKE_VERSION)

    assert "no server binary" in str(failure.value)
    assert not os.path.exists(os.path.join(server_root, FAKE_VERSION))


def test_install_refuses_to_write_outside_the_target(
    monkeypatch, platform_key, server_root, tmp_path
):
    """An archive entry naming its way out of the tree is not extracted.

    A package is remote input. This is the tarfile ``data`` filter doing its
    job, asserted here rather than assumed, because the day it stops being
    passed is the day this is the only thing that notices.
    """
    escapee = tmp_path / "escaped.txt"
    payload = _package_bytes(
        (("bin/mariadbd", "#!/bin/sh\n", 0o755), (f"../../{escapee.name}", "x", 0o644))
    )
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    with pytest.raises(Exception):
        sandbox_servers.install(FAKE_VERSION)

    assert not escapee.exists()
    assert not os.path.exists(os.path.join(server_root, FAKE_VERSION))


def test_install_refuses_a_series(monkeypatch, platform_key, server_root):
    """``install`` takes a release, not a series - the caller resolves that."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.install(FAKE_SERIES)

    assert "series" in str(failure.value)


def test_install_refuses_a_version_that_is_not_published(
    monkeypatch, platform_key, server_root
):
    """An unpublished version is refused with what IS available for the machine."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.install("9.9.9")

    assert FAKE_VERSION in str(failure.value)


def test_install_replaces_an_existing_installation(
    monkeypatch, platform_key, server_root
):
    """Re-installing a version replaces it and leaves no work directory behind."""
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    stale = _make_install(server_root, FAKE_VERSION)
    with open(os.path.join(stale, "stale.txt"), "w", encoding="utf-8") as handle:
        handle.write("from the previous install\n")

    target = sandbox_servers.install(FAKE_VERSION)

    assert not os.path.exists(os.path.join(target, "stale.txt"))
    assert os.path.isfile(os.path.join(target, "bin", "mariadbd"))
    assert os.listdir(server_root) == [FAKE_VERSION]


def test_a_rejected_package_that_cannot_be_deleted_is_still_refused(
    monkeypatch, platform_key, server_root
):
    """Failing to delete the bad download does not turn it into a good one.

    The removal is a tidy-up. The refusal is the point, and it has to survive a
    read-only temp directory or a file somebody else has open.
    """
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): b"not the package"})

    def refuse(path):
        raise OSError("read-only file system")

    monkeypatch.setattr(sandbox_servers.os, "remove", refuse)

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.install(FAKE_VERSION)

    assert "checksum" in str(failure.value)
    assert not os.path.exists(os.path.join(server_root, FAKE_VERSION))


def test_a_failed_swap_puts_the_previous_installation_back(
    monkeypatch, platform_key, server_root
):
    """If the finished copy cannot be moved into place, the old one returns.

    The window between "the installed copy has been moved aside" and "the new
    one is in its place" is the only moment at which a machine has no server of
    that version at all. It must not be where a failure leaves it.
    """
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    working = _make_install(server_root, FAKE_VERSION)
    with open(os.path.join(working, "keep.txt"), "w", encoding="utf-8") as handle:
        handle.write("still here\n")

    real_rename = os.rename
    failed = []

    def fail_the_swap(source, destination):
        # Only the FIRST move into place fails. The renames that stage the new
        # copy, and the one that puts the old copy back, must still work - a
        # stub that broke those too would be testing a rollback that never ran.
        if os.path.basename(destination) == FAKE_VERSION and not failed:
            failed.append(destination)
            raise OSError("the swap failed")
        return real_rename(source, destination)

    monkeypatch.setattr(sandbox_servers.os, "rename", fail_the_swap)

    with pytest.raises(OSError):
        sandbox_servers.install(FAKE_VERSION)

    assert os.path.isfile(os.path.join(working, "keep.txt"))
    assert sandbox_servers.installed_versions() == [FAKE_VERSION]


def test_a_failed_install_leaves_the_installed_copy_alone(
    monkeypatch, platform_key, server_root
):
    """A download that fails part way through does not cost the working copy."""
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})

    def fail(url, timeout=None):
        raise OSError("the network went away")

    monkeypatch.setattr(sandbox_servers.urllib.request, "urlopen", fail)

    working = _make_install(server_root, FAKE_VERSION)
    with open(os.path.join(working, "keep.txt"), "w", encoding="utf-8") as handle:
        handle.write("still here\n")

    with pytest.raises(OSError):
        sandbox_servers.install(FAKE_VERSION)

    assert os.path.isfile(os.path.join(working, "keep.txt"))
    assert sandbox_servers.installed_versions() == [FAKE_VERSION]


# --------------------------------------------------------------------------
# Clearing the macOS quarantine attributes
# --------------------------------------------------------------------------


def test_quarantine_flags_are_only_cleared_on_macos(monkeypatch, tmp_path):
    """Nothing is run on Linux or Windows - there is nothing to clear there."""
    calls = []
    monkeypatch.setattr(sandbox_servers.sys, "platform", "linux")
    monkeypatch.setattr(
        sandbox_servers.subprocess, "run", lambda *a, **kw: calls.append(a)
    )

    sandbox_servers.clear_quarantine_flags(str(tmp_path))

    assert calls == []


def test_quarantine_flags_are_cleared_recursively_on_macos(monkeypatch, tmp_path):
    """On macOS the whole installation is cleared before it is ever used."""
    calls = []
    monkeypatch.setattr(sandbox_servers.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: "/usr/bin/xattr")
    monkeypatch.setattr(
        sandbox_servers.subprocess,
        "run",
        lambda command, **kw: calls.append(command) or _completed_process(""),
    )

    sandbox_servers.clear_quarantine_flags(str(tmp_path))

    assert calls == [["/usr/bin/xattr", "-cr", str(tmp_path)]]


def test_a_missing_xattr_is_reported_and_not_fatal(monkeypatch, tmp_path, capsys):
    """Without xattr the install stands and says what to run by hand."""
    monkeypatch.setattr(sandbox_servers.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: None)

    sandbox_servers.clear_quarantine_flags(str(tmp_path))

    assert "xattr -cr" in capsys.readouterr().err


def test_a_failing_xattr_is_reported_and_not_fatal(monkeypatch, tmp_path, capsys):
    """An installation is not thrown away because clearing its flags failed."""
    monkeypatch.setattr(sandbox_servers.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox_servers.shutil, "which", lambda name: "/usr/bin/xattr")
    monkeypatch.setattr(
        sandbox_servers.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="denied"),
    )

    sandbox_servers.clear_quarantine_flags(str(tmp_path))

    assert "denied" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Resolving a request to a server
# --------------------------------------------------------------------------


def test_resolve_prefers_the_server_on_the_path(monkeypatch, platform_key, server_root):
    """A machine that already has the version does not download a second copy."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: FAKE_VERSION)
    _stub_download(monkeypatch, {})  # any download at all fails the test

    resolved = sandbox_servers.resolve(FAKE_SERIES)

    assert resolved == sandbox_servers.ResolvedServer(
        FAKE_VERSION, None, sandbox_servers.SOURCE_PATH
    )


def test_resolve_ignores_a_path_server_of_another_version(
    monkeypatch, platform_key, server_root
):
    """The PATH is only used when it is the version that was asked for."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: "12.3.2")
    _make_install(server_root, FAKE_VERSION)

    resolved = sandbox_servers.resolve(FAKE_VERSION)

    assert resolved.source == sandbox_servers.SOURCE_INSTALLED
    assert resolved.mariadbd_path.endswith(os.path.join(FAKE_VERSION, "bin", "mariadbd"))


def test_resolve_takes_the_newest_installed_patch_of_a_series(
    monkeypatch, platform_key, server_root
):
    """Asking for a series gets the best copy on the machine, not the first."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)
    _make_install(server_root, FAKE_OLDER_VERSION)
    _make_install(server_root, FAKE_VERSION)

    resolved = sandbox_servers.resolve(FAKE_SERIES)

    assert resolved.version == FAKE_VERSION
    assert resolved.source == sandbox_servers.SOURCE_INSTALLED


def test_resolve_downloads_a_version_the_machine_does_not_have(
    monkeypatch, platform_key, server_root
):
    """Neither on the PATH nor installed: it is fetched, and it says so."""
    payload = _package_bytes(_PACKAGE_ENTRIES)
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: payload})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)
    _stub_download(monkeypatch, {_url_of(FAKE_VERSION): payload})

    resolved = sandbox_servers.resolve(FAKE_VERSION)

    assert resolved.version == FAKE_VERSION
    assert resolved.source == sandbox_servers.SOURCE_DOWNLOADED
    assert os.path.isfile(resolved.mariadbd_path)


def test_resolve_downloads_the_latest_patch_of_a_requested_series(
    monkeypatch, platform_key, server_root
):
    """A series with nothing installed downloads its newest published release."""
    newest = _package_bytes(_PACKAGE_ENTRIES)
    oldest = _package_bytes(_PACKAGE_ENTRIES + (("share/old", "x", 0o644),))
    _stub_index(
        monkeypatch,
        platform_key,
        {FAKE_VERSION: newest, FAKE_OLDER_VERSION: oldest},
    )
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)
    requested = _stub_download(monkeypatch, {_url_of(FAKE_VERSION): newest})

    resolved = sandbox_servers.resolve(FAKE_SERIES)

    assert resolved.version == FAKE_VERSION
    assert requested == [_url_of(FAKE_VERSION)]


def test_resolve_refuses_a_series_that_is_not_published(
    monkeypatch, platform_key, server_root
):
    """A series nothing has and nothing publishes is refused, not downloaded."""
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.resolve("99.9")

    assert "99.9" in str(failure.value)


def test_resolve_refuses_a_series_not_built_for_this_platform(
    monkeypatch, platform_key, server_root
):
    """A series the index HAS but has no package of for this machine.

    Distinct from an unknown series: the index knows the series perfectly well,
    it simply cannot supply it here - so the message names the platform rather
    than the version.
    """
    index = _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    for series in index["serverVersions"]:
        for patch_group in series["patches"]:
            for packages in patch_group.values():
                packages[0]["os"] = "solaris-sparc"
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.resolve(FAKE_SERIES)

    assert "not published for" in str(failure.value)


def test_resolve_reports_an_installation_that_lost_its_binary(
    monkeypatch, platform_key, server_root, tmp_path
):
    """An installation disturbed between the check and the swap is reported.

    ``install`` checks the staging copy before moving it into place, so this
    can only happen if something removed the binary in between - but "the
    version is installed and has no server in it" is a state worth naming
    rather than returning None as a path.
    """
    _stub_index(monkeypatch, platform_key, {FAKE_VERSION: b"x"})
    monkeypatch.setattr(sandbox_servers, "path_server_version", lambda: None)

    emptied = tmp_path / "emptied"
    emptied.mkdir()
    monkeypatch.setattr(sandbox_servers, "install", lambda version: str(emptied))

    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.resolve(FAKE_VERSION)

    assert "holds no server binary" in str(failure.value)


def test_resolve_refuses_a_malformed_version(server_root):
    """A typo is answered before anything is searched or fetched."""
    with pytest.raises(mysqlsh.Error) as failure:
        sandbox_servers.resolve("latest")

    assert "major.minor" in str(failure.value)


# --------------------------------------------------------------------------
# Where installations live
# --------------------------------------------------------------------------


def test_server_root_follows_the_data_home_on_posix(monkeypatch):
    """On Linux and macOS an installation is under the XDG data home."""
    monkeypatch.setattr(general.os, "name", "posix")
    monkeypatch.setattr(general, "get_data_home", lambda: "/home/tester/.local/share")

    assert general.get_sandbox_server_root() == os.path.join(
        "/home/tester/.local/share", general.SANDBOX_SERVER_DIR_NAME
    )


def test_server_root_uses_local_app_data_on_windows(monkeypatch):
    """On Windows it is %LOCALAPPDATA%\\Programs, not an XDG path."""
    monkeypatch.setattr(general.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")

    root = general.get_sandbox_server_root()

    assert root.startswith(r"C:\Users\tester\AppData\Local")
    assert "Programs" in root
    assert root.endswith(general.SANDBOX_SERVER_DIR_NAME)


def test_server_root_falls_back_when_local_app_data_is_unset(monkeypatch):
    """A stripped environment gets the default rather than no answer at all."""
    monkeypatch.setattr(general.os, "name", "nt")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    root = general.get_sandbox_server_root()

    assert os.path.join("AppData", "Local", "Programs") in root


def test_server_path_is_the_version_under_the_root(monkeypatch):
    """The version is the directory name; there is no version file inside."""
    monkeypatch.setattr(general, "get_sandbox_server_root", lambda: "/srv/roots")

    assert general.get_sandbox_server_path("11.8.9") == os.path.join(
        "/srv/roots", "11.8.9"
    )


# --------------------------------------------------------------------------
# The MCP tools
# --------------------------------------------------------------------------


def test_the_listing_tool_is_advertised():
    """sandbox.list_available_versions is served with the sandbox group."""
    assert "sandbox.list_available_versions" in helpers.list_tool_names(["sandbox"])


def test_the_listing_tool_answers_with_the_shipped_versions():
    """Over the protocol, the tool lists what the shipped index publishes."""
    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.list_available_versions",
    )

    assert result.is_error is False
    versions = helpers.tool_payload(result)
    assert versions == sandbox_servers.available_versions()
    assert versions, "the shipped index publishes nothing for this platform"


def test_the_listing_tool_lists_one_series():
    """The series argument switches it to that series' patch releases."""
    series = sandbox_servers.available_versions()[0].rsplit(".", 1)[0]

    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.list_available_versions",
        arguments={"series": series},
    )

    assert result.is_error is False
    # A list is sent as one content block per element, and tool_payload gives a
    # lone block back as a scalar - so a series with a single patch release
    # arrives as the string, not as a one-element list.
    payload = helpers.tool_payload(result)
    listed = payload if isinstance(payload, list) else [payload]
    assert listed == sandbox_servers.available_versions(series)


def test_the_listing_tool_reports_a_bad_series():
    """A malformed series comes back as a tool error that still has its message.

    Which is the tool_registrar's doing - see its module docstring. Without it
    SDK 2.1 answers a mysqlsh.Error with a bare "Error executing tool".
    """
    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.list_available_versions",
        arguments={"series": "not-a-version"},
    )

    assert result.is_error is True
    assert "major.minor" in helpers.tool_payload(result)


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _deploy_tool(monkeypatch, resolved):
    """Registers the sandbox tools against stand-ins and returns sandbox.deploy.

    Driven in-process rather than over stdio because what is being checked is
    the message a successful deploy answers with, and paying for a real server
    start - three times, once per source - to read a string back would be a
    poor trade. The shell's ``sandbox`` global and the secret store are the two
    seams: neither is what these tests are about, and both have real side
    effects.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        resolved: What :func:`sandbox_servers.resolve` should answer, or None
            to make any call to it a failure.

    Returns:
        A ``(deploy, deployed)`` pair: the tool function, and the list every
        call's options dict is appended to.
    """
    import mysqlsh.globals

    deployed = []
    monkeypatch.setattr(
        mysqlsh.globals,
        "sandbox",
        SimpleNamespace(deploy=lambda port, options: deployed.append((port, options))),
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_functions.config, "store_connection", lambda uri, password: None
    )

    def resolve(version):
        assert resolved is not None, f"resolve({version!r}) should not have been called"
        return resolved

    monkeypatch.setattr(sandbox_servers, "resolve", resolve)

    recorder = _ToolRecorder()
    sandbox_functions.register_sandbox_tools(recorder)
    return recorder.tools["sandbox.deploy"], deployed


def test_deploy_without_a_version_says_nothing_about_one(monkeypatch):
    """The message is unchanged when no version was asked for."""
    deploy, deployed = _deploy_tool(monkeypatch, None)

    message = asyncio.run(deploy(None, 3399))

    assert message == "Sandbox instance deployed and started on port 3399."
    assert deployed[0][1].get("mariadbdPath") is None


def test_deploy_on_the_path_server_names_the_version_it_got(monkeypatch):
    """A series request reports the patch release that actually runs.

    And passes no ``mariadbdPath``: the shell finds the server on the PATH
    itself, which is the whole reason the PATH is searched first.
    """
    deploy, deployed = _deploy_tool(
        monkeypatch,
        sandbox_servers.ResolvedServer("12.3.2", None, sandbox_servers.SOURCE_PATH),
    )

    message = asyncio.run(deploy(None, 3399, server_version="12.3"))

    assert "MariaDB 12.3.2" in message
    assert "found on the PATH" in message
    # No binary was named, so nothing has to be named to start it again either.
    assert "sandbox.start" not in message
    assert "mariadbdPath" not in deployed[0][1]


@pytest.mark.parametrize(
    "source, description",
    [
        (sandbox_servers.SOURCE_INSTALLED, "already downloaded"),
        (sandbox_servers.SOURCE_DOWNLOADED, "downloaded now"),
    ],
)
def test_deploy_on_a_downloaded_server_says_how_to_start_it_again(
    monkeypatch, source, description
):
    """A server that is not the PATH's is passed through, and named for later.

    ``sandbox.start`` takes no ``server_version``, so an instance built on a
    downloaded server would come back up on the PATH's server unless the client
    is told what to pass. That makes the path part of the answer, not a detail.
    """
    binary = os.path.join("/srv", "roots", "11.8.9", "bin", "mariadbd")
    deploy, deployed = _deploy_tool(
        monkeypatch, sandbox_servers.ResolvedServer("11.8.9", binary, source)
    )

    message = asyncio.run(deploy(None, 3399, server_version="11.8.9"))

    assert f"MariaDB 11.8.9 ({description})" in message
    assert f"mariadbd_path='{binary}'" in message
    assert deployed[0][1]["mariadbdPath"] == binary


def test_deploy_refuses_a_version_and_a_path_together(allowed_temp_dir):
    """The two arguments that both name the server cannot both be given."""
    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.deploy",
        arguments={
            "port": helpers.find_free_port(),
            "sandbox_dir": allowed_temp_dir,
            "server_version": "11.8",
            "mariadbd_path": "/usr/bin/mariadbd",
        },
    )

    assert result.is_error is True
    assert "cannot be combined" in helpers.tool_payload(result)


def test_deploy_reports_an_unknown_version_without_deploying(allowed_temp_dir):
    """An impossible version is refused before a sandbox directory is touched."""
    port = helpers.find_free_port()

    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.deploy",
        arguments={
            "port": port,
            "sandbox_dir": allowed_temp_dir,
            "server_version": "99.9.9",
        },
    )

    assert result.is_error is True
    assert not os.path.isdir(os.path.join(allowed_temp_dir, str(port)))


# --------------------------------------------------------------------------
# The real thing
# --------------------------------------------------------------------------


@pytest.mark.e2e
def test_a_sandbox_really_runs_a_downloaded_server(allowed_temp_dir, monkeypatch):
    """Downloads a published server for real and deploys a sandbox on it.

    The one test here that reaches the network. It proves the parts the stubbed
    tests cannot: that the published packages are still downloadable and still
    match their checksums, that a real one extracts to a server that really
    runs, and that the shell's sandbox accepts the path this hands it.

    The version is deliberately the OLDEST the index publishes, so that it is
    not the one the developer's machine has on its PATH - a test that resolved
    to the PATH server would download nothing and prove none of the above.
    """
    versions = sandbox_servers.available_versions()
    if not versions:
        pytest.skip("no server packages are published for this platform")

    wanted = versions[0]
    if sandbox_servers.path_server_version() == wanted:
        pytest.skip(f"the server on the PATH is already {wanted}")

    # Installed into the test's own root so the developer's home is left alone.
    root = os.path.join(allowed_temp_dir, "servers")
    monkeypatch.setattr(general, "get_sandbox_server_root", lambda: root)
    monkeypatch.setattr(
        general,
        "get_sandbox_server_path",
        lambda version: os.path.join(root, version),
    )

    resolved = sandbox_servers.resolve(wanted)
    assert resolved.source == sandbox_servers.SOURCE_DOWNLOADED
    assert resolved.version == wanted

    from mysqlsh.globals import sandbox, shell

    port = helpers.find_free_port()
    sandbox.deploy(
        port,
        {
            "password": "mcp_pytest_root",
            "sandboxDir": allowed_temp_dir,
            "ssl": False,
            "mariadbdPath": resolved.mariadbd_path,
        },
    )
    try:
        session = shell.open_session(f"root:mcp_pytest_root@127.0.0.1:{port}")
        try:
            running = session.run_sql("SELECT VERSION()").fetch_one()[0]
        finally:
            session.close()

        # The sandbox runs the version that was downloaded, not the PATH's.
        assert running.startswith(wanted)
    finally:
        sandbox.stop(port, {"sandboxDir": allowed_temp_dir, "password": "mcp_pytest_root"})
        sandbox.delete(port, {"sandboxDir": allowed_temp_dir})
