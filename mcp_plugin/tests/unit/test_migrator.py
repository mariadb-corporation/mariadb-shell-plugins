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

Covers lib/setup.py's download/extract path and lib/general.py's
``get_migrator_path``. The download itself is stubbed out - the archive is built
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
import zipfile

import pytest

import mysqlsh

from mcp_plugin.lib import general, setup


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
    """Points the plugin data path at a temp directory for the test.

    Both the download target and the temp directory the archive is downloaded
    into are derived from it, so this keeps the whole install out of the real
    plugin data directory.

    Yields:
        The absolute path of the stand-in plugin data directory.
    """
    data_path = tmp_path / "plugin_data"
    data_path.mkdir()
    monkeypatch.setattr(general, "get_plugin_data_path", lambda: str(data_path))
    yield str(data_path)


def _stub_download(monkeypatch, entries):
    """Makes the download write a locally built archive instead of fetching one."""
    def fake_download(url, archive_path):
        _write_archive(archive_path, entries)

    monkeypatch.setattr(setup, "_download_archive", fake_download)


def test_get_migrator_path_does_not_create_the_directory(migrator_data_path):
    """The tooling directory sits in the plugin data path and is not created."""
    target_dir = general.get_migrator_path()

    assert target_dir == os.path.join(migrator_data_path, general.MIGRATOR_DIR_NAME)
    # Its existence is what says whether the tooling has been downloaded, so
    # merely asking for the path must not answer that question with a yes.
    assert not os.path.exists(target_dir)


def test_downloading_installs_the_tooling(migrator_data_path, monkeypatch):
    """The archive lands in mariadb-migrator, unwrapped and still executable."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)

    target_dir = setup.download_migrator()

    assert target_dir == general.get_migrator_path()
    assert os.path.basename(target_dir) == "mariadb-migrator"

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

    # The installed release is recorded, so a copy installed before the
    # configured version was bumped can be told from one installed after it.
    assert setup.installed_migrator_version() == general.MIGRATOR_VERSION

    # What was executable in the archive is executable on disk - extractall()
    # would have dropped this, and the tooling is scripts.
    assert os.access(entry_point, os.X_OK)
    assert os.access(script, os.X_OK)
    assert not os.access(os.path.join(target_dir, "README.md"), os.X_OK)

    # A directory that holds no files comes across as its own archive entry.
    assert os.path.isdir(os.path.join(target_dir, "logs"))

    # Nothing is left beside the installed copy.
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]


def test_only_a_single_wrapping_directory_is_stripped(migrator_data_path, monkeypatch):
    """An archive that is not wrapped in one directory is extracted as it is.

    The negative control for the stripping: an archive whose only entry is a
    top-level FILE would lose that file to a strip, and one with several
    top-level directories would lose all but an arbitrary one of them.
    """
    _stub_download(monkeypatch, (("mariadb-migrator", "#!/bin/sh\n", 0o755),))

    target_dir = setup.download_migrator()
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    _stub_download(
        monkeypatch,
        (
            ("scripts/00_precheck.sh", "#!/bin/sh\n", 0o755),
            ("sql/precheck.sql", "SELECT 1;\n", 0),
        ),
    )

    setup.download_migrator()
    assert os.path.isfile(os.path.join(target_dir, "scripts", "00_precheck.sh"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))


def test_downloading_again_replaces_the_installed_copy(migrator_data_path, monkeypatch):
    """A second download is the new version, not the two versions merged."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup.download_migrator()

    _stub_download(
        monkeypatch,
        (
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/mariadb-migrator", "#!/bin/sh\n# v2\n", 0o755),
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/scripts/00_precheck.sh", "#!/bin/sh\n", 0o755),
        ),
    )
    setup.download_migrator()

    with open(os.path.join(target_dir, "mariadb-migrator"), encoding="utf-8") as script:
        assert "# v2" in script.read()
    # Files the new version does not have are gone, rather than left behind from
    # the version before it.
    assert not os.path.exists(os.path.join(target_dir, "README.md"))
    assert not os.path.exists(os.path.join(target_dir, "sql", "precheck.sql"))

    # The staging and backup directories the swap uses are cleaned up again.
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]


def test_a_failed_download_keeps_the_installed_copy(migrator_data_path, monkeypatch):
    """An install that was working is not lost to a download that fails."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup.download_migrator()

    def failing_download(url, archive_path):
        # Write a partial archive first, so the failure is one that happens with
        # something already on disk rather than with nothing.
        _write_archive(archive_path, _SOURCE_ARCHIVE[:2])
        raise OSError("the network went away")

    monkeypatch.setattr(setup, "_download_archive", failing_download)

    with pytest.raises(OSError):
        setup.download_migrator()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]


def test_the_url_names_the_configured_release(monkeypatch):
    """The download URL is built from the version configured in lib/general.py."""
    assert general.MIGRATOR_VERSION in setup.migrator_archive_url()
    assert setup.migrator_archive_url().endswith(f"{general.MIGRATOR_VERSION}.zip")

    # Bumping the setting is all there is to installing another release, so the
    # URL has to follow it rather than having been fixed at import time.
    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    assert setup.migrator_archive_url().endswith("/refs/tags/v9.9.9.zip")


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
        setup, "migrator_archive_url", lambda: pathlib.Path(archive_path).as_uri()
    )

    target_dir = setup.download_migrator()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "scripts", "00_precheck.sh"))
    # The archive is not left behind in the plugin data directory.
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]


def test_a_failed_swap_puts_the_installed_copy_back(migrator_data_path, monkeypatch):
    """Failing to move the new copy into place restores the old one."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup.download_migrator()

    real_rename = os.rename

    def failing_rename(source, destination):
        # Moving the freshly extracted copy into place is the one that fails;
        # moving the installed copy out of the way, and back, must not.
        if str(source).endswith(".new"):
            raise OSError("could not rename the new copy into place")
        return real_rename(source, destination)

    monkeypatch.setattr(os, "rename", failing_rename)

    with pytest.raises(OSError):
        setup.download_migrator()

    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))
    assert os.path.isfile(os.path.join(target_dir, "sql", "precheck.sql"))
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]


def test_the_setup_option_follows_what_is_installed(migrator_data_path, monkeypatch):
    """Download when nothing is installed, remove when something is - only those."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    answers = []
    asked = []

    def fake_prompt(message, default=True):
        asked.append((message, default))
        return answers.pop(0)

    monkeypatch.setattr(setup, "_prompt_yes_no", fake_prompt)
    target_dir = general.get_migrator_path()

    # Nothing installed: a download is offered, and declining it downloads
    # nothing.
    assert "Download" in setup._migrator_menu_label()
    answers.append(False)
    setup._manage_migrator()
    assert not os.path.exists(target_dir)
    assert "Download" in asked[-1][0] and asked[-1][1] is True

    answers.append(True)
    setup._manage_migrator()
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    # Something installed: there is nothing left to download and what is offered
    # instead is removing it - and, being the one step that running the setup
    # again cannot undo, offered without suggesting it.
    assert setup._migrator_menu_label() == "Remove the MySQL-to-MariaDB migration tooling"
    answers.append(False)
    setup._manage_migrator()
    assert "Remove" in asked[-1][0] and asked[-1][1] is False
    # Declined, so it is still installed.
    assert os.path.isfile(os.path.join(target_dir, "mariadb-migrator"))

    # Another release being configured does NOT turn the option into an update:
    # a different release is removed and downloaded again.
    monkeypatch.setattr(general, "MIGRATOR_VERSION", "v9.9.9")
    assert setup._migrator_menu_label() == "Remove the MySQL-to-MariaDB migration tooling"
    answers.append(True)
    setup._manage_migrator()
    assert "Remove" in asked[-1][0]
    # Removing leaves the plugin data directory as it was before any of this.
    assert not os.path.exists(target_dir)
    assert os.listdir(migrator_data_path) == []

    # And with it gone, the configured release is what a download installs.
    answers.append(True)
    setup._manage_migrator()
    assert "v9.9.9" in asked[-1][0]
    assert setup.installed_migrator_version() == "v9.9.9"


def test_removing_the_tooling_takes_any_leftovers_with_it(migrator_data_path, monkeypatch):
    """Removal also clears what an interrupted download may have left behind."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup.download_migrator()
    os.makedirs(target_dir + ".new")
    os.makedirs(target_dir + ".old")

    assert setup.remove_migrator() == target_dir
    assert os.listdir(migrator_data_path) == []

    # Removing what is not there is not an error - the setup only offers it for
    # an installed copy, but nothing about it depends on that.
    assert setup.remove_migrator() == target_dir


def test_a_failed_download_is_reported_and_not_raised(
    migrator_data_path, monkeypatch, capsys
):
    """A download that fails must not abort the setup the user is in the middle of."""
    def failing_download(url, archive_path):
        raise OSError("the network went away")

    monkeypatch.setattr(setup, "_download_archive", failing_download)
    monkeypatch.setattr(setup, "_prompt_yes_no", lambda message, default=True: True)

    setup._manage_migrator()

    output = capsys.readouterr().out
    assert "Could not install the migration tooling" in output
    assert "the network went away" in output
    assert not os.path.exists(general.get_migrator_path())


def test_a_failed_removal_is_reported_and_not_raised(
    migrator_data_path, monkeypatch, capsys
):
    """Nor must a removal that fails - the rest of the setup is still reachable."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    setup.download_migrator()

    def failing_remove():
        raise OSError("permission denied")

    monkeypatch.setattr(setup, "remove_migrator", failing_remove)
    monkeypatch.setattr(setup, "_prompt_yes_no", lambda message, default=True: True)

    setup._manage_migrator()

    output = capsys.readouterr().out
    assert "Could not remove the migration tooling" in output
    assert "permission denied" in output


def test_a_copy_without_a_recorded_version_is_reported_as_unknown(
    migrator_data_path, capsys
):
    """A copy installed before the version was recorded still reads as installed."""
    os.makedirs(general.get_migrator_path())

    assert setup.installed_migrator_version() == ""
    assert setup._print_migrator() is True
    # Installed is installed, whichever release it is: removing is what is
    # offered, there being no update step.
    assert "Remove" in setup._migrator_menu_label()

    output = capsys.readouterr().out
    assert "unknown version" in output
    # The configured release is named too, along with what installing it takes,
    # since nothing here will do it in one step.
    assert general.MIGRATOR_VERSION in output
    assert "remove the installed one and download it again" in output


def test_an_archive_cannot_write_outside_the_target_directory(
    migrator_data_path, monkeypatch
):
    """A path-traversal entry is refused, and refused before anything is replaced."""
    _stub_download(monkeypatch, _SOURCE_ARCHIVE)
    target_dir = setup.download_migrator()

    _stub_download(
        monkeypatch,
        (
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/mariadb-migrator", "#!/bin/sh\n", 0o755),
            ("Mysql-to-MariaDB-Migration-1.4.0-beta/../escaped.txt", "owned\n", 0),
        ),
    )

    with pytest.raises(mysqlsh.Error):
        setup.download_migrator()

    assert not os.path.exists(os.path.join(migrator_data_path, "escaped.txt"))
    # The copy that was installed is untouched, and no half-extracted one is left.
    assert os.path.isfile(os.path.join(target_dir, "README.md"))
    assert sorted(os.listdir(migrator_data_path)) == ["mariadb-migrator"]
