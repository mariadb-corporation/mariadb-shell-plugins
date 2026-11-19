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

"""Tests for dumps in a single .mariadb.dump.zip file (lib/util_zip.py).

Without a server: the filter port against the shell's rules, the archive's
checks and how it finds a dump's data files, packing, and the planning of a
load. The streaming load then runs against a fake loader, which loads what it
finds in the folder in the order the shell does and writes the shell's
progress lines: it shows the budget kept, loaded chunks emptied, the tables a
filter leaves out never extracted, a failed load resumed without loading a
chunk twice, a wrong filter caught, a stalled loader not hanging the load, and
a broken zip stopping the load. The end-to-end run against a real shell is in
test_util.py.
"""
# cSpell:ignore mysqlsh zstd

import json
import os
import threading
import time
import zipfile

import pytest

from mysqlsh import task_manager

from mcp_plugin.lib import util_zip
from mcp_plugin.lib.util_process import Abort

KB = 1024


# --- a dump, made up ----------------------------------------------------------


def _write(directory, name, data):
    with open(os.path.join(directory, name), "wb") as file:
        file.write(data if isinstance(data, bytes) else json.dumps(data).encode("utf-8"))


def make_dump(directory, chunk_size=100 * KB, chunks=10):
    """A dump of two schemas, written as the shell writes one.

    shop.orders is chunked; shop.parted has two partitions, one named p@1;
    shop.1 is a table named by digits; shop.small is not chunked; other.t is
    in a second schema. Returns the data units, as (schema, table, partition,
    [data file names in order]).
    """
    os.makedirs(directory, exist_ok=True)
    units = []

    def data(name, size):
        _write(directory, name, os.urandom(size))
        _write(directory, name + ".idx", os.urandom(16))

    def chunked(schema, table, partition, basename, count):
        names = []
        for i in range(count):
            last = i == count - 1
            name = f"{basename}@{'@' if last else ''}{i}.tsv.zst"
            data(name, chunk_size)
            names.append(name)
        units.append((schema, table, partition, names))

    _write(directory, "@.json", {"schemas": ["shop", "other"], "basenames": {"shop": "shop", "other": "other"}})
    _write(directory, "@.sql", b"-- preamble")
    _write(directory, "@.post.sql", b"-- postamble")
    _write(directory, "shop.json", {
        "schema": "shop", "tables": ["orders", "parted", "1", "small"],
        "basenames": {"orders": "shop@orders", "parted": "shop@parted", "1": "shop@1", "small": "shop@small"},
    })
    _write(directory, "shop.sql", b"CREATE SCHEMA shop;")
    _write(directory, "other.json", {"schema": "other", "tables": ["t"], "basenames": {"t": "other@t"}})
    _write(directory, "other.sql", b"CREATE SCHEMA other;")

    _write(directory, "shop@orders.json", {"extension": "tsv.zst", "chunking": True})
    _write(directory, "shop@orders.sql", b"CREATE TABLE orders (id INT);")
    chunked("shop", "orders", "", "shop@orders", chunks)

    _write(directory, "shop@parted.json", {
        "extension": "tsv.zst", "chunking": True,
        "basenames": {"p0": "shop@parted@p0", "p@1": "shop@parted@p%401"},
    })
    chunked("shop", "parted", "p0", "shop@parted@p0", 2)
    chunked("shop", "parted", "p@1", "shop@parted@p%401", 2)

    _write(directory, "shop@1.json", {"extension": "tsv.zst", "chunking": True})
    chunked("shop", "1", "", "shop@1", 3)

    _write(directory, "shop@small.json", {"extension": "tsv.zst", "chunking": False})
    data("shop@small.tsv.zst", 10 * KB)
    units.append(("shop", "small", "", ["shop@small.tsv.zst"]))

    _write(directory, "other@t.json", {"extension": "tsv.zst", "chunking": True})
    chunked("other", "t", "", "other@t", 2)

    _write(directory, "@.done.json", {"end": True})
    return units


def make_zip(tmp_path, **kwargs):
    directory = str(tmp_path / "dump")
    units = make_dump(directory, **kwargs)
    zip_path = str(tmp_path / f"shop{util_zip.ZIP_SUFFIX}")
    util_zip.pack(directory, zip_path)
    return zip_path, units


def new_task():
    return task_manager.Task("util", "load_dump", "a test load")


# --- the filter port ----------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("shop.orders", ("shop", "orders")),
    ("`shop`.`orders`", ("shop", "orders")),
    ("`my.shop`.orders", ("my.shop", "orders")),
    ("`a``b`.`c``d`", ("a`b", "c`d")),
    ("shop.`kühl`", ("shop", "kühl")),
    ("$s.t_1", ("$s", "t_1")),
])
def test_a_table_filter_is_split_as_the_shell_splits_it(text, expected):
    assert util_zip._split_schema_table(text) == expected


@pytest.mark.parametrize("text", [
    "orders",          # no schema
    "shop.",           # no table
    "shop.orders.x",   # one name too many
    "shop.kühl",       # unquoted, but not ASCII
    "shop.123",        # unquoted digits
    "`shop.orders",    # unterminated
    "shop orders",
])
def test_a_table_filter_the_shell_refuses_is_refused(text):
    with pytest.raises(ValueError):
        util_zip._split_schema_table(text)


@pytest.mark.parametrize("text,expected", [
    ("shop", "shop"),
    ("`shop`", "shop"),
    ("`a``b`", "a`b"),
    # what cannot be unquoted is taken as written
    ("kühl", "kühl"),
    ("my.shop", "my.shop"),
    ("123", "123"),
])
def test_a_schema_filter_is_unquoted_where_it_can_be(text, expected):
    assert util_zip._parse_schema(text) == expected


@pytest.mark.parametrize("options,schema,table,included", [
    ({}, "shop", "orders", True),
    ({"loadData": False}, "shop", "orders", False),
    ({"includeSchemas": ["other"]}, "shop", "orders", False),
    ({"includeSchemas": ["other"]}, "other", "t", True),
    ({"excludeSchemas": ["shop"]}, "shop", "orders", False),
    ({"excludeSchemas": ["shop"], "includeTables": ["shop.orders"]}, "shop", "orders", False),
    ({"includeTables": ["shop.orders"]}, "shop", "orders", True),
    ({"includeTables": ["shop.orders"]}, "shop", "small", False),
    ({"includeTables": ["shop.orders"], "excludeTables": ["shop.orders"]}, "shop", "orders", False),
    ({"excludeTables": ["`shop`.`orders`"]}, "shop", "orders", False),
    ({"excludeTables": ["shop.orders"]}, "shop", "small", True),
    # a schema's filter does not reach another schema's tables of that name
    ({"includeTables": ["other.orders"]}, "shop", "orders", False),
    # names match exactly, as in the shell
    ({"includeTables": ["shop.Orders"]}, "shop", "orders", False),
])
def test_the_filter_takes_the_tables_the_shell_takes(options, schema, table, included):
    assert util_zip.TableFilter(options).table_included(schema, table) is included


def test_filters_the_port_cannot_read_leave_every_table_extracted():
    assert util_zip.TableFilter.from_options({"includeTables": ["noschema"]}) is None
    assert util_zip.TableFilter.from_options({"includeTables": "shop.orders"}) is None
    assert util_zip.TableFilter.from_options({"includeTables": ["shop.orders"]}) is not None


# --- the archive -------------------------------------------------------------


def test_the_archive_finds_every_data_file_by_the_metadata(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=1 * KB)
    archive = util_zip.DumpArchive.open(zip_path)

    found = {
        (u.schema, u.table, u.partition): [c.name for c in u.chunks] for u in archive.units
    }
    assert found == {(s, t, p): names for s, t, p, names in units}

    orders = next(u for u in archive.units if u.table == "orders")
    assert [c.index for c in orders.chunks] == list(range(10))
    assert [c.last for c in orders.chunks] == [False] * 9 + [True]
    assert orders.chunks[0].idx_name == "shop@orders@0.tsv.zst.idx"

    small = next(u for u in archive.units if u.table == "small")
    assert [(c.index, c.idx_name) for c in small.chunks] == [(-1, "shop@small.tsv.zst.idx")]

    # the metadata, @.json first and @.done.json in none of it
    assert archive.metadata[0] == "@.json"
    assert "@.done.json" not in archive.metadata
    assert "shop@orders.sql" in archive.metadata
    assert not any(name.endswith(".tsv.zst") for name in archive.metadata)


def test_an_earlier_loads_progress_file_in_the_zip_is_never_extracted(tmp_path):
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=2)
    _write(directory, "load-progress.server-1.json", b'{"op":"SERVER-UUID"}\n')
    zip_path = str(tmp_path / f"x{util_zip.ZIP_SUFFIX}")
    util_zip.pack(directory, zip_path)

    archive = util_zip.DumpArchive.open(zip_path)
    assert archive.ignored == ["load-progress.server-1.json"]
    assert "load-progress.server-1.json" not in archive.metadata


def _zip_with(tmp_path, entries, done=True):
    zip_path = str(tmp_path / f"bad{util_zip.ZIP_SUFFIX}")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("@.json", json.dumps({"schemas": []}))
        if done:
            archive.writestr("@.done.json", "{}")
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                archive.writestr(entry, b"x")
            else:
                archive.writestr(entry, b"x")
    return zip_path


@pytest.mark.parametrize("name", ["../evil", "sub/file", "/abs", "C:evil", "back\\slash"])
def test_an_entry_outside_the_top_of_the_zip_is_refused(tmp_path, name):
    with pytest.raises(util_zip.ZipDumpError, match="no file of a dump"):
        util_zip.DumpArchive.open(_zip_with(tmp_path, [name]))


def test_a_symlink_in_the_zip_is_refused(tmp_path):
    link = zipfile.ZipInfo("link")
    link.external_attr = (0o120777 << 16)
    with pytest.raises(util_zip.ZipDumpError, match="no regular file"):
        util_zip.DumpArchive.open(_zip_with(tmp_path, [link]))


def test_a_name_twice_in_the_zip_is_refused(tmp_path):
    with pytest.warns(UserWarning):
        zip_path = _zip_with(tmp_path, ["a.sql", "a.sql"])
    with pytest.raises(util_zip.ZipDumpError, match="twice"):
        util_zip.DumpArchive.open(zip_path)


def test_a_dump_that_did_not_finish_is_refused(tmp_path):
    with pytest.raises(util_zip.ZipDumpError, match="no @.done.json"):
        util_zip.DumpArchive.open(_zip_with(tmp_path, [], done=False))


def test_a_file_that_is_no_zip_is_refused(tmp_path):
    path = tmp_path / f"x{util_zip.ZIP_SUFFIX}"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(util_zip.ZipDumpError, match="no valid ZIP"):
        util_zip.DumpArchive.open(str(path))


# --- the dump ----------------------------------------------------------------


def test_a_dump_is_packed_stored_in_order_and_its_folder_emptied(tmp_path):
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=3)
    names = set(os.listdir(directory))
    zip_path = str(tmp_path / f"out{util_zip.ZIP_SUFFIX}")
    task = new_task()

    files, size = util_zip.pack(directory, zip_path, task)

    assert files == len(names) and size == os.path.getsize(zip_path)
    assert os.listdir(directory) == []
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        assert archive.testzip() is None
    order = [info.filename for info in infos]
    assert set(order) == names
    assert order[0] == "@.json" and order[-1] == "@.done.json"
    assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
    assert [s["name"] for s in task.stages] == ["Packing the dump"]
    assert task.progress_info["percent"] == 100.0


def test_a_cancelled_pack_leaves_no_zip(tmp_path):
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=2)
    zip_path = str(tmp_path / f"out{util_zip.ZIP_SUFFIX}")
    task = new_task()
    task.cancel_requested = True

    with pytest.raises(RuntimeError, match="Interrupted by user"):
        util_zip.pack(directory, zip_path, task)
    assert not os.path.exists(zip_path)
    assert not os.path.exists(zip_path + ".part")


def test_dump_to_zip_removes_its_folder_also_when_the_dump_fails(tmp_path):
    zip_path = str(tmp_path / f"out{util_zip.ZIP_SUFFIX}")

    def failing(directory):
        make_dump(directory, chunk_size=KB, chunks=1)
        raise RuntimeError("the dump failed")

    with pytest.raises(RuntimeError, match="the dump failed"):
        util_zip.dump_to_zip(new_task(), zip_path, failing)
    assert not os.path.exists(util_zip.dump_directory(zip_path))
    assert not os.path.exists(zip_path)

    result = util_zip.dump_to_zip(
        new_task(), zip_path, lambda d: make_dump(d, chunk_size=KB, chunks=1)
    )
    assert result["output_url"] == zip_path and result["files"] > 0
    assert not os.path.exists(util_zip.dump_directory(zip_path))
    util_zip.DumpArchive.open(zip_path)


def test_a_dump_never_overwrites_a_zip_or_a_left_over_folder(tmp_path):
    zip_path = str(tmp_path / f"out{util_zip.ZIP_SUFFIX}")
    util_zip.check_dump_target(zip_path)

    os.makedirs(util_zip.dump_directory(zip_path))
    util_zip.check_dump_target(zip_path)  # empty: the shell takes it
    _write(util_zip.dump_directory(zip_path), "@.json", b"{}")
    with pytest.raises(util_zip.ZipDumpError, match="left over"):
        util_zip.check_dump_target(zip_path)

    with open(zip_path, "wb"):
        pass
    with pytest.raises(util_zip.ZipDumpError, match="already exists"):
        util_zip.check_dump_target(zip_path)

    with pytest.raises(util_zip.ZipDumpError, match="does not exist"):
        util_zip.check_dump_target(str(tmp_path / "nowhere" / f"x{util_zip.ZIP_SUFFIX}"))


# --- planning a load ---------------------------------------------------------


@pytest.mark.parametrize("options,message", [
    ({"progressFile": ""}, "progressFile cannot be empty"),
    ({"waitDumpTimeout": 10}, "waitDumpTimeout is set by the tool"),
    ({"progressFile": 1}, "must be a path"),
])
def test_options_a_zip_load_cannot_take_are_refused(tmp_path, options, message):
    zip_path, _ = make_zip(tmp_path, chunk_size=KB, chunks=1)
    with pytest.raises(util_zip.ZipDumpError, match=message):
        util_zip.plan_load(util_zip.DumpArchive.open(zip_path), options)


def test_a_folder_next_to_the_zip_the_server_did_not_make_is_refused(tmp_path):
    zip_path, _ = make_zip(tmp_path, chunk_size=KB, chunks=1)
    directory = util_zip.extraction_directory(zip_path)
    os.makedirs(directory)
    _write(directory, "mine.txt", b"keep me")
    with pytest.raises(util_zip.ZipDumpError, match="not made by the MCP server"):
        util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {})


def test_the_budget_is_twice_the_threads_largest_chunks_and_a_gigabyte_at_the_least(tmp_path):
    zip_path, _ = make_zip(tmp_path, chunk_size=KB, chunks=1)
    archive = util_zip.DumpArchive.open(zip_path)
    assert util_zip.plan_load(archive, {}).budget == util_zip.MIN_BUDGET_BYTES
    archive.chunks["shop@1@0.tsv.zst"].size = 200 << 20
    assert util_zip.plan_load(archive, {"threads": 8}).budget == 2 * 8 * (200 << 20)
    assert util_zip.plan_load(archive, {}, budget_mb=5).budget == 5 << 20
    with pytest.raises(util_zip.ZipDumpError, match="positive"):
        util_zip.plan_load(archive, {}, budget_mb=0)


def test_a_disk_without_room_for_the_budget_is_refused(tmp_path, monkeypatch):
    zip_path, _ = make_zip(tmp_path, chunk_size=KB, chunks=1)
    monkeypatch.setattr(util_zip.shutil, "disk_usage", lambda path: type("U", (), {"free": 1024})())
    with pytest.raises(util_zip.ZipDumpError, match="needs .* free"):
        util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {})


def test_the_load_waits_for_the_extraction_and_maps_its_target_schema(tmp_path):
    directory = str(tmp_path / "dump")
    os.makedirs(directory)
    _write(directory, "@.json", {"schemas": ["shop"]})
    _write(directory, "shop.json", {"tables": []})
    _write(directory, "@.done.json", {})
    zip_path = str(tmp_path / f"one{util_zip.ZIP_SUFFIX}")
    util_zip.pack(directory, zip_path)

    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {"schema": "copy"})
    assert plan.options["waitDumpTimeout"] == util_zip.WAIT_DUMP_TIMEOUT_SECONDS
    assert plan.rename == {"copy": "shop"}
    assert plan.directory == util_zip.extraction_directory(os.path.abspath(zip_path))


# --- the load, against a fake loader ----------------------------------------


class FakeLoader:
    """Loads what it finds in the folder as the shell's loader does.

    Each round it loads one chunk of every table that has its next chunk
    complete in the folder, in the order of the chunks, writing a start and
    an end line to the progress file, and ends once @.done.json is there and
    every table it loads is loaded. Its progress file survives it, so a
    second run resumes as the shell does.
    """

    def __init__(self, units, include=None, observe=None, fail_after=None, start_anyway=(),
                 wait_for_done=False):
        self.units = units
        self.include = include or (lambda schema, table: True)
        self.observe = observe
        self.fail_after = fail_after
        self.start_anyway = start_anyway
        self.wait_for_done = wait_for_done
        self.loaded = []
        self.options = []

    def __call__(self, directory, options, abort):
        self.options.append(dict(options))
        progress = options.get("progressFile") or os.path.join(directory, "load-progress.fake.json")
        done = set()
        if os.path.exists(progress):
            with open(progress) as file:
                for line in file:
                    entry = json.loads(line)
                    if entry["op"] == "TABLE-DATA" and entry["done"]:
                        done.add((entry["schema"], entry["table"], entry.get("partition", ""), entry["chunk"]))
        loaded_now = 0
        deadline = time.monotonic() + 30

        def log(entry):
            entry["timestamp"] = int(time.time() * 1000)
            with open(progress, "a") as file:
                file.write(json.dumps(entry) + "\n")

        log({"op": "SERVER-UUID", "done": True})
        units = [u for u in self.units if self.include(u[0], u[1]) or (u[0], u[1]) in self.start_anyway]

        while True:
            if abort.reason is not None:
                raise RuntimeError(abort.reason)
            names = set(os.listdir(directory))
            complete = "@.done.json" in names
            progressed = False
            if not self.wait_for_done or complete:
                for schema, table, partition, chunks in units:
                    for index, name in enumerate(chunks):
                        key = (schema, table, partition, -1 if name.endswith("small.tsv.zst") else index)
                        if key in done:
                            continue
                        if name not in names:
                            break
                        if self.observe:
                            self.observe(directory)
                        entry = {"op": "TABLE-DATA", "schema": schema, "table": table, "chunk": key[3]}
                        if partition:
                            entry["partition"] = partition
                        log({**entry, "done": False})
                        self.loaded.append((key, os.path.getsize(os.path.join(directory, name))))
                        log({**entry, "done": True, "rows": 1})
                        done.add(key)
                        loaded_now += 1
                        progressed = True
                        if self.fail_after is not None and loaded_now >= self.fail_after:
                            raise RuntimeError("the loader failed")
                        break
            wanted = sum(len(u[3]) for u in units if self.include(u[0], u[1]))
            if complete and len(done) >= wanted:
                return
            if not progressed:
                time.sleep(0.01)
            if time.monotonic() > deadline:
                raise AssertionError("the fake loader waited for data in vain")


def _on_disk(directory):
    """The bytes of data files (chunk and index) in the folder, not emptied."""
    return sum(
        os.path.getsize(os.path.join(directory, name))
        for name in os.listdir(directory)
        if ".tsv.zst" in name and not name.endswith(".extracting")
    )


def test_a_zip_loads_within_its_budget_and_its_folder_is_removed(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=100 * KB, chunks=20)
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {}, budget_mb=1)
    largest = plan.archive.largest_chunk
    peaks = []
    loader = FakeLoader(units, observe=lambda d: peaks.append(_on_disk(d)))
    task = new_task()

    result = util_zip.load_from_zip(task, plan, loader)

    assert result == {"url": os.path.abspath(zip_path)}
    assert len(loader.loaded) == sum(len(u[3]) for u in units)
    # every chunk was loaded whole
    sizes = {name: size for name, size in plan.archive.entries.items()}
    assert all(size > 0 for _, size in loader.loaded)
    # never more than the budget on disk, but for the chunk extracted last
    assert max(peaks) <= plan.budget + largest
    assert plan.archive.data_bytes > 2 * plan.budget
    assert not os.path.exists(plan.directory)
    assert loader.options[0]["waitDumpTimeout"] == util_zip.WAIT_DUMP_TIMEOUT_SECONDS
    assert task.stages[0]["name"] == "Extracting the dump"
    assert task.stages[0]["status"] == "completed"
    assert "at most" in task.messages[-1]["text"]
    assert sizes


def test_the_tables_a_filter_leaves_out_are_never_extracted(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=10 * KB, chunks=3)
    options = {"excludeTables": ["shop.orders"], "excludeSchemas": ["other"]}
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), options)
    seen = {}

    def observe(directory):
        for name in os.listdir(directory):
            if name.startswith(("shop@orders@", "other@t@")) and ".tsv.zst" in name:
                seen[name] = max(seen.get(name, 0), os.path.getsize(os.path.join(directory, name)))

    filters = util_zip.TableFilter(options)
    loader = FakeLoader(units, include=filters.table_included, observe=observe)
    util_zip.load_from_zip(new_task(), plan, loader)

    loaded_tables = {key[:2] for key, _ in loader.loaded}
    assert ("shop", "orders") not in loaded_tables and ("other", "t") not in loaded_tables
    # the left-out tables' data files were empty placeholders all along
    assert seen and set(seen.values()) == {0}
    assert not any(name.endswith(".idx") for name in seen)


def test_a_failed_load_resumes_with_its_progress_file_and_loads_no_chunk_twice(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=20 * KB, chunks=10)
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {}, budget_mb=1)
    first = FakeLoader(units, fail_after=6)

    with pytest.raises(RuntimeError, match="the loader failed"):
        util_zip.load_from_zip(new_task(), plan, first)

    # the folder stays, with the loaded chunks emptied but kept by name
    directory = plan.directory
    assert os.path.isdir(directory)
    loaded = [key for key, _ in first.loaded]
    state = util_zip._State.read(directory)
    assert len(state.emptied) == len(loaded)
    assert all(os.path.getsize(os.path.join(directory, n)) == 0 for n in state.emptied)
    progress = state.progress_file
    assert progress == os.path.join(directory, "load-progress.fake.json")

    # the next load of the zip is given that progress file
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {}, budget_mb=1)
    assert plan.wipe is False and plan.options["progressFile"] == progress
    second = FakeLoader(units)
    util_zip.load_from_zip(new_task(), plan, second)

    keys = [key for key, _ in second.loaded]
    assert not set(keys) & set(loaded)
    assert len(keys) + len(loaded) == sum(len(u[3]) for u in units)
    assert all(size > 0 for _, size in second.loaded)
    assert not os.path.exists(directory)


def test_another_progress_file_for_a_partly_loaded_folder_is_refused(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=10 * KB, chunks=4)
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {})
    with pytest.raises(RuntimeError):
        util_zip.load_from_zip(new_task(), plan, FakeLoader(units, fail_after=2))

    archive = util_zip.DumpArchive.open(zip_path)
    with pytest.raises(util_zip.ZipDumpError, match="give that one as progressFile"):
        util_zip.plan_load(archive, {"progressFile": str(tmp_path / "elsewhere.json")})

    # resetProgress starts over: the folder is wiped
    plan = util_zip.plan_load(archive, {"resetProgress": True})
    assert plan.wipe is True and "progressFile" not in plan.options
    loader = FakeLoader(units)
    util_zip.load_from_zip(new_task(), plan, loader)
    assert len(loader.loaded) == sum(len(u[3]) for u in units)


def test_a_changed_zip_starts_its_folder_over(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=10 * KB, chunks=4)
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {})
    with pytest.raises(RuntimeError):
        util_zip.load_from_zip(new_task(), plan, FakeLoader(units, fail_after=2))

    os.utime(zip_path, ns=(1, 1))
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {})
    assert plan.wipe is True


def test_a_table_the_filter_got_wrong_fails_the_load_loudly(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=10 * KB, chunks=3)
    options = {"excludeTables": ["shop.orders"]}
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), options)
    filters = util_zip.TableFilter(options)
    # this loader loads shop.orders after all
    loader = FakeLoader(units, include=filters.table_included, start_anyway=[("shop", "orders")])
    task = new_task()

    with pytest.raises(RuntimeError, match="`shop`.`orders`, which the MCP server took"):
        util_zip.load_from_zip(task, plan, loader)

    # the next load wipes the folder and extracts every table
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), options)
    assert plan.wipe is True and plan.filters is None


def test_a_loader_that_takes_nothing_does_not_hang_the_load(tmp_path, monkeypatch):
    monkeypatch.setattr(util_zip, "STALL_SECONDS", 0.5)
    zip_path, units = make_zip(tmp_path, chunk_size=200 * KB, chunks=10)
    plan = util_zip.plan_load(util_zip.DumpArchive.open(zip_path), {}, budget_mb=1)
    # loads nothing before the dump is complete, as a loader stuck elsewhere
    loader = FakeLoader(units, wait_for_done=True)
    task = new_task()

    util_zip.load_from_zip(task, plan, loader)

    assert len(loader.loaded) == sum(len(u[3]) for u in units)
    assert any("without waiting for it" in m["text"] for m in task.messages)


def test_a_broken_zip_stops_the_load_with_the_reason(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=50 * KB, chunks=4)
    archive = util_zip.DumpArchive.open(zip_path)
    with zipfile.ZipFile(zip_path) as zipped:
        info = zipped.getinfo("shop@orders@@3.tsv.zst")
    # a byte of the stored data changed: its CRC no longer matches
    with open(zip_path, "r+b") as file:
        file.seek(info.header_offset + 30 + len(info.filename.encode()) + 100)
        byte = file.read(1)
        file.seek(-1, os.SEEK_CUR)
        file.write(bytes([byte[0] ^ 0xFF]))
    plan = util_zip.plan_load(archive, {})
    loader = FakeLoader(units)

    with pytest.raises(RuntimeError, match="Extracting the dump from the zip failed: Bad CRC"):
        util_zip.load_from_zip(new_task(), plan, loader)


def test_two_loads_of_one_zip_at_once_are_refused(tmp_path):
    zip_path, units = make_zip(tmp_path, chunk_size=10 * KB, chunks=2)
    archive = util_zip.DumpArchive.open(zip_path)
    first_started = threading.Event()
    release = threading.Event()
    errors = []

    def slow(directory, options, abort):
        first_started.set()
        release.wait(10)
        FakeLoader(units)(directory, options, abort)

    thread = threading.Thread(
        target=lambda: util_zip.load_from_zip(new_task(), util_zip.plan_load(archive, {}), slow)
    )
    thread.start()
    assert first_started.wait(10)
    try:
        util_zip.load_from_zip(new_task(), util_zip.plan_load(archive, {}), FakeLoader(units))
    except RuntimeError as error:
        errors.append(str(error))
    # the refused load did not release the first one's claim
    with pytest.raises(RuntimeError, match="being loaded by another task"):
        util_zip.load_from_zip(new_task(), util_zip.plan_load(archive, {}), FakeLoader(units))
    release.set()
    thread.join(30)
    assert errors and "being loaded by another task" in errors[0]


# --- the abort the extractor stops a load by ----------------------------------


def test_an_abort_reaches_the_utility_running_and_one_started_later():
    stopped = []
    abort = Abort()
    abort.attach(lambda: stopped.append("first"))
    abort.request("why")
    abort.request("a later reason")
    assert stopped == ["first"] and abort.reason == "why"
    abort.attach(lambda: stopped.append("second"))
    assert stopped == ["first", "second"]
    abort.attach(None)


# --- what a dump holds -------------------------------------------------------


def _complete_metadata(directory):
    """Adds what the shell writes to @.json and @.done.json of a schema dump."""
    with open(os.path.join(directory, "@.json")) as file:
        root = json.load(file)
    root.update({
        "origin": "dumpSchemas", "dumper": "mysqlsh Ver 26.11.2", "server": "db1",
        "serverVersion": "11.8.3-MariaDB", "user": "root", "begin": "2026-11-19 12:00:00",
        "options": {"threads": 2}, "estimatedRowCount": 99, "estimatedDataSize": 999,
    })
    _write(directory, "@.json", root)
    _write(directory, "@.done.json", {
        "end": "2026-11-19 12:01:00", "dataBytes": 5000,
        "tableRows": {"shop": {"orders": 10, "small": 2}, "other": {"t": 3}},
        "tableDataBytes": {"shop": {"orders": 4000, "small": 500}, "other": {"t": 500}},
    })


def test_a_dump_folder_and_its_zip_tell_the_same(tmp_path):
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=2)
    _complete_metadata(directory)
    folder = util_zip.inspect_dump(directory)

    zip_path = str(tmp_path / f"shop{util_zip.ZIP_SUFFIX}")
    util_zip.pack(directory, zip_path)
    zipped = util_zip.inspect_dump(zip_path)

    assert folder["format"] == "folder" and zipped["format"] == "zip"
    assert zipped["size_bytes"] == os.path.getsize(zip_path)
    for info in (folder, zipped):
        assert info["kind"] == "dumpSchemas"
        assert info["complete"] is True
        assert info["source"] == {"server": "db1", "server_version": "11.8.3-MariaDB", "user": "root"}
        assert (info["begin"], info["end"]) == ("2026-11-19 12:00:00", "2026-11-19 12:01:00")
        assert info["options"] == {"threads": 2}
        assert info["includes_users"] is False
        assert info["rows"] == 15 and info["data_bytes"] == 5000
        assert "estimated_rows" not in info
        assert [s["name"] for s in info["schemas"]] == ["shop", "other"]
        shop = info["schemas"][0]
        assert shop["tables"] == ["orders", "parted", "1", "small"] and shop["table_count"] == 4
        assert (shop["rows"], shop["data_bytes"]) == (12, 4500)
    keys = lambda info: {k: v for k, v in info.items() if k not in ("url", "format", "size_bytes")}
    assert keys(folder) == keys(zipped)


def test_a_dump_still_being_written_is_told_with_its_estimates(tmp_path):
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=1)
    _complete_metadata(directory)
    os.remove(os.path.join(directory, "@.done.json"))
    os.remove(os.path.join(directory, "other.json"))  # not written yet
    _write(directory, "@.users.sql", b"-- users")

    info = util_zip.inspect_dump(directory)

    assert info["complete"] is False and info["end"] is None
    assert (info["estimated_rows"], info["estimated_data_bytes"]) == (99, 999)
    assert "rows" not in info and "rows" not in info["schemas"][0]
    assert info["includes_users"] is True


def test_a_long_list_of_tables_is_cut_but_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(util_zip, "INSPECT_NAMES_LIMIT", 2)
    directory = str(tmp_path / "dump")
    make_dump(directory, chunk_size=KB, chunks=1)
    shop = util_zip.inspect_dump(directory)["schemas"][0]
    assert shop["tables"] == ["orders", "parted"] and shop["table_count"] == 4


@pytest.mark.parametrize("what", ["missing", "empty folder", "no zip"])
def test_what_is_no_dump_is_refused(tmp_path, what):
    path = str(tmp_path / "x")
    if what == "empty folder":
        os.makedirs(path)
    elif what == "no zip":
        with open(path, "w") as file:
            file.write("text")
    with pytest.raises(util_zip.ZipDumpError, match="does not exist|no @.json|no valid ZIP"):
        util_zip.inspect_dump(path)
