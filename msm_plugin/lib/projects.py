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

"""The list of schema projects a user works with.

A schema project is a folder on disk; this module keeps the list of the ones
the user has added, so that a client such as the MariaDB VS Code extension
can show them without searching the disk. Each project is filed in a folder
of the list (``/Shop/Production``, not a directory), and may carry a caption
and a color, exactly as the MCP plugin keeps the details of a stored
database connection, so the same client code shows both.

Each project also says whether an LLM may work on it (``mcpAccess``): an MCP
server started without ``--gui`` - the one agents talk to - refuses every path
in or below a project that says no, and leaves it out of its listing, while
the ``--gui`` server of the VS Code extension works on all of them. A project
added through the extension starts with no access, one added anywhere else
with access; an entry that does not say (written before the setting existed)
has access, as every project had then.

The list is a JSON file in the plugin's data directory::

    {
      "version": 1,
      "projects": {
        "/Users/ada/work/shop.msm.project": {
          "folder": "/Shop", "caption": "Shop (prod)", "color": "green",
          "mcpAccess": false
        },
        "/Users/ada/work/notes.msm.project": {"mcpAccess": true}
      }
    }

keyed by the project folder's absolute path, each text detail written only
when set and ``mcpAccess`` always. ``projects.json`` is the shell user's own list. An MCP server serving
several users keeps one list per user, ``projects/<owner>.json``, where the
owner is the user's ``mcp_user_id`` (a UUID): the ``owner`` argument of every
function here selects it, and None is the shell user's list.

The file is the one record of which projects are listed, so a change lost to
a race would lose a project: every change takes an advisory lock on
``<file>.lock`` and replaces the file whole, through ``<file>.tmp`` and a
rename, so a reader never sees half of it.

This module imports nothing from the shell at module level, so the MCP plugin
imports it directly (as it does :mod:`msm_plugin.lib.source`) and passes the
owner, which the shell-facing functions in :mod:`msm_plugin.management` have no
business taking.
"""

import contextlib
import json
import os
import re
import sys
import unicodedata

# The file holding the shell user's list, in the plugin's data directory.
PROJECTS_FILE_NAME = "projects.json"

# The directory below it holding one list per owner, ``<owner>.json``.
OWNER_LISTS_DIR_NAME = "projects"

# The version of the file format, written into the file so a later format can
# tell what it is reading.
FILE_VERSION = 1

# The file a project folder is recognized by.
PROJECT_SETTINGS_FILE_NAME = "msm.project.json"

# The folder of the list a project is in when it is in none: the top level.
ROOT_FOLDER = "/"

# The colors a project can be shown in. Names rather than values, so each
# client maps them onto its own palette; the same six the MCP plugin gives a
# stored connection (``mcp_plugin.lib.config.CONNECTION_COLORS``), so a client
# shows both lists with one piece of code.
COLORS = ("red", "orange", "yellow", "green", "blue", "purple")

# The longest caption a project can carry.
MAX_CAPTION_LENGTH = 100

# The text details kept for a project, in the order they are reported.
DETAIL_FIELDS = ("folder", "caption", "color")

# The file's key for whether an LLM may work on the project, and what an entry
# that does not say means: access, as every project had before the setting.
MCP_ACCESS_FIELD = "mcpAccess"
DEFAULT_MCP_ACCESS = True

# An owner is a user's mcp_user_id: a canonical lower-case UUID, and nothing
# else, since it becomes a file name.
_OWNER_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# --- Where the lists are ---------------------------------------------------


def get_data_path() -> str:
    """Returns the plugin's data directory, creating it if needed.

    Imported here rather than at the top: :mod:`msm_plugin.lib.core` needs
    the shell, and this module is imported by the MCP plugin's tests as well.
    """
    from msm_plugin.lib import core

    return core.get_msm_plugin_data_path()


def normalize_owner(owner):
    """Returns an owner in the one form it names a list, or None for none.

    Args:
        owner: A user's ``mcp_user_id``, in any case, or None.

    Returns:
        The UUID in lower case, or None.

    Raises:
        ValueError: If it is not a UUID. An owner becomes a file name, so
            nothing else is accepted.
    """
    if owner is None:
        return None

    normalized = str(owner).strip().lower()
    if not _OWNER_PATTERN.match(normalized):
        raise ValueError(
            f"The owner of a project list must be a user id (a UUID), not "
            f"{owner!r}."
        )

    return normalized


def get_projects_file_path(owner=None) -> str:
    """Returns the file holding one owner's project list.

    Args:
        owner: The user whose list it is (see :func:`normalize_owner`), or
            None for the shell user's own.

    Returns:
        The path. The file need not exist yet.
    """
    owner = normalize_owner(owner)
    if owner is None:
        return os.path.join(get_data_path(), PROJECTS_FILE_NAME)

    return os.path.join(get_data_path(), OWNER_LISTS_DIR_NAME, f"{owner}.json")


# --- The one form of every value ------------------------------------------


def normalize_folder(folder) -> str:
    """Returns a list folder in the one form it is stored and reported in.

    The same rules as a stored connection's folder: ``/`` separates, the
    leading slash may be left out, empty elements - a doubled or a trailing
    slash - and blanks around a name are dropped, and None, ``""`` and ``/``
    all mean the top level.

    Args:
        folder: The folder, as a caller wrote it: ``/Shop/Production``.

    Returns:
        ``""`` for the top level, otherwise ``/`` followed by the folder
        names joined with ``/``.

    Raises:
        ValueError: If it is not a string.
    """
    if folder is None:
        return ""

    if not isinstance(folder, str):
        raise ValueError(
            f"The folder must be a string such as '/Shop', not {folder!r}."
        )

    elements = [element.strip() for element in folder.split("/")]

    return "".join(f"/{element}" for element in elements if element != "")


def normalize_caption(caption) -> str:
    """Returns a caption in the one form it is stored in.

    Args:
        caption: The caption, as a caller wrote it. None and blanks mean none.

    Returns:
        The caption without surrounding blanks, ``""`` for none.

    Raises:
        ValueError: If it is not a string, holds a line break or another
            control character, or is longer than :data:`MAX_CAPTION_LENGTH`.
    """
    if caption is None:
        return ""

    if not isinstance(caption, str):
        raise ValueError(f"The caption must be a string, not {caption!r}.")

    caption = caption.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in caption):
        raise ValueError("The caption must be a single line of text.")

    if len(caption) > MAX_CAPTION_LENGTH:
        raise ValueError(
            f"The caption may have at most {MAX_CAPTION_LENGTH} characters, "
            f"and it has {len(caption)}."
        )

    return caption


def normalize_color(color) -> str:
    """Returns a color in the one form it is stored in.

    Args:
        color: One of :data:`COLORS`, in any case. None and ``""`` mean none.

    Returns:
        The color name in lower case, ``""`` for none.

    Raises:
        ValueError: If it names no color in :data:`COLORS`.
    """
    if color is None:
        return ""

    normalized = str(color).strip().lower()
    if normalized and normalized not in COLORS:
        raise ValueError(
            f"'{color}' is not a color. Use one of: {', '.join(COLORS)}, or "
            "'' for none."
        )

    return normalized


def normalize_project_path(project_path) -> str:
    """Returns a project folder's path in the one form it is stored in.

    Args:
        project_path: The project folder, as a caller wrote it. ``~`` is the
            home directory, a relative path is taken from the current
            directory.

    Returns:
        The absolute path, without a trailing separator.

    Raises:
        ValueError: If it is not a non-empty string.
    """
    if not isinstance(project_path, str) or not project_path.strip():
        raise ValueError("A project_path has to be given.")

    return os.path.normpath(os.path.abspath(os.path.expanduser(project_path.strip())))


def _same_path(left, right) -> bool:
    """Returns whether two normalized paths name the same folder.

    Case does not count where the file system ignores it (see
    :func:`_folded`).
    """
    return _folded(left) == _folded(right)


def _ignores_case(path: str) -> bool:
    """Returns whether the file system holding a path ignores case.

    The deepest folder of the path that exists is asked: a file system that
    opens the same folder under its name in the other case ignores case. A
    name without letters tells nothing, so the folder above it is asked; the
    root of a disk, which has none, leaves it to the platform. This is the
    disk's answer, not ``os.path.normcase``'s, which folds case on Windows
    only: a macOS volume ignores case as well, by default.

    Args:
        path: An absolute path, with every link in it resolved.
    """
    folder = path
    while True:
        parent, name = os.path.split(folder)
        if parent == folder or not name:
            return sys.platform in ("darwin", "win32")
        swapped = name.swapcase()
        if os.path.lexists(folder) and swapped != name:
            try:
                return os.path.samefile(folder, os.path.join(parent, swapped))
            except OSError:
                return False
        folder = parent


def _folded(path: str) -> str:
    """Returns a normalized path in the one spelling it compares by.

    On a file system that ignores case, ``Shop.msm.project`` and
    ``SHOP.msm.project`` open the same folder, and so does a name in
    another Unicode normalization form on macOS: a comparison that kept the
    spelling would let the one that is not listed past a listed project
    that keeps to the VS Code extension. The path is folded there, and left
    as written where the file system tells the two apart.
    """
    if _ignores_case(path):
        return unicodedata.normalize("NFC", path).casefold()

    return path


def is_project_folder(project_path) -> bool:
    """Returns whether a folder is a schema project: it has a settings file."""
    return os.path.isfile(os.path.join(project_path, PROJECT_SETTINGS_FILE_NAME))


def read_schema_name(project_path):
    """Returns the schema name a project's settings file states, or None.

    None for a folder that is no project, or whose settings file cannot be
    read: the list still names the project, and a client shows what it can.
    """
    try:
        with open(
            os.path.join(project_path, PROJECT_SETTINGS_FILE_NAME), "r",
            encoding="utf-8"
        ) as settings_file:
            settings = json.load(settings_file)
    except (OSError, ValueError):
        return None

    schema_name = settings.get("schemaName") if isinstance(settings, dict) else None

    return schema_name if isinstance(schema_name, str) else None


def normalize_mcp_access(mcp_access) -> bool:
    """Returns whether an LLM may work on a project, as a bool.

    Raises:
        ValueError: If it is not a bool. ``"false"`` would be true, so a string
            is refused rather than guessed at.
    """
    if not isinstance(mcp_access, bool):
        raise ValueError(f"mcp_access must be true or false, not {mcp_access!r}.")

    return mcp_access


def normalize_details(folder, caption, color, mcp_access=None) -> dict:
    """Returns the details a caller gave, normalized; None ones left out.

    Raises:
        ValueError: If a detail is not valid.
    """
    changes = {}
    if mcp_access is not None:
        changes[MCP_ACCESS_FIELD] = normalize_mcp_access(mcp_access)
    if folder is not None:
        changes["folder"] = normalize_folder(folder)
    if caption is not None:
        changes["caption"] = normalize_caption(caption)
    if color is not None:
        changes["color"] = normalize_color(color)

    return changes


# --- The file ------------------------------------------------------------------


@contextlib.contextmanager
def _file_lock(path: str):
    """Holds an exclusive lock for a read-modify-write of one file.

    Advisory, on a lock file of its own (``<path>.lock``), so readers are
    never blocked. ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows.

    Args:
        path (str): The file being changed.

    Yields:
        None, while the lock is held.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(f"{path}.lock", "a+b") as lock_file:
        if sys.platform == "win32":  # pragma: no cover - Windows
            import msvcrt

            lock_file.seek(0)
            while True:
                try:
                    # LK_LOCK retries for about ten seconds before raising.
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_file(path: str, strict: bool) -> dict:
    """Returns the entries a list file holds: project path to its details.

    Args:
        path (str): The list file.
        strict (bool): Whether a file that cannot be read as a list is an
            error. A listing reads leniently - a damaged file lists nothing
            rather than failing every client - but a change must not replace
            a damaged file with the one entry it was asked to make.

    Returns:
        A dict of stored project path to its non-empty details, in the file's
        order.

    Raises:
        ValueError: If strict and the file is not a project list.
    """
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as list_file:
            content = json.load(list_file)
        if not isinstance(content, dict) or not isinstance(
            content.get("projects"), dict
        ):
            raise ValueError("it does not hold a project list")
    except (OSError, ValueError) as error:
        if strict:
            raise ValueError(
                f"The project list '{path}' cannot be read ({error}). Fix or "
                "remove the file."
            ) from None
        return {}

    return {
        project_path: _kept_details(entry)
        for project_path, entry in content["projects"].items()
        if isinstance(project_path, str) and isinstance(entry, dict)
    }


def _kept_details(entry: dict) -> dict:
    """Returns the details of a stored entry worth keeping: the known ones,
    the text ones only when set."""
    kept = {
        field: value
        for field, value in entry.items()
        if field in DETAIL_FIELDS and isinstance(value, str) and value
    }
    if isinstance(entry.get(MCP_ACCESS_FIELD), bool):
        kept[MCP_ACCESS_FIELD] = entry[MCP_ACCESS_FIELD]

    return kept


def _write_file(path: str, entries: dict) -> None:
    """Replaces a list file whole and atomically."""
    content = {
        "version": FILE_VERSION,
        "projects": {
            project_path: entries[project_path] for project_path in sorted(entries)
        },
    }
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as list_file:
        json.dump(content, list_file, indent=4)
    os.replace(temporary, path)


def _stored_key(entries: dict, project_path: str):
    """Returns the key a project is stored under, or None if it is not."""
    for stored in entries:
        if _same_path(stored, project_path):
            return stored

    return None


def _listed(project_path: str, entry: dict) -> dict:
    """Returns one project as the list reports it."""
    return {
        "project_path": project_path,
        "folder": entry.get("folder") or ROOT_FOLDER,
        "caption": entry.get("caption", ""),
        "color": entry.get("color", ""),
        "mcp_access": entry.get(MCP_ACCESS_FIELD, DEFAULT_MCP_ACCESS),
        "schema_name": read_schema_name(project_path),
        "exists": is_project_folder(project_path),
    }


# --- The list ------------------------------------------------------------------


def list_projects(owner=None, mcp_access_only=False) -> list:
    """Returns the projects of one list, with their details.

    Args:
        owner: The user whose list it is, or None for the shell user's own.
        mcp_access_only: Whether to leave out the projects an LLM may not
            work on, as the MCP server does for its agents.

    Returns:
        A list of dicts, sorted by project path: ``project_path``, the
        ``folder`` of the list it is filed in (:data:`ROOT_FOLDER` for the
        top level), its ``caption`` and ``color`` (``""`` for none), whether
        an LLM may work on it (``mcp_access``), the
        ``schema_name`` its settings file states (None where that cannot be
        read) and whether the folder still ``exists`` as a project - one
        that was moved or deleted stays listed, so a client can show it as
        missing and offer to remove it.
    """
    entries = _read_file(get_projects_file_path(owner), strict=False)
    return [
        _listed(project_path, entries[project_path])
        for project_path in sorted(entries)
        if not mcp_access_only
        or entries[project_path].get(MCP_ACCESS_FIELD, DEFAULT_MCP_ACCESS)
    ]


def _real_path(path: str) -> str:
    """Returns a path with every link in it resolved, folded where the file
    system ignores case (see :func:`_folded`).

    An empty path is the current directory, as it is to every tool that
    takes the path on to the file system: a check that skipped it would let
    ``""`` reach a restricted project the working directory is in.

    Raises:
        ValueError: If it is not a string.
    """
    if not isinstance(path, str):
        raise ValueError(f"A path has to be a string, not {path!r}.")

    return _folded(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def _is_within(path: str, folder: str) -> bool:
    """Returns whether a resolved path is a resolved folder or inside it."""
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        # Different drives on Windows.
        return False


def find_restricted_project(path, owner=None):
    """Returns the listed project an LLM may not work on that holds a path.

    Links are resolved on both sides, so a link pointing into a project does
    not get around it, and neither does a project listed through one.

    Args:
        path: A file or folder; it need not exist.
        owner: The user whose list it is, or None for the shell user's own.

    Returns:
        The project's path as listed, if ``path`` is it or is inside it and the
        project says no to ``mcpAccess``; None otherwise.
    """
    entries = _read_file(get_projects_file_path(owner), strict=False)
    restricted = [
        project_path
        for project_path, entry in entries.items()
        if not entry.get(MCP_ACCESS_FIELD, DEFAULT_MCP_ACCESS)
    ]
    # Most lists restrict nothing, so the path is only resolved for some.
    resolved = _real_path(path) if restricted else None
    for project_path in restricted:
        if _is_within(resolved, _real_path(project_path)):
            return project_path

    return None


def add_project(
    project_path,
    folder=None,
    caption=None,
    color=None,
    owner=None,
    mcp_access=None,
    default_mcp_access=DEFAULT_MCP_ACCESS,
) -> dict:
    """Adds an existing schema project to a list.

    Adding a project that is listed already changes the details given and
    keeps the others, so this is safe to call again.

    Args:
        project_path: The project folder (see :func:`normalize_project_path`).
            It has to be a schema project, i.e. hold a ``msm.project.json``.
        folder: The folder of the list to file it in (see
            :func:`normalize_folder`); None keeps what a listed project has,
            the top level for a new one.
        caption: The caption to show it with (see :func:`normalize_caption`);
            None keeps, ``""`` clears.
        color: The color to show it in (see :func:`normalize_color`); None
            keeps, ``""`` clears.
        owner: The user whose list it is, or None for the shell user's own.
        mcp_access: Whether an LLM may work on it; None keeps what a listed
            project has, ``default_mcp_access`` for a new one.
        default_mcp_access: What a new entry gets when ``mcp_access`` is None:
            access, unless the caller is the VS Code extension's server.

    Returns:
        The project as :func:`list_projects` reports it.

    Raises:
        ValueError: If the folder is no schema project, or a detail is not
            valid. Nothing is written then.
    """
    project_path = normalize_project_path(project_path)
    changes = normalize_details(folder, caption, color, mcp_access)
    if not is_project_folder(project_path):
        raise ValueError(
            f"The path {project_path} does not contain a MSM project folder."
        )

    return _change(
        owner,
        project_path,
        changes,
        must_be_listed=False,
        new_mcp_access=normalize_mcp_access(default_mcp_access),
    )


def update_project(
    project_path, folder=None, caption=None, color=None, owner=None, mcp_access=None
) -> dict:
    """Changes how a listed project is filed and shown.

    Args:
        project_path: The project folder, as it is listed. It does not have to
            exist on disk any more: a missing project can still be moved.
        folder: The folder of the list to move it to; None keeps the one it
            is in, ``""`` or ``/`` the top level.
        caption: The caption; None keeps, ``""`` clears.
        color: The color; None keeps, ``""`` clears.
        owner: The user whose list it is, or None for the shell user's own.
        mcp_access: Whether an LLM may work on it; None keeps.

    Returns:
        The project as :func:`list_projects` reports it.

    Raises:
        ValueError: If the project is not listed, or a detail is not valid.
            Nothing is written then.
    """
    project_path = normalize_project_path(project_path)
    changes = normalize_details(folder, caption, color, mcp_access)

    return _change(owner, project_path, changes, must_be_listed=True)


def _change(owner, project_path, changes, must_be_listed, new_mcp_access=None) -> dict:
    """Applies normalized detail changes to one project under the lock.

    ``new_mcp_access`` is what a project not listed yet gets unless the
    changes say; every entry written says, so the file shows the setting."""
    path = get_projects_file_path(owner)
    with _file_lock(path):
        entries = _read_file(path, strict=True)
        stored = _stored_key(entries, project_path)
        if stored is None and must_be_listed:
            raise ValueError(f"The project {project_path} is not in the project list.")

        key = stored if stored is not None else project_path
        entry = dict(entries.get(key, {}))
        entry.setdefault(
            MCP_ACCESS_FIELD,
            new_mcp_access if stored is None and new_mcp_access is not None
            else DEFAULT_MCP_ACCESS,
        )
        entry.update(changes)
        entry = _kept_details(entry)
        if stored is None or entry != entries[key]:
            entries[key] = entry
            _write_file(path, entries)

    return _listed(key, entry)


def remove_project(project_path, owner=None) -> str:
    """Takes a project off a list. The folder on disk is left as it is.

    Args:
        project_path: The project folder, as it is listed. It does not have to
            exist on disk any more.
        owner: The user whose list it is, or None for the shell user's own.

    Returns:
        The project path as it was listed.

    Raises:
        ValueError: If the project is not listed.
    """
    project_path = normalize_project_path(project_path)

    path = get_projects_file_path(owner)
    with _file_lock(path):
        entries = _read_file(path, strict=True)
        stored = _stored_key(entries, project_path)
        if stored is None:
            raise ValueError(f"The project {project_path} is not in the project list.")

        del entries[stored]
        _write_file(path, entries)

    return stored


def remove_owner(owner) -> None:
    """Deletes a user's list, when the user is removed. Nothing if there is none.

    Args:
        owner: The user whose list it is (see :func:`normalize_owner`).

    Returns:
        None
    """
    owner = normalize_owner(owner)
    if owner is None:
        raise ValueError("The owner of the list to remove has to be given.")

    path = get_projects_file_path(owner)
    for file in (path, f"{path}.tmp", f"{path}.lock"):
        try:
            os.remove(file)
        except FileNotFoundError:
            pass
