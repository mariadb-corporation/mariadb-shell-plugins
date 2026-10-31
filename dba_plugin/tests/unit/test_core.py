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

"""Tests for the dba plugin's core: the vendor interface, the API keys, the
operations and the shell functions, over a stub vendor."""

# cSpell:ignore mysqlsh MariaDB

import threading
import time

import pytest

import mysqlsh
from mysqlsh import task_manager

from dba_plugin.lib import api_keys, object_types, objects, operations, vendor as vendor_lib, vendors
from dba_plugin.lib import settings_fields as sf
from dba_plugin.lib.general import DbaError, SettingError
from dba_plugin.lib.rest_client import VendorApiError


class StubVendor(vendor_lib.Vendor):
    """A vendor answering from memory."""

    name = "stubcloud"
    display_name = "Stub Cloud"
    feature_sets = (object_types.CORE_COMPUTE,)
    api_key_help = "Stub keys come from nowhere."
    objects = []
    deployed = []
    deleted = []
    keys = []
    hold = False
    flaky = 0
    delete_fails = False

    def __init__(self, api_key=None, project=None):
        super().__init__(api_key, project)
        StubVendor.keys.append(api_key)

    def create_client(self):
        return None

    def _info_vms(self):
        return {"sizes": [{"name": "small"}]}

    def _list_vms(self):
        return list(StubVendor.objects)

    def _create_vms(self, settings):
        settings = vendor_lib.check_settings("vms", settings, supported=("size",))
        StubVendor.deployed.append(settings)
        deployed = vendor_lib.make_object("vms", 9, settings["name"], "starting", labels=settings["labels"])
        StubVendor.objects.append(deployed)
        return dict(deployed)

    @staticmethod
    def _check_vms(settings):
        if settings.get("size") == "huge":
            raise DbaError("Stub Cloud has no size 'huge'.")

    def _status_vms(self, obj):
        # Asked by id, as the vendors are. A VM starts on the first look, and
        # runs from the second on, unless a test holds it at a status of its
        # own; the first `flaky` asks fail as an API blip does.
        if StubVendor.flaky:
            StubVendor.flaky -= 1
            raise VendorApiError("The Stub Cloud API refused GET with HTTP 502: bad gateway", 502)
        stored = next((o for o in StubVendor.objects if o["id"] == obj["id"]), None)
        if stored is None:
            raise VendorApiError("The Stub Cloud API refused GET with HTTP 404: not found", 404)
        reported = dict(stored)
        if stored["status"] == "starting" and not StubVendor.hold:
            stored["status"] = "running"
        return reported

    def _delete_vms(self, obj):
        StubVendor.deleted.append(obj["id"])
        if StubVendor.delete_fails:
            next(o for o in StubVendor.objects if o["id"] == obj["id"])["status"] = "delete_failed"
        else:
            StubVendor.objects[:] = [o for o in StubVendor.objects if o["id"] != obj["id"]]
        return {"action": "delete"}

    def _list_volumes(self):
        return []


class NativeVendor(StubVendor):
    """The same, with projects of its own."""

    name = "nativecloud"
    display_name = "Native Cloud"
    feature_sets = (object_types.MANAGED_SERVICE,)
    native_projects = True


@pytest.fixture
def stub(monkeypatch, clean_api_keys):
    monkeypatch.setitem(vendors._vendors, StubVendor.name, StubVendor)
    monkeypatch.setitem(vendors._vendors, NativeVendor.name, NativeVendor)
    StubVendor.objects = [
        vendor_lib.make_object("vms", 1, "db1", "running", "z1", {"env": "prod", vendor_lib.PROJECT_LABEL: "alpha"}),
        vendor_lib.make_object("vms", 2, "db2", "off", "z1", {"env": "test"}),
        vendor_lib.make_object("vms", 3, "twin", "running"),
        vendor_lib.make_object("vms", 4, "twin", "running"),
    ]
    StubVendor.deployed, StubVendor.deleted, StubVendor.keys = [], [], []
    StubVendor.hold, StubVendor.flaky, StubVendor.delete_fails = False, 0, False
    monkeypatch.setattr(operations, "POLL_INTERVAL", 0.01)
    task_manager.clear()
    yield StubVendor
    for task in task_manager.list_tasks():
        task.cancel()
    task_manager.clear()


def _ended(answer, owner=None, timeout=5.0):
    """The snapshot of the task a deploy or delete started, once it ended."""
    task = task_manager.get(answer["task_id"], owner)
    deadline = time.monotonic() + timeout
    while task.status not in task_manager.FINISHED_STATES:
        assert time.monotonic() < deadline, f"the task is still {task.status}"
        task.wait_for_change(task.version, 0.1)
    return task.snapshot()


# --- The interface ------------------------------------------------------------


@pytest.mark.parametrize(
    "tags, expected",
    [
        (None, {}),
        ("", {}),
        ({"env": "prod", "any": None}, {"env": "prod", "any": None}),
        (["env=prod", "any"], {"env": "prod", "any": None}),
        ("env=prod, any", {"env": "prod", "any": None}),
    ],
)
def test_tags_are_taken_in_every_form(tags, expected):
    assert vendor_lib.parse_tags(tags) == expected


def test_settings_are_checked():
    with pytest.raises(DbaError, match="settings must be a dict"):
        vendor_lib.check_settings("vms", "name=x")
    with pytest.raises(DbaError, match="Unsupported settings for vms: colour"):
        vendor_lib.check_settings("vms", {"name": "x", "colour": "red"})
    with pytest.raises(DbaError, match="Missing settings for vms: name, image"):
        vendor_lib.check_settings("vms", {}, required=("name", "image"))
    with pytest.raises(DbaError, match="The setting labels must be a dict"):
        vendor_lib.check_settings("vms", {"name": "x", "labels": ["a"]})

    assert vendor_lib.check_settings("vms", {"name": "x"}) == {
        "name": "x", "labels": {}, "vendor_settings": {}
    }


def test_object_support_follows_the_methods_a_vendor_has():
    assert StubVendor.object_types() == ["vms", "volumes"]
    vms, volumes = StubVendor.object_support()
    assert vms["operations"] == ["info", "list", "status", "create", "delete"]
    assert volumes["operations"] == ["list", "status"]
    assert vms["feature_set"] == object_types.CORE_COMPUTE


def test_an_unsupported_operation_is_refused_with_what_is(stub):
    vendor = StubVendor("key")
    with pytest.raises(DbaError, match="Unknown object type 'servers'"):
        vendor.list_objects("servers")
    with pytest.raises(DbaError, match="Stub Cloud does not support dns. It supports: vms, volumes"):
        vendor.list_objects("dns")
    with pytest.raises(DbaError, match="Stub Cloud does not support 'create' for volumes"):
        vendor.create_object("volumes", {"name": "v"})


def test_objects_are_found_by_name_then_id(stub):
    vendor = StubVendor("key")
    assert vendor.find_object("vms", "db2")["id"] == "2"
    assert vendor.find_object("vms", "1")["name"] == "db1"
    with pytest.raises(DbaError, match="There are 2 vms named 'twin'.*3, 4"):
        vendor.find_object("vms", "twin")
    with pytest.raises(DbaError, match="There is no vms named 'nope' at Stub Cloud"):
        vendor.find_object("vms", "nope")


def test_a_project_is_a_label_for_a_vendor_without_projects(stub):
    assert [o["name"] for o in StubVendor("key", "alpha").list_objects("vms")] == ["db1"]
    assert [o["name"] for o in NativeVendor("key", "alpha").list_objects("vms")] == [
        "db1", "db2", "twin", "twin"
    ]

    StubVendor("key", "alpha").create_object("vms", {"name": "new", "labels": {"env": "prod"}})
    NativeVendor("key", "alpha").create_object("vms", {"name": "new"})
    assert [d["labels"] for d in StubVendor.deployed] == [
        {"env": "prod", vendor_lib.PROJECT_LABEL: "alpha"}, {}
    ]


def test_registering_checks_the_vendor(monkeypatch):
    class Nameless(vendor_lib.Vendor):
        pass

    class Odd(vendor_lib.Vendor):
        name = "odd"
        feature_sets = ("Serverless",)

    class Impostor(vendor_lib.Vendor):
        name = "stubcloud"

    monkeypatch.setitem(vendors._vendors, StubVendor.name, StubVendor)
    with pytest.raises(DbaError, match="has no valid name"):
        vendors.register(Nameless)
    with pytest.raises(DbaError, match="Unknown feature set 'Serverless'"):
        vendors.register(Odd)
    with pytest.raises(DbaError, match="'stubcloud' is already registered"):
        vendors.register(Impostor)


# --- API keys ------------------------------------------------------------------


def test_api_keys_are_kept_per_context_and_project(clean_api_keys):
    from mysqlsh.globals import shell

    assert api_keys.store_api_key("stubcloud", " k1 ") == "GUI:DBA_API_KEY:stubcloud:default"
    api_keys.store_api_key("stubcloud", "k2", "beta", api_keys.CONTEXT_MCP)

    assert shell.read_secret("GUI:DBA_API_KEY:stubcloud:default") == "k1"
    assert api_keys.read_api_key("stubcloud") == "k1"
    assert api_keys.read_api_key("stubcloud", "beta") is None
    assert api_keys.read_api_key("stubcloud", "beta", fall_back_to_default=True) == "k1"
    assert api_keys.read_api_key("stubcloud", "beta", "mcp") == "k2"
    assert api_keys.list_api_keys() == [{"vendor": "stubcloud", "project": "default"}]
    assert api_keys.list_api_keys("MCP") == [{"vendor": "stubcloud", "project": "beta"}]

    assert api_keys.delete_api_key("stubcloud") is True
    assert api_keys.delete_api_key("stubcloud") is False
    assert api_keys.list_api_keys() == []


def test_api_key_names_are_checked(clean_api_keys):
    with pytest.raises(DbaError, match="The API key is empty"):
        api_keys.store_api_key("stubcloud", "  ")
    with pytest.raises(DbaError, match="cannot contain ':'"):
        api_keys.store_api_key("stubcloud", "k", "a:b")
    with pytest.raises(DbaError, match="Unknown API key context 'CLI'"):
        api_keys.store_api_key("stubcloud", "k", context="CLI")


# --- Operations ----------------------------------------------------------------


def test_vendors_are_listed_by_feature_set(stub):
    operations.set_api_key("stubcloud", "k")
    listed = {v["name"]: v for v in operations.list_vendors()}
    assert listed["stubcloud"]["api_key_projects"] == ["default"]
    assert listed["stubcloud"]["object_types"] == ["vms", "volumes"]

    managed = [v["name"] for v in operations.list_vendors("managedservice")]
    assert "nativecloud" in managed and "stubcloud" not in managed
    with pytest.raises(DbaError, match="Unknown feature set 'Cheap'"):
        operations.list_vendors("Cheap")


def test_object_support_without_a_vendor_lists_every_type(stub):
    support = {entry["object_type"]: entry for entry in operations.object_support()}
    assert list(support) == list(object_types.OBJECT_TYPES)
    assert "stubcloud" in support["vms"]["vendors"]
    assert "size" in support["vms"]["settings"] and "name" in support["vms"]["settings"]


def test_a_missing_key_says_how_to_store_one(stub):
    with pytest.raises(DbaError) as error:
        operations.list_objects("stubcloud", "vms", project="alpha")
    assert str(error.value) == (
        "No Stub Cloud API key is stored for project 'alpha'. Store one with "
        "dba.set.apiKey('stubcloud', <key>, 'alpha'). Stub keys come from nowhere."
    )
    with pytest.raises(DbaError, match="mcp setup --setDbaApiKey=nativecloud --dbaProject=alpha"):
        operations.list_objects("nativecloud", "vms", project="alpha", context="MCP")


def test_a_native_project_needs_its_own_key(stub):
    operations.set_api_key("nativecloud", "default-key")
    operations.set_api_key("stubcloud", "default-key")
    with pytest.raises(DbaError, match="No Native Cloud API key is stored for project 'alpha'"):
        operations.list_objects("nativecloud", "vms", project="alpha")
    assert len(operations.list_objects("stubcloud", "vms", project="alpha")) == 1


def test_operations_use_the_stored_key(stub):
    operations.set_api_key("stubcloud", "k")
    assert operations.object_info("stubcloud", "vms") == {"sizes": [{"name": "small"}]}
    assert operations.object_status("stubcloud", "vms", "db2")["status"] == "off"
    assert [o["id"] for o in operations.list_objects("stubcloud", "VMS", {"env": "prod"})] == ["1"]
    db2 = StubVendor.objects[1]
    deleted = _ended(operations.delete_object("stubcloud", "vms", "db2"))
    assert deleted["status"] == task_manager.COMPLETED
    assert deleted["result"] == {"deleted": db2, "result": {"action": "delete"}}
    assert set(StubVendor.keys) == {"k"}


# --- Deploying and deleting as tasks ---------------------------------------------


def test_a_creation_is_a_task_that_ends_once_the_object_is_ready(stub):
    operations.set_api_key("stubcloud", "k")
    started = operations.create_object("stubcloud", "vms", {"name": "db9", "size": "small"}, owner="me")
    assert set(started) == {"task_id", "status"}

    ended = _ended(started, "me")
    assert ended["status"] == task_manager.COMPLETED
    assert ended["task_type"] == "dba"
    assert ended["kind"] == "create_object"
    assert ended["title"] == "Create vms 'db9' on Stub Cloud"
    assert (ended["vendor"], ended["project"], ended["object_type"]) == ("stubcloud", None, "vms")
    assert ended["result"]["name"] == "db9"
    assert ended["result"]["status"] == "running"
    assert [stage["name"] for stage in ended["stages"]] == ["Creating", "Waiting until it is ready"]
    assert [m["text"] for m in ended["messages"]] == [
        "Stub Cloud accepted the vms 'db9' (id 9).",
        "The vms 'db9' is starting.",
        "The vms 'db9' is running.",
    ]
    # the task is its owner's only
    assert task_manager.list_tasks() == []


def test_a_deletion_is_a_task_that_ends_once_the_object_is_gone(stub):
    operations.set_api_key("stubcloud", "k")
    ended = _ended(operations.delete_object("stubcloud", "vms", "db1"))
    assert ended["status"] == task_manager.COMPLETED
    assert ended["result"]["deleted"]["name"] == "db1"
    assert ended["messages"][-1]["text"] == "The vms 'db1' is gone."
    assert StubVendor.deleted == ["1"]


@pytest.mark.parametrize(
    "call, refusal",
    [
        (lambda: operations.create_object("stubcloud", "vms", {"size": "small"}), "Missing settings for vms: name"),
        (lambda: operations.create_object("stubcloud", "vms", {"name": "db9", "color": "red"}), "Unsupported settings for vms: color"),
        (lambda: operations.create_object("stubcloud", "vms", {"name": "db9", "size": "huge"}), "no size 'huge'"),
        (lambda: operations.create_object("stubcloud", "volumes", {"name": "v"}), "does not support 'create' for volumes"),
        (lambda: operations.create_object("nocloud", "vms", {"name": "v"}), "Unknown vendor 'nocloud'"),
        (lambda: operations.delete_object("stubcloud", "vms", ""), "object_name is empty"),
        (lambda: operations.delete_object("nativecloud", "vms", "db1"), "No Native Cloud API key"),
    ],
)
def test_what_can_be_checked_at_once_fails_the_call(stub, call, refusal):
    operations.set_api_key("stubcloud", "k")
    with pytest.raises(DbaError, match=refusal):
        call()
    assert task_manager.list_tasks() == []


def test_what_the_vendor_refuses_fails_the_task(stub):
    operations.set_api_key("stubcloud", "k")
    # a standardized setting this vendor does not take
    ended = _ended(operations.create_object("stubcloud", "vms", {"name": "db9", "image": "debian-13"}))
    assert ended["status"] == task_manager.FAILED
    assert "Unsupported settings for vms: image" in ended["error"]
    ended = _ended(operations.delete_object("stubcloud", "vms", "nothere"))
    assert ended["status"] == task_manager.FAILED
    assert "There is no vms named 'nothere'" in ended["error"]


def test_a_few_failed_polls_do_not_fail_the_creation(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.flaky = operations.MAX_POLL_ERRORS
    ended = _ended(operations.create_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.COMPLETED
    warnings = [m["text"] for m in ended["messages"] if m["level"] == "warning"]
    assert len(warnings) == operations.MAX_POLL_ERRORS
    assert "HTTP 502" in warnings[0]


def test_polls_that_keep_failing_fail_the_creation(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.flaky = operations.MAX_POLL_ERRORS + 1
    ended = _ended(operations.create_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.FAILED
    assert "HTTP 502" in ended["error"]


def test_a_server_created_stopped_is_created(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    started = operations.create_object("stubcloud", "vms", {"name": "db9"})
    StubVendor.objects[-1]["status"] = "off"
    ended = _ended(started)
    assert ended["status"] == task_manager.COMPLETED
    assert ended["result"]["status"] == "off"


def test_a_deletion_the_vendor_fails_fails_at_once(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.delete_fails = True
    began = time.monotonic()
    ended = _ended(operations.delete_object("stubcloud", "vms", "db1"))
    assert ended["status"] == task_manager.FAILED
    assert ended["error"] == "Stub Cloud reports The vms 'db1' as delete_failed."
    assert time.monotonic() - began < 2


def test_an_object_is_one_tasks_by_its_id_and_in_the_default_project(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    first = operations.create_object("stubcloud", "vms", {"name": "db9"})
    task = task_manager.get(first["task_id"])
    while task.stage != "Waiting until it is ready":
        task.wait_for_change(task.version, 1)

    # by the id the deployment got, and with the default project named
    with pytest.raises(DbaError, match="A task is already working on the vms '9'"):
        operations.delete_object("stubcloud", "vms", "9")
    with pytest.raises(DbaError, match="A task is already working on the vms 'db9'"):
        operations.delete_object("stubcloud", "VMS", "db9", project="default")

    task.cancel()
    _ended(first)
    _ended(operations.delete_object("stubcloud", "vms", "9"))


def test_a_task_that_cannot_start_lets_go_of_its_object(stub, monkeypatch):
    operations.set_api_key("stubcloud", "k")

    start = threading.Thread.start

    def no_thread(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    with pytest.raises(DbaError, match="^The task could not start: can't start new thread$"):
        operations.create_object("stubcloud", "vms", {"name": "db9"})
    [failed] = task_manager.list_tasks()
    assert failed.status == task_manager.FAILED

    # the object is free again for the next task
    monkeypatch.setattr(threading.Thread, "start", start)
    assert _ended(operations.create_object("stubcloud", "vms", {"name": "db9"}))["status"] == "completed"

def test_a_failed_status_fails_the_creation(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    started = operations.create_object("stubcloud", "vms", {"name": "db9"})
    StubVendor.objects[-1]["status"] = "error"
    ended = _ended(started)
    assert ended["status"] == task_manager.FAILED
    assert ended["error"] == "Stub Cloud reports The vms 'db9' as error."


def test_a_creation_waits_only_so_long(stub, monkeypatch):
    operations.set_api_key("stubcloud", "k")
    monkeypatch.setattr(operations, "CREATE_TIMEOUT", 0)
    StubVendor.hold = True
    ended = _ended(operations.create_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.FAILED
    assert "is still starting after 0 minutes" in ended["error"]


def test_a_cancel_stops_the_waiting_at_once(stub, monkeypatch):
    operations.set_api_key("stubcloud", "k")
    monkeypatch.setattr(operations, "POLL_INTERVAL", 60)
    StubVendor.hold = True
    started = operations.create_object("stubcloud", "vms", {"name": "db9"})
    task = task_manager.get(started["task_id"])
    task.wait_for_change(0, 1)
    while task.stage != "Waiting until it is ready":
        task.wait_for_change(task.version, 1)

    began = time.monotonic()
    task.cancel()
    assert _ended(started)["status"] == task_manager.CANCELLED
    assert time.monotonic() - began < 2


def test_one_object_is_worked_on_by_one_task_at_a_time(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    first = operations.create_object("stubcloud", "vms", {"name": "db9"})
    with pytest.raises(DbaError, match="A task is already working on the vms 'db9'"):
        operations.create_object("stubcloud", "vms", {"name": "db9"})
    # another object is fine
    operations.create_object("stubcloud", "vms", {"name": "db10"})

    task_manager.get(first["task_id"]).cancel()
    _ended(first)
    _ended(operations.delete_object("stubcloud", "vms", "db9"))


# --- The shell functions ---------------------------------------------------------


def test_the_shell_functions_report_a_shell_error(stub):
    # Through the shell global a mysqlsh.Error arrives as a RuntimeError,
    # its message kept.
    with pytest.raises(RuntimeError, match="Unknown vendor 'nocloud'"):
        mysqlsh.globals.dba.list.vendor.objects("nocloud", "vms")


def test_the_shell_functions_use_the_gui_keys(stub):
    dba = mysqlsh.globals.dba
    stored = dba.set.api_key("stubcloud", "k", "alpha")
    assert stored["secret"] == "GUI:DBA_API_KEY:stubcloud:alpha"

    listed = dba.list.vendor.objects("stubcloud", "vms", ["env=prod"], "alpha")
    assert [o["name"] for o in listed] == ["db1"]

    started = dba.create.vendor.object("stubcloud", "vms", {"name": "db9", "size": "small"}, "alpha")
    deployed = _ended(started)["result"]
    assert deployed["labels"] == {vendor_lib.PROJECT_LABEL: "alpha"}
    assert dba.get.vendor.object_status("stubcloud", "vms", "db1", "alpha")["status"] == "running"
    assert dba.get.vendor.object_info("stubcloud", "vms", "alpha")["sizes"] == [{"name": "small"}]
    deleted = _ended(dba.delete.vendor.object("stubcloud", "vms", "db1", "alpha"))
    assert deleted["result"]["result"] == {"action": "delete"}

    # the task global follows them, as the shell's own tasks
    listed = mysqlsh.globals.task.list("dba")
    assert [t["kind"] for t in listed] == ["create_object", "delete_object"]
    assert mysqlsh.globals.task.get(started["task_id"])["status"] == "completed"


def test_object_names_are_camel_cased():
    assert objects.camel_case("mariadb_cloud") == "mariadbCloud"
    assert objects.camel_case("hetzner") == "hetzner"


def test_shell_values_become_plain_python():
    assert objects.plain({"a": [1, {"b": (2,)}]}) == {"a": [1, {"b": [2]}]}


def test_on_the_command_line_a_creation_waits_for_its_task(stub, monkeypatch, capsys):
    dba = mysqlsh.globals.dba
    dba.set.api_key("stubcloud", "k")
    # as the shell runs mariadb-shell -- dba create vendor object ...
    monkeypatch.setattr(task_manager, "cli_function", "dba.create.vendor.object")

    deployed = dba.create.vendor.object("stubcloud", "vms", {"name": "db9", "size": "small"})
    assert deployed["name"] == "db9"
    assert deployed["status"] == "running"
    assert "The vms 'db9' is running." in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="color"):
        dba.create.vendor.object("stubcloud", "vms", {"name": "db10", "color": "red"})
    # another function is no command-line call: it returns the task's id
    assert "task_id" in dba.delete.vendor.object("stubcloud", "vms", "db9")


@pytest.mark.parametrize(
    "status, state",
    [
        ("running", "ready"), ("Online", "ready"), ("active", "ready"),
        ("off", "stopped"), ("stopped", "stopped"),
        ("initializing", "pending"), ("maintenance", "pending"), ("pending_create", "pending"),
        ("pending_delete", "deleting"), ("deleting", "deleting"),
        ("error", "failed"), ("delete_failed", "failed"),
        (None, "unknown"),
    ],
)
def test_every_status_has_a_state_in_the_same_words_for_every_vendor(status, state):
    assert vendor_lib.state_of(status) == state
    assert vendor_lib.make_object("vms", 1, "db1", status)["state"] == state


def test_one_pager_follows_page_numbers_offsets_and_tokens():
    pages = {None: ([1, 2], "b"), "b": ([3], "c"), "c": ([4], None)}
    assert vendor_lib.paginate(lambda cursor: pages[cursor]) == [1, 2, 3, 4]
    # a cursor seen before would page in a circle
    looping = {None: ([1], "a"), "a": ([2], "a")}
    assert vendor_lib.paginate(lambda cursor: looping[cursor]) == [1, 2]
    assert vendor_lib.paginate(lambda cursor: ([cursor], (cursor or 0) + 1), max_pages=3) == [None, 1, 2]


def test_independent_requests_run_side_by_side():
    import threading as threads

    barrier = threads.Barrier(3, timeout=5)

    def request(answer):
        barrier.wait()  # only returns once all three run at once
        return answer

    assert vendor_lib.parallel(*(lambda n=n: request(n) for n in range(3))) == [0, 1, 2]


def test_reading_calls_share_one_vendor_per_api_key(stub):
    operations.set_api_key("stubcloud", "k1")
    operations.list_objects("stubcloud", "vms")
    operations.object_status("stubcloud", "vms", "db1")
    assert StubVendor.keys == ["k1"]
    # a key stored anew gets a vendor of its own
    operations.set_api_key("stubcloud", "k2")
    operations.list_objects("stubcloud", "vms")
    assert StubVendor.keys == ["k1", "k2"]


# --- Settings described for a dialog, and validated ------------------------------


class DialogVendor(StubVendor):
    """A vendor whose settings differ from the standardized ones."""

    name = "dialogcloud"
    display_name = "Dialog Cloud"
    create_settings = {
        "vms": {"required": ("name", "size"), "supported": ("size", "location", "image", "networks")},
    }
    settings_fields = {
        "vms": {
            "size": {"is_updatable": True},
            "turbo": sf.field("Turbo", "Runs it faster.", sf.BOOLEAN, default=False,
                              vendor_key="options.turbo"),
            "cores": sf.field("Cores", "How many.", sf.INTEGER, minimum=1, maximum=8),
        }
    }
    settings_order = {"vms": ("location", "size")}
    settings_groups = {"size": {"caption": "Plan"}}

    def _info_vms(self):
        return {
            "sizes": [
                {"name": "small", "description": "1 vCPU", "locations": ["z1"], "architecture": "x86_64"},
                {"name": "big", "description": "8 vCPU", "locations": ["z1", "z2"], "architecture": "arm64"},
            ],
            "locations": [{"name": "z1", "city": "Here"}, {"name": "z2", "city": "There"}],
            "images": [{"name": "linux-x86", "architecture": "x86_64"}, {"name": "linux-arm", "architecture": "arm64"}],
        }

    def _list_networking(self):
        return [vendor_lib.make_object("networking", 5, "net-z1", "active", "z1")]

    def _create_vms(self, settings):
        settings = self.check_create_settings("vms", settings)
        StubVendor.deployed.append(settings)
        deployed = vendor_lib.make_object("vms", 9, settings["name"], "running")
        StubVendor.objects.append(deployed)
        return dict(deployed)


@pytest.fixture
def dialog(stub, monkeypatch):
    monkeypatch.setitem(vendors._vendors, DialogVendor.name, DialogVendor)
    operations.set_api_key("dialogcloud", "k")
    return DialogVendor


def test_the_fields_of_a_type_follow_the_vendor():
    fields = DialogVendor.fields("vms")
    assert list(fields) == [
        "location", "size", "name", "image", "networks", "turbo", "cores", "labels", "vendor_settings",
    ]
    assert [details["position"] for details in fields.values()] == list(range(1, 10))
    assert [key for key, details in fields.items() if details["required"]] == ["size", "name"]
    assert fields["size"]["is_updatable"] and not fields["location"]["is_updatable"]
    assert fields["turbo"]["vendor_specific"] and fields["turbo"]["vendor_key"] == "options.turbo"
    assert not fields["size"]["vendor_specific"]
    assert "firewalls" not in fields  # not supported
    # every attribute a dialog relies on is there
    assert set(fields["cores"]) >= {
        "key", "position", "caption", "description", "type", "group", "required", "is_updatable",
        "advanced", "vendor_specific", "minimum", "maximum",
    }
    # standard choices come as dicts
    rules = StubVendor.fields("firewalls")["rules"]["item_fields"]["direction"]
    assert rules["choices"] == [
        {"value": "in", "caption": "in", "description": None, "details": {}},
        {"value": "out", "caption": "out", "description": None, "details": {}},
    ]
    # the descriptions object_support reports are those of the fields
    assert vendor_lib.settings_keys("vms")["size"] == sf.FIELDS["vms"]["size"]["description"]


def test_settings_problems_are_collected_with_their_keys(dialog):
    vendor = DialogVendor(api_key="k")
    checked, errors = vendor.settings_problems(
        "vms", {"name": "", "cores": 9, "turbo": "sometimes", "colour": "red", "labels": []}
    )
    assert errors == [
        {"settings": ["colour"], "message": "Unsupported settings for vms: colour. Supported: image, "
         "labels, location, name, networks, size, vendor_settings."},
        {"settings": ["name", "size"], "message": "Missing settings for vms: name, size."},
        {"settings": ["labels"], "message": "The setting labels must be a dict."},
        {"settings": ["turbo"], "message": "The setting turbo must be true or false, not 'sometimes'."},
        {"settings": ["cores"], "message": "The setting cores must be at most 8."},
    ]
    assert vendor.settings_problems("vms", "name=x") == (None, [{"settings": [], "message": "settings must be a dict."}])
    # the first problem is what a deployment refuses with
    with pytest.raises(SettingError, match="Unsupported settings for vms: colour") as error:
        vendor.check_create_settings("vms", {"name": "x", "size": "small", "colour": "red"})
    assert error.value.settings == ["colour"]


def test_a_vendors_own_settings_go_into_its_request(dialog):
    started = operations.create_object(
        "dialogcloud", "vms",
        {"name": "db9", "size": "small", "turbo": "yes", "cores": "4", "vendor_settings": {"zone": "a"}},
    )
    assert _ended(started)["status"] == task_manager.COMPLETED
    [deployed] = StubVendor.deployed
    assert "turbo" not in deployed and "cores" not in deployed
    assert deployed["vendor_settings"] == {"zone": "a", "options": {"turbo": True}, "cores": 4}


def test_the_settings_info_has_the_choices_the_vendor_offers(dialog):
    info = operations.settings_info("dialogcloud", "VMS")
    assert (info["vendor"], info["display_name"], info["object_type"]) == ("dialogcloud", "Dialog Cloud", "vms")
    assert [group["key"] for group in info["groups"]] == ["location", "size", "general", "image", "networking", "labels", "advanced"]
    assert info["groups"][1]["caption"] == "Plan"
    assert info["groups"][1]["description"] == sf.GROUPS["size"]["description"]

    settings = info["settings"]
    assert settings["size"]["choices"][1] == {
        "value": "big", "caption": "big", "description": "8 vCPU",
        "details": {"locations": ["z1", "z2"], "architecture": "arm64"},
    }
    assert [c["value"] for c in settings["location"]["choices"]] == ["z1", "z2"]
    assert settings["networks"]["choices"] == [
        {"value": "net-z1", "caption": "net-z1", "description": None,
         "details": {"id": "5", "location": "z1", "status": "active", "state": "ready", "labels": {}}}
    ]
    assert "choices" not in settings["turbo"]

    # the choices that apply with what is chosen already
    assert [c["value"] for c in vendor_lib.applying_choices(settings, "size", {"location": "z2"})] == ["big"]
    assert [c["value"] for c in vendor_lib.applying_choices(settings, "image", {"size": "big"})] == ["linux-arm"]
    assert len(vendor_lib.applying_choices(settings, "image", {"size": "unknown"})) == 2

    with pytest.raises(DbaError, match="does not support 'create' for volumes"):
        operations.settings_info("dialogcloud", "volumes")


def test_settings_are_validated_against_what_the_vendor_offers(dialog):
    def validate(settings, project=None):
        return operations.validate_settings("dialogcloud", "vms", settings, project)

    assert validate({"name": "db", "size": "big", "location": "z2", "image": "linux-arm"}) == {
        "valid": True, "errors": [], "warnings": [],
    }
    assert validate({"name": "db", "size": "small", "location": "z2"})["errors"] == [
        {"settings": ["size", "location"], "message": "Dialog Cloud does not offer the size 'small' with location 'z2'."}
    ]
    assert validate({"name": "db", "size": "tiny", "location": "z9"})["errors"] == [
        {"settings": ["location"], "message": "Dialog Cloud does not offer the location 'z9'. Choose one of: z1, z2."},
        {"settings": ["size"], "message": "Dialog Cloud does not offer the size 'tiny'. Choose one of: small, big."},
    ]
    assert validate({"name": "db", "size": "small", "image": "my-own"})["warnings"] == [
        {"settings": ["image"], "message": "Dialog Cloud does not list the image 'my-own'; creating the object fails if the vendor does not take it."}
    ]
    # a network not listed is an error, but a warning in a project a vendor
    # without projects may not have labelled it with
    assert validate({"name": "db", "size": "small", "networks": "net-x"})["errors"] == [
        {"settings": ["networks"], "message": "There is no networking named 'net-x' at Dialog Cloud."}
    ]
    in_project = validate({"name": "db", "size": "small", "networks": ["net-x"]}, "alpha")
    assert in_project["valid"] is True
    assert in_project["warnings"][0]["message"] == "There is no networking named 'net-x' at Dialog Cloud in project 'alpha'."

    # a setting refused before any offer is asked for, by the vendor's own rule too
    refused = validate({"name": "db", "size": "huge", "cores": 0})
    assert refused["errors"] == [{"settings": ["cores"], "message": "The setting cores must be at least 1."}]
    assert validate({"name": "db", "size": "huge", "location": "z1"})["errors"] == [
        {"settings": [], "message": "Stub Cloud has no size 'huge'."}
    ]
    with pytest.raises(DbaError, match="Unknown vendor"):
        operations.validate_settings("nocloud", "vms", {})


def test_the_stub_vendors_own_rule_is_a_validation_error(dialog):
    operations.set_api_key("stubcloud", "k")
    result = operations.validate_settings("stubcloud", "vms", {"name": "db", "size": "huge"})
    assert result == {"valid": False, "errors": [{"settings": [], "message": "Stub Cloud has no size 'huge'."}], "warnings": []}


def test_the_shell_functions_describe_and_validate_settings(dialog):
    dba = mysqlsh.globals.dba
    info = dba.get.vendor.object_settings_info("dialogcloud", "vms")
    # the shell sorts a dict's keys: the position keeps the order
    by_position = sorted(info["settings"].values(), key=lambda details: details["position"])
    assert [details["key"] for details in by_position][:2] == ["location", "size"]
    assert [group["key"] for group in info["groups"]][:2] == ["location", "size"]
    result = dba.validate.vendor.object_settings("dialogcloud", "vms", {"name": "db", "size": "big", "turbo": True})
    assert result["valid"] is True
    with pytest.raises(RuntimeError, match="Unknown vendor 'nocloud'"):
        dba.validate.vendor.object_settings("nocloud", "vms", {})
