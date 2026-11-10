# Copyright (c) 2025, 2026, Oracle and/or its affiliates.
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

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from msm_plugin.management import *

SCHEMA_NAME = "my_schema"
COPYRIGHT_HOLDER = "Oracle and/or its affiliates."

MSM_SECTION_140_SQL_CONTENT_001 = """
CREATE TABLE `my_schema`.`my_1st_table`(
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name1` VARCHAR(255)
);"""

MSM_SECTION_140_SQL_CONTENT_002 = """
CREATE TABLE `my_schema`.`my_2nd_table`(
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name2` VARCHAR(255)
);
"""

MSM_SECTION_150_SQL_CONTENT_002 = r"""
DELIMITER %%

DROP PROCEDURE IF EXISTS `my_schema`.`my_1st_proc`%%
CREATE PROCEDURE `my_schema`.`my_1st_proc`(INOUT val INT)
BEGIN
    SET val = val + 1;
END%%

DELIMITER ;
"""

MSM_SECTION_140_SQL_CONTENT_003 = """
CREATE TABLE `my_schema`.`my_3nd_table`(
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name3` VARCHAR(255)
);
"""


def drop_schema(session, schema_name):
    lib.core.MsmDbExec(
        f"DROP SCHEMA IF EXISTS {lib.core.quote_ident(schema_name)}"
    ).exec(session)


@contextmanager
def fresh_schema(session, schema_name):
    # Dropped before the block, to start fresh, and after it, to leave nothing
    # behind
    drop_schema(session, schema_name)
    try:
        yield
    finally:
        drop_schema(session, schema_name)


def count_tables(session, schema_name, table_type):
    return (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(session, [schema_name, table_type])
        .first["n"]
    )


def table_exists(session, schema_name, table_name):
    return (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?"
        )
        .exec(session, [schema_name, table_name])
        .first["n"]
        == 1
    )


def update_script(files):
    # The update script among the files prepare_release returns
    scripts = [
        file for file in files if os.path.basename(os.path.dirname(file)) == "updates"
    ]
    assert len(scripts) == 1, f"prepare_release returned no single update script: {files}"
    return scripts[0]


def test_msm_sections():
    tests_folder = Path(__file__).parent.parent

    with tempfile.TemporaryDirectory() as temp_dir:
        # temp_dir = os.path.join(os.path.expanduser("~"), "Documents", "temp")
        # Create a new MSM project
        project_path = create_new_project_folder(
            schema_name=SCHEMA_NAME,
            target_path=temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
        )

        # Update the development file with the first CREATE TABLE
        dev_file_path = lib.management.get_schema_development_file_path(
            schema_project_path=project_path
        )

        set_section_sql_content(
            file_path=dev_file_path,
            section_id="140",
            sql_content=MSM_SECTION_140_SQL_CONTENT_001,
        )

        # Check if the content was written to the file
        sql_content = get_sql_content_from_section(
            file_path=dev_file_path, section_id="140"
        )

        assert sql_content == MSM_SECTION_140_SQL_CONTENT_001.strip()

        # Add more content and check if the content was updated correctly
        set_section_sql_content(
            file_path=dev_file_path,
            section_id="140",
            sql_content=(
                MSM_SECTION_140_SQL_CONTENT_001 + "\n" + MSM_SECTION_140_SQL_CONTENT_002
            ),
        )

        sql_content = get_sql_content_from_section(
            file_path=dev_file_path, section_id="140"
        )

        assert (
            sql_content
            == (
                MSM_SECTION_140_SQL_CONTENT_001 + "\n" + MSM_SECTION_140_SQL_CONTENT_002
            ).strip()
        )


def test_create_new_project_folder():
    with tempfile.TemporaryDirectory() as temp_dir:
        # temp_dir = os.path.join(os.path.expanduser("~"), "Documents", "temp")

        project_path = create_new_project_folder(
            schema_name=SCHEMA_NAME,
            target_path=temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
        )

        assert os.path.exists(project_path)

        assert os.path.exists(os.path.join(project_path, "README.md"))
        assert os.path.exists(os.path.join(project_path, "msm.project.json"))

        project_settings = get_project_settings(schema_project_path=project_path)
        assert project_settings.get("copyrightHolder", None) == COPYRIGHT_HOLDER

        # The notices are held as a list, with the single holder field kept as a
        # mirror of the first entry for readers of the earlier format.
        year_of_creation = project_settings.get("yearOfCreation")
        assert project_settings.get("copyrights") == [
            {
                "holder": COPYRIGHT_HOLDER,
                "yearOfCreation": year_of_creation,
                "tracksUpdates": True,
            }
        ]

        # The generated files carry the notice of the project, so the notices of
        # this repository that the templates hold must be gone. Exactly one is
        # left, rather than one per notice the template carried.
        readme = Path(os.path.join(project_path, "README.md")).read_text()
        assert readme.count("Copyright") == 1
        assert "MariaDB plc" not in readme
        assert readme.rstrip().endswith(
            f"Copyright (c) {year_of_creation}, {COPYRIGHT_HOLDER}"
        )

        project_info = get_project_information(schema_project_path=project_path)

        current_dev_version = project_info.get("currentDevelopmentVersion", None)
        assert current_dev_version == "0.0.1"


def test_schema_name_has_to_be_usable_unquoted_unless_special_chars_are_allowed(
    temp_dir,
):
    with pytest.raises(ValueError, match="allow_special_chars"):
        create_new_project_folder(
            schema_name="shop-db",
            target_path=temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
        )
    # A rejected name leaves no project folder behind
    assert not os.path.exists(os.path.join(temp_dir, "shop-db.msm.project"))

    project_path = create_new_project_folder(
        schema_name="shop-db",
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        allow_special_chars=True,
    )
    assert get_project_settings(schema_project_path=project_path)[
        "schemaName"
    ] == "shop-db"

    # The project folder is named after the schema with whitespace replaced,
    # the project keeps the name as given
    project_path = create_new_project_folder(
        schema_name="my shop",
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        allow_special_chars=True,
    )
    assert os.path.basename(project_path) == "my_shop.msm.project"
    assert get_project_settings(schema_project_path=project_path)[
        "schemaName"
    ] == "my shop"


def test_check_mysql_identifier():
    for name in ("123", "2024"):
        with pytest.raises(ValueError, match="solely of digits"):
            lib.management.check_mysql_identifier(name)
    for name in ("a-b", "my schema", "$shop", "café"):
        with pytest.raises(ValueError, match="basic Latin letters"):
            lib.management.check_mysql_identifier(name)
    for name in (None, ""):
        with pytest.raises(ValueError, match="has to be given"):
            lib.management.check_mysql_identifier(name)
    for name in ("shop", "Shop_2", "_x", "a$b", "a_b$1", "9lives"):
        lib.management.check_mysql_identifier(name)
    for name in ("123", "my schema", "a-b"):
        lib.management.check_mysql_identifier(
            name, must_be_usable_when_unquoted=False
        )


def test_render_copyright_notices():
    # A single holder, whose year of creation is the only year shown. The
    # notice does not follow today's date: the years are the stored ones.
    single = {"copyrights": [{"holder": "ACME Corp.", "yearOfCreation": "2025",
                              "tracksUpdates": True}]}
    assert (
        lib.management.render_copyright_notices(single)
        == "Copyright (c) 2025, ACME Corp."
    )

    # The holder is written as stored: a final period is the user's to add
    assert lib.management.render_copyright_notices(
        {"copyrights": [{"holder": "ACME Corp", "yearOfCreation": "2026"}]}
    ) == "Copyright (c) 2026, ACME Corp"

    # The year of the last update is shown where it differs from the year of
    # creation, and the prefix goes on every line
    two_holders = {
        "copyrights": [
            {
                "holder": "Upstream Inc.",
                "yearOfCreation": "2021",
                "yearOfLastUpdate": "2024",
                "tracksUpdates": False,
            },
            {"holder": "MariaDB plc.", "yearOfCreation": "2026",
             "yearOfLastUpdate": "2030", "tracksUpdates": True},
            {"holder": "Same Year", "yearOfCreation": "2026",
             "yearOfLastUpdate": "2026", "tracksUpdates": True},
        ]
    }
    assert lib.management.render_copyright_notices(two_holders, prefix=" * ") == (
        " * Copyright (c) 2021, 2024, Upstream Inc.\n"
        " * Copyright (c) 2026, 2030, MariaDB plc.\n"
        " * Copyright (c) 2026, Same Year"
    )


def in_year(monkeypatch, year):
    """Applies changes as if in the given year."""
    monkeypatch.setattr(lib.management, "current_year", lambda: str(year))


def test_copyright_settings_migration(monkeypatch):
    # The single holder format of projects created before the copyrights list
    legacy = {"copyrightHolder": "ACME Corp.", "yearOfCreation": "2025"}
    assert lib.management.get_project_copyrights(legacy) == [
        {"holder": "ACME Corp.", "yearOfCreation": "2025", "tracksUpdates": True}
    ]

    # A second notice that had been squeezed into the copyrightHolder field,
    # comment prefix included, is taken apart into its own entry again
    squeezed = {
        "copyrightHolder": (
            "Oracle and/or its affiliates.\n * Copyright (c) 2026, MariaDB plc"
        ),
        "yearOfCreation": "2025",
    }
    assert lib.management.get_project_copyrights(squeezed) == [
        {
            "holder": "Oracle and/or its affiliates.",
            "yearOfCreation": "2025",
            "tracksUpdates": False,
        },
        {"holder": "MariaDB plc", "yearOfCreation": "2026", "tracksUpdates": True},
    ]

    # Only the holder added last follows a change applied in a later year
    copyrights = lib.management.get_project_copyrights(squeezed)
    in_year(monkeypatch, 2030)
    assert lib.management.bump_copyright_years(copyrights)
    assert lib.management.render_copyright_notices({"copyrights": copyrights}) == (
        "Copyright (c) 2025, Oracle and/or its affiliates.\n"
        "Copyright (c) 2026, 2030, MariaDB plc"
    )


def test_bump_copyright_years(monkeypatch):
    copyrights = [
        {"holder": "Upstream Inc.", "yearOfCreation": "2021", "tracksUpdates": False},
        {"holder": "ACME Corp.", "yearOfCreation": "2026", "tracksUpdates": True},
    ]
    # Nothing to do in the year of creation
    in_year(monkeypatch, 2026)
    assert not lib.management.bump_copyright_years(copyrights)
    assert "yearOfLastUpdate" not in copyrights[1]

    # 2026, then 2026, 2027, then 2026, 2028
    in_year(monkeypatch, 2027)
    assert lib.management.bump_copyright_years(copyrights)
    assert lib.management.get_copyright_years(copyrights[1]) == "2026, 2027"
    in_year(monkeypatch, 2027)
    assert not lib.management.bump_copyright_years(copyrights)
    in_year(monkeypatch, 2028)
    assert lib.management.bump_copyright_years(copyrights)
    assert lib.management.get_copyright_years(copyrights[1]) == "2026, 2028"

    # The inherited notice stays frozen
    assert lib.management.get_copyright_years(copyrights[0]) == "2021"

    # A year that lies before the last update does not move it back
    in_year(monkeypatch, 2027)
    assert not lib.management.bump_copyright_years(copyrights)
    assert copyrights[1]["yearOfLastUpdate"] == "2028"


def test_refresh_notice_years():
    copyrights = [
        {"holder": "Upstream Inc.", "yearOfCreation": "2021", "tracksUpdates": False},
        {"holder": "ACME Corp", "yearOfCreation": "2026", "yearOfLastUpdate": "2028",
         "tracksUpdates": True},
    ]
    text = (
        "/*\n"
        " * Copyright (c) 2021, Upstream Inc.\n"
        " * Copyright (c) 2026, 2027, ACME Corp.\n"
        " *\n"
        " * Copyright (c) 2026, ACME Corp in the license text stays as it is.\n"
        " */\n"
    )
    # Only the years of the holder that tracks updates change, in the first
    # run of notices; a holder written with the period msm used to add is
    # still found, and the line keeps it
    assert lib.management.refresh_notice_years(text, copyrights) == text.replace(
        "2026, 2027, ACME Corp.", "2026, 2028, ACME Corp."
    )

    # A notice comment of its own, and a line without years of update
    assert lib.management.refresh_notice_years(
        "/* Copyright (c) 2026, ACME Corp */\nSELECT 1;\n", copyrights
    ) == "/* Copyright (c) 2026, 2028, ACME Corp */\nSELECT 1;\n"

    # Nothing to find, nothing changed
    assert lib.management.refresh_notice_years("SELECT 1;\n", copyrights) == "SELECT 1;\n"

    # A holder starting with four digits is not taken for a last-update year
    labs = [{"holder": "2048 Labs", "yearOfCreation": "2026", "yearOfLastUpdate": "2027",
             "tracksUpdates": True}]
    assert lib.management.refresh_notice_years(
        "-- Copyright (c) 2026, 2048 Labs\n", labs
    ) == "-- Copyright (c) 2026, 2027, 2048 Labs\n"
    assert lib.management.refresh_notice_years(
        "-- Copyright (c) 2026, 2027, 2048 Labs\n", labs
    ) == "-- Copyright (c) 2026, 2027, 2048 Labs\n"


def test_check_copyright_year():
    assert lib.management.check_copyright_year(2024) == "2024"
    assert lib.management.check_copyright_year(" 2024 ") == "2024"
    for year in ("24", "twenty", "", None, True, "2024-01-01"):
        with pytest.raises(ValueError, match="four digits"):
            lib.management.check_copyright_year(year)
    with pytest.raises(ValueError, match="future"):
        lib.management.check_copyright_year(int(lib.management.current_year()) + 1)


def test_create_new_project_folder_with_a_year_of_creation(tmp_path):
    temp_dir = str(tmp_path)
    this_year = lib.management.current_year()
    earlier = str(int(this_year) - 2)
    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME,
        target_path=temp_dir,
        copyright_holder="ACME Corp.",
        year_of_creation=earlier,
    )

    # Creating the project is a change applied this year
    project_settings = get_project_settings(schema_project_path=project_path)
    assert project_settings["copyrights"] == [
        {
            "holder": "ACME Corp.",
            "yearOfCreation": earlier,
            "tracksUpdates": True,
            "yearOfLastUpdate": this_year,
        }
    ]
    assert project_settings["yearOfCreation"] == earlier
    readme = Path(os.path.join(project_path, "README.md")).read_text()
    assert readme.rstrip().endswith(f"Copyright (c) {earlier}, {this_year}, ACME Corp.")
    dev_script = Path(
        lib.management.get_schema_development_file_path(schema_project_path=project_path)
    ).read_text()
    assert f" * Copyright (c) {earlier}, {this_year}, ACME Corp.\n" in dev_script

    # A year that is not one is refused before anything is written
    for year in ("26", str(int(this_year) + 1)):
        with pytest.raises(ValueError, match="yearOfCreation"):
            create_new_project_folder(
                schema_name="other_schema",
                target_path=temp_dir,
                copyright_holder="ACME Corp.",
                year_of_creation=year,
            )
    assert not os.path.exists(os.path.join(temp_dir, "other_schema.msm.project"))


def test_update_copyright_years(tmp_path, monkeypatch):
    temp_dir = str(tmp_path)
    this_year = lib.management.current_year()
    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME,
        target_path=temp_dir,
        copyrights=[
            {"holder": "Upstream Inc.", "yearOfCreation": "2021", "tracksUpdates": False},
            {"holder": "ACME Corp.", "tracksUpdates": True},
        ],
        license="MIT",
    )
    dev_file = lib.management.get_schema_development_file_path(
        schema_project_path=project_path
    )
    sections = os.path.join(project_path, "development", "sections")
    os.makedirs(sections)
    breakout = os.path.join(sections, "140-10_tables.sql")
    Path(breakout).write_text(
        lib.sections.breakout_header(
            get_project_settings(schema_project_path=project_path), "140", "Tables"
        )
        + "CREATE TABLE t (id INT);\n"
    )
    released = os.path.join(project_path, "releases", "versions", "old.sql")
    Path(released).write_text(f"-- Copyright (c) {this_year}, ACME Corp.\n")

    # Nothing to do in the year the project was created
    assert lib.management.update_copyright_years(project_path) == []

    later = str(int(this_year) + 1)
    in_year(monkeypatch, later)
    written = lib.management.update_copyright_years(project_path)
    settings_path = os.path.join(project_path, "msm.project.json")
    assert written[0] == settings_path
    assert dev_file in written and breakout in written
    assert os.path.join(project_path, "README.md") in written
    assert released not in written

    project_settings = get_project_settings(schema_project_path=project_path)
    assert project_settings["copyrights"][1]["yearOfLastUpdate"] == later
    assert "yearOfLastUpdate" not in project_settings["copyrights"][0]

    dev_script = Path(dev_file).read_text()
    assert " * Copyright (c) 2021, Upstream Inc.\n" in dev_script
    assert f" * Copyright (c) {this_year}, {later}, ACME Corp.\n" in dev_script
    assert f"-- Copyright (c) {this_year}, {later}, ACME Corp.\n" in Path(breakout).read_text()
    readme = Path(os.path.join(project_path, "README.md")).read_text()
    assert f"Copyright (c) {this_year}, {later}, ACME Corp." in readme
    # A released file keeps the notice it was released with
    assert Path(released).read_text() == f"-- Copyright (c) {this_year}, ACME Corp.\n"

    # Once per year
    assert lib.management.update_copyright_years(project_path) == []


def test_every_write_to_a_project_moves_the_copyright_years_along(tmp_path, monkeypatch):
    from msm_plugin.lib import sections

    this_year = int(lib.management.current_year())
    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME, target_path=str(tmp_path), copyright_holder="ACME Corp."
    )
    dev_file = lib.management.get_schema_development_file_path(
        schema_project_path=project_path
    )

    def last_update():
        return get_project_settings(schema_project_path=project_path)["copyrights"][0].get(
            "yearOfLastUpdate"
        )

    # Reading leaves the years as they are
    in_year(monkeypatch, this_year + 1)
    lib.management.set_development_version(
        new_version="0.0.2", schema_project_path=project_path, write_to_file=False
    )
    assert last_update() is None

    # A file of the project, found from the file
    lib.management.set_section_sql_content("140", "\nSELECT 1;\n", dev_file)
    assert last_update() == str(this_year + 1)

    writes = [
        lambda: set_development_version(schema_project_path=project_path, version="0.0.3"),
        lambda: lib.management.set_online_alter_table(project_path, "t", {"lockWait": 2}),
        lambda: lib.management.set_online_alter_defaults(project_path, {"maxRetries": 3}),
        lambda: sections.write_section_parts(
            project_path, "140", [sections.Part("140-10_tables.sql", "Tables", "\nSELECT 2;\n")]
        ),
    ]
    for offset, write in enumerate(writes, start=2):
        in_year(monkeypatch, this_year + offset)
        write()
        assert last_update() == str(this_year + offset)
    assert f" * Copyright (c) {this_year}, {this_year + len(writes) + 1}, ACME Corp.\n" in Path(
        dev_file
    ).read_text()


def test_a_refused_write_leaves_the_copyright_years(tmp_path, monkeypatch):
    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME, target_path=str(tmp_path), copyright_holder="ACME Corp."
    )
    in_year(monkeypatch, int(lib.management.current_year()) + 1)
    with pytest.raises(ValueError):
        lib.management.set_online_alter_table(project_path, "t", {"lockWait": 0})
    # ... and so does one that writes nothing
    assert lib.management.set_online_alter_table(project_path, "t", None) == []
    assert get_project_settings(schema_project_path=project_path)["copyrights"][0].get(
        "yearOfLastUpdate"
    ) is None


def test_a_write_inside_a_write_moves_the_years_once(tmp_path, monkeypatch):
    from msm_plugin.lib.project_changes import changes_project

    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME, target_path=str(tmp_path), copyright_holder="ACME Corp."
    )
    calls = []
    real = lib.management.update_copyright_years
    monkeypatch.setattr(
        lib.management, "update_copyright_years", lambda path: calls.append(path) or real(path)
    )

    @changes_project
    def inner(schema_project_path):
        pass

    @changes_project
    def outer(schema_project_path):
        inner(schema_project_path)
        inner(schema_project_path=schema_project_path)

    outer(project_path)
    assert calls == [project_path]


def test_a_legacy_project_gets_its_copyrights_list_written(tmp_path, monkeypatch):
    temp_dir = str(tmp_path)
    project_path = create_new_project_folder(
        schema_name=SCHEMA_NAME, target_path=temp_dir, copyright_holder="ACME Corp."
    )
    settings_path = os.path.join(project_path, "msm.project.json")
    with open(settings_path) as f:
        raw = json.load(f)
    del raw["copyrights"]
    raw["yearOfCreation"] = "2020"
    with open(settings_path, "w") as f:
        json.dump(raw, f)

    later = str(int(lib.management.current_year()) + 1)
    in_year(monkeypatch, later)
    assert lib.management.update_copyright_years(project_path)[0] == settings_path
    with open(settings_path) as f:
        raw = json.load(f)
    assert raw["copyrights"] == [
        {"holder": "ACME Corp.", "yearOfCreation": "2020", "tracksUpdates": True,
         "yearOfLastUpdate": later}
    ]


def test_license_text_holds_every_notice():
    project_settings = {
        "license": "GPL-2.0",
        "customLicense": "",
        "copyrights": [
            {
                "holder": "Upstream Inc.",
                "yearOfCreation": "2021",
                "tracksUpdates": False,
            },
            {"holder": "ACME Corp.", "yearOfCreation": "2026", "tracksUpdates": True},
        ],
    }

    license_text = lib.management.get_license_text(project_settings=project_settings)

    assert " * Copyright (c) 2021, Upstream Inc." in license_text
    assert " * Copyright (c) 2026" in license_text
    assert "ACME Corp." in license_text
    # The notices of this repository, which the license template carries on its
    # first lines, are stripped rather than being handed to the project
    assert "Oracle" not in license_text
    assert "MariaDB plc" not in license_text
    # No placeholder is left unsubstituted
    assert "${" not in license_text


def test_the_license_sentence_ends_with_one_period():
    # The BSD text ends a sentence after ${copyright_holder}: a holder stored
    # with its own final period does not get a second one there, while the
    # notice shows the holder as stored
    license_text = lib.management.get_license_text(project_settings={
        "license": "BSD",
        "customLicense": "",
        "copyrights": [{"holder": "ACME Corp.", "yearOfCreation": "2026",
                        "tracksUpdates": True}],
    })
    assert "was developed by the ACME Corp.\n" in license_text
    assert ".." not in license_text
    assert " * Copyright (c) 2026, ACME Corp.\n" in license_text


def test_a_last_update_before_the_creation_is_refused(tmp_path):
    with pytest.raises(ValueError, match="yearOfLastUpdate 2021 lies before"):
        create_new_project_folder(
            schema_name=SCHEMA_NAME,
            target_path=str(tmp_path),
            copyrights=[{"holder": "X", "yearOfCreation": "2025",
                         "yearOfLastUpdate": "2021", "tracksUpdates": False}],
        )
    assert not os.path.exists(os.path.join(str(tmp_path), f"{SCHEMA_NAME}.msm.project"))


def test_create_new_project_folder_with_multiple_copyrights():
    copyrights = [
        {"holder": "Upstream Inc.", "yearOfCreation": "2021", "tracksUpdates": False},
        {"holder": "ACME Corp.", "yearOfCreation": lib.management.current_year(),
         "tracksUpdates": True},
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        project_path = create_new_project_folder(
            schema_name=SCHEMA_NAME,
            target_path=temp_dir,
            copyrights=copyrights,
            license="GPL-2.0",
        )

        project_settings = get_project_settings(schema_project_path=project_path)
        assert project_settings.get("copyrights") == copyrights
        assert project_settings.get("copyrightHolder") == "Upstream Inc."

        # Both notices reach the markdown files
        readme = Path(os.path.join(project_path, "README.md")).read_text()
        assert "Copyright (c) 2021, Upstream Inc." in readme
        assert "ACME Corp." in readme

        # ... and the license block of the development script
        dev_file_path = lib.management.get_schema_development_file_path(
            schema_project_path=project_path
        )
        dev_script = Path(dev_file_path).read_text()
        assert " * Copyright (c) 2021, Upstream Inc." in dev_script
        assert "ACME Corp." in dev_script
        assert "${" not in dev_script


def test_set_development_version():
    with tempfile.TemporaryDirectory() as temp_dir:
        # temp_dir = os.path.join(os.path.expanduser("~"), "Documents", "temp")

        project_path = create_new_project_folder(
            schema_name=SCHEMA_NAME,
            target_path=temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
        )

        # Set the development version to 0.0.2
        set_development_version(schema_project_path=project_path, version="0.0.2")
        project_info = get_project_information(schema_project_path=project_path)
        current_dev_version = project_info.get("currentDevelopmentVersion", None)
        assert current_dev_version == "0.0.2"

        # Set the development version back to 0.0.1
        set_development_version(schema_project_path=project_path, version="0.0.1")
        project_info = get_project_information(schema_project_path=project_path)
        current_dev_version = project_info.get("currentDevelopmentVersion", None)
        assert current_dev_version == "0.0.1"

        # Check that there are no released versions
        released_version = get_released_versions(schema_project_path=project_path)
        assert len(released_version) == 0


def test_prepare_release(sandbox_session, project_path):
    # Since the project has just been created, there is no deployment script yet
    with pytest.raises(Exception):
        generate_deployment_script(schema_project_path=project_path)

    # ----------------------------------------------------------------------
    # Write some SQL to the development/my_schema_next.sql file
    dev_sql_file_path = os.path.join(project_path, "development", "my_schema_next.sql")
    set_section_sql_content(
        file_path=dev_sql_file_path,
        section_id="140",
        sql_content=MSM_SECTION_140_SQL_CONTENT_001,
    )

    files_for_release = prepare_release(
        schema_project_path=project_path, version="0.0.1", next_version="0.0.2"
    )

    # Since this is the first release, there will only be the versions/my_schema_0.0.1.sql file
    # and no updates file
    assert len(files_for_release) == 1

    released_version = get_released_versions(schema_project_path=project_path)
    assert len(released_version) == 1

    last_released_version = get_last_released_version(schema_project_path=project_path)
    assert last_released_version == [0, 0, 1]

    # Check if the content of the version SQL file has the right content
    version_sql_file_path = os.path.join(
        project_path, "releases", "versions", "my_schema_0.0.1.sql"
    )
    sql_content_001 = get_sql_content_from_section(
        file_path=dev_sql_file_path, section_id="140"
    )
    assert sql_content_001 == MSM_SECTION_140_SQL_CONTENT_001.strip()

    # Generate deployment script
    deployment_sql_file_path = generate_deployment_script(
        schema_project_path=project_path
    )

    # Check if the schema and the table have been created
    assert sandbox_session is not None

    lib.core.execute_msm_sql_script(
        session=sandbox_session, sql_file_path=deployment_sql_file_path
    )

    assert lib.core.db_schema_exists(sandbox_session, SCHEMA_NAME)

    assert count_tables(sandbox_session, SCHEMA_NAME, "BASE TABLE") == 1

    # msm_schema_version and msm_schema_diagrams
    assert count_tables(sandbox_session, SCHEMA_NAME, "VIEW") == 2

    # ----------------------------------------------------------------------
    # Add more SQL to the development/my_schema_next.sql file

    # Add another table
    sql_content_dev = get_sql_content_from_section(
        file_path=dev_sql_file_path, section_id="140"
    )
    sql_content_dev_140 = sql_content_dev + "\n" + MSM_SECTION_140_SQL_CONTENT_002
    set_section_sql_content(
        file_path=dev_sql_file_path, section_id="140", sql_content=sql_content_dev_140
    )

    # Add a stored procedure
    sql_content_dev = get_sql_content_from_section(
        file_path=dev_sql_file_path, section_id="150"
    )
    set_section_sql_content(
        file_path=dev_sql_file_path,
        section_id="150",
        sql_content=MSM_SECTION_150_SQL_CONTENT_002,
    )

    # Prepare 0.0.2 release
    prepare_release(
        schema_project_path=project_path, version="0.0.2", next_version="0.0.3"
    )

    # Check the content of the versions/0.0.2 SQL file section 140
    version_sql_file_path = os.path.join(
        project_path, "releases", "versions", "my_schema_0.0.2.sql"
    )
    sql_content_002 = get_sql_content_from_section(
        file_path=version_sql_file_path, section_id="140"
    )
    assert sql_content_002 == sql_content_dev_140.strip()

    # Set the upgrade code in the 0.0.1 to 0.0.2 update file
    update_sql_file_path = os.path.join(
        project_path, "releases", "updates", "my_schema_0.0.1_to_0.0.2.sql"
    )
    set_section_sql_content(
        file_path=update_sql_file_path,
        section_id="240",
        sql_content=MSM_SECTION_140_SQL_CONTENT_002,
    )

    update_sql_file_path = os.path.join(
        project_path, "releases", "updates", "my_schema_0.0.1_to_0.0.2.sql"
    )
    set_section_sql_content(
        file_path=update_sql_file_path,
        section_id="250",
        sql_content=MSM_SECTION_150_SQL_CONTENT_002,
    )

    # Generate deployment script
    deployment_sql_file_path = generate_deployment_script(
        schema_project_path=project_path
    )

    # Run deployment script
    lib.core.execute_msm_sql_script(
        session=sandbox_session, sql_file_path=deployment_sql_file_path
    )

    # Check that there are now two tables
    assert count_tables(sandbox_session, SCHEMA_NAME, "BASE TABLE") == 2

    # msm_schema_version and msm_schema_diagrams
    assert count_tables(sandbox_session, SCHEMA_NAME, "VIEW") == 2

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as proc_count FROM information_schema.ROUTINES "
            "WHERE ROUTINE_SCHEMA = ? AND ROUTINE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "PROCEDURE"])
        .first["proc_count"]
    ) == 1

    # ----------------------------------------------------------------------
    # Prepare 0.0.3 Release

    # Add more SQL to the development/my_schema_next.sql file
    sql_content_dev = get_sql_content_from_section(
        file_path=dev_sql_file_path, section_id="140"
    )
    sql_content_dev = sql_content_dev + "\n" + MSM_SECTION_140_SQL_CONTENT_003

    set_section_sql_content(
        file_path=dev_sql_file_path, section_id="140", sql_content=sql_content_dev
    )

    # Prepare the 0.0.3 release
    prepare_release(
        schema_project_path=project_path, version="0.0.3", next_version="0.0.4"
    )

    # Set the upgrade code in the 0.0.2 to 0.0.3 update file
    update_sql_file_path = os.path.join(
        project_path, "releases", "updates", "my_schema_0.0.2_to_0.0.3.sql"
    )
    set_section_sql_content(
        file_path=update_sql_file_path,
        section_id="240",
        sql_content=MSM_SECTION_140_SQL_CONTENT_003,
    )

    # Generate deployment script
    deployment_sql_file_path = generate_deployment_script(
        schema_project_path=project_path
    )

    # Run deployment script
    lib.core.execute_msm_sql_script(
        session=sandbox_session, sql_file_path=deployment_sql_file_path
    )

    # Check that there are now two tables
    assert count_tables(sandbox_session, SCHEMA_NAME, "BASE TABLE") == 3

    # msm_schema_version and msm_schema_diagrams
    assert count_tables(sandbox_session, SCHEMA_NAME, "VIEW") == 2


def test_deployment(sandbox_session, project_path):
    # Ensure to start fresh
    drop_schema(sandbox_session, SCHEMA_NAME)

    # Deploy all released versions after each other to test update capability
    released_versions = get_released_versions(schema_project_path=project_path)

    assert len(released_versions) > 0

    version_str = "%d.%d.%d" % tuple(released_versions[0])
    result_msg = deploy_schema(schema_project_path=project_path, version=version_str)

    assert result_msg == (
        f"Deployment of `{SCHEMA_NAME}` version "
        f"{version_str} completed successfully."
    )

    assert len(released_versions) > 1

    version_str_next = "%d.%d.%d" % tuple(released_versions[1])
    result_msg = deploy_schema(
        schema_project_path=project_path, version=version_str_next
    )

    assert result_msg == (
        f"Completed the update of `{SCHEMA_NAME}` version "
        f"{version_str} to {version_str_next} successfully."
    )

    # for version in released_versions:
    #     version_str = '%d.%d.%d' % tuple(version)
    #     deploy_schema(schema_project_path=project_path, version=version_str)

    #     # Check if the right version has actually been deployed
    #     assert (lib.core.MsmDbExec(
    #         "SELECT CONCAT(major, '.', minor, '.', patch) AS version "
    #         f"FROM {lib.core.quote_ident(SCHEMA_NAME)}.`msm_schema_version`")
    #         .exec(sandbox_session).first["version"]) == version_str


def test_deployment_backup_restores_failed_update(
    sandbox_session, project_path, temp_dir
):
    def deployed_version():
        return (
            lib.core.MsmDbExec(
                "SELECT CONCAT(major, '.', minor, '.', patch) AS version "
                f"FROM {lib.core.quote_ident(SCHEMA_NAME)}.`msm_schema_version`"
            )
            .exec(sandbox_session)
            .first["version"]
        )

    drop_schema(sandbox_session, SCHEMA_NAME)

    released_versions = get_released_versions(schema_project_path=project_path)
    assert len(released_versions) > 1
    version_str = "%d.%d.%d" % tuple(released_versions[0])
    version_str_next = "%d.%d.%d" % tuple(released_versions[1])

    deploy_schema(schema_project_path=project_path, version=version_str)
    lib.core.MsmDbExec(
        f"INSERT INTO {lib.core.quote_ident(SCHEMA_NAME)}.`my_1st_table` "
        "(`name1`) VALUES ('kept')"
    ).exec(sandbox_session)

    # Break the update in a copy of the project, so the update to the next
    # version fails after it has already changed the schema
    broken_project_path = os.path.join(temp_dir, "broken_project")
    shutil.copytree(project_path, broken_project_path)
    with open(
        os.path.join(
            broken_project_path,
            "releases",
            "deployment",
            f"{SCHEMA_NAME}_deployment_{version_str_next}.sql",
        ),
        "a",
    ) as f:
        f.write("\nSELECT * FROM `no_such_schema`.`no_such_table`;\n")

    backup_directory = os.path.join(temp_dir, "backup")
    with pytest.raises(Exception) as exc_info:
        deploy_schema(
            schema_project_path=broken_project_path,
            version=version_str_next,
            backup_directory=backup_directory,
            backup=True,
        )

    assert "The schema has been restored" in str(exc_info.value)
    assert deployed_version() == version_str
    assert (
        lib.core.MsmDbExec(
            f"SELECT `name1` FROM {lib.core.quote_ident(SCHEMA_NAME)}.`my_1st_table`"
        )
        .exec(sandbox_session)
        .first["name1"]
    ) == "kept"
    assert not os.path.exists(backup_directory)

    # A working update with a backup removes the dump once it is done
    deploy_schema(
        schema_project_path=project_path,
        version=version_str_next,
        backup_directory=backup_directory,
        backup=True,
    )
    assert deployed_version() == version_str_next
    assert not os.path.exists(backup_directory)


def test_deployment_takes_the_lock_before_it_looks_at_the_schema(
    sandbox_session, project_path, temp_dir
):
    # With another MSM update holding the lock, a deployment stops before it
    # looks at the schema: it neither backs it up nor drops and restores it
    # under the feet of the update that holds the lock
    import mysqlsh
    import msm_plugin.tests.unit.helpers as helpers

    drop_schema(sandbox_session, SCHEMA_NAME)
    released_versions = get_released_versions(schema_project_path=project_path)
    deploy_schema(
        schema_project_path=project_path,
        version="%d.%d.%d" % tuple(released_versions[0]),
    )

    data = helpers.get_connection_data()
    holder = mysqlsh.globals.shell.open_session(
        f"{data['user']}:{data['password']}@{data['host']}:{data['port']}"
    )
    log_path = lib.core.get_msm_schema_update_log_path()
    log_size = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    lock = "CONCAT('MSM_METADATA_LOCK', '_', MD5(?))"
    try:
        holder.run_sql(f"SELECT GET_LOCK({lock}, 0)", [SCHEMA_NAME])
        with pytest.raises(Exception, match="Failed to acquire MSM schema update lock"):
            deploy_schema(
                schema_project_path=project_path,
                version="%d.%d.%d" % tuple(released_versions[1]),
                backup_directory=os.path.join(temp_dir, "backup"),
                backup=True,
            )
    finally:
        holder.run_sql(f"SELECT RELEASE_LOCK({lock})", [SCHEMA_NAME])
        holder.close()

    with open(log_path) as f:
        f.seek(log_size)
        log = f.read()
    assert "Failed to acquire MSM schema update lock" in log
    assert "dump" not in log
    assert "restored" not in log
    assert not os.path.exists(os.path.join(temp_dir, "backup"))


def test_deployment_backup_keeps_the_global_session(
    sandbox_session, project_path, temp_dir
):
    # The backup's dump and load run on the given session, which is the
    # shell's global session only while they run
    import mysqlsh
    import msm_plugin.tests.unit.helpers as helpers

    drop_schema(sandbox_session, SCHEMA_NAME)
    released_versions = get_released_versions(schema_project_path=project_path)
    deploy_schema(
        schema_project_path=project_path,
        version="%d.%d.%d" % tuple(released_versions[0]),
    )

    data = helpers.get_connection_data()
    other = mysqlsh.globals.shell.open_session(
        f"{data['user']}:{data['password']}@{data['host']}:{data['port']}"
    )
    try:
        global_session = mysqlsh.globals.shell.get_session()
        assert other.connection_id != global_session.connection_id

        deploy_schema(
            schema_project_path=project_path,
            version="%d.%d.%d" % tuple(released_versions[1]),
            backup_directory=os.path.join(temp_dir, "backup"),
            backup=True,
            session=other,
        )

        assert (
            mysqlsh.globals.shell.get_session().connection_id
            == global_session.connection_id
        )
    finally:
        other.close()


def test_deployment_runs_rest_service_section(sandbox_session, temp_dir):
    # Section 180 holds the REST Service definition. Once a project has more
    # than one release, the deployment script is built from the deployment
    # template, which has to carry the target version's section 180 so that
    # it is run on a fresh deployment as well as on an update. A plain table
    # stands in for the REST statements, so the test does not depend on the
    # REST metadata schema being configured.
    schema_name = "msm_rest_section"
    project = create_new_project_folder(
        schema_name=schema_name,
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        overwrite_existing=True,
    )
    dev_file = os.path.join(project, "development", f"{schema_name}_next.sql")

    def marker_tables():
        return [
            row["TABLE_NAME"]
            for row in lib.core.MsmDbExec(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME LIKE 'rest_marker_%' "
                "ORDER BY TABLE_NAME"
            )
            .exec(sandbox_session, [schema_name])
            .items
        ]

    set_section_sql_content(
        file_path=dev_file,
        section_id="140",
        sql_content=f"CREATE TABLE `{schema_name}`.`t`(`id` INT PRIMARY KEY);",
    )
    set_section_sql_content(
        file_path=dev_file,
        section_id="180",
        sql_content=f"CREATE TABLE IF NOT EXISTS `{schema_name}`.`rest_marker_1`(`id` INT);",
    )
    prepare_release(schema_project_path=project, version="1.0.0", next_version="1.1.0")

    set_section_sql_content(
        file_path=dev_file,
        section_id="180",
        sql_content=(
            f"CREATE TABLE IF NOT EXISTS `{schema_name}`.`rest_marker_1`(`id` INT);\n"
            f"CREATE TABLE IF NOT EXISTS `{schema_name}`.`rest_marker_2`(`id` INT);"
        ),
    )
    prepare_release(schema_project_path=project, version="1.1.0", next_version="1.2.0")

    # Generated after 1.1.0 exists, so both come from the deployment template
    for version in ("1.0.0", "1.1.0"):
        generate_deployment_script(schema_project_path=project, version=version)

    with fresh_schema(sandbox_session, schema_name):
        deploy_schema(schema_project_path=project, version="1.0.0")
        assert marker_tables() == ["rest_marker_1"]

        deploy_schema(schema_project_path=project, version="1.1.0")
        assert marker_tables() == ["rest_marker_1", "rest_marker_2"]

        drop_schema(sandbox_session, schema_name)
        deploy_schema(schema_project_path=project, version="1.1.0")
        assert marker_tables() == ["rest_marker_1", "rest_marker_2"]


def test_release_states_its_own_version(temp_dir):
    # A new project develops 0.0.1. Released as 1.0.0, the release script's
    # msm_schema_version view has to say 1.0.0, not the development version
    # the development file's section 910 states; the development file goes on
    # with the next version.
    schema_name = "msm_release_version"
    project = create_new_project_folder(
        schema_name=schema_name,
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        overwrite_existing=True,
    )
    version_file = os.path.join(
        project, "releases", "versions", f"{schema_name}_1.0.0.sql"
    )

    prepare_release(schema_project_path=project, version="1.0.0", next_version="1.0.1")

    released = get_sql_content_from_section(file_path=version_file, section_id="910")
    assert "SELECT 1, 0, 0;" in released
    assert "SELECT 0, 0, 1;" not in released
    assert (
        lib.management.get_schema_development_version(schema_project_path=project)
        == "1.0.1"
    )

    # The deployment script of the only release is that release's script.
    deployment_file = generate_deployment_script(
        schema_project_path=project, version="1.0.0"
    )
    with open(deployment_file, "r") as f:
        assert "SELECT 1, 0, 0;" in f.read()


def test_release_under_another_version_deploys_and_updates(
    sandbox_session, temp_dir
):
    # Deployed from a release made under another version than the one being
    # developed, the schema has to report the released version, or the next
    # update is refused ("the version 0.0.1 cannot be updated").
    schema_name = "msm_release_update"
    project = create_new_project_folder(
        schema_name=schema_name,
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        overwrite_existing=True,
    )
    dev_file = os.path.join(project, "development", f"{schema_name}_next.sql")
    set_section_sql_content(
        file_path=dev_file,
        section_id="140",
        sql_content=f"CREATE TABLE `{schema_name}`.`t1`(`id` INT PRIMARY KEY);",
    )
    prepare_release(schema_project_path=project, version="1.0.0", next_version="1.1.0")
    # Generated while 1.0.0 is the only release: the release script itself.
    generate_deployment_script(schema_project_path=project, version="1.0.0")

    def schema_version():
        return lib.management.get_schema_version(
            session=sandbox_session, schema_name=schema_name
        )

    with fresh_schema(sandbox_session, schema_name):
        deploy_schema(schema_project_path=project, version="1.0.0")
        assert schema_version() == "1.0.0"

        # Released as 1.2.0 while 1.1.0 is developed, with an update script
        # that adds a table.
        set_section_sql_content(
            file_path=dev_file,
            section_id="140",
            sql_content=(
                f"CREATE TABLE `{schema_name}`.`t1`(`id` INT PRIMARY KEY);\n"
                f"CREATE TABLE `{schema_name}`.`t2`(`id` INT PRIMARY KEY);"
            ),
        )
        files = prepare_release(
            schema_project_path=project, version="1.2.0", next_version="1.3.0"
        )
        update_file = update_script(files)
        set_section_sql_content(
            file_path=update_file,
            section_id="240",
            sql_content=f"CREATE TABLE `{schema_name}`.`t2`(`id` INT PRIMARY KEY);",
        )
        generate_deployment_script(schema_project_path=project, version="1.2.0")

        deploy_schema(schema_project_path=project, version="1.2.0")
        assert schema_version() == "1.2.0"
        assert table_exists(sandbox_session, schema_name, "t2")


def test_grant_to_missing_role_creates_no_user(sandbox_session, temp_dir):
    # The scripts set their own sql_mode in section 010. Without
    # NO_AUTO_CREATE_USER, MariaDB answers a GRANT to an account that does not
    # exist by creating a user of that name without a password, instead of
    # failing. Both a first release (a copy of the version file) and an
    # upgrade (where section 270 runs before section 170 has created the
    # role) must fail instead, and leave no such user behind.
    schema_name = "msm_grant_check"
    role = "msm_grant_check_role"
    project = create_new_project_folder(
        schema_name=schema_name,
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        overwrite_existing=True,
    )
    dev_file = os.path.join(project, "development", f"{schema_name}_next.sql")

    def accounts_named_role():
        return (
            lib.core.MsmDbExec(
                "SELECT COUNT(*) AS n FROM mysql.user WHERE user = ? AND is_role = 'N'"
            )
            .exec(sandbox_session, [role])
            .first["n"]
        )

    def cleanup():
        drop_schema(sandbox_session, schema_name)
        lib.core.MsmDbExec(f"DROP ROLE IF EXISTS `{role}`").exec(sandbox_session)
        lib.core.MsmDbExec(f"DROP USER IF EXISTS `{role}`@`%`").exec(sandbox_session)

    set_section_sql_content(
        file_path=dev_file,
        section_id="140",
        sql_content=f"CREATE TABLE `{schema_name}`.`t`(`id` INT PRIMARY KEY);",
    )
    set_section_sql_content(
        file_path=dev_file,
        section_id="170",
        sql_content=f"GRANT SELECT ON `{schema_name}`.* TO `{role}`;",
    )
    prepare_release(schema_project_path=project, version="1.0.0", next_version="1.1.0")
    generate_deployment_script(schema_project_path=project, version="1.0.0")

    cleanup()
    try:
        # First release: the GRANT names a role that nothing creates
        with pytest.raises(Exception):
            deploy_schema(schema_project_path=project, version="1.0.0")
        assert accounts_named_role() == 0

        # Upgrade: 1.0.0 without the role, 1.1.0 creates it in section 170,
        # and the update script wrongly grants to it in section 270
        set_section_sql_content(file_path=dev_file, section_id="170", sql_content="")
        set_development_version(schema_project_path=project, version="1.0.0")
        prepare_release(
            schema_project_path=project,
            version="1.0.0",
            next_version="1.1.0",
            overwrite_existing=True,
            allow_to_stay_on_same_version=True,
        )
        generate_deployment_script(
            schema_project_path=project, version="1.0.0", overwrite_existing=True
        )
        set_section_sql_content(
            file_path=dev_file,
            section_id="170",
            sql_content=(
                f"CREATE ROLE IF NOT EXISTS `{role}`;\n"
                f"GRANT SELECT ON `{schema_name}`.* TO `{role}`;"
            ),
        )
        files = prepare_release(
            schema_project_path=project, version="1.1.0", next_version="1.2.0"
        )
        update_file = update_script(files)
        set_section_sql_content(
            file_path=update_file,
            section_id="270",
            sql_content=f"GRANT SHOW VIEW ON `{schema_name}`.* TO `{role}`;",
        )
        generate_deployment_script(schema_project_path=project, version="1.1.0")

        deploy_schema(schema_project_path=project, version="1.0.0")
        with pytest.raises(Exception):
            deploy_schema(schema_project_path=project, version="1.1.0")
        assert accounts_named_role() == 0
    finally:
        cleanup()


def test_substitution_helpers():
    settings = {
        "schemaName": "/*<msm:schema_prefix>*/my_schema/*<msm:schema_postfix>*/",
        "substitutions": {
            "schema_prefix": {},
            "schema_postfix": {"default": "_x", "pattern": "(_[a-z0-9]+)?"},
        },
    }
    resolve = lib.management.resolve_substitutions
    effective = lib.management.get_effective_schema_name

    # Defaults, and given values overriding them
    assert resolve(settings) == {"schema_prefix": "", "schema_postfix": "_x"}
    assert resolve(settings, {"schema_prefix": "acme_"}) == {
        "schema_prefix": "acme_",
        "schema_postfix": "_x",
    }
    assert effective(settings) == "my_schema_x"
    assert (
        effective(settings, {"schema_prefix": "acme_", "schema_postfix": "_eu"})
        == "acme_my_schema_eu"
    )

    # Undeclared names, values outside the identifier characters and values
    # not matching the declared pattern are rejected
    with pytest.raises(ValueError, match="not declared"):
        resolve(settings, {"other": "x"})
    with pytest.raises(ValueError, match="only letters, digits and _"):
        resolve(settings, {"schema_prefix": "a`; DROP SCHEMA x; --"})
    with pytest.raises(ValueError, match="has to match"):
        resolve(settings, {"schema_postfix": "EU"})
    with pytest.raises(ValueError, match="longer than 64"):
        effective(settings, {"schema_prefix": "p" * 60})

    # A script may only use declared placeholders
    apply = lib.management.apply_substitutions
    assert (
        apply("USE /*<msm:schema_prefix>*/s/*<msm:schema_postfix>*/;", resolve(settings))
        == "USE s_x;"
    )
    with pytest.raises(ValueError, match="does not declare"):
        apply("USE /*<msm:typo>*/s;", resolve(settings))

    # Names with placeholders are written unquoted, others are quoted
    identifier = lib.management.get_schema_identifier
    assert identifier("plain") == "`plain`"
    assert identifier(settings["schemaName"]) == settings["schemaName"]
    with pytest.raises(ValueError, match="only contain letters"):
        identifier("/*<msm:schema_prefix>*/my-schema")


def test_source_statements(temp_dir):
    sections = os.path.join(temp_dir, "sections")
    os.makedirs(sections)
    with open(os.path.join(sections, "part.sql"), "w") as f:
        f.write("-- header\nSELECT 1;\nSELECT 2;\n-- footer\n")
    substitute = lib.management.substitute_source_statements_with_content

    def expand(statement):
        return substitute(f"USE s;\n{statement}\nSELECT 3;\n", temp_dir)

    whole = "USE s;\n-- header\nSELECT 1;\nSELECT 2;\n-- footer\nSELECT 3;\n"
    # Without a slice, or with an empty one, the whole file
    assert expand("SOURCE './sections/part.sql'; -- all") == whole
    assert expand('SOURCE "./sections/part.sql"[:];') == whole
    # Character offsets, as before
    assert expand("SOURCE './sections/part.sql'[10:-10];") == (
        "USE s;\nSELECT 1;\nSELECT 2;\nSELECT 3;\n"
    )
    # Line numbers: 1-based, the end line included, ln-1 the last line
    lines = {
        "[ln2:]": "SELECT 1;\nSELECT 2;\n-- footer\n",
        "[ln2:ln3]": "SELECT 1;\nSELECT 2;\n",
        "[ln2:ln-2]": "SELECT 1;\nSELECT 2;\n",
        "[:ln1]": "-- header\n",
        "[ln-1:]": "-- footer\n",
        "[ln3:ln99]": "SELECT 2;\n-- footer\n",
        "[ln2:-10]": "SELECT 1;\nSELECT 2;\n",
    }
    for slicing, content in lines.items():
        assert expand(f"SOURCE './sections/part.sql'{slicing};") == (
            f"USE s;\n{content}SELECT 3;\n"
        ), slicing
    # Indentation is applied to every line; the last line needs no line break
    assert substitute("  SOURCE './sections/part.sql'[ln2:ln3];", temp_dir) == (
        "  SELECT 1;\n  SELECT 2;\n"
    )
    # Only "\n" ends a line, and a last line without one counts
    with open(os.path.join(sections, "form_feed.sql"), "w") as f:
        f.write("SELECT 1;\f-- page\nSELECT 2;")
    assert expand("SOURCE './sections/form_feed.sql'[ln2:];") == (
        "USE s;\nSELECT 2;SELECT 3;\n"
    )
    assert expand("SOURCE './sections/form_feed.sql'[:ln-2];") == (
        "USE s;\nSELECT 1;\f-- page\nSELECT 3;\n"
    )
    # The path ends on the directive's line, and only ASCII white space counts
    assert expand("SOURCE './sections/\npart.sql';") == (
        "USE s;\nSOURCE './sections/\npart.sql';\nSELECT 3;\n"
    )
    assert expand("SOURCE\u00a0'./sections/part.sql';") == (
        "USE s;\nSOURCE\u00a0'./sections/part.sql';\nSELECT 3;\n"
    )
    for slicing in ("[ln0:]", "[ln:]", "[-:]"):
        with pytest.raises(ValueError, match="neither a character offset"):
            expand(f"SOURCE './sections/part.sql'{slicing};")
    with pytest.raises(ValueError, match="could not be resolved"):
        expand("SOURCE './sections/missing.sql';")


def test_deployment_with_substitutions(sandbox_session, temp_dir):
    # The schema name and the role carry the placeholders. Run without
    # substitutions the comments are whitespace, so the plain names are
    # deployed; with them, the prefixed and postfixed ones.
    base = "msm_subst"
    template = f"/*<msm:schema_prefix>*/{base}/*<msm:schema_postfix>*/"
    role = f"/*<msm:schema_prefix>*/{base}_role/*<msm:schema_postfix>*/"
    project = create_new_project_folder(
        schema_name=base,
        target_path=temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
        overwrite_existing=True,
    )

    settings_file = os.path.join(project, "msm.project.json")
    with open(settings_file) as f:
        settings = json.load(f)
    settings["schemaName"] = template
    settings["substitutions"] = {
        "schema_prefix": {"default": ""},
        "schema_postfix": {"default": "", "pattern": "(_[a-z0-9]+)?"},
    }
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=4)

    dev_file = os.path.join(project, "development", f"{base}_next.sql")
    with open(dev_file) as f:
        script = f.read()
    with open(dev_file, "w") as f:
        f.write(script.replace(f"`{base}`", template))
    set_section_sql_content(
        file_path=dev_file,
        section_id="140",
        sql_content="CREATE TABLE `t`(`id` INT PRIMARY KEY);",
    )
    set_section_sql_content(
        file_path=dev_file,
        section_id="170",
        sql_content=(
            f"CREATE ROLE IF NOT EXISTS {role};\n" f"GRANT SELECT ON `t` TO {role};"
        ),
    )
    # A single release is deployed with a copy of its version script, which
    # carries the development version (0.0.1) the project was created with
    prepare_release(schema_project_path=project, version="0.0.1", next_version="0.0.2")
    generate_deployment_script(schema_project_path=project, version="0.0.1")

    schemas = [base, f"acme_{base}_eu"]
    roles = [f"{base}_role", f"acme_{base}_role_eu"]

    def cleanup():
        for schema in schemas:
            drop_schema(sandbox_session, schema)
        for r in roles:
            lib.core.MsmDbExec(f"DROP ROLE IF EXISTS {lib.core.quote_ident(r)}").exec(
                sandbox_session
            )

    def grants_of(r):
        return [
            list(row.values())[0]
            for row in lib.core.MsmDbExec(f"SHOW GRANTS FOR {lib.core.quote_ident(r)}")
            .exec(sandbox_session)
            .items
        ]

    cleanup()
    try:
        values = {"schema_prefix": "acme_", "schema_postfix": "_eu"}
        assert deploy_schema(
            schema_project_path=project, substitutions=values
        ) == (f"Deployment of `acme_{base}_eu` version 0.0.1 completed successfully.")
        assert table_exists(sandbox_session, f"acme_{base}_eu", "t")
        assert not table_exists(sandbox_session, base, "t")
        assert f"GRANT SELECT ON `acme_{base}_eu`.`t` TO `acme_{base}_role_eu`" in (
            grants_of(f"acme_{base}_role_eu")
        )

        # The same values find the deployed schema again
        assert "already on the requested version" in deploy_schema(
            schema_project_path=project, substitutions=values
        )

        # Without values, the defaults give the plain names
        assert deploy_schema(schema_project_path=project) == (
            f"Deployment of `{base}` version 0.0.1 completed successfully."
        )
        assert table_exists(sandbox_session, base, "t")
        assert f"GRANT SELECT ON `{base}`.`t` TO `{base}_role`" in grants_of(
            f"{base}_role"
        )

        with pytest.raises(ValueError, match="not declared"):
            deploy_schema(schema_project_path=project, substitutions={"prefix": "x"})
        with pytest.raises(ValueError, match="has to match"):
            deploy_schema(
                schema_project_path=project, substitutions={"schema_postfix": "EU"}
            )
    finally:
        cleanup()


def test_markdown_files_of_the_users_own_keep_their_years(tmp_path, monkeypatch):
    from msm_plugin.lib import management

    in_year(monkeypatch, "2026")
    project_path = management.create_schema_project_folder(
        "own_md", str(tmp_path), "ACME Corp.", license="MIT")
    notes = Path(project_path, "development", "notes.md")
    notes.write_text("Copyright (c) 2026, ACME Corp.\n")

    in_year(monkeypatch, "2027")
    written = management.update_copyright_years(project_path)
    assert os.path.join(project_path, "README.md") in written
    assert str(notes) not in written
    assert notes.read_text() == "Copyright (c) 2026, ACME Corp.\n"
