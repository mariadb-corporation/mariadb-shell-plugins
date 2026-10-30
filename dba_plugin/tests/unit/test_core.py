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
from dba_plugin.lib.general import DbaError
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

    def _deploy_vms(self, settings):
        settings = vendor_lib.check_settings("vms", settings, supported=("size",))
        StubVendor.deployed.append(settings)
        deployed = vendor_lib.make_object("vms", 9, settings["name"], "starting", labels=settings["labels"])
        StubVendor.objects.append(deployed)
        return dict(deployed)

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
    assert vms["operations"] == ["info", "list", "status", "deploy", "delete"]
    assert volumes["operations"] == ["list", "status"]
    assert vms["feature_set"] == object_types.CORE_COMPUTE


def test_an_unsupported_operation_is_refused_with_what_is(stub):
    vendor = StubVendor("key")
    with pytest.raises(DbaError, match="Unknown object type 'servers'"):
        vendor.list_objects("servers")
    with pytest.raises(DbaError, match="Stub Cloud does not support dns. It supports: vms, volumes"):
        vendor.list_objects("dns")
    with pytest.raises(DbaError, match="Stub Cloud does not support 'deploy' for volumes"):
        vendor.deploy_object("volumes", {"name": "v"})


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

    StubVendor("key", "alpha").deploy_object("vms", {"name": "new", "labels": {"env": "prod"}})
    NativeVendor("key", "alpha").deploy_object("vms", {"name": "new"})
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


def test_a_deployment_is_a_task_that_ends_once_the_object_is_ready(stub):
    operations.set_api_key("stubcloud", "k")
    started = operations.deploy_object("stubcloud", "vms", {"name": "db9", "size": "small"}, owner="me")
    assert set(started) == {"task_id", "status"}

    ended = _ended(started, "me")
    assert ended["status"] == task_manager.COMPLETED
    assert ended["task_type"] == "dba"
    assert ended["kind"] == "deploy_object"
    assert ended["title"] == "Deploy vms 'db9' on Stub Cloud"
    assert (ended["vendor"], ended["project"], ended["object_type"]) == ("stubcloud", None, "vms")
    assert ended["result"]["name"] == "db9"
    assert ended["result"]["status"] == "running"
    assert [stage["name"] for stage in ended["stages"]] == ["Deploying", "Waiting until it is ready"]
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
        (lambda: operations.deploy_object("stubcloud", "vms", {"size": "small"}), "Missing settings for vms: name"),
        (lambda: operations.deploy_object("stubcloud", "vms", {"name": "db9", "color": "red"}), "Unsupported settings for vms: color"),
        (lambda: operations.deploy_object("stubcloud", "volumes", {"name": "v"}), "does not support 'deploy' for volumes"),
        (lambda: operations.deploy_object("nocloud", "vms", {"name": "v"}), "Unknown vendor 'nocloud'"),
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
    ended = _ended(operations.deploy_object("stubcloud", "vms", {"name": "db9", "image": "debian-13"}))
    assert ended["status"] == task_manager.FAILED
    assert "Unsupported settings for vms: image" in ended["error"]
    ended = _ended(operations.delete_object("stubcloud", "vms", "nothere"))
    assert ended["status"] == task_manager.FAILED
    assert "There is no vms named 'nothere'" in ended["error"]


def test_a_few_failed_polls_do_not_fail_the_deployment(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.flaky = operations.MAX_POLL_ERRORS
    ended = _ended(operations.deploy_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.COMPLETED
    warnings = [m["text"] for m in ended["messages"] if m["level"] == "warning"]
    assert len(warnings) == operations.MAX_POLL_ERRORS
    assert "HTTP 502" in warnings[0]


def test_polls_that_keep_failing_fail_the_deployment(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.flaky = operations.MAX_POLL_ERRORS + 1
    ended = _ended(operations.deploy_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.FAILED
    assert "HTTP 502" in ended["error"]


def test_a_server_deployed_stopped_is_deployed(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    started = operations.deploy_object("stubcloud", "vms", {"name": "db9"})
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
    first = operations.deploy_object("stubcloud", "vms", {"name": "db9"})
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
        operations.deploy_object("stubcloud", "vms", {"name": "db9"})
    [failed] = task_manager.list_tasks()
    assert failed.status == task_manager.FAILED

    # the object is free again for the next task
    monkeypatch.setattr(threading.Thread, "start", start)
    assert _ended(operations.deploy_object("stubcloud", "vms", {"name": "db9"}))["status"] == "completed"

def test_a_failed_status_fails_the_deployment(stub):
    operations.set_api_key("stubcloud", "k")
    StubVendor.hold = True
    started = operations.deploy_object("stubcloud", "vms", {"name": "db9"})
    StubVendor.objects[-1]["status"] = "error"
    ended = _ended(started)
    assert ended["status"] == task_manager.FAILED
    assert ended["error"] == "Stub Cloud reports The vms 'db9' as error."


def test_a_deployment_waits_only_so_long(stub, monkeypatch):
    operations.set_api_key("stubcloud", "k")
    monkeypatch.setattr(operations, "DEPLOY_TIMEOUT", 0)
    StubVendor.hold = True
    ended = _ended(operations.deploy_object("stubcloud", "vms", {"name": "db9"}))
    assert ended["status"] == task_manager.FAILED
    assert "is still starting after 0 minutes" in ended["error"]


def test_a_cancel_stops_the_waiting_at_once(stub, monkeypatch):
    operations.set_api_key("stubcloud", "k")
    monkeypatch.setattr(operations, "POLL_INTERVAL", 60)
    StubVendor.hold = True
    started = operations.deploy_object("stubcloud", "vms", {"name": "db9"})
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
    first = operations.deploy_object("stubcloud", "vms", {"name": "db9"})
    with pytest.raises(DbaError, match="A task is already working on the vms 'db9'"):
        operations.deploy_object("stubcloud", "vms", {"name": "db9"})
    # another object is fine
    operations.deploy_object("stubcloud", "vms", {"name": "db10"})

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

    started = dba.deploy.vendor.object("stubcloud", "vms", {"name": "db9", "size": "small"}, "alpha")
    deployed = _ended(started)["result"]
    assert deployed["labels"] == {vendor_lib.PROJECT_LABEL: "alpha"}
    assert dba.get.vendor.object_status("stubcloud", "vms", "db1", "alpha")["status"] == "running"
    assert dba.get.vendor.object_info("stubcloud", "vms", "alpha")["sizes"] == [{"name": "small"}]
    deleted = _ended(dba.delete.vendor.object("stubcloud", "vms", "db1", "alpha"))
    assert deleted["result"]["result"] == {"action": "delete"}

    # the task global follows them, as the shell's own tasks
    listed = mysqlsh.globals.task.list("dba")
    assert [t["kind"] for t in listed] == ["deploy_object", "delete_object"]
    assert mysqlsh.globals.task.get(started["task_id"])["status"] == "completed"


def test_object_names_are_camel_cased():
    assert objects.camel_case("mariadb_cloud") == "mariadbCloud"
    assert objects.camel_case("hetzner") == "hetzner"


def test_shell_values_become_plain_python():
    assert objects.plain({"a": [1, {"b": (2,)}]}) == {"a": [1, {"b": [2]}]}


def test_on_the_command_line_a_deployment_waits_for_its_task(stub, monkeypatch, capsys):
    dba = mysqlsh.globals.dba
    dba.set.api_key("stubcloud", "k")
    # as the shell runs mariadb-shell -- dba deploy vendor object ...
    monkeypatch.setattr(task_manager, "cli_function", "dba.deploy.vendor.object")

    deployed = dba.deploy.vendor.object("stubcloud", "vms", {"name": "db9", "size": "small"})
    assert deployed["name"] == "db9"
    assert deployed["status"] == "running"
    assert "The vms 'db9' is running." in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="color"):
        dba.deploy.vendor.object("stubcloud", "vms", {"name": "db10", "color": "red"})
    # another function is no command-line call: it returns the task's id
    assert "task_id" in dba.delete.vendor.object("stubcloud", "vms", "db9")
