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

"""Tests for installing the MySQL-to-MariaDB migration tooling.

Covers lib/setup_migrator.py's download/extract path and lib/general.py's
data-home and ``get_migrator_path`` helpers. The tooling installs into
``<data home>/mariadb-migrator/<version>``, so the release is part of the path
and there is no version file inside an install; the fixture below points the
data home at a temp directory. The download itself is stubbed out - the archive is built
locally and handed to the extraction as if it had just been fetched - so the
suite neither needs the network nor depends on what a given release of the
tooling contains. The archive is given the shape GitHub's source archives for a
release tag have (one top-level directory named after the project and the
release, directory entries of its own, executable scripts inside it), which is
what the extraction is written for.
"""

# cSpell:ignore mysqlsh MariaDB

import os
import pathlib
import stat
import subprocess
import sys
import zipfile

import pytest

import mysqlsh

from mcp_plugin.lib import general, setup_migrator, setup_prompts


def _write_archive(archive_path, entries):
    """Writes a zip archive with the given (name, contents, mode) entries.

    A name ending in a slash is written as a directory entry, which is how the
    real archive carries its directories.

    Args:
        archive_path: The archive to create.
        entries: Tuples of the entry's archive path, its contents and its unix
            mode (0 to leave the mode unrecorded, as a plain file has it).

    Returns:
        The archive path.
    """
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, contents, mode in entries:
            info = zipfile.ZipInfo(name)
            if name.endswith("/"):
                info.external_attr = (stat.S_IFDIR | (mode or 0o755)) << 16
            elif mode:
                info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, contents)

    return str(archive_path)


# The shape of the real archive: everything below one top-level directory named
# after the project and its branch, its directories carried as entries of their
# own, and the entry point and the scripts marked executable.
_SOURCE_ARCHIVE = (
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/", "", 0),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/mariadb-migrator", "#!/bin/sh\n", 0o755),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/README.md", "# tooling\n", 0),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/scripts/", "", 0),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/scripts/00_precheck.sh", "#!/bin/sh\n", 0o755),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/sql/", "", 0),
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/sql/precheck.sql", "SELECT 1;\n", 0),
    # A directory with no files in it exists only as its own entry.
    ("Mysql-to-MariaDB-Migration-1.4.0-beta/logs/", "", 0),
)


@pytest.fixture
def migrator_data_path(tmp_path, monkeypatch):
    """Points the data home at a temp directory for the test.

    The tooling root, every release directory under it and the temp directory
    the archive is downloaded into are all derived from the data home, so this
    keeps the whole install out of the real ``~/.local/share``.

    Yields:
        The absolute path of the stand-in data home.
    """
    data_home = tmp_path / "data_home"
    data_home.mkdir()
    monkeypatch.setattr(general, "get_data_home", lambda: str(data_home))
    yield str(data_home)


def _stub_download(monkeypatch, entries):
    """Makes the download write a locally built archive instead of fetching one."""
    def fake_download(url, archive_path):
        _write_archive(archive_path, entries)

    monkeypatch.setattr(setup_migrator, "_download_archive", fake_download)


def test_the_install_path_is_the_data_home_the_dir_and_the_release(migrator_data_path):
    """<data home>/mariadb-migrator/<version>, and asking does not create it."""
    root_dir = general.get_migrator_root()
    target_dir = general.get_migrator_path()

    assert root_dir == os.path.join(migrator_data_path, general.MIGRATOR_DIR_NAME)
    assert target_dir == os.path.join(root_dir, general.MIGRATOR_VERSION)
    # The release is part of the path, which is what lets releases sit side by
    # side and makes the directory name the only record of what a copy is.
    assert os.path.basename(target_dir) == general.MIGRATOR_VERSION
    # An explicit release overrides the configured one.
    assert general.get_migrator_path("v0.0.1") == os.path.join(root_dir, "v0.0.1")

    # Its existence is what says whether the tooling has been downloaded, so
    # merely asking for the path must not answer that question with a yes.
    assert not os.path.exists(target_dir)
    assert not os.path.exists(root_dir)


def test_downloading_installs_the_tooling(migrator_data_path, monkeypatch):
    """The archive lands in mariadb-migrator, unwrapped and still executable."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)

    target_dir = setup_migrator.download()

    assert target_dir == general.get_migrator_path()
    assert os.path.basename(target_dir) == general.MIGRATOR_VERSION
    assert os.path.basename(os.path.dirname(target_dir)) == "mariadb-migrator"

    # The archive's own top-level directory is stripped, so the tooling's layout
    # starts directly inside the target directory.
    entry_point = os.path.join(target_dir, "mariadb-migrator")
    script = os.path.join(target_dir, "scripts", "00_precheck.sh")
    assert os.path.isfile(entry_point)
    assert os.path.isfile(os.path.join(target_dir, "README.md"))
    assert os.path.isfile(script)
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))
    assert not os.path.exists(
        os.path.join(target_dir, "Mysql-to-MariaDB-Migration-1.4.0-beta")
    )

    # The directory name is the record of which release this is - there is no
    # version file inside the install to disagree with it.
    assert setup_migrator.installed_versions() == [general.MIGRATOR_VERSION]
    assert setup_migrator.is_installed() is True

    # What was executable in the archive is executable on disk - extractall()
    # would have dropped this, and the tooling is scripts.
    assert os.access(entry_point, os.X_OK)
    assert os.access(script, os.X_OK)
    assert not os.access(os.path.join(target_dir, "README.md"), os.X_OK)

    # A directory that holds no files comes across as its own archive entry.
    assert os.path.isdir(os.path.join(target_dir, "logs"))

    # Nothing is left beside the installed copy.
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_only_a_single_wrapping_directory_is_stripped(migrator_data_path, monkeypatch):
    """An archive that is not wrapped in one directory is extracted as it is.

    The negative control for the stripping: an archive whose only entry is a
    top-level FILE would lose that file to a strip, and one with several
    top-level directories would lose all but an arbitrary one of them.
    """
    _stub_download(monkeypatch, (("mariadb-migrator", "#!/bin/sh\n", 0o755),))

    target_dir = setup_migrator.download()
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    _stub_download(
        monkeypatch,
        (
            ("scripts/00_precheck.sh", "#!/bin/sh\n", 0o755),
            ("sql/precheck.sql", "SELECT 1;\n", 0),
        ),
    )

    setup_migrator.download()
    assert os.path.isfile(os.path.join(target_dir, "scripts", "00_precheck.sh"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))


def test_downloading_again_replaces_the_installed_copy(migrator_data_path, monkeypatch):
    """A second download is the new version, not the two versions merged."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    _stub_download(
        monkeypatch,
        (
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/mariadb-migrator", "#!/bin/sh\n# v2\n", 0o755),
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/scripts/00_precheck.sh", "#!/bin/sh\n", 0o755),
        ),
    )
    setup_migrator.download()

    with open(os.path.join(target_dir, "mariadb-migrator"), encoding="utf-8") as script:
        assert "# v2" in script.read()
    # Files the new version does not have are gone, rather than left behind from
    # the version before it.
    assert not os.path.exists(os.path.join(target_dir, "README.md"))
    assert not os.path.exists(os.path.join(target_dir, "sql", "precheck.sql"))

    # The staging and backup directories the swap uses are cleaned up again.
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_a_failed_download_keeps_the_installed_copy(migrator_data_path, monkeypatch):
    """An install that was working is not lost to a download that fails."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    def failing_download(url, archive_path):
        # Write a partial archive first, so the failure is one that happens with
        # something already on disk rather than with nothing.
        _write_archive(archive_path, _SOURCE_ARCHIVE[:2])
        raise OSError("the network went away")

    monkeypatch.setattr(setup_migrator, "_download_archive", failing_download)

    with pytest.raises(OSError):
        setup_migrator.download()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_the_url_names_the_configured_release(monkeypatch):
    """The download URL is built from the version configured in lib/general.py."""
    assert general.MIGRATOR_VERSION in setup_migrator.archive_url()
    assert setup_migrator.archive_url().endswith(f"{general.MIGRATOR_VERSION}.zip")

    # Bumping the setting is all there is to installing another release, so the
    # URL has to follow it rather than having been fixed at import time.
    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    assert setup_migrator.archive_url().endswith("/refs/tags/v9.9.9.zip")


def test_the_archive_is_really_fetched_from_the_configured_url(
    migrator_data_path, tmp_path, monkeypatch
):
    """The download itself is exercised, over a file:// URL instead of the network.

    Every other test here stubs the fetch out; this one leaves it in place and
    only redirects it, so that the one step nothing else covers - reading the
    response and writing it to disk - is covered too, without the suite
    depending on GitHub being reachable or on what a release contains.
    """
    archive_path = _write_archive(tmp_path / "source.zip", _SOURCE_ARCHIVE)
    monkeypatch.setattr(
        setup_migrator, "archive_url", lambda: pathlib.Path(archive_path).as_uri()
    )

    target_dir = setup_migrator.download()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "scripts", "00_precheck.sh"))
    # The archive is not left behind in the plugin data directory.
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_a_failed_swap_puts_the_installed_copy_back(migrator_data_path, monkeypatch):
    """Failing to move the new copy into place restores the old one."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    real_rename = os.rename

    def failing_rename(source, destination):
        # Moving the freshly extracted copy into place is the one that fails;
        # moving the installed copy out of the way, and back, must not.
        if str(source).endswith(".new"):
            raise OSError("could not rename the new copy into place")
        return real_rename(source, destination)

    monkeypatch.setattr(os, "rename", failing_rename)

    with pytest.raises(OSError):
        setup_migrator.download()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_the_setup_option_follows_what_is_installed(migrator_data_path, monkeypatch):
    """Download when nothing is installed, remove when something is - only those."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    answers = []
    asked = []

    def fake_prompt(message, default=True):
        asked.append((message, default))
        return answers.pop(0)

    monkeypatch.setattr(setup_prompts, "yes_no", fake_prompt)
    target_dir = general.get_migrator_path()

    # Nothing installed: a download is offered, and declining it downloads
    # nothing.
    assert "Download" in setup_migrator.menu_label()
    answers.append(False)
    setup_migrator.manage()
    assert not os.path.exists(target_dir)
    assert "Download" in asked[-1][0] and asked[-1][1] is True

    answers.append(True)
    setup_migrator.manage()
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    # Something installed: there is nothing left to download and what is offered
    # instead is removing it - and, being the one step that running the setup
    # again cannot undo, offered without suggesting it.
    assert setup_migrator.menu_label() == "Remove the MySQL-to-MariaDB migration tooling"
    answers.append(False)
    setup_migrator.manage()
    assert "Remove" in asked[-1][0] and asked[-1][1] is False
    # Declined, so it is still installed.
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    # Another release being configured does NOT turn the option into an update:
    # a different release is removed and downloaded again.
    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    assert setup_migrator.menu_label() == "Remove the MySQL-to-MariaDB migration tooling"
    answers.append(True)
    setup_migrator.manage()
    assert "Remove" in asked[-1][0]
    # Removing leaves the data home as it was before any of this.
    assert not os.path.exists(target_dir)
    assert os.listdir(migrator_data_path) == []

    # And with it gone, the configured release is what a download installs.
    answers.append(True)
    setup_migrator.manage()
    assert "v9.9.9" in asked[-1][0]
    assert setup_migrator.installed_versions() == ["v9.9.9"]


def test_removing_the_tooling_takes_every_release_and_any_leftovers(
    migrator_data_path, monkeypatch
):
    """Removal clears the whole root: other releases and download leftovers too."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    setup_migrator.download()
    root_dir = general.get_migrator_root()
    # A release the pin has moved past, and what an interrupted download leaves.
    os.makedirs(os.path.join(root_dir, "v1.3.0-beta"))
    os.makedirs(os.path.join(root_dir, ".v1.4.0-beta.new"))
    os.makedirs(os.path.join(root_dir, ".v1.4.0-beta.old"))

    assert setup_migrator.remove() == root_dir
    # The root itself goes, so the data home is as it was before any of this.
    assert not os.path.exists(root_dir)
    assert os.listdir(migrator_data_path) == []

    # Removing what is not there is not an error - the setup only offers it for
    # an installed copy, but nothing about it depends on that.
    assert setup_migrator.remove() == root_dir


def test_a_failed_download_is_reported_and_not_raised(
    migrator_data_path, monkeypatch, capsys
):
    """A download that fails must not abort the setup the user is in the middle of."""
    def failing_download(url, archive_path):
        raise OSError("the network went away")

    monkeypatch.setattr(setup_migrator, "_download_archive", failing_download)
    monkeypatch.setattr(setup_prompts, "yes_no", lambda message, default=True: True)

    setup_migrator.manage()

    output = capsys.readouterr().out
    assert "Could not install the migration tooling" in output
    assert "the network went away" in output
    assert not os.path.exists(general.get_migrator_path())


def test_a_failed_removal_is_reported_and_not_raised(
    migrator_data_path, monkeypatch, capsys
):
    """Nor must a removal that fails - the rest of the setup is still reachable."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    setup_migrator.download()

    def failing_remove():
        raise OSError("permission denied")

    monkeypatch.setattr(setup_migrator, "remove", failing_remove)
    monkeypatch.setattr(setup_prompts, "yes_no", lambda message, default=True: True)

    setup_migrator.manage()

    output = capsys.readouterr().out
    assert "Could not remove the migration tooling" in output
    assert "permission denied" in output


def test_a_release_the_pin_moved_past_is_named_and_still_removable(
    migrator_data_path, capsys
):
    """Other releases are reported, and are what the remove step will also take.

    The install carries no version file, so a directory is all it takes to be a
    release - which is the point of putting the release in the path.
    """
    os.makedirs(os.path.join(general.get_migrator_root(), "v1.3.0-beta"))
    # Download working directories are dot-prefixed and are not releases.
    os.makedirs(os.path.join(general.get_migrator_root(), ".v1.4.0-beta.new"))

    assert setup_migrator.installed_versions() == ["v1.3.0-beta"]
    # The CONFIGURED release is not installed, so this is False ...
    assert setup_migrator.is_installed() is False
    assert setup_migrator.print_status() is False
    output = capsys.readouterr().out
    assert "not downloaded yet" in output
    assert "Other releases installed: v1.3.0-beta" in output

    # ... but something IS installed, so the step on offer is removing it: with
    # removal being all-or-nothing, offering a download would leave the old
    # release with no way of ever being removed.
    assert "Remove" in setup_migrator.menu_label()


def test_an_archive_cannot_write_outside_the_target_directory(
    migrator_data_path, monkeypatch
):
    """A path-traversal entry is refused, and refused before anything is replaced."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    _stub_download(
        monkeypatch,
        (
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/mariadb-migrator", "#!/bin/sh\n", 0o755),
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/../escaped.txt", "owned\n", 0),
        ),
    )

    with pytest.raises(mysqlsh.Error):
        setup_migrator.download()

    assert not os.path.exists(os.path.join(migrator_data_path, "escaped.txt"))
    # The copy that was installed is untouched, and no half-extracted one is left.
    assert os.path.isfile(os.path.join(target_dir, "README.md"))
    assert sorted(os.listdir(general.get_migrator_root())) == [general.MIGRATOR_VERSION]


def test_the_tooling_is_not_offered_on_windows(migrator_data_path, monkeypatch):
    """The migration tooling is a POSIX-shell payload, so Windows never sees it.

    The predicate is deliberately "not Windows" rather than "Linux or macOS", so
    both directions are asserted: this platform (whatever CI runs on, as long as
    it is not Windows) supports it, and ``os.name == "nt"`` does not.
    """
    assert os.name != "nt", "this test assumes the suite does not run on Windows"
    assert setup_migrator.is_supported() is True

    monkeypatch.setattr(os, "name", "nt")
    assert setup_migrator.is_supported() is False


def test_the_windows_menu_leaves_the_entry_out_and_renumbers(monkeypatch):
    """Dropping the entry shifts Finish down; the menu is built, not written out.

    Proves the two things the platform gate has to get right: the migration entry
    is absent, and "Finish" is 5 rather than 6 - a menu that kept a hole at 5
    would still list six choices.
    """
    from mcp_plugin.lib import setup

    supported = setup._menu_entries()
    assert len(supported) == 5
    assert "migration tooling" in supported[-1][0]

    monkeypatch.setattr(setup_migrator, "is_supported", lambda: False)
    unsupported = setup._menu_entries()
    assert len(unsupported) == 4
    assert not any("migration" in label for label, _ in unsupported)
    # The labels that remain are unchanged and in the same order.
    assert [label for label, _ in unsupported] == [label for label, _ in supported[:4]]


def test_the_data_home_follows_the_xdg_specification(monkeypatch):
    """$XDG_DATA_HOME when absolute, ~/.local/share otherwise."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert general.get_data_home() == os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    assert general.get_migrator_root() == os.path.join(
        os.path.expanduser("~"), ".local", "share", "mariadb-migrator"
    )

    monkeypatch.setenv("XDG_DATA_HOME", "/opt/somewhere/share")
    assert general.get_data_home() == "/opt/somewhere/share"
    assert general.get_migrator_path() == os.path.join(
        "/opt/somewhere/share", "mariadb-migrator", general.MIGRATOR_VERSION
    )

    # The specification says a relative value is to be ignored rather than
    # resolved against the current directory, which would put the install
    # wherever the shell happened to be started.
    monkeypatch.setenv("XDG_DATA_HOME", "relative/share")
    assert general.get_data_home() == os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )

    # An empty value is unset, not the filesystem root.
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert general.get_data_home() == os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def test_releases_install_side_by_side(migrator_data_path, monkeypatch):
    """Downloading a second release leaves the first one where it is.

    This is what the release-in-the-path buys: bumping the pin does not extract
    a new release over the top of an old one. The menu will not get here - with
    something installed it offers removal (see menu_label) - but download() is
    the function that has to be right about it either way.
    """
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    first = setup_migrator.download()

    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    second = setup_migrator.download()

    assert first != second
    assert os.path.isfile(os.path.join(first, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(second, "mariadb-migrator"))
    assert setup_migrator.installed_versions() == ["v1.4.0-beta", "v9.9.9"]

    # No working directories left behind by either download.
    assert sorted(os.listdir(general.get_migrator_root())) == [
        "v1.4.0-beta", "v9.9.9"
    ]


# --- The venv, the dependencies and the wrapper ----------------------------


@pytest.fixture
def migrator_bin_home(tmp_path, monkeypatch):
    """Points the wrapper's install directory at a temp directory.

    Without this the wrapper would be written to the real ~/.local/bin, which is
    on the developer's PATH.

    Yields:
        The absolute path of the stand-in bin directory.
    """
    bin_home = tmp_path / "bin_home"
    monkeypatch.setattr(general, "get_bin_home", lambda: str(bin_home))
    yield str(bin_home)


def test_provisioning_builds_a_working_venv_and_installs_nothing_extra(
    migrator_data_path, monkeypatch
):
    """The venv is real, built by THIS interpreter, and pip lands inside it.

    The dependency install is stubbed out: what is under test is that a usable
    environment is created with the shell's own interpreter, not that PyPI can
    be reached. A release whose manifest is absent must still leave a venv.
    """
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    # _SOURCE_ARCHIVE ships no orchestrator/requirements.txt, so this exercises
    # the no-manifest path - the venv still has to come out of it.
    venv_dir = setup_migrator.provision(target_dir)

    assert venv_dir == os.path.join(target_dir, setup_migrator.MIGRATOR_VENV_DIR)
    venv_python = setup_migrator._venv_python(target_dir)
    assert os.path.exists(venv_python)
    assert os.access(venv_python, os.X_OK)
    # A real venv, and one that knows it is one: prefix moves, base_prefix does
    # not. This is what the tooling's launcher relies on.
    probe = subprocess.run(
        [venv_python, "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
        capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "True"
    # A SYMLINK to the shell's own interpreter, not a copy of it. The shell's
    # interpreter is built with Py_ENABLE_SHARED and resolves libpython through
    # a loader path relative to itself, so a copy inside the venv cannot find
    # that library and dies with the loader's exit status 127 before running
    # anything. This is exactly what broke CI on Linux while passing on the
    # machine the shell was built on, where a stale absolute rpath from the
    # build tree happened to still resolve.
    assert os.path.islink(venv_python) or os.path.islink(
        os.path.join(venv_dir, "bin", os.path.basename(sys._base_executable))
    ), "the venv interpreter must be a symlink, not a copy"
    linked = os.path.join(venv_dir, "bin", os.path.basename(sys._base_executable))
    assert os.path.realpath(linked) == os.path.realpath(sys._base_executable)

    # Built by the interpreter running this code - the one the shell bundles -
    # so no system python3 was involved.
    assert os.path.isfile(os.path.join(venv_dir, "pyvenv.cfg"))
    with open(os.path.join(venv_dir, "pyvenv.cfg"), encoding="utf-8") as cfg:
        assert os.path.dirname(sys.executable) in cfg.read()


def test_provisioning_installs_the_manifest_into_the_venv(
    migrator_data_path, monkeypatch
):
    """A release WITH a manifest has it installed, by the venv's own pip."""
    archive = _SOURCE_ARCHIVE + (
        ("Mysql-to-MariaDB-Migration-1.4.0-beta/orchestrator/", "", 0),
        (
            "Mysql-to-MariaDB-Migration-1.4.0-beta/orchestrator/requirements.txt",
            "# nothing to install, but the manifest exists\n",
            0,
        ),
    )
    _stub_download(monkeypatch, archive)
    target_dir = setup_migrator.download()

    calls = []
    real_run = subprocess.run

    def recording_run(command, **kwargs):
        calls.append((command, kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr(setup_migrator.subprocess, "run", recording_run)

    setup_migrator.provision(target_dir)

    # EnvBuilder(with_pip=True) shells out to ensurepip first, so the dependency
    # install is selected rather than assumed to be the only subprocess.
    installs = [
        (command, kwargs) for command, kwargs in calls
        if command[1:4] == ["-m", "pip", "install"]
    ]
    assert len(installs) == 1
    command, kwargs = installs[0]
    # The venv's interpreter, not this one and not a system python3.
    assert command[0] == setup_migrator._venv_python(target_dir)
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[-2:] == ["-r", os.path.join(
        target_dir, setup_migrator.MIGRATOR_REQUIREMENTS
    )]
    # --require-virtualenv so a broken venv path can never install into the
    # shell's own site-packages.
    assert "--require-virtualenv" in command
    assert kwargs["cwd"] == target_dir


def test_a_failed_dependency_install_is_reported_with_pips_own_words(
    migrator_data_path, monkeypatch
):
    """pip's diagnosis is surfaced rather than restated."""
    archive = _SOURCE_ARCHIVE + (
        ("Mysql-to-MariaDB-Migration-1.4.0-beta/orchestrator/", "", 0),
        (
            "Mysql-to-MariaDB-Migration-1.4.0-beta/orchestrator/requirements.txt",
            "nonexistent-package-that-will-never-resolve==1.0\n",
            0,
        ),
    )
    _stub_download(monkeypatch, archive)
    target_dir = setup_migrator.download()

    def failing_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="ERROR: No matching distribution found"
        )

    monkeypatch.setattr(setup_migrator.subprocess, "run", failing_run)

    with pytest.raises(mysqlsh.Error) as error:
        setup_migrator.provision(target_dir)

    assert "No matching distribution found" in str(error.value)


def test_the_wrapper_runs_the_install_from_its_own_directory(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """The generated wrapper cds in, activates the venv, and execs the launcher."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    path = setup_migrator.install_wrapper(target_dir)

    assert path == os.path.join(migrator_bin_home, general.MIGRATOR_DIR_NAME)
    assert os.access(path, os.X_OK)
    script = open(path, encoding="utf-8").read()

    assert script.startswith("#!/bin/sh\n")
    assert setup_migrator.MIGRATOR_WRAPPER_MARKER in script
    # The three things the wrapper exists to do.
    assert f'cd "$MIGRATOR_HOME"' in script
    assert f"{setup_migrator.MIGRATOR_VENV_DIR}/bin/activate" in script
    assert 'exec "$MIGRATOR_HOME/mariadb-migrator" "$@"' in script
    # It names the release it points at, and the install it points into.
    assert general.MIGRATOR_VERSION in script
    assert target_dir in script
    # Nothing left staged beside it.
    assert os.listdir(migrator_bin_home) == [general.MIGRATOR_DIR_NAME]


def test_the_wrapper_is_actually_runnable(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """Executed for real: it must reach the install's own entry point.

    The extracted entry point is a stub shell script, so what this proves is the
    wrapper's plumbing - the cd, the activation and the exec - rather than the
    tooling's behaviour.
    """
    entry = "#!/bin/sh\necho \"ran in $(pwd) with args: $*\"\n"
    archive = tuple(
        (name, entry, mode) if name.endswith("/mariadb-migrator") else (name, body, mode)
        for name, body, mode in _SOURCE_ARCHIVE
    )
    _stub_download(monkeypatch, archive)
    target_dir = setup_migrator.download()
    setup_migrator.provision(target_dir)
    path = setup_migrator.install_wrapper(target_dir)

    result = subprocess.run(
        [path, "plan", "--mode", "one_step"],
        capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    # Ran from the install directory, with the arguments passed straight through.
    assert f"ran in {os.path.realpath(target_dir)}" in result.stdout.replace(
        "/private/var", "/var"
    ) or f"ran in {target_dir}" in result.stdout
    assert "with args: plan --mode one_step" in result.stdout


def test_the_wrapper_replaces_one_pointing_at_an_older_release(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """A wrapper of ours is overwritten, not duplicated or left stale."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    old_target = setup_migrator.download()
    path = setup_migrator.install_wrapper(old_target)
    assert general.MIGRATOR_VERSION in open(path, encoding="utf-8").read()

    # The pin moves on, and the new release is installed and wrapped.
    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    new_target = setup_migrator.download()
    assert new_target != old_target

    new_path = setup_migrator.install_wrapper(new_target)

    assert new_path == path
    script = open(path, encoding="utf-8").read()
    assert new_target in script
    assert "v9.9.9" in script
    # The old release is no longer what the wrapper runs.
    assert old_target not in script
    assert os.listdir(migrator_bin_home) == [general.MIGRATOR_DIR_NAME]


def test_a_foreign_file_of_the_same_name_is_never_overwritten(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """Somebody else's mariadb-migrator on PATH is left exactly as it was."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()

    os.makedirs(migrator_bin_home, exist_ok=True)
    foreign = os.path.join(migrator_bin_home, general.MIGRATOR_DIR_NAME)
    with open(foreign, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n# somebody's own build\necho mine\n")
    os.chmod(foreign, 0o755)

    with pytest.raises(mysqlsh.Error) as error:
        setup_migrator.install_wrapper(target_dir)

    assert "was not created by mcp.setup" in str(error.value)
    # Untouched, contents and all.
    assert "somebody's own build" in open(foreign, encoding="utf-8").read()

    # And removal leaves it alone too, rather than deleting a stranger's file.
    assert setup_migrator.remove_wrapper() == ""
    assert os.path.exists(foreign)


def test_removing_the_tooling_takes_the_wrapper_with_it(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """A wrapper pointing at a tree that is gone would be worse than none."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()
    path = setup_migrator.install_wrapper(target_dir)
    assert os.path.exists(path)

    setup_migrator.remove()

    assert not os.path.exists(path)
    assert not os.path.exists(general.get_migrator_root())


def test_a_symlink_left_by_an_earlier_install_counts_as_ours(
    migrator_data_path, migrator_bin_home, monkeypatch
):
    """A symlink into the tooling root is replaced; one pointing away is not."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup_migrator.download()
    os.makedirs(migrator_bin_home, exist_ok=True)
    path = os.path.join(migrator_bin_home, general.MIGRATOR_DIR_NAME)

    # The other shape an earlier install could plausibly have left behind.
    os.symlink(os.path.join(target_dir, "mariadb-migrator"), path)
    assert setup_migrator._wrapper_is_ours(path) is True
    replaced = setup_migrator.install_wrapper(target_dir)
    assert replaced == path
    assert not os.path.islink(path)
    assert setup_migrator.MIGRATOR_WRAPPER_MARKER in open(path, encoding="utf-8").read()

    # A symlink to something outside the tooling root is somebody else's.
    os.remove(path)
    outside = os.path.join(migrator_data_path, "someone-elses-binary")
    with open(outside, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n")
    os.symlink(outside, path)
    assert setup_migrator._wrapper_is_ours(path) is False
    with pytest.raises(mysqlsh.Error):
        setup_migrator.install_wrapper(target_dir)


def test_an_unreadable_file_is_not_treated_as_ours(migrator_bin_home):
    """A name we cannot read is a name we must not overwrite."""
    os.makedirs(migrator_bin_home, exist_ok=True)
    # A directory is the simplest thing that is neither a symlink nor readable
    # as a file - open() raises OSError rather than returning contents.
    path = os.path.join(migrator_bin_home, general.MIGRATOR_DIR_NAME)
    os.makedirs(path)

    assert setup_migrator._wrapper_is_ours(path) is False


def test_the_path_hint_appears_only_when_the_bin_dir_is_not_on_path(
    migrator_bin_home, monkeypatch, capsys
):
    """Installed but unreachable by name looks like a failure unless it is said."""
    monkeypatch.setenv("PATH", migrator_bin_home)
    setup_migrator._print_path_hint()
    output = capsys.readouterr().out
    assert "is not on your PATH" not in output
    assert f"Run it as '{general.MIGRATOR_DIR_NAME}'." in output

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    setup_migrator._print_path_hint()
    output = capsys.readouterr().out
    assert "is not on your PATH" in output
    assert f'export PATH="{migrator_bin_home}:$PATH"' in output


def test_a_provisioning_failure_leaves_the_extracted_copy_and_says_so(
    migrator_data_path, migrator_bin_home, monkeypatch, capsys
):
    """The download is not undone by a failure in the step after it."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    monkeypatch.setattr(setup_prompts, "yes_no", lambda message, default=True: True)

    def failing_provision(target_dir):
        raise mysqlsh.Error("no space left on device")

    monkeypatch.setattr(setup_migrator, "provision", failing_provision)

    setup_migrator.manage()

    output = capsys.readouterr().out
    # mysqlsh.Error stringifies with a "Shell Error: " prefix, hence the split.
    assert "Could not prepare the virtual environment:" in output
    assert "no space left on device" in output
    # Still installed - just not yet runnable, which is what the message says.
    assert "The tooling itself is installed in" in output
    assert os.path.isfile(
        os.path.join(general.get_migrator_path(), "mariadb-migrator")
    )
    # And no wrapper was installed pointing at an unprovisioned copy.
    assert not os.path.exists(setup_migrator.wrapper_path())


def test_a_wrapper_failure_still_leaves_a_usable_install(
    migrator_data_path, migrator_bin_home, monkeypatch, capsys
):
    """Without the wrapper the tooling is still runnable, and the way is given."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    monkeypatch.setattr(setup_prompts, "yes_no", lambda message, default=True: True)
    monkeypatch.setattr(setup_migrator, "provision", lambda target_dir: "venv")

    def failing_wrapper(target_dir):
        raise mysqlsh.Error("permission denied")

    monkeypatch.setattr(setup_migrator, "install_wrapper", failing_wrapper)

    setup_migrator.manage()

    output = capsys.readouterr().out
    assert "Could not install the wrapper:" in output
    assert "permission denied" in output
    assert "cd " in output and "./mariadb-migrator" in output


def test_a_successful_download_reports_every_step(
    migrator_data_path, migrator_bin_home, monkeypatch, capsys
):
    """Download, venv and wrapper are each reported, in that order."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    monkeypatch.setattr(setup_prompts, "yes_no", lambda message, default=True: True)
    monkeypatch.setattr(setup_migrator, "provision", lambda target_dir: "the-venv")

    setup_migrator.manage()

    output = capsys.readouterr().out
    assert output.index("installed in") < output.index("Virtual environment ready")
    assert output.index("Virtual environment ready") < output.index("wrapper installed as")
    assert os.path.exists(setup_migrator.wrapper_path())
