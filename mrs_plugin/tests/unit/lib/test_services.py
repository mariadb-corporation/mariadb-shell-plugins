# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
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

import tempfile
import os
import json
import zipfile
import filecmp
import datetime
import difflib
import io
import pytest

from mrs_plugin import lib
from mrs_plugin.tests.unit.helpers import (
    ServiceCT,
    SchemaCT,
    RestObjectCT,
    TableContents,
    get_default_rest_object_init,
    create_test_db,
)
from lib.core import MrsDbSession


def test_get_services(phone_book, table_contents):
    with MrsDbSession(session=phone_book["session"]) as session:
        service_table: TableContents = table_contents("service")
        services = lib.services.get_services(session=session)

        assert len(service_table.items) == len(services)
        assert len(services) == 1

        with ServiceCT(session, "/service2") as service2_id:
            services = lib.services.get_services(session=session)
            assert len(service_table.items) == len(services)
            assert len(services) == 2

            with ServiceCT(session, "/service3") as service3_id:
                services = lib.services.get_services(session=session)
                assert len(service_table.items) == len(services)
                assert len(services) == 3

            services = lib.services.get_services(session=session)
            assert len(service_table.items) == len(services)
            assert len(services) == 2

        services = lib.services.get_services(session=session)
        assert len(service_table.items) == len(services)
        assert len(services) == 1


def mock_github_archive(mocker, project_dir):
    """Serves project_dir as GitHub's branch archive for every download.

    GitHub wraps the files of a branch archive in a single `<repo>-<branch>/`
    folder, which load_project() descends into. Returns the list of requested
    URLs.
    """
    requested_urls = []

    def urlopen(url, *args, **kwargs):
        requested_urls.append(url)
        repo, branch = url.split("/")[-5], url.split("/")[-1].removesuffix(".zip")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            for root, _, files in os.walk(project_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    zip_file.write(
                        file_path,
                        os.path.join(
                            f"{repo}-{branch}", os.path.relpath(file_path, project_dir)
                        ),
                    )
        archive.seek(0)
        return archive

    mocker.patch("urllib.request.urlopen", side_effect=urlopen)
    return requested_urls


@pytest.mark.skipif(
    os.getcwd() == "/environment/shell-plugins/mrs_plugin",
    reason="Test skipped when running on jenkins",
)
def test_service_as_project(phone_book, table_contents, mocker):
    session = phone_book["session"]

    create_test_db(session, "MyTestDb1")
    create_test_db(session, "MyTestDb2")

    services = [
        {
            "name": "/myService1",
            "include_database_endpoints": False,
            "include_static_endpoints": False,
            "include_dynamic_endpoints": False,
        },
        {
            "name": "/myService2",
            "include_database_endpoints": False,
            "include_static_endpoints": False,
            "include_dynamic_endpoints": False,
        },
    ]

    schemas = [
        {
            "name": "MyTestDb1",
            "file_path": None,
        },
        {
            "name": "MyTestDb2",
            "file_path": None,
        },
    ]

    project_settings = {
        "name": "testProject",
        "icon_path": None,
        "description": "This is a test project",
        "publisher": "Oracle",
        "version": "1.0.0",
    }

    service1 = ServiceCT(session, "/myService1")
    schema1 = SchemaCT(session, service1.id, "MyTestDb1", "/MyTestDb1")
    RestObjectCT(
        session,
        **get_default_rest_object_init(session, schema1.id, "Contacts", "/Contacts"),
    )
    RestObjectCT(
        session,
        **get_default_rest_object_init(session, schema1.id, "Addresses", "/Addresses"),
    )
    RestObjectCT(
        session,
        **get_default_rest_object_init(
            session,
            schema1.id,
            "GetAllContacts",
            "/GetAllContacts",
            rest_object_type="PROCEDURE",
        ),
    )

    service2 = ServiceCT(session, "/myService2")
    schema2 = SchemaCT(session, service2.id, "MyTestDb2", "/MyTestDb2")
    RestObjectCT(
        session,
        **get_default_rest_object_init(session, schema2.id, "Contacts", "/Contacts"),
    )
    RestObjectCT(
        session,
        **get_default_rest_object_init(session, schema2.id, "Addresses", "/Addresses"),
    )
    RestObjectCT(
        session,
        **get_default_rest_object_init(
            session,
            schema2.id,
            "GetAllContacts",
            "/GetAllContacts",
            rest_object_type="PROCEDURE",
        ),
    )
    RestObjectCT(
        session,
        **get_default_rest_object_init(
            session, schema2.id, "ContactBasicInfo", "/ContactBasicInfo"
        ),
    )

    # Test storing the project into a directory
    with tempfile.TemporaryDirectory(delete=False) as directory_1:
        project_settings["icon_path"] = os.path.join(directory_1, "icon1.svg")
        project_file_path = os.path.join(directory_1, "mrs.package.json")
        service1_service_path = os.path.join(directory_1, "myService1.service.mrs.sql")
        service2_service_path = os.path.join(directory_1, "myService2.service.mrs.sql")
        test_schema1_dir = os.path.join(directory_1, "MyTestDb1")
        test_schema2_dir = os.path.join(directory_1, "MyTestDb2")

        with open(project_settings["icon_path"], "w") as iconFile:
            iconFile.write(" ")

        lib.services.store_project(
            session, directory_1, services, schemas, project_settings, False
        )

        assert os.path.isfile(project_file_path)
        with open(project_file_path, "r") as f:
            data = json.load(f)

            assert data == {
                "name": project_settings["name"],
                "version": project_settings["version"],
                "restServices": [
                    {
                        "fileName": "myService1.service.mrs.sql",
                        "serviceName": "/myService1",
                    },
                    {
                        "fileName": "myService2.service.mrs.sql",
                        "serviceName": "/myService2",
                    },
                ],
                "schemas": [
                    {
                        "path": "MyTestDb1",
                        "schemaName": "MyTestDb1",
                        "format": "dump",
                    },
                    {
                        "path": "MyTestDb2",
                        "schemaName": "MyTestDb2",
                        "format": "dump",
                    },
                ],
                "creationDate": data["creationDate"],
                "publisher": project_settings["publisher"],
                "description": project_settings["description"],
                "icon": "appIcon.svg",
            }

        assert os.path.isfile(service1_service_path)
        assert os.path.isfile(service2_service_path)
        assert os.path.isdir(test_schema1_dir)
        assert os.path.isdir(test_schema2_dir)

    # Test storing the project into a zip file
    with tempfile.TemporaryDirectory(delete=False) as directory_zip_1:
        project_settings["icon_path"] = os.path.join(directory_zip_1, "icon1.svg")
        project_file_path = os.path.join(directory_zip_1, "mrs.package.json")
        service1_service_path = os.path.join(
            directory_zip_1, "myService1.service.mrs.sql"
        )
        service2_service_path = os.path.join(
            directory_zip_1, "myService2.service.mrs.sql"
        )
        test_schema1_dir = os.path.join(directory_zip_1, "MyTestDb1")
        test_schema2_dir = os.path.join(directory_zip_1, "MyTestDb2")

        with open(project_settings["icon_path"], "w") as iconFile:
            iconFile.write(" ")

        zip_path = os.path.join(directory_zip_1, "project.mrs.zip")
        lib.services.store_project(
            session, zip_path, services, schemas, project_settings, True
        )

        assert os.path.isfile(zip_path)

        assert zipfile.is_zipfile(zip_path)

        with zipfile.ZipFile(zip_path) as myzip:
            assert zipfile.Path(myzip, "mrs.package.json").is_file
            with myzip.open("mrs.package.json") as f:
                data = json.load(f)

                assert data == {
                    "name": project_settings["name"],
                    "version": project_settings["version"],
                    "restServices": [
                        {
                            "fileName": "myService1.service.mrs.sql",
                            "serviceName": "/myService1",
                        },
                        {
                            "fileName": "myService2.service.mrs.sql",
                            "serviceName": "/myService2",
                        },
                    ],
                    "schemas": [
                        {
                            "path": "MyTestDb1",
                            "schemaName": "MyTestDb1",
                            "format": "dump",
                        },
                        {
                            "path": "MyTestDb2",
                            "schemaName": "MyTestDb2",
                            "format": "dump",
                        },
                    ],
                    "creationDate": data["creationDate"],
                    "publisher": project_settings["publisher"],
                    "description": project_settings["description"],
                    "icon": "appIcon.svg",
                }

    for service_data in services:
        service = lib.services.get_service(
            session, url_context_root=service_data["name"]
        )

        session.run_sql(
            f"DROP REST SERVICE {lib.core.quote_ident(service['url_context_root'])}"
        )

    for schema_data in schemas:
        session.run_sql(f"DROP SCHEMA {schema_data["name"]}")

    lib.services.load_project(session, directory_1)

    with tempfile.TemporaryDirectory(delete=False) as directory_2:
        lib.services.store_project(
            session, directory_2, services, schemas, project_settings, False
        )

    compare = filecmp.dircmp(directory_1, directory_2)

    for file in compare.diff_files:
        with open(os.path.join(directory_1, file)) as f1:
            with open(os.path.join(directory_2, file)) as f2:
                if file == "mrs.package.json":
                    json1 = json.load(f1)
                    json2 = json.load(f2)

                    assert "creationDate" in json1
                    assert "creationDate" in json2

                    assert datetime.datetime.strptime(
                        json1.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )
                    assert datetime.datetime.strptime(
                        json2.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )

                    # make these dates the same, so we can compare the json objects
                    json1["creationDate"] = json2["creationDate"]

                    assert json1 == json2

                    compare.diff_files.remove("mrs.package.json")
                else:
                    file1_content = f1.read()
                    file2_content = f2.read()

                    for line in difflib.unified_diff(
                        file1_content,
                        file2_content,
                        os.path.join(directory_1, file),
                        os.path.join(directory_2, file),
                        lineterm="",
                    ):
                        print(line)

    assert not compare.diff_files

    for service_name in ["myService1", "myService2"]:
        service = lib.services.get_service(session, url_context_root=f"/{service_name}")
        session.run_sql(
            f"DROP REST SERVICE {lib.core.quote_ident(service['url_context_root'])}"
        )

    # test loading from GitHub, served from the project stored above
    requested_urls = mock_github_archive(mocker, directory_1)

    lib.services.load_project(session, "github.com/mrs-tests/mrs-project|dev")

    with tempfile.TemporaryDirectory(delete=False) as directory_3:
        lib.services.store_project(
            session, directory_3, services, schemas, project_settings, False
        )

    compare = filecmp.dircmp(directory_1, directory_3)

    for file in compare.diff_files:
        with open(os.path.join(directory_1, file)) as f1:
            with open(os.path.join(directory_3, file)) as f2:
                if file == "mrs.package.json":
                    json1 = json.load(f1)
                    json2 = json.load(f2)

                    assert "creationDate" in json1
                    assert "creationDate" in json2

                    assert datetime.datetime.strptime(
                        json1.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )
                    assert datetime.datetime.strptime(
                        json2.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )

                    # make these dates the same, so we can compare the json objects
                    json1["creationDate"] = json2["creationDate"]

                    assert json1 == json2

                    compare.diff_files.remove("mrs.package.json")
                else:
                    file1_content = f1.read()
                    file2_content = f2.read()

                    for line in difflib.unified_diff(
                        file1_content,
                        file2_content,
                        os.path.join(directory_1, file),
                        os.path.join(directory_3, file),
                        lineterm="",
                    ):
                        print(line)

    assert not compare.diff_files

    for service_name in ["myService1", "myService2"]:
        service = lib.services.get_service(session, url_context_root=f"/{service_name}")
        session.run_sql(
            f"DROP REST SERVICE {lib.core.quote_ident(service['url_context_root'])}"
        )

    # test loading from GitHub using the short form, which defaults to main
    lib.services.load_project(session, "github/mrs-tests/mrs-project")

    assert requested_urls == [
        "https://github.com/mrs-tests/mrs-project/archive/refs/heads/dev.zip",
        "https://github.com/mrs-tests/mrs-project/archive/refs/heads/main.zip",
    ]

    with tempfile.TemporaryDirectory(delete=False) as directory_3:
        lib.services.store_project(
            session, directory_3, services, schemas, project_settings, False
        )

    compare = filecmp.dircmp(directory_1, directory_3)

    for file in compare.diff_files:
        with open(os.path.join(directory_1, file)) as f1:
            with open(os.path.join(directory_3, file)) as f2:
                if file == "mrs.package.json":
                    json1 = json.load(f1)
                    json2 = json.load(f2)

                    assert "creationDate" in json1
                    assert "creationDate" in json2

                    assert datetime.datetime.strptime(
                        json1.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )
                    assert datetime.datetime.strptime(
                        json2.get("creationDate"), "%Y-%m-%d %H:%M:%S"
                    )

                    # make these dates the same, so we can compare the json objects
                    json1["creationDate"] = json2["creationDate"]

                    assert json1 == json2

                    compare.diff_files.remove("mrs.package.json")
                else:
                    file1_content = f1.read()
                    file2_content = f2.read()

                    for line in difflib.unified_diff(
                        file1_content,
                        file2_content,
                        os.path.join(directory_1, file),
                        os.path.join(directory_3, file),
                        lineterm="",
                    ):
                        print(line)

    assert not compare.diff_files

    for service_name in ["myService1", "myService2"]:
        service = lib.services.get_service(session, url_context_root=f"/{service_name}")
        session.run_sql(
            f"DROP REST SERVICE {lib.core.quote_ident(service['url_context_root'])}"
        )
