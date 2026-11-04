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

"""Tests for the project list: msm_plugin.lib.projects and its shell functions."""

import json
import os

import pytest

from msm_plugin import management
from msm_plugin.lib import projects

OWNER = "3f2a8c1e-0b6d-4e7a-9c1f-5d2e8b7a4c60"
OTHER_OWNER = "7a0d2b4c-6e8f-4a1b-9c3d-5e7f9a1b3c5d"


@pytest.fixture
def data_path(tmp_path, monkeypatch):
    """Keeps every list a test writes in a directory of its own."""
    path = tmp_path / "plugin_data"
    path.mkdir()
    monkeypatch.setattr(projects, "get_data_path", lambda: str(path))

    return str(path)


def _make_project(root, name, schema_name=None):
    """Makes a folder that passes for a schema project and returns its path."""
    folder = os.path.join(str(root), f"{name}.msm.project")
    os.makedirs(folder)
    with open(os.path.join(folder, "msm.project.json"), "w") as settings:
        json.dump({"schemaName": schema_name or name}, settings)

    return folder


def _paths(listed):
    return [project["project_path"] for project in listed]


# --- the values ------------------------------------------------------------------


def test_a_folder_has_one_spelling():
    assert projects.normalize_folder(None) == ""
    assert projects.normalize_folder("") == ""
    assert projects.normalize_folder("/") == ""
    assert projects.normalize_folder("Shop") == "/Shop"
    assert projects.normalize_folder(" /Shop// Production / ") == "/Shop/Production"
    with pytest.raises(ValueError, match="folder must be a string"):
        projects.normalize_folder(["Shop"])


def test_a_caption_is_one_trimmed_line():
    assert projects.normalize_caption(None) == ""
    assert projects.normalize_caption("  Shop (prod) ") == "Shop (prod)"
    with pytest.raises(ValueError, match="single line"):
        projects.normalize_caption("two\nlines")
    with pytest.raises(ValueError, match="at most 100"):
        projects.normalize_caption("x" * 101)
    with pytest.raises(ValueError, match="must be a string"):
        projects.normalize_caption(1)


def test_a_color_is_one_of_the_names():
    assert projects.normalize_color(None) == ""
    assert projects.normalize_color("") == ""
    assert projects.normalize_color(" Green ") == "green"
    with pytest.raises(ValueError, match="not a color"):
        projects.normalize_color("teal")


def test_an_owner_is_a_uuid_or_nothing():
    assert projects.normalize_owner(None) is None
    assert projects.normalize_owner(OWNER.upper()) == OWNER
    for bad in ("ada", "../x", "", "3f2a8c1e"):
        with pytest.raises(ValueError, match="user id"):
            projects.normalize_owner(bad)


def test_each_owner_has_a_file_of_their_own(data_path):
    assert projects.get_projects_file_path() == os.path.join(data_path, "projects.json")
    assert projects.get_projects_file_path(OWNER) == os.path.join(
        data_path, "projects", f"{OWNER}.json"
    )


# --- the list --------------------------------------------------------------------


def test_an_empty_list_lists_nothing(data_path):
    assert projects.list_projects() == []
    assert projects.list_projects(OWNER) == []
    assert not os.path.exists(projects.get_projects_file_path())


def test_a_project_is_added_listed_and_removed(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    added = projects.add_project(shop, folder="Shop", caption=" Prod ", color="GREEN")
    assert added == {
        "project_path": shop,
        "folder": "/Shop",
        "caption": "Prod",
        "color": "green",
        "mcp_access": True,
        "schema_name": "shop",
        "exists": True,
    }
    assert projects.list_projects() == [added]

    with open(projects.get_projects_file_path()) as list_file:
        content = json.load(list_file)
    assert content == {
        "version": 1,
        "projects": {
            shop: {"folder": "/Shop", "caption": "Prod", "color": "green", "mcpAccess": True}
        },
    }

    assert projects.remove_project(shop) == shop
    assert projects.list_projects() == []
    with pytest.raises(ValueError, match="not in the project list"):
        projects.remove_project(shop)
    assert os.path.isdir(shop), "removing from the list must not touch the disk"


def test_a_project_without_details_is_at_the_top_level(data_path, tmp_path):
    notes = _make_project(tmp_path, "notes")

    listed = projects.add_project(notes)

    assert listed["folder"] == "/"
    assert listed["caption"] == ""
    assert listed["color"] == ""
    with open(projects.get_projects_file_path()) as list_file:
        assert json.load(list_file)["projects"] == {notes: {"mcpAccess": True}}


def test_only_a_project_folder_can_be_added(data_path, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(ValueError, match="does not contain a MSM project"):
        projects.add_project(str(plain))
    with pytest.raises(ValueError, match="project_path has to be given"):
        projects.add_project("")
    assert not os.path.exists(projects.get_projects_file_path())


def test_a_detail_is_checked_before_anything_is_written(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    with pytest.raises(ValueError, match="not a color"):
        projects.add_project(shop, color="teal")

    assert projects.list_projects() == []


def test_the_path_is_stored_absolute_and_expanded(data_path, tmp_path, monkeypatch):
    shop = _make_project(tmp_path, "shop")
    monkeypatch.chdir(tmp_path)

    listed = projects.add_project("shop.msm.project/")

    assert listed["project_path"] == shop
    assert projects.list_projects() == [listed]


def test_adding_again_changes_the_details_given_and_keeps_the_rest(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    projects.add_project(shop, folder="/Shop", caption="Prod", color="green")

    again = projects.add_project(shop, caption="Production")

    assert again["folder"] == "/Shop"
    assert again["caption"] == "Production"
    assert again["color"] == "green"
    assert len(projects.list_projects()) == 1


def test_an_update_moves_recaptions_and_clears(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    projects.add_project(shop, folder="/Shop", caption="Prod", color="green")

    moved = projects.update_project(shop, folder="/Archive/2026")
    assert moved["folder"] == "/Archive/2026"
    assert moved["caption"] == "Prod"
    assert moved["color"] == "green"

    cleared = projects.update_project(shop, folder="/", caption="", color="")
    assert cleared["folder"] == "/"
    assert cleared["caption"] == ""
    assert cleared["color"] == ""
    with open(projects.get_projects_file_path()) as list_file:
        assert json.load(list_file)["projects"] == {shop: {"mcpAccess": True}}


def test_an_update_needs_a_listed_project(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    with pytest.raises(ValueError, match="not in the project list"):
        projects.update_project(shop, folder="/Shop")


def test_a_missing_project_stays_listed_and_can_still_be_moved(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    projects.add_project(shop, folder="/Shop")
    os.remove(os.path.join(shop, "msm.project.json"))

    listed = projects.list_projects()[0]
    assert listed["exists"] is False
    assert listed["schema_name"] is None

    assert projects.update_project(shop, folder="/Gone")["folder"] == "/Gone"
    assert projects.remove_project(shop) == shop


def test_the_list_is_sorted_by_path(data_path, tmp_path):
    for name in ("zeta", "alpha", "mid"):
        projects.add_project(_make_project(tmp_path, name))

    assert _paths(projects.list_projects()) == sorted(_paths(projects.list_projects()))


def test_owners_keep_lists_of_their_own(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    notes = _make_project(tmp_path, "notes")
    projects.add_project(shop)
    projects.add_project(notes, owner=OWNER)
    projects.add_project(notes, folder="/Mine", owner=OTHER_OWNER)

    assert _paths(projects.list_projects()) == [shop]
    assert _paths(projects.list_projects(OWNER)) == [notes]
    assert projects.list_projects(OTHER_OWNER)[0]["folder"] == "/Mine"
    with pytest.raises(ValueError, match="not in the project list"):
        projects.remove_project(shop, owner=OWNER)

    projects.remove_owner(OWNER)

    assert projects.list_projects(OWNER) == []
    assert not os.path.exists(projects.get_projects_file_path(OWNER))
    assert _paths(projects.list_projects(OTHER_OWNER)) == [notes]
    assert _paths(projects.list_projects()) == [shop]
    projects.remove_owner(OWNER)  # nothing to remove is fine
    with pytest.raises(ValueError, match="owner of the list"):
        projects.remove_owner(None)


def test_a_damaged_file_lists_nothing_and_is_not_overwritten(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    path = projects.get_projects_file_path()
    with open(path, "w") as list_file:
        list_file.write("{not json")

    assert projects.list_projects() == []
    with pytest.raises(ValueError, match="cannot be read"):
        projects.add_project(shop)
    with open(path) as list_file:
        assert list_file.read() == "{not json"

    with open(path, "w") as list_file:
        json.dump({"version": 1, "projects": []}, list_file)
    with pytest.raises(ValueError, match="does not hold a project list"):
        projects.remove_project(shop)


def test_unknown_fields_and_shapes_are_ignored(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    with open(projects.get_projects_file_path(), "w") as list_file:
        json.dump(
            {
                "version": 1,
                "projects": {
                    shop: {"folder": "/Shop", "secret": "x", "color": 3, "caption": ""},
                    "/elsewhere": "not a dict",
                },
            },
            list_file,
        )

    listed = projects.list_projects()

    assert len(listed) == 1
    assert listed[0]["folder"] == "/Shop"
    assert listed[0]["color"] == ""


# --- the shell functions -----------------------------------------------------------


def test_the_shell_functions_manage_the_list(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    added = management.add_project(shop, folder="/Shop", color="blue")
    assert added["folder"] == "/Shop"
    assert management.list_projects() == [added]

    updated = management.update_project(shop, caption="Prod")
    assert updated["caption"] == "Prod"
    assert updated["color"] == "blue"

    assert management.remove_project(shop) == shop
    assert management.list_projects() == []


def test_a_created_project_is_added_to_the_list(data_path, tmp_path):
    created = management.create_new_project_folder(
        schema_name="msm_listed", target_path=str(tmp_path), copyright_holder="Ada"
    )

    listed = projects.list_projects()
    assert _paths(listed) == [created]
    assert listed[0]["schema_name"] == "msm_listed"
    assert listed[0]["exists"] is True


def test_creating_a_project_can_leave_the_list_alone(data_path, tmp_path):
    management.create_new_project_folder(
        schema_name="msm_unlisted",
        target_path=str(tmp_path),
        copyright_holder="Ada",
        add_to_project_list=False,
    )

    assert projects.list_projects() == []


# --- MCP access -----------------------------------------------------------------


def test_mcp_access_is_kept_and_defaults_by_caller(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    notes = _make_project(tmp_path, "notes")

    assert projects.add_project(shop, default_mcp_access=False)["mcp_access"] is False
    assert projects.add_project(notes)["mcp_access"] is True
    # Added again, a listed project keeps its setting unless told.
    assert projects.add_project(shop, caption="Prod")["mcp_access"] is False
    assert projects.add_project(shop, mcp_access=True)["mcp_access"] is True
    assert projects.update_project(shop, mcp_access=False)["mcp_access"] is False
    assert projects.update_project(shop, color="red")["mcp_access"] is False

    with open(projects.get_projects_file_path()) as list_file:
        stored = json.load(list_file)["projects"]
    assert stored[shop] == {"caption": "Prod", "color": "red", "mcpAccess": False}
    assert stored[notes] == {"mcpAccess": True}


def test_mcp_access_has_to_be_a_bool(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    with pytest.raises(ValueError, match="true or false"):
        projects.add_project(shop, mcp_access="false")
    assert projects.list_projects() == []


def test_an_entry_from_before_the_setting_has_access(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    with open(projects.get_projects_file_path(), "w") as list_file:
        json.dump({"version": 1, "projects": {shop: {"folder": "/Shop"}}}, list_file)

    assert projects.list_projects()[0]["mcp_access"] is True
    assert projects.find_restricted_project(shop) is None


def test_the_listing_can_leave_out_the_projects_without_access(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    notes = _make_project(tmp_path, "notes")
    projects.add_project(shop, mcp_access=False)
    projects.add_project(notes)

    assert _paths(projects.list_projects()) == sorted([shop, notes])
    assert _paths(projects.list_projects(mcp_access_only=True)) == [notes]


def test_a_restricted_project_is_found_for_any_path_in_it(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    notes = _make_project(tmp_path, "notes")
    projects.add_project(shop, mcp_access=False)
    projects.add_project(notes)

    assert projects.find_restricted_project(shop) == shop
    assert projects.find_restricted_project(os.path.join(shop, "development", "x.sql")) == shop
    assert projects.find_restricted_project(notes) is None
    assert projects.find_restricted_project(str(tmp_path)) is None
    # A sibling whose name starts the same is not inside it.
    assert projects.find_restricted_project(shop + "2") is None
    # Another owner's list is another list.
    assert projects.find_restricted_project(shop, OWNER) is None


def test_a_link_does_not_get_around_a_restricted_project(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")
    projects.add_project(shop, mcp_access=False)
    link = tmp_path / "shortcut"
    os.symlink(shop, link)

    assert projects.find_restricted_project(os.path.join(str(link), "msm.project.json")) == shop


def test_the_shell_functions_set_mcp_access(data_path, tmp_path):
    shop = _make_project(tmp_path, "shop")

    assert management.add_project(shop)["mcp_access"] is True
    assert management.update_project(shop, mcp_access=False)["mcp_access"] is False
