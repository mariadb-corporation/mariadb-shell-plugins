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

import os
from pathlib import Path
import tempfile
import shutil
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


def test_render_copyright_notices():
    # A single holder, whose year of creation is the only year shown
    single = {"copyrights": [{"holder": "ACME Corp.", "yearOfCreation": "2025"}]}
    assert (
        lib.management.render_copyright_notices(single, current_year="2027")
        == "Copyright (c) 2025, ACME Corp."
    )

    # A holder stored without the trailing period gets exactly one, so a notice
    # never ends up with two
    assert lib.management.render_copyright_notices(
        {"copyrights": [{"holder": "ACME Corp", "yearOfCreation": "2026"}]},
        current_year="2026",
    ) == "Copyright (c) 2026, ACME Corp."

    # The holder that tracks updates follows the current year, the inherited one
    # stays frozen at the years it carries, and the prefix goes on every line
    two_holders = {
        "copyrights": [
            {
                "holder": "Upstream Inc.",
                "yearOfCreation": "2021",
                "yearOfLastUpdate": "2024",
                "tracksUpdates": False,
            },
            {"holder": "MariaDB plc.", "yearOfCreation": "2026", "tracksUpdates": True},
        ]
    }
    assert lib.management.render_copyright_notices(
        two_holders, prefix=" * ", current_year="2030"
    ) == (
        " * Copyright (c) 2021, 2024, Upstream Inc.\n"
        " * Copyright (c) 2026, 2030, MariaDB plc."
    )


def test_copyright_settings_migration():
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

    # Only the holder added last follows the current year
    assert lib.management.render_copyright_notices(squeezed, current_year="2030") == (
        "Copyright (c) 2025, Oracle and/or its affiliates.\n"
        "Copyright (c) 2026, 2030, MariaDB plc."
    )


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


def test_create_new_project_folder_with_multiple_copyrights():
    copyrights = [
        {"holder": "Upstream Inc.", "yearOfCreation": "2021", "tracksUpdates": False},
        {"holder": "ACME Corp.", "yearOfCreation": "2026", "tracksUpdates": True},
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

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as schema_count FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME])
        .first["schema_count"]
    ) == 1

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "BASE TABLE"])
        .first["table_count"]
    ) == 1

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "VIEW"])
        .first["table_count"]
    ) == 1

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
    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "BASE TABLE"])
        .first["table_count"]
    ) == 2

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "VIEW"])
        .first["table_count"]
    ) == 1

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
    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "BASE TABLE"])
        .first["table_count"]
    ) == 3

    assert (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = ?"
        )
        .exec(sandbox_session, [SCHEMA_NAME, "VIEW"])
        .first["table_count"]
    ) == 1


def test_deployment(sandbox_session, project_path):
    # Ensure to start fresh
    lib.core.MsmDbExec(
        f"DROP SCHEMA IF EXISTS {lib.core.quote_ident(SCHEMA_NAME)}"
    ).exec(sandbox_session)

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
