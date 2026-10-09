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

    lib.core.MsmDbExec(
        f"DROP SCHEMA IF EXISTS {lib.core.quote_ident(SCHEMA_NAME)}"
    ).exec(sandbox_session)

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

    quoted_schema = lib.core.quote_ident(schema_name)
    lib.core.MsmDbExec(f"DROP SCHEMA IF EXISTS {quoted_schema}").exec(sandbox_session)
    try:
        deploy_schema(schema_project_path=project, version="1.0.0")
        assert marker_tables() == ["rest_marker_1"]

        deploy_schema(schema_project_path=project, version="1.1.0")
        assert marker_tables() == ["rest_marker_1", "rest_marker_2"]

        lib.core.MsmDbExec(f"DROP SCHEMA {quoted_schema}").exec(sandbox_session)
        deploy_schema(schema_project_path=project, version="1.1.0")
        assert marker_tables() == ["rest_marker_1", "rest_marker_2"]
    finally:
        lib.core.MsmDbExec(f"DROP SCHEMA IF EXISTS {quoted_schema}").exec(
            sandbox_session
        )


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
    quoted_schema = lib.core.quote_ident(schema_name)

    def accounts_named_role():
        return (
            lib.core.MsmDbExec(
                "SELECT COUNT(*) AS n FROM mysql.user WHERE user = ? AND is_role = 'N'"
            )
            .exec(sandbox_session, [role])
            .first["n"]
        )

    def cleanup():
        lib.core.MsmDbExec(f"DROP SCHEMA IF EXISTS {quoted_schema}").exec(
            sandbox_session
        )
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
        update_file = [f for f in files if "_to_" in f][0]
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
            lib.core.MsmDbExec(
                f"DROP SCHEMA IF EXISTS {lib.core.quote_ident(schema)}"
            ).exec(sandbox_session)
        for r in roles:
            lib.core.MsmDbExec(f"DROP ROLE IF EXISTS {lib.core.quote_ident(r)}").exec(
                sandbox_session
            )

    def table_exists(schema):
        return (
            lib.core.MsmDbExec(
                "SELECT COUNT(*) AS n FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = 't'"
            )
            .exec(sandbox_session, [schema])
            .first["n"]
            == 1
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
        assert table_exists(f"acme_{base}_eu")
        assert not table_exists(base)
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
        assert table_exists(base)
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
