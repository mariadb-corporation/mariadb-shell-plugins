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

"""Interactive setup for the MariaDB MCP Server Plugin.

Guides the user through configuring the MariaDB connections and the local
directories the MCP server is allowed to access, and installing (or removing)
the MySQL-to-MariaDB migration tooling. Connections are verified with
``shell.open_session`` before their password is stored (see
:mod:`mcp_plugin.lib.config`); the migration tooling lives in the plugin data
directory (see :func:`download_migrator` and :func:`remove_migrator`), and it is
offered from the management menu only, never from the first run.
"""

# cSpell:ignore mysqlsh MariaDB urllib

import os
import shutil
import tempfile
import urllib.request
import zipfile

import mysqlsh

from mcp_plugin.lib import config, general


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def _prompt(message: str, options: dict = None) -> str:
    """Prompts the user for input, returning the entered (stripped) string."""
    return _shell().prompt(message, options if options is not None else {}).strip()


def _prompt_password(message: str) -> str:
    """Prompts the user for a password without echoing it."""
    return _shell().prompt(message, {"type": "password"})


def _prompt_yes_no(message: str, default: bool = True) -> bool:
    """Prompts the user for a yes/no answer.

    Args:
        message (str): The question to ask.
        default (bool): The answer to use when the user just presses Enter.

    Returns:
        The user's answer as a boolean.
    """
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = _prompt(message + suffix).lower()
    if answer == "":
        return default
    return answer in ("y", "yes")


def _select_index(message: str, count: int) -> int:
    """Prompts the user to pick an item number in the range 1..count.

    Args:
        message (str): The prompt message.
        count (int): The number of items to choose from.

    Returns:
        The selected zero-based index, or -1 if the user cancelled.
    """
    while True:
        answer = _prompt(message + " (or leave empty to cancel): ")
        if answer == "":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        print(f"Please enter a number between 1 and {count}.")


# --- Connections -----------------------------------------------------------


def _print_connections() -> list:
    """Prints the configured connections and returns them."""
    connections = config.list_connection_uris()
    if connections:
        print("\nConfigured connections:")
        for index, uri in enumerate(connections, start=1):
            print(f"  {index}. {uri}")
    else:
        print("\nNo connections configured yet.")
    return connections


def _add_connection() -> None:
    """Prompts for a connection URI and password, verifies and stores it.

    What is stored is the normalized URI rather than what was typed: it is the
    key the connection is then looked up under, and one canonical spelling per
    connection is what keeps the same connection from being configured twice
    over (see :func:`mcp_plugin.lib.config.normalize_connection_uri`).
    """
    entered_uri = _prompt(
        "Enter the MariaDB connection URI (e.g. user@host:3306): "
    )
    if entered_uri == "":
        return

    uri = config.normalize_connection_uri(entered_uri)
    if uri is None:
        print(f"'{entered_uri}' is not a valid connection URI.")
        print("The connection was not stored.")
        return

    if uri != entered_uri:
        print(f"The connection will be stored as '{uri}'.")

    password = _prompt_password(f"Enter the password for '{uri}': ")

    # Verify the credentials by opening (and immediately closing) a session.
    try:
        connection_data = _shell().parse_uri(uri)
        connection_data["password"] = password
        session = _shell().open_session(connection_data)
        session.close()
    except Exception as error:  # noqa: BLE001 - surface any connection failure
        print(f"Could not connect to '{uri}': {error}")
        print("The connection was not stored.")
        return

    config.store_connection(uri, password)
    print(f"Connection '{uri}' verified and stored.")


def _delete_connection() -> None:
    """Prompts the user to delete one of the configured connections."""
    connections = _print_connections()
    if not connections:
        return

    index = _select_index("Enter the number of the connection to delete", len(connections))
    if index < 0:
        return

    uri = connections[index]
    config.delete_connection(uri)
    print(f"Connection '{uri}' deleted.")


# --- Allowed paths ---------------------------------------------------------


def _print_paths() -> list:
    """Prints the configured allowed paths and returns them."""
    paths = config.get_allowed_paths()
    if paths:
        print("\nAllowed paths:")
        for index, path in enumerate(paths, start=1):
            print(f"  {index}. {path}")
    else:
        print("\nNo allowed paths configured yet.")
    return paths


def _add_path() -> None:
    """Prompts for a directory to allow, defaulting to the current directory."""
    default_path = os.path.abspath(os.getcwd())
    entered = _prompt(
        f"Enter a directory the MCP server may access (default: {default_path}): "
    )
    path = os.path.abspath(os.path.expanduser(entered)) if entered else default_path

    if not os.path.isdir(path):
        print(f"'{path}' is not an existing directory. It was not added.")
        return

    paths = config.get_allowed_paths()
    if path in paths:
        print(f"'{path}' is already allowed.")
        return

    paths.append(path)
    config.set_allowed_paths(paths)
    print(f"Allowed path '{path}' added.")


def _delete_path() -> None:
    """Prompts the user to delete one of the allowed paths."""
    paths = _print_paths()
    if not paths:
        return

    index = _select_index("Enter the number of the path to delete", len(paths))
    if index < 0:
        return

    path = paths.pop(index)
    config.set_allowed_paths(paths)
    print(f"Allowed path '{path}' deleted.")


# --- MySQL-to-MariaDB migration tooling ------------------------------------

# Where the migration tooling is fetched from: the source archive GitHub builds
# for a release tag, the tag being configured as
# :data:`mcp_plugin.lib.general.MIGRATOR_VERSION`. The archive contains a single
# top-level directory named after the project and the release, which is stripped
# on extraction, so the tooling's own layout (``mariadb-migrator``, ``scripts/``,
# ``sql/``, ...) ends up directly inside the target directory.
MIGRATOR_ARCHIVE_URL_TEMPLATE = (
    "https://github.com/mariadb-corporation/Mysql-to-MariaDB-Migration"
    "/archive/refs/tags/{version}.zip"
)

# How long (in seconds) to wait for the download before giving up. Generous
# enough for a slow link and short enough that an unreachable host does not look
# like a hung shell.
MIGRATOR_DOWNLOAD_TIMEOUT = 120

# Name of the file the installed release is recorded in, written inside the
# tooling directory. Without it a copy installed before the configured version
# was bumped is indistinguishable from one installed after it, so the setup could
# not tell the user WHICH release they have - only that they have one.
MIGRATOR_VERSION_FILE = ".migrator-version"


def migrator_archive_url() -> str:
    """Returns the URL the configured migration tooling release is fetched from.

    Returns:
        The download URL for :data:`mcp_plugin.lib.general.MIGRATOR_VERSION`.
    """
    return MIGRATOR_ARCHIVE_URL_TEMPLATE.format(version=general.MIGRATOR_VERSION)


def installed_migrator_version() -> str:
    """Returns the release of the installed migration tooling.

    Returns:
        The recorded version, or an empty string when nothing is installed or
        the installed copy has no version recorded.
    """
    try:
        with open(
            os.path.join(general.get_migrator_path(), MIGRATOR_VERSION_FILE),
            "r",
            encoding="utf-8",
        ) as version_file:
            return version_file.read().strip()
    except OSError:
        # Not installed, or installed without the version being recorded.
        return ""


def _download_archive(url: str, archive_path: str) -> None:
    """Downloads the given URL to a local file.

    Args:
        url (str): The URL to download.
        archive_path (str): The file to write the response body to.

    Returns:
        None
    """
    with urllib.request.urlopen(url, timeout=MIGRATOR_DOWNLOAD_TIMEOUT) as response:
        with open(archive_path, "wb") as archive_file:
            shutil.copyfileobj(response, archive_file)


def _archive_prefix(members: list) -> str:
    """Returns the single top-level directory of an archive, if it has one.

    A source archive wraps everything in one directory named after the project
    and its branch, which is not part of the tooling's own layout and would
    otherwise have to be named by every caller. Stripping it is only safe when
    there really is just the one, so anything else yields no prefix.

    Args:
        members (list): The archive's :class:`zipfile.ZipInfo` entries.

    Returns:
        The prefix to strip, including its trailing slash, or an empty string.
    """
    top_level = {member.filename.split("/", 1)[0] for member in members if member.filename}
    nested = any("/" in member.filename.rstrip("/") for member in members)
    if len(top_level) == 1 and nested:
        return next(iter(top_level)) + "/"

    return ""


def _archive_destination(target_dir: str, relative_path: str) -> str:
    """Returns where an archive entry may be written, refusing to escape.

    An archive is remote input, and an entry naming ``../`` or an absolute path
    would have the extraction write outside the directory it was asked to fill.

    Args:
        target_dir (str): The directory being extracted into.
        relative_path (str): The entry's path within the archive.

    Returns:
        The absolute path to write the entry to.
    """
    base = os.path.abspath(target_dir)
    destination = os.path.abspath(os.path.join(base, relative_path))
    if destination != base and os.path.commonpath([base, destination]) != base:
        raise mysqlsh.Error(
            f"The downloaded archive contains an entry ('{relative_path}') that "
            "would be written outside the target directory. It was not extracted."
        )

    return destination


def _extract_archive(archive_path: str, target_dir: str) -> None:
    """Extracts a downloaded archive into the given directory.

    The archive's single top-level directory is stripped (see
    :func:`_archive_prefix`) and the recorded file modes are restored, which
    :meth:`zipfile.ZipFile.extractall` does not do - the migration tooling is a
    set of shell scripts plus the ``mariadb-migrator`` entry point, so without
    the executable bit nothing that was extracted can be run.

    Args:
        archive_path (str): The downloaded archive.
        target_dir (str): The directory to extract into; created if needed.

    Returns:
        None
    """
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        prefix = _archive_prefix(members)

        os.makedirs(target_dir, exist_ok=True)
        for member in members:
            if not member.filename.startswith(prefix):
                continue

            relative_path = member.filename[len(prefix):]
            if not relative_path:
                continue

            destination = _archive_destination(target_dir, relative_path)
            if member.is_dir():
                os.makedirs(destination, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(member) as source:
                with open(destination, "wb") as extracted:
                    shutil.copyfileobj(source, extracted)

            mode = (member.external_attr >> 16) & 0o777
            if mode:
                os.chmod(destination, mode)


def download_migrator() -> str:
    """Downloads the MySQL-to-MariaDB migration tooling into the plugin data dir.

    The configured release (see :func:`migrator_archive_url`) is fetched and
    extracted into :func:`mcp_plugin.lib.general.get_migrator_path`, replacing
    whatever is installed there, and the release is recorded in the installed
    copy. The new copy is extracted next to the old one and only swapped in once
    it is complete, so a download that fails part-way through - or an archive
    that turns out to contain something it should not - leaves a previously
    installed copy exactly as it was.

    Returns:
        The directory the tooling was installed in.
    """
    target_dir = general.get_migrator_path()
    staging_dir = target_dir + ".new"
    previous_dir = target_dir + ".old"

    # Anything left behind by an interrupted earlier run.
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(previous_dir, ignore_errors=True)

    # Downloaded inside the plugin data directory rather than into the system
    # temp directory: it is the directory the plugin already owns, and it is on
    # the same filesystem as the target, so nothing has to be copied across
    # devices to get there.
    with tempfile.TemporaryDirectory(dir=general.get_plugin_data_path()) as download_dir:
        archive_path = os.path.join(download_dir, "migrator.zip")
        try:
            _download_archive(migrator_archive_url(), archive_path)
            _extract_archive(archive_path, staging_dir)
            # Recorded before the swap, so an installed copy always has its
            # version beside it rather than for a moment without.
            with open(
                os.path.join(staging_dir, MIGRATOR_VERSION_FILE),
                "w",
                encoding="utf-8",
            ) as version_file:
                version_file.write(general.MIGRATOR_VERSION + "\n")
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

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

    return target_dir


def remove_migrator() -> str:
    """Removes the installed MySQL-to-MariaDB migration tooling.

    Anything an interrupted download left beside the installed copy goes with
    it, so that removing really does leave the plugin data directory as it was
    before the tooling was ever installed.

    Args:
        None

    Returns:
        The directory that was removed, whether or not it existed.
    """
    target_dir = general.get_migrator_path()
    if os.path.isdir(target_dir):
        shutil.rmtree(target_dir)

    shutil.rmtree(target_dir + ".new", ignore_errors=True)
    shutil.rmtree(target_dir + ".old", ignore_errors=True)

    return target_dir


def _migrator_is_installed() -> bool:
    """Returns whether the migration tooling is installed.

    This is the whole of what the setup offers to do with the tooling: install
    it, or remove what is installed. There is deliberately no update - a release
    other than the configured one is removed and downloaded again, which is two
    menu choices rather than a third code path for something that hardly comes up.

    Returns:
        True when a copy is installed, whichever release it is.
    """
    return os.path.isdir(general.get_migrator_path())


def _migrator_menu_label() -> str:
    """Returns the menu entry for whatever the migration tooling now needs."""
    if _migrator_is_installed():
        return "Remove the MySQL-to-MariaDB migration tooling"

    return (
        "Download the MySQL-to-MariaDB migration tooling "
        f"({general.MIGRATOR_VERSION})"
    )


def _print_migrator() -> bool:
    """Prints which migration tooling is installed and returns whether any is."""
    target_dir = general.get_migrator_path()
    installed = os.path.isdir(target_dir)
    if not installed:
        print(
            "\nMigration tooling not downloaded yet "
            f"(configured release: {general.MIGRATOR_VERSION})."
        )
        return False

    version = installed_migrator_version() or "unknown version"
    print(f"\nMigration tooling ({version}) installed in: {target_dir}")
    if version != general.MIGRATOR_VERSION:
        # There is no update step, so say what installing the configured release
        # takes rather than leaving the difference as something to puzzle over.
        print(
            f"  Configured release: {general.MIGRATOR_VERSION} - remove the "
            "installed one and download it again to change that."
        )

    return True


def _manage_migrator() -> None:
    """Downloads the migration tooling, or removes what is installed.

    Which of the two it is follows from what is installed: there is nothing to
    download while a copy is there, so what the setup offers then is removing
    it. Installing a different release means removing this one and downloading
    again.
    """
    _print_migrator()

    if _migrator_is_installed():
        # Deleting is the one thing that cannot be undone by running the setup
        # again, so it is also the one question that does not suggest going ahead.
        if not _prompt_yes_no(
            f"Remove the migration tooling from '{general.get_migrator_path()}'?",
            default=False,
        ):
            return

        try:
            removed_dir = remove_migrator()
        except OSError as error:
            print(f"Could not remove the migration tooling: {error}")
            return

        print(f"Migration tooling removed from '{removed_dir}'.")
        return

    if not _prompt_yes_no(
        "Download the MySQL-to-MariaDB migration tooling "
        f"{general.MIGRATOR_VERSION}?",
        default=True,
    ):
        return

    print(f"Downloading {migrator_archive_url()} ...")
    try:
        target_dir = download_migrator()
    except Exception as error:  # noqa: BLE001 - surface any download failure
        print(f"Could not install the migration tooling: {error}")
        return

    print(f"Migration tooling {general.MIGRATOR_VERSION} installed in '{target_dir}'.")


# --- Entry points ----------------------------------------------------------


def _first_run() -> None:
    """Guided first-run configuration: add connections, then allowed paths.

    The migration tooling is deliberately NOT part of this: it is a download the
    plugin does not need in order to serve anything, so it stays a step the user
    goes and asks for from the menu rather than one the first run walks into.
    """
    print("Let's configure the MariaDB connections the MCP server may use.")
    while _prompt_yes_no("Add a connection?", default=True):
        _add_connection()

    print(
        "\nNow choose the local directories the MCP server is allowed to access."
    )
    while _prompt_yes_no("Add an allowed path?", default=True):
        _add_path()

    # Ensure a settings file exists so subsequent runs use the management menu.
    config.set_allowed_paths(config.get_allowed_paths())


def _menu() -> None:
    """Management menu: connections, allowed paths and the migration tooling."""
    actions = {
        "1": _add_connection,
        "2": _delete_connection,
        "3": _add_path,
        "4": _delete_path,
        "5": _manage_migrator,
    }
    while True:
        _print_connections()
        _print_paths()
        _print_migrator()
        print(
            "\nWhat would you like to do?\n"
            "  1. Add a connection\n"
            "  2. Delete a connection\n"
            "  3. Add an allowed path\n"
            "  4. Delete an allowed path\n"
            f"  5. {_migrator_menu_label()}\n"
            "  6. Finish"
        )
        choice = _prompt("Enter your choice: ")
        if choice == "6" or choice == "":
            break
        action = actions.get(choice)
        if action is None:
            print("Please enter a number between 1 and 6.")
            continue
        action()


def run_setup() -> None:
    """Runs the interactive MCP server setup.

    Returns:
        None
    """
    if not _shell().options.useWizards:
        raise mysqlsh.Error(
            "mcp.setup must be run from an interactive shell session."
        )

    print("=== MariaDB MCP Server setup ===")
    print(f"Configuration is stored in: {general.get_plugin_data_path()}")

    if config.settings_file_exists() or config.list_connection_uris():
        _menu()
    else:
        _first_run()

    print("\nSetup complete.")
