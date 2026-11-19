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

"""Dumps as single ZIP files: ``<name>.mariadb.dump.zip``.

The shell's dump and load utilities work with folders. The util tools take a
file name ending in :data:`ZIP_SUFFIX` as well:

- **A dump** goes to a folder next to the zip (:func:`dump_directory`), which
  :func:`pack` then puts into the zip, file by file, deleting each file once
  it is in. The entries are stored, not compressed again: the data files are
  zstd already. The order is ``@.json``, the other metadata, the data and
  ``@.done.json`` last.
- **A load** extracts into a folder next to the zip
  (:func:`extraction_directory`) while the shell loads from it, as a load
  from a dump that is still being written (``waitDumpTimeout``). The
  :class:`Extractor` writes the metadata first, then the data chunks in turns
  across the tables, each table's in order, and ``@.done.json`` last, when
  the loader takes the dump as complete. It keeps at most a budget of bytes
  extracted but not loaded on disk, and empties a chunk (truncates it to 0
  bytes) once the load's progress file says it was loaded. So a load needs
  the zip plus the budget on disk, not twice the dump.

Why a loaded chunk is emptied and not deleted: a load resumed after a crash
finds the chunks it loaded by their names, and skips them by the progress
file without opening them; a chunk that is gone leaves its table waiting for
it. The folder keeps a log of its own (:data:`STATE_FILE`) of what was
extracted and emptied, and a load of the same zip resumes there. Emptied
chunks hold no data any more, so they may only ever be seen through the
progress file that says they are loaded: a load on a folder that has them is
given that progress file, and the shell refuses it for another server.

The data of a table the load's filters leave out is never extracted: its
chunks become empty placeholders, which the loader never looks at. Which
tables those are is :class:`TableFilter`, a port of the shell's
``Filtering_options``. Should it ever be wrong, the loader starts loading a
placeholder: the extractor sees that in the progress file and fails the load
loudly, and the next load of the zip extracts every table.
"""
# cSpell:ignore mysqlsh MariaDB zstd

import json
import os
import re
import shutil
import threading
import time
import zipfile
import zlib
from collections import deque
from typing import Callable, Optional

ZIP_SUFFIX = ".mariadb.dump.zip"

# The folder of a load next to its zip keeps this log of what it holds.
STATE_FILE = ".mcp-zip-load.log"

# The load's progress file in that folder, where the client gives none.
PROGRESS_FILE = "load-progress.json"

# How long the loader waits for more of the dump: extraction never pauses
# while the loader has nothing to do, and a failed extractor stops the load,
# so this only ends a load whose extractor hangs.
WAIT_DUMP_TIMEOUT_SECONDS = 86400

# The budget of bytes extracted but not yet loaded, at the least.
MIN_BUDGET_BYTES = 1 << 30

# How long the progress file may stay unchanged, with the budget used up,
# before the extractor stops waiting for the loader and extracts the rest.
STALL_SECONDS = 120

POLL_SECONDS = 0.25

# The load's threads, where the options do not give them: the shell's default.
DEFAULT_LOAD_THREADS = 4

_ROOT_METADATA = "@.json"
_DONE_METADATA = "@.done.json"
_PART_SUFFIX = ".part"
_EXTRACTING_SUFFIX = ".extracting"
_IDX_SUFFIX = ".idx"
# a load's progress file, the shell's (load-progress.<server>.json) or ours
_PROGRESS_FILE_NAME = re.compile(r"^load-progress(\..+)?\.json$")
_COPY_BUFFER = 1 << 20
_MAX_METADATA_BYTES = 64 << 20
_REPORT_SECONDS = 0.5
# the margin of free space a load keeps, beyond what it extracts
_FREE_SPACE_MARGIN = 64 << 20

_MESSAGE_INTERRUPTED = "Interrupted by user"

# The folders next to zips that a task dumps to or loads from now.
_active_lock = threading.Lock()
_active = set()


class ZipDumpError(Exception):
    """What a call to a util tool cannot do with a zip, before any task."""


_SCHEME_DELIMITER = "://"


def local_path(url) -> Optional[str]:
    """The path on this machine a URL names, or None for a remote one.

    Follows the shell's storage layer (``mysqlshdk/libs/storage``): the scheme
    is what comes before the first ``://``, a URL without one is a local path,
    and so is one whose scheme is ``file`` in any case - with the scheme
    stripped. Every other scheme (an ``https`` PAR, say) is remote.
    """
    if not isinstance(url, str):
        return None

    position = url.find(_SCHEME_DELIMITER)
    if position < 0:
        return url
    if url[:position].lower() == "file":
        return url[position + len(_SCHEME_DELIMITER):]

    return None


def is_zip_url(url) -> bool:
    """Whether a tool's URL names a dump as a single ZIP file."""
    return isinstance(url, str) and url.lower().endswith(ZIP_SUFFIX)


def _stem(zip_path: str) -> str:
    return zip_path[: -len(".zip")]


def dump_directory(zip_path: str) -> str:
    """The folder a dump is written to before it is packed into the zip."""
    return _stem(zip_path) + ".tmp"


def extraction_directory(zip_path: str) -> str:
    """The folder a load extracts the zip to, next to it."""
    return _stem(zip_path) + ".extracted"


def _format_bytes(count: int) -> str:
    value = float(count)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024


def _interrupted(task) -> bool:
    return task is not None and getattr(task, "cancel_requested", False)


# --- the dump ----------------------------------------------------------------


def check_dump_target(zip_path: str) -> None:
    """Refuses a zip a dump cannot be written to.

    Raises:
        ZipDumpError: The zip exists, its folder does not, or the folder the
            dump is written to first is left over from a dump that did not
            finish.
    """
    if os.path.lexists(zip_path):
        raise ZipDumpError(f"The file {zip_path} already exists.")

    parent = os.path.dirname(os.path.abspath(zip_path))
    if not os.path.isdir(parent):
        raise ZipDumpError(f"The folder {parent} does not exist.")

    with _active_lock:
        if dump_directory(zip_path) in _active:
            raise ZipDumpError(f"{zip_path} is being written by another task already.")

    for path, may_be_empty_folder in ((dump_directory(zip_path), True), (zip_path + _PART_SUFFIX, False)):
        if os.path.lexists(path) and not (
            may_be_empty_folder and os.path.isdir(path) and not os.listdir(path)
        ):
            raise ZipDumpError(
                f"{path} already exists. It is left over from a dump to "
                f"{os.path.basename(zip_path)} that did not finish: remove it."
            )


def _dump_order(name: str):
    if name == _ROOT_METADATA:
        return (0, name)
    if name == _DONE_METADATA:
        return (3, name)
    if name.endswith((".json", ".sql")):
        return (1, name)
    return (2, name)


def pack(directory: str, zip_path: str, task=None) -> tuple:
    """Puts a dump's folder into a zip, deleting each file once it is in.

    The zip is written as ``<zip>.part`` and renamed when it is complete, so
    a zip by its name is always a whole one.

    Args:
        directory (str): The dump's folder.
        zip_path (str): The zip to write.
        task (Task): Reports the stage and its progress, and is checked for
            a cancel.

    Returns:
        tuple: The number of files, and the size of the zip.

    Raises:
        RuntimeError: The task was cancelled.
    """
    stage = "Packing the dump"
    files = []
    for root, dirs, names in os.walk(directory):
        dirs.sort()
        for name in names:
            path = os.path.join(root, name)
            entry = os.path.relpath(path, directory).replace(os.sep, "/")
            files.append((entry, path, os.path.getsize(path)))
    files.sort(key=lambda f: _dump_order(f[0]))

    total = sum(size for _, _, size in files)
    part = zip_path + _PART_SUFFIX
    started = time.monotonic()
    reported = 0.0
    written = 0

    if task is not None:
        task.stage_started(stage)
        task.progress(0, total, stage=stage)

    try:
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for entry, path, _ in files:
                info = zipfile.ZipInfo.from_file(path, entry)
                info.compress_type = zipfile.ZIP_STORED
                with open(path, "rb") as source, archive.open(info, "w") as target:
                    while True:
                        if _interrupted(task):
                            raise RuntimeError(_MESSAGE_INTERRUPTED)
                        block = source.read(_COPY_BUFFER)
                        if not block:
                            break
                        target.write(block)
                        written += len(block)
                        now = time.monotonic()
                        if task is not None and now - reported >= _REPORT_SECONDS:
                            reported = now
                            task.progress(written, total, stage=stage)
                os.remove(path)
        os.replace(part, zip_path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise

    if task is not None:
        task.progress(total, total, stage=stage)
        task.stage_finished(stage, seconds=round(time.monotonic() - started, 1))

    return len(files), os.path.getsize(zip_path)


def dump_to_zip(task, zip_path: str, run_dump: Callable[[str], None]) -> dict:
    """Runs a dump into the folder next to the zip, and packs it.

    The folder is removed whatever happens: a dump cannot be resumed.

    Args:
        task (Task): The task the dump runs for.
        zip_path (str): The zip to write.
        run_dump: Runs the dump into the folder it is given.

    Returns:
        dict: The task's result.
    """
    directory = dump_directory(zip_path)
    with _active_lock:
        if directory in _active:
            raise RuntimeError(f"{zip_path} is being written by another task already.")
        _active.add(directory)
    try:
        run_dump(directory)
        if not os.path.isfile(os.path.join(directory, _ROOT_METADATA)):
            # a dry run: nothing was dumped, and an empty zip would be no
            # dump, and in the way of the next one
            if task is not None:
                task.message(f"Nothing was dumped, so {zip_path} was not written.")
            return {"output_url": zip_path, "files": 0, "bytes": 0}
        files, size = pack(directory, zip_path, task)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        with _active_lock:
            _active.discard(directory)

    if task is not None:
        task.message(f"Packed {files} files into {zip_path} ({_format_bytes(size)}).")

    return {"output_url": zip_path, "files": files, "bytes": size}


# --- the load's filters ------------------------------------------------------

_UNQUOTED_IDENTIFIER = re.compile(r"[A-Za-z0-9_$]+")


def _span_identifier(text: str, offset: int) -> tuple:
    """An identifier at ``offset``, quoted or not, and where it ends.

    A port of ``span_quotable_sql_identifier`` (mysqlshdk/libs/utils/
    utils_lexing.cc), without ANSI quotes: a backtick-quoted identifier, ``````
    standing for a backtick, or an unquoted one of ASCII letters, digits,
    ``_`` and ``$`` that is not all digits.
    """
    if offset >= len(text):
        return "", offset

    if text[offset] == "`":
        offset += 1
        value = []
        while offset < len(text):
            if text[offset] == "`":
                if offset + 1 < len(text) and text[offset + 1] == "`":
                    value.append("`")
                    offset += 2
                    continue
                return "".join(value), offset + 1
            value.append(text[offset])
            offset += 1
        raise ValueError("Invalid syntax in identifier")

    match = _UNQUOTED_IDENTIFIER.match(text, offset)
    if match is None:
        raise ValueError("Invalid character in identifier")
    if match.group().isdigit():
        raise ValueError("An unquoted identifier may not consist solely of digits")
    return match.group(), match.end()


def _parse_schema(text: str) -> str:
    """A schema filter's name: unquoted where it can be, as given otherwise.

    As ``Filtering_options::Schema_filters::parse_schema``.
    """
    try:
        value, end = _span_identifier(text, 0)
    except ValueError:
        return text
    return value if end == len(text) else text


def _split_schema_table(text: str) -> tuple:
    """A table filter's ``schema.table``, as ``shcore::split_schema_and_table``.

    Raises:
        ValueError: It is no ``schema.table``; the shell refuses it too.
    """
    schema, position = _span_identifier(text, 0)
    if position < len(text):
        if text[position] != ".":
            raise ValueError(f"Invalid object name: {text}")
        table, position = _span_identifier(text, position + 1)
        if position < len(text):
            raise ValueError(f"Invalid object name: {text}")
    else:
        schema, table = "", schema

    if not table or not schema:
        raise ValueError(f"Invalid object name: {text}")

    return schema, table


class TableFilter:
    """Which tables' data a load takes, by its options.

    A port of ``Filtering_options::Schema_filters::is_included`` and
    ``Object_filters::is_included`` (mysqlshdk/libs/db/filtering_options.cc),
    the check ``Dump_loader::is_table_included`` makes. Names match exactly;
    an exclusion wins. The names are the dump's: with the ``schema`` option,
    the loader maps the target back to the dump's schema before it checks.
    """

    def __init__(self, options: dict):
        self.load_data = options.get("loadData", True) is not False
        self.include_schemas = {_parse_schema(s) for s in self._list(options, "includeSchemas")}
        self.exclude_schemas = {_parse_schema(s) for s in self._list(options, "excludeSchemas")}
        self.include_tables = {_split_schema_table(t) for t in self._list(options, "includeTables")}
        self.exclude_tables = {_split_schema_table(t) for t in self._list(options, "excludeTables")}

    @staticmethod
    def _list(options: dict, name: str) -> list:
        value = options.get(name)
        if value is None:
            return []
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{name} is not a list of names")
        return list(value)

    @classmethod
    def from_options(cls, options: dict) -> Optional["TableFilter"]:
        """The filter, or None where the options are beyond what it reads.

        The shell refuses filters it cannot parse, and fails the load itself;
        without a filter, every table is extracted.
        """
        try:
            return cls(options)
        except ValueError:
            return None

    def schema_included(self, schema: str) -> bool:
        if schema in self.exclude_schemas:
            return False
        return not self.include_schemas or schema in self.include_schemas

    def table_included(self, schema: str, table: str) -> bool:
        """Whether the load loads this table's data."""
        if not self.load_data or not self.schema_included(schema):
            return False
        if (schema, table) in self.exclude_tables:
            return False
        return not self.include_tables or (schema, table) in self.include_tables


# --- the archive -------------------------------------------------------------


class DataUnit:
    """A table, or a partition of one: what the loader loads chunk by chunk."""

    __slots__ = ("schema", "table", "partition", "basename", "extension", "chunked", "chunks")

    def __init__(self, schema, table, partition, basename, extension, chunked):
        self.schema = schema
        self.table = table
        self.partition = partition
        self.basename = basename
        self.extension = extension
        self.chunked = chunked
        self.chunks = []

    def label(self) -> str:
        name = f"`{self.schema}`.`{self.table}`"
        return f"{name} partition `{self.partition}`" if self.partition else name


class DataChunk:
    """A data file of a dump, with its ``.idx`` file where it has one."""

    __slots__ = ("name", "idx_name", "size", "unit", "index")

    def __init__(self, name, unit, index, size):
        self.name = name
        self.idx_name = None
        self.size = size
        self.unit = unit
        # as the progress file has it: -1 for a table that is not chunked
        self.index = index

    def names(self) -> tuple:
        return (self.idx_name, self.name) if self.idx_name else (self.name,)


def _ignored(name: str) -> bool:
    """Whether a file beside a dump's own is never part of it.

    An earlier load's progress file or log, or a file being extracted or
    dumped.
    """
    return bool(_PROGRESS_FILE_NAME.match(name)) or name == STATE_FILE or name.endswith(
        (_EXTRACTING_SUFFIX, ".dumping")
    )


def _not_a_dump(message: str) -> ZipDumpError:
    """A refusal of what holds no dump the tools can read (not an I/O error)."""
    return ZipDumpError("Not a dump: " + message)


def _cannot_read(path: str, error: OSError) -> ZipDumpError:
    return ZipDumpError(f"Cannot read {path}: {error.strerror or error}.")


# What a zip made by an archiver holds beside the files it was given.
_ARCHIVER_FOLDER = "__MACOSX/"
_ARCHIVER_FILES = (".DS_Store",)


def _dump_members(infos: list) -> tuple:
    """The members of a zip that are the dump's files, and the folder they are in.

    A dump's files are at the top of the zip, as the dump tools write it, or
    all in one folder at the top, as an archiver zips a dump's folder (Finder,
    Explorer, ``zip -r``). The folder's own entries, the archiver's
    ``__MACOSX/`` and ``.DS_Store`` files are no files of the dump.

    Returns:
        tuple: ``(prefix, [(name, info), ...])``, with ``name`` the file's
        name in the dump, without the folder.
    """
    files = [
        info for info in infos
        if not info.filename.endswith("/") and not info.filename.startswith(_ARCHIVER_FOLDER)
    ]
    prefix = ""
    tops = {info.filename.split("/", 1)[0] for info in files if "/" in info.filename}
    if files and all("/" in info.filename for info in files) and len(tops) == 1:
        prefix = tops.pop() + "/"

    members = []
    for info in files:
        name = info.filename[len(prefix):]
        if name in _ARCHIVER_FILES:
            continue
        members.append((name, info))
    return prefix, members


def _check_entry(name: str, info: zipfile.ZipInfo) -> None:
    if (
        not name
        or "/" in name
        or "\\" in name
        or "\0" in name
        or name in (".", "..")
        or (len(name) > 1 and name[1] == ":")
    ):
        raise _not_a_dump(
            f"the zip holds '{info.filename}', which is no file of a dump: a "
            "dump's files are all at the top of the zip, or all in one folder there."
        )
    if info.flag_bits & 0x1:
        raise _not_a_dump(f"the zip's entry '{info.filename}' is encrypted.")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise _not_a_dump(
            f"the zip's entry '{info.filename}' is compressed with a method other "
            "than store or deflate."
        )
    mode = (info.external_attr >> 16) & 0o170000
    if mode not in (0, 0o100000):
        raise _not_a_dump(f"the zip's entry '{info.filename}' is no regular file.")


class _ZipFiles:
    """Reads a dump's files from a zip, by their names in the dump."""

    def __init__(self, zipped: zipfile.ZipFile, archive: "DumpArchive"):
        self._zipped = zipped
        self._archive = archive

    def read(self, name: str) -> bytes:
        return self._zipped.read(self._archive.member(name))


class _FolderFiles:
    """Reads a dump's files from its folder."""

    def __init__(self, directory: str):
        self._directory = directory

    def read(self, name: str) -> bytes:
        with open(os.path.join(self._directory, name), "rb") as file:
            return file.read()


class DumpArchive:
    """A dump, in a zip or a folder: its metadata read, and its data files found.

    Built by :meth:`open` for a zip, whose entries it checks, or by
    :meth:`open_folder`; either reads the list of files and the dump's
    metadata, and no data.
    """

    def __init__(self, path: str, read_tables: bool = True):
        self.path = os.path.abspath(path)
        self.format = "zip"
        # whether each table's metadata is read, which finds the data files
        # by name; without it, data files are told from metadata by suffix
        self.read_tables = read_tables
        self.size = 0
        self.mtime_ns = 0
        # every entry, by name: its size
        self.entries = {}
        # the folder of a zip the dump's files are in, "" at the top
        self.prefix = ""
        # @.json, @.done.json (None while a dump is not complete), and each
        # schema's <schema>.json
        self.root = None
        self.done = None
        self.schema_metadata = {}
        # entries the load never extracts: an earlier load's progress file
        self.ignored = []
        self.schemas = []
        self.units = []
        self.chunks = {}
        # the metadata, in the order it is extracted
        self.metadata = []

    def identity(self) -> dict:
        return {"path": self.path, "size": self.size, "mtime_ns": self.mtime_ns}

    def _data_files(self):
        """The sizes of the data files (index files with them, where found)."""
        if self.read_tables:
            return [chunk.size for chunk in self.chunks.values()]
        return [
            size for name, size in self.entries.items()
            if not _ignored(name) and not name.endswith((".json", ".sql"))
        ]

    @property
    def data_bytes(self) -> int:
        return sum(self._data_files())

    @property
    def metadata_bytes(self) -> int:
        return sum(self.entries[name] for name in self.metadata) + self.entries.get(_DONE_METADATA, 0)

    @property
    def extracted_bytes(self) -> int:
        """What the dump's files take on disk, every one extracted."""
        return sum(size for name, size in self.entries.items() if name not in self.ignored)

    @property
    def largest_chunk(self) -> int:
        return max(self._data_files(), default=0)

    @classmethod
    def open(cls, path: str, require_complete: bool = True, read_tables: bool = True) -> "DumpArchive":
        """Reads and checks a zip of a dump.

        Args:
            path (str): The zip.
            require_complete (bool): Whether a dump without @.done.json is
                refused, as a load refuses it.
            read_tables (bool): Whether each table's metadata is read.

        Raises:
            ZipDumpError: It is no zip, or no (complete) dump made by the
                util tools, or holds what no dump holds.
        """
        archive = cls(path, read_tables)
        try:
            stat = os.stat(archive.path)
        except OSError as error:
            raise _cannot_read(path, error) from error
        archive.size = stat.st_size
        archive.mtime_ns = stat.st_mtime_ns

        try:
            with zipfile.ZipFile(archive.path) as zipped:
                archive.prefix, members = _dump_members(zipped.infolist())
                for name, info in members:
                    _check_entry(name, info)
                    if name in archive.entries:
                        raise _not_a_dump(f"the zip holds '{info.filename}' twice.")
                    archive.entries[name] = info.file_size
                archive._read(_ZipFiles(zipped, archive), require_complete)
        except zipfile.BadZipFile as error:
            raise _not_a_dump(f"{path} is no valid ZIP file: {error}.") from error
        except OSError as error:
            raise _cannot_read(path, error) from error

        return archive

    def member(self, name: str) -> str:
        """The name in the zip of one of the dump's files."""
        return self.prefix + name

    @classmethod
    def open_folder(cls, path: str, read_tables: bool = True) -> "DumpArchive":
        """Reads a dump's folder, complete or not.

        Raises:
            ZipDumpError: It holds no dump.
        """
        archive = cls(path, read_tables)
        archive.format = "folder"
        try:
            with os.scandir(archive.path) as listing:
                for entry in listing:
                    if entry.is_file():
                        archive.entries[entry.name] = entry.stat().st_size
            archive.size = sum(archive.entries.values())
            archive._read(_FolderFiles(archive.path), require_complete=False)
        except OSError as error:
            raise _cannot_read(path, error) from error
        return archive

    def _read(self, files, require_complete: bool) -> None:
        where = self.format
        if _ROOT_METADATA not in self.entries:
            raise _not_a_dump(
                f"the {where} holds no {_ROOT_METADATA}, so it is no dump made "
                "by the util.dump_* tools."
            )
        if require_complete and _DONE_METADATA not in self.entries:
            raise _not_a_dump(
                f"the {where} holds no {_DONE_METADATA}, so it is no complete dump "
                "made by the util.dump_* tools."
            )

        self.root = root = self._json(files, _ROOT_METADATA)
        if _DONE_METADATA in self.entries:
            self.done = self._json(files, _DONE_METADATA)
        if not isinstance(root.get("schemas"), list):
            raise _not_a_dump(
                f"the dump in the {where} has a layout the MCP server cannot read; "
                "load it with the shell's util.load_dump."
            )

        schema_basenames = root.get("basenames") or {}
        for schema in root["schemas"]:
            self.schemas.append(schema)
            self._read_schema(files, schema, schema_basenames.get(schema, schema))

        if self.read_tables:
            self._find_chunks()
        else:
            for name in self.entries:
                if _ignored(name):
                    self.ignored.append(name)
                elif name != _DONE_METADATA and name.endswith((".json", ".sql")):
                    self.metadata.append(name)

    def _json(self, files, name: str) -> dict:
        if self.entries[name] > _MAX_METADATA_BYTES:
            raise _not_a_dump(f"the dump's metadata file {name} is too large.")
        try:
            value = json.loads(files.read(name).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, zlib.error) as error:
            raise _not_a_dump(f"the dump's metadata file {name} cannot be read: {error}.") from error
        if not isinstance(value, dict):
            raise _not_a_dump(f"the dump's metadata file {name} is no JSON object.")
        return value

    def _read_schema(self, files, schema: str, basename: str) -> None:
        name = basename + ".json"
        if name not in self.entries:
            if self.done is None:
                # a dump still being written: its schemas come later
                return
            raise _not_a_dump(f"the dump holds no {name}, the metadata of schema `{schema}`.")
        metadata = self._json(files, name)
        self.schema_metadata[schema] = metadata
        if not self.read_tables or metadata.get("includesData", True) is False:
            return

        table_basenames = metadata.get("basenames") or {}
        for table in metadata.get("tables") or []:
            table_basename = table_basenames.get(table, f"{basename}@{table}")
            table_name = table_basename + ".json"
            if table_name not in self.entries:
                # a table with neither DDL nor data has no metadata of its own
                continue
            table_metadata = self._json(files, table_name)
            if table_metadata.get("includesData", True) is False:
                continue

            extension = table_metadata.get("extension", "tsv")
            chunked = bool(table_metadata.get("chunking", False))
            partitions = table_metadata.get("basenames") or {}
            if partitions:
                for partition, partition_basename in partitions.items():
                    self.units.append(
                        DataUnit(schema, table, partition, partition_basename, extension, chunked)
                    )
            else:
                self.units.append(DataUnit(schema, table, "", table_basename, extension, chunked))

    def _chunk_of(self, name: str, chunked: dict):
        """The unit and index of a chunked table's data file.

        ``<basename>@<N>.<ext>``, the last chunk ``<basename>@@<N>.<ext>``.
        """
        position = name.find("@")
        while position >= 0:
            unit = chunked.get(name[:position])
            if unit is not None:
                rest = name[position + 1:].removeprefix("@")
                match = re.fullmatch(r"(\d+)\." + re.escape(unit.extension), rest)
                if match:
                    return unit, int(match.group(1))
            position = name.find("@", position + 1)
        return None

    def _find_chunks(self) -> None:
        unchunked = {}
        chunked = {}
        for unit in self.units:
            if unit.chunked:
                chunked[unit.basename] = unit
            else:
                unchunked[f"{unit.basename}.{unit.extension}"] = unit

        others = []
        for name, size in self.entries.items():
            if name.endswith(_IDX_SUFFIX) or name == _DONE_METADATA:
                continue
            if _ignored(name):
                self.ignored.append(name)
                continue

            unit = unchunked.get(name)
            if unit is not None:
                chunk = DataChunk(name, unit, -1, size)
            else:
                found = self._chunk_of(name, chunked)
                if found is None:
                    others.append(name)
                    continue
                unit, index = found
                chunk = DataChunk(name, unit, index, size)

            unit.chunks.append(chunk)
            self.chunks[name] = chunk

        for name, size in self.entries.items():
            if not name.endswith(_IDX_SUFFIX):
                continue
            chunk = self.chunks.get(name[: -len(_IDX_SUFFIX)])
            if chunk is not None:
                chunk.idx_name = name
                chunk.size += size
            else:
                others.append(name)

        for unit in self.units:
            unit.chunks.sort(key=lambda c: c.index)

        others.sort(key=lambda n: (n != _ROOT_METADATA, n))
        self.metadata = others


def default_budget(archive: "DumpArchive", threads=DEFAULT_LOAD_THREADS) -> int:
    """The bytes a load of a zip extracts ahead of it, where not given.

    Twice the load's threads times the largest chunk, and
    :data:`MIN_BUDGET_BYTES` at the least.
    """
    if not isinstance(threads, int) or isinstance(threads, bool) or threads < 1:
        threads = DEFAULT_LOAD_THREADS
    return max(MIN_BUDGET_BYTES, 2 * threads * archive.largest_chunk)


def disk_needed(archive: "DumpArchive", budget: int) -> int:
    """The free disk space a load of a zip needs next to it, with that budget."""
    return archive.metadata_bytes + min(budget, archive.data_bytes) + _FREE_SPACE_MARGIN


# --- what a dump holds -------------------------------------------------------

# The names of a schema's objects, or of the users, listed at the most; the
# counts are always given.
INSPECT_NAMES_LIMIT = 100


def _names(metadata: dict, key: str) -> list:
    return [n for n in (metadata.get(key) or []) if isinstance(n, str)]


def inspect_dump(path: str, schema: Optional[str] = None) -> dict:
    """What a dump in a folder or a zip holds, from its metadata alone.

    Args:
        path (str): The dump's folder, or its zip.
        schema (str): Only this schema, with every name of its objects; by
            default every schema, with at most :data:`INSPECT_NAMES_LIMIT`
            names of each kind of object.

    Returns:
        dict: Its kind (the dump utility that made it), whether it is
        complete, where and when it was made and with which options, its
        users and its schemas with their objects; the rows and data sizes are
        exact for a complete dump (@.done.json) and estimates otherwise.

    Raises:
        ZipDumpError: It is no dump ("Not a dump: ..."), cannot be read
            ("Cannot read ..."), or holds no such schema.
    """
    path = os.path.abspath(path)
    if os.path.isdir(path):
        archive = DumpArchive.open_folder(path, read_tables=False)
    elif os.path.isfile(path):
        archive = DumpArchive.open(path, require_complete=False, read_tables=False)
    else:
        raise ZipDumpError(f"Cannot read {path}: it does not exist.")

    if schema is not None and schema not in archive.schemas:
        raise ZipDumpError(f"The dump holds no schema `{schema}`.")
    limit = None if schema is not None else INSPECT_NAMES_LIMIT

    root = archive.root
    done = archive.done
    table_rows = (done or {}).get("tableRows") or {}
    table_bytes = (done or {}).get("tableDataBytes") or {}

    schemas = []
    for name in archive.schemas:
        if schema is not None and name != schema:
            continue
        metadata = archive.schema_metadata.get(name) or {}
        entry = {"name": name}
        for key in ("tables", "views", "functions", "procedures", "events"):
            names = _names(metadata, key)
            entry[key[:-1] + "_count"] = len(names)
            entry[key] = names[:limit]
        if done is not None:
            entry["rows"] = sum((table_rows.get(name) or {}).values())
            entry["data_bytes"] = sum((table_bytes.get(name) or {}).values())
        schemas.append(entry)
    users = _names(root, "users")

    info = {
        "url": archive.path,
        "format": archive.format,
        "kind": root.get("origin"),
        "complete": done is not None,
        "dumped_by": root.get("dumper"),
        "source": {
            "server": root.get("server") or root.get("hostname"),
            "server_version": root.get("serverVersion"),
            "user": root.get("user"),
        },
        "begin": root.get("begin"),
        "end": (done or {}).get("end"),
        "options": root.get("options") or {},
        "includes_users": "@.users.sql" in archive.entries,
        # the accounts, as the shell writes them ('user'@'host')
        "user_count": len(users),
        "users": users[:limit],
        "schema_count": len(archive.schemas),
        "schemas": schemas,
        # the data files as they are on disk or in the zip: compressed
        "data_file_bytes": archive.data_bytes,
        "size_bytes": archive.size,
        # every file of the dump extracted: for a zip, its stored entries
        "extracted_bytes": archive.extracted_bytes,
    }
    if archive.format == "zip":
        # the free space util.load_dump checks for next to the zip, with the
        # default budget
        info["disk_needed_bytes"] = disk_needed(archive, default_budget(archive))
    if done is not None:
        info["rows"] = sum(sum((t or {}).values()) for t in table_rows.values())
        info["data_bytes"] = done.get("dataBytes")
    else:
        info["estimated_rows"] = root.get("estimatedRowCount")
        info["estimated_data_bytes"] = root.get("estimatedDataSize")
    return info


# --- the load ----------------------------------------------------------------


class _State:
    """The log a load's folder keeps of what it holds: one JSON per line."""

    def __init__(self):
        self.zip = None
        self.extracted = set()
        self.emptied = set()
        self.placeholders = set()
        self.progress_file = None
        self.poisoned = None

    @classmethod
    def read(cls, directory: str) -> Optional["_State"]:
        path = os.path.join(directory, STATE_FILE)
        try:
            with open(path, encoding="utf-8") as file:
                lines = file.read().splitlines()
        except FileNotFoundError:
            return None

        state = cls()
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                # a line cut short by a crash
                continue
            if not isinstance(record, dict):
                continue
            if "zip" in record:
                state.zip = record["zip"]
            elif "extracted" in record:
                state.extracted.add(record["extracted"])
                state.placeholders.discard(record["extracted"])
            elif "emptied" in record:
                state.emptied.add(record["emptied"])
            elif "placeholder" in record:
                state.placeholders.add(record["placeholder"])
            elif "progressFile" in record:
                state.progress_file = record["progressFile"]
            elif "poisoned" in record:
                state.poisoned = record["poisoned"]
        return state


class LoadPlan:
    """How a load of a zip goes: its folder, and what it does with it.

    Made by :func:`plan_load` when the tool is called, so that what cannot
    work fails the call itself.
    """

    def __init__(self, archive, directory, wipe, state, options, progress_file, filters, rename, budget):
        self.archive = archive
        self.directory = directory
        # the folder holds what is not resumed, and is started over
        self.wipe = wipe
        # what an earlier load left in the folder, resumed; None where the
        # folder is new or started over
        self.state = state
        # the options the shell's load is run with
        self.options = options
        # the load's progress file, always given to the shell
        self.progress_file = progress_file
        self.filters = filters
        # the target schema of the schema option, mapped to the dump's
        self.rename = rename
        self.budget = budget


def plan_load(archive: DumpArchive, options: dict, budget_mb=None) -> LoadPlan:
    """Decides how a load of a zip goes, refusing what cannot work.

    The load is always given its progress file: the client's, the one an
    earlier load of the zip used, or :data:`PROGRESS_FILE` in the folder.
    The shell refuses a progress file of another server, which keeps a
    folder whose loaded chunks are emptied from being loaded anywhere else.

    Args:
        archive (DumpArchive): The zip.
        options (dict): The load's options, as the client gave them.
        budget_mb (int): The most megabytes of chunks extracted and not yet
            loaded; by default :func:`default_budget`.

    Raises:
        ZipDumpError: The options or the folder next to the zip do not allow
            the load.
    """
    if options.get("progressFile") == "":
        raise ZipDumpError(
            "progressFile cannot be empty for a dump in a zip: the load's "
            "progress file says which chunks were loaded and can be deleted."
        )
    if "waitDumpTimeout" in options:
        raise ZipDumpError(
            "waitDumpTimeout is set by the tool for a dump in a zip: the zip "
            "is extracted while the dump loads."
        )
    user_progress = options.get("progressFile")
    if user_progress is not None:
        user_progress = local_path(user_progress)
        if user_progress is None:
            raise ZipDumpError("progressFile must be a local file for a dump in a zip.")
        user_progress = os.path.abspath(user_progress)

    directory = extraction_directory(archive.path)
    state = None
    if os.path.lexists(directory):
        if not os.path.isdir(directory):
            raise ZipDumpError(f"{directory}, where the zip is extracted to, is no folder.")
        state = _State.read(directory)
        if state is None and os.listdir(directory):
            raise ZipDumpError(
                f"{directory}, where the zip is extracted to, exists and was not "
                "made by the MCP server: move it out of the way."
            )

    # a poisoned folder is started over with every table extracted
    filters = TableFilter.from_options(options)
    if state is not None and (
        state.zip != archive.identity() or state.poisoned or options.get("resetProgress")
    ):
        if state.poisoned:
            filters = None
        state = None
    recorded = state.progress_file if state is not None else None
    if (
        user_progress is not None
        and recorded is not None
        and state.emptied
        and user_progress != recorded
    ):
        raise ZipDumpError(
            f"The zip was partly loaded from {directory} already, with the "
            f"progress file {recorded}: give that one as progressFile, or "
            "resetProgress: true to start the load over."
        )
    progress_file = user_progress or recorded or os.path.join(directory, PROGRESS_FILE)
    if state is not None and state.emptied and not os.path.isfile(progress_file):
        # the progress file that says the emptied chunks are loaded is gone:
        # the loader would load them again, as empty files
        state = None
        progress_file = user_progress or os.path.join(directory, PROGRESS_FILE)
    # the folder is wiped where it holds anything and is not resumed
    wipe = state is None and os.path.isdir(directory) and bool(os.listdir(directory))

    run_options = dict(options)
    run_options["waitDumpTimeout"] = WAIT_DUMP_TIMEOUT_SECONDS
    run_options["progressFile"] = progress_file

    rename = {}
    target_schema = options.get("schema")
    if isinstance(target_schema, str) and target_schema and len(archive.schemas) == 1:
        rename[target_schema] = archive.schemas[0]

    if budget_mb is not None:
        if not isinstance(budget_mb, int) or isinstance(budget_mb, bool) or budget_mb < 1:
            raise ZipDumpError("extraction_budget_mb must be a positive number of megabytes.")
        budget = budget_mb << 20
    else:
        budget = default_budget(archive, options.get("threads", DEFAULT_LOAD_THREADS))

    needed = disk_needed(archive, budget)
    if state is not None:
        # what is extracted already is on the disk
        needed = max(0, needed - sum(
            archive.entries.get(name, 0) for name in state.extracted - state.emptied
        ))
    free = shutil.disk_usage(os.path.dirname(directory)).free
    if wipe:
        # what the folder holds is removed before anything is extracted
        with os.scandir(directory) as listing:
            free += sum(entry.stat().st_size for entry in listing if entry.is_file())
    if free < needed:
        raise ZipDumpError(
            f"Extracting the zip next to it needs {_format_bytes(needed)} free on "
            f"its disk, which has {_format_bytes(free)}."
        )

    return LoadPlan(
        archive, directory, wipe, state, run_options, progress_file, filters, rename, budget
    )


_EXTRACT_STAGE = "Extracting the dump"


class Extractor:
    """Extracts a zip into its load's folder while the load runs.

    :meth:`prepare` extracts the metadata before the load starts; the thread
    :meth:`start` starts extracts the data, as the budget allows, and empties
    every chunk the progress file says was loaded, until :meth:`stop`.
    """

    def __init__(self, task, plan: LoadPlan):
        self.task = task
        self.plan = plan
        self.archive = plan.archive
        self.directory = plan.directory
        self.finished = False
        # whether this load holds the folder in the registry of loads
        self._claimed = False
        self._state = None
        self._log = None
        self._zip = None
        self._thread = None
        self._stop = threading.Event()
        self._abort = None
        # chunks extracted and not yet loaded, and the bytes they hold
        self._pending = set()
        self._pending_bytes = 0
        self._peak_bytes = 0
        self._extracted_bytes = 0
        self._to_empty = set()
        # chunks the progress file says were loaded before they were extracted
        self._loaded = set()
        self._by_key = {}
        self._units_by_key = {}
        self._progress_offset = 0
        self._progress_rest = b""
        self._progress_size = -1
        self._polled = 0.0
        self._last_progress = time.monotonic()
        self._full_since = None
        # chunks the loader started in this load and has not finished
        self._in_progress = set()
        self._uncapped = False
        self._stage_started = None
        # progress lines before this (with a margin for clock steps) are an
        # earlier load's
        self._since_ms = int(time.time() * 1000) - 5000

    # --- the folder ------------------------------------------------------

    def _path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def _record(self, **record) -> None:
        """Logs what the folder holds; flushed in batches.

        A record lost to a crash only makes the next load extract a file
        again: an emptied chunk whose record is lost is extracted again, and
        emptied again by the progress file.
        """
        self._log.write(json.dumps(record, ensure_ascii=False) + "\n")

    def prepare(self) -> None:
        """Readies the folder and extracts the metadata.

        Raises:
            RuntimeError: The folder is in use by another load, or a file
                could not be written.
        """
        with _active_lock:
            if self.directory in _active:
                raise RuntimeError(f"{self.archive.path} is being loaded by another task already.")
            _active.add(self.directory)
            self._claimed = True

        self.task.stage_started(_EXTRACT_STAGE)
        self._stage_started = time.monotonic()

        if self.plan.wipe:
            shutil.rmtree(self.directory)
        os.makedirs(self.directory, exist_ok=True)

        self._log = open(self._path(STATE_FILE), "a", encoding="utf-8")
        state = self.plan.state
        if state is None:
            state = _State()
            state.zip = self.archive.identity()
            self._record(zip=state.zip)
        self._state = state
        if state.progress_file != self.plan.progress_file:
            state.progress_file = self.plan.progress_file
            self._record(progressFile=state.progress_file)

        on_disk = {}
        with os.scandir(self.directory) as listing:
            for entry in listing:
                if entry.name.endswith(_EXTRACTING_SUFFIX):
                    os.remove(entry.path)
                elif entry.is_file():
                    on_disk[entry.name] = entry.stat().st_size

        self._zip = zipfile.ZipFile(self.archive.path)

        for unit in self.archive.units:
            self._units_by_key[(unit.schema, unit.table, unit.partition)] = unit
            for chunk in unit.chunks:
                self._by_key[(unit.schema, unit.table, unit.partition, chunk.index)] = chunk

        self._check_extracted(on_disk)

        for name in self.archive.metadata:
            if name not in state.extracted:
                self._extract(name)

        self._make_placeholders(on_disk)

        if _DONE_METADATA in on_disk and self._data_to_extract():
            # left by an earlier load that extracted everything: the loader
            # takes the dump as complete with it, and would not wait for what
            # is extracted again; it is extracted last, as ever
            os.remove(self._path(_DONE_METADATA))
            state.extracted.discard(_DONE_METADATA)
        self._log.flush()

    def _check_extracted(self, on_disk: dict) -> None:
        """Forgets what an earlier load extracted and is no longer there."""
        state = self._state
        for name in list(state.extracted - state.emptied):
            if on_disk.get(name) != self.archive.entries.get(name):
                state.extracted.discard(name)
        for name in state.emptied | state.placeholders:
            if name not in on_disk:
                open(self._path(name), "wb").close()
                on_disk[name] = 0

    def _data_to_extract(self) -> bool:
        """Whether a chunk the load takes is neither extracted nor emptied."""
        state = self._state
        return any(
            chunk.name not in state.emptied
            and not all(name in state.extracted for name in chunk.names())
            for unit in self.archive.units if self._included(unit)
            for chunk in unit.chunks
        )

    def _included(self, unit: DataUnit) -> bool:
        filters = self.plan.filters
        return filters is None or filters.table_included(unit.schema, unit.table)

    def _make_placeholders(self, on_disk: dict) -> None:
        """Empty data files for the tables the load leaves out.

        A placeholder an earlier load made for a table this one loads is
        extracted after all, by the data phase.
        """
        state = self._state
        for unit in self.archive.units:
            included = self._included(unit)
            for chunk in unit.chunks:
                if chunk.name in state.emptied:
                    continue
                if included:
                    if chunk.name in state.placeholders:
                        # the loader would take the empty file for the chunk
                        # and load nothing of it: gone until it is extracted
                        if chunk.name in on_disk:
                            os.remove(self._path(chunk.name))
                            del on_disk[chunk.name]
                        state.placeholders.discard(chunk.name)
                        state.extracted.discard(chunk.name)
                    continue
                if chunk.name in state.placeholders:
                    continue
                for name in chunk.names():
                    if name in on_disk:
                        os.remove(self._path(name))
                    state.extracted.discard(name)
                open(self._path(chunk.name), "wb").close()
                state.placeholders.add(chunk.name)
                self._record(placeholder=chunk.name)

    def _extract(self, name: str) -> None:
        """Extracts one entry under a temporary name, then renames it."""
        target = self._path(name)
        temporary = target + _EXTRACTING_SUFFIX
        with self._zip.open(self.archive.member(name)) as source, open(temporary, "wb") as file:
            shutil.copyfileobj(source, file, _COPY_BUFFER)
            self._extracted_bytes += file.tell()
        os.replace(temporary, target)
        self._state.extracted.add(name)
        self._record(extracted=name)

    # --- the thread ------------------------------------------------------

    def start(self, abort) -> None:
        """Starts extracting the data; a failure stops the load by ``abort``."""
        self._abort = abort
        self._thread = threading.Thread(
            target=self._run, name=f"zip-extract-{self.task.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Ends the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def close(self) -> None:
        """Ends the folder's use by this load."""
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if self._log is not None:
            self._log.close()
            self._log = None
        with _active_lock:
            if self._claimed:
                _active.discard(self.directory)
                self._claimed = False

    def _stopping(self) -> bool:
        return self._stop.is_set() or _interrupted(self.task)

    def _run(self) -> None:
        try:
            self._extract_data()
            if self._stopping():
                return
            if _DONE_METADATA not in self._state.extracted:
                self._extract(_DONE_METADATA)
            self.finished = True
            self.task.stage_finished(
                _EXTRACT_STAGE, seconds=round(time.monotonic() - self._stage_started, 1)
            )
            while not self._stopping():
                self._poll()
                self._stop.wait(POLL_SECONDS)
            self._poll(force=True)
        except Exception as error:  # noqa: BLE001 - reported as the load's failure
            reason = str(error).strip() or error.__class__.__name__
            self._abort.request(f"Extracting the dump from the zip failed: {reason}")

    def _queue(self) -> deque:
        """The units with chunks still to extract, each with its chunks.

        The chunks an earlier load extracted and did not load are pending.
        """
        state = self._state
        units = deque()
        for unit in self.archive.units:
            if not self._included(unit):
                continue
            chunks = deque()
            for chunk in unit.chunks:
                if chunk.name in state.emptied:
                    continue
                if all(name in state.extracted for name in chunk.names()):
                    self._pending_chunk(chunk)
                else:
                    chunks.append(chunk)
            if chunks:
                units.append((unit, chunks))
        return units

    def _pending_chunk(self, chunk: DataChunk) -> None:
        if chunk.name not in self._pending:
            self._pending.add(chunk.name)
            self._pending_bytes += chunk.size
            self._peak_bytes = max(self._peak_bytes, self._pending_bytes)
            if chunk.name in self._loaded:
                self._to_empty.add(chunk)

    def _extract_data(self) -> None:
        units = self._queue()
        while units:
            self._poll()
            if self._stopping():
                return
            unit, chunks = units[0]
            chunk = chunks[0]
            if not self._room_for(chunk):
                self._stop.wait(POLL_SECONDS)
                continue
            units.popleft()
            chunks.popleft()
            for name in chunk.names():
                if name not in self._state.extracted:
                    self._extract(name)
            self._pending_chunk(chunk)
            if chunks:
                units.append((unit, chunks))

    def _room_for(self, chunk: DataChunk) -> bool:
        """Whether the budget lets the chunk be extracted now.

        A chunk always may when nothing is pending. When, with the budget
        used up, the loader has no chunk in progress and its progress file
        stays unchanged for :data:`STALL_SECONDS`, it is not taking what was
        extracted - the extractor then stops keeping to the budget, so the
        load never hangs on it. A loader busy with slow chunks is no stall:
        the clock starts again while any chunk is in progress.
        """
        if self._uncapped or not self._pending or self._pending_bytes + chunk.size <= self.plan.budget:
            self._full_since = None
            return True

        now = time.monotonic()
        if self._full_since is None or self._in_progress:
            self._full_since = now
        if now - max(self._full_since, self._last_progress) >= STALL_SECONDS:
            self._uncapped = True
            self.task.message(
                f"The load took none of the extracted chunks for {STALL_SECONDS} "
                "seconds; the rest of the zip is extracted without waiting for it, "
                "which takes more disk space.",
                "warning",
            )
            return True
        return False

    # --- the progress file ----------------------------------------------

    def _poll(self, force: bool = False) -> None:
        """Reads what the load added to its progress file, and acts on it.

        At most every :data:`POLL_SECONDS`, unless forced.
        """
        now = time.monotonic()
        if not force and now - self._polled < POLL_SECONDS:
            return
        self._polled = now

        self._read_progress()
        self._empty_loaded()
        self._log.flush()

    def _read_progress(self) -> None:
        path = self.plan.progress_file
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size == self._progress_size:
            return
        if size < self._progress_offset:
            # the loader rewrote it on starting again: what the earlier run
            # had in progress ended with it
            self._progress_offset = 0
            self._progress_rest = b""
            self._in_progress.clear()
        self._progress_size = size
        self._last_progress = time.monotonic()

        with open(path, "rb") as file:
            file.seek(self._progress_offset)
            data = file.read()
        self._progress_offset += len(data)
        lines = (self._progress_rest + data).split(b"\n")
        self._progress_rest = lines.pop()

        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                self._on_progress(entry)

    def _on_progress(self, entry: dict) -> None:
        op = entry.get("op")
        if op not in ("TABLE-DATA", "TABLE-DATA-BULK"):
            return

        schema = entry.get("schema")
        key = (self.plan.rename.get(schema, schema), entry.get("table"), entry.get("partition") or "")

        if op == "TABLE-DATA":
            chunk = self._by_key.get(key + (entry.get("chunk"),))
            chunks = [chunk] if chunk is not None else []
        else:
            unit = self._units_by_key.get(key)
            chunks = unit.chunks if unit is not None else []

        for chunk in chunks:
            if chunk.name in self._state.placeholders:
                # an earlier load's lines may name a table this one leaves out
                if entry.get("timestamp", 0) >= self._since_ms:
                    self._poison(chunk)
                continue
            if not entry.get("done"):
                # an earlier load's start lines were never finished in this one
                if op == "TABLE-DATA" and entry.get("timestamp", 0) >= self._since_ms:
                    self._in_progress.add(chunk.name)
                continue
            self._in_progress.discard(chunk.name)
            if chunk.name in self._pending:
                self._to_empty.add(chunk)
            elif self.plan.state is not None or entry.get("timestamp", 0) >= self._since_ms:
                # loaded already (an emptied chunk whose record was lost, say):
                # emptied once it is extracted again; an earlier load's lines
                # only count where this load resumes it
                self._loaded.add(chunk.name)

    def _poison(self, chunk: DataChunk) -> None:
        reason = (
            f"The load started on the data of {chunk.unit.label()}, which the "
            "MCP server took its options to leave out, so it was not extracted "
            "from the zip and that table's data is not loaded. Drop what this "
            "load created, and load the zip again: the next load extracts "
            "every table."
        )
        self._state.poisoned = reason
        self._record(poisoned=reason)
        self._log.flush()
        raise RuntimeError(reason)

    def _empty_loaded(self) -> None:
        """Empties the chunks loaded since, keeping their names."""
        for chunk in list(self._to_empty):
            try:
                for name in chunk.names():
                    os.truncate(self._path(name), 0)
            except OSError:
                # still open in the loader (Windows): next time
                continue
            self._to_empty.discard(chunk)
            self._pending.discard(chunk.name)
            self._pending_bytes -= chunk.size
            self._state.emptied.add(chunk.name)
            self._record(emptied=chunk.name)

    def report(self) -> None:
        """Says what the extraction took on disk."""
        self.task.message(
            f"Extracted {_format_bytes(self._extracted_bytes)} from the zip; at most "
            f"{_format_bytes(self._peak_bytes)} of data not yet loaded was on disk at once."
        )


def load_from_zip(task, plan: LoadPlan, run_load) -> dict:
    """Loads a zip, extracting it next to it while the load runs.

    The folder is removed once the load completed, and kept otherwise, so
    that a load of the same zip resumes there.

    Args:
        task (Task): The task the load runs for.
        plan (LoadPlan): From :func:`plan_load`.
        run_load: Runs the load: ``run_load(directory, options, abort)``, with
            ``abort`` the :class:`mcp_plugin.lib.util_process.Abort` the
            extractor stops it by.

    Returns:
        dict: The task's result.
    """
    from mcp_plugin.lib.util_process import Abort

    extractor = Extractor(task, plan)
    abort = Abort()
    try:
        extractor.prepare()
        extractor.start(abort)
        try:
            run_load(plan.directory, plan.options, abort)
        finally:
            extractor.stop()
        if abort.reason is not None:
            raise RuntimeError(abort.reason)
        if not extractor.finished:
            raise RuntimeError("The load completed before the zip was extracted.")
    finally:
        extractor.close()

    extractor.report()
    shutil.rmtree(plan.directory, ignore_errors=True)
    return {"url": plan.archive.path}
