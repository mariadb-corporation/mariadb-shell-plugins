# Copyright (c) 2021, 2026, Oracle and/or its affiliates.
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
import re
import pytest
import zipfile
import filecmp
import datetime
import difflib
import tempfile

from mrs_plugin.services import *
from .helpers import (
    ServiceCT,
    SchemaCT,
    DbObjectCT,
    get_default_db_object_init,
    create_test_db,
)
from mrs_plugin import lib

service_create_statement = """CREATE OR REPLACE REST SERVICE /test
    COMMENT 'Test service'
    OPTIONS {
        "headers": {
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, Origin, X-Auth-Token",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"
        },
        "http": {
            "allowedOrigin": "auto"
        },
        "logging": {
            "exceptions": true,
            "request": {
                "body": true,
                "headers": true
            },
            "response": {
                "body": true,
                "headers": true
            }
        },
        "returnInternalErrorDetails": true,
        "includeLinksInResults": false
    }
    ADD AUTH APP `MRS Auth App` IF EXISTS;"""

service_create_statement_include_database_endpoints = """CREATE OR REPLACE REST SERVICE /test
    COMMENT 'Test service'
    OPTIONS {
        "headers": {
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, Origin, X-Auth-Token",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"
        },
        "http": {
            "allowedOrigin": "auto"
        },
        "logging": {
            "exceptions": true,
            "request": {
                "body": true,
                "headers": true
            },
            "response": {
                "body": true,
                "headers": true
            }
        },
        "returnInternalErrorDetails": true,
        "includeLinksInResults": false
    }
    ADD AUTH APP `MRS Auth App` IF EXISTS;

CREATE REST ROLE `DBA` ON SERVICE /test
    COMMENT 'Database administrator.';

CREATE REST ROLE `Maintenance Admin` EXTENDS `DBA` ON SERVICE /test
    COMMENT 'Maintenance administrator.';

CREATE REST ROLE `Process Admin` EXTENDS `Maintenance Admin` ON SERVICE /test
    COMMENT 'Process administrator.';

CREATE OR REPLACE REST SCHEMA /AnalogPhoneBook ON SERVICE /test
    FROM `AnalogPhoneBook`
    AUTHENTICATION NOT REQUIRED;

CREATE OR REPLACE REST VIEW /Contacts
    ON SERVICE /test SCHEMA /AnalogPhoneBook
    AS `AnalogPhoneBook`.`Contacts` CLASS MyServiceAnalogPhoneBookContacts {
        id: id @KEY @SORTABLE,
        fName: f_name,
        lName: l_name,
        number: number,
        email: email
    }
    AUTHENTICATION REQUIRED;

CREATE OR REPLACE REST SCHEMA /MobilePhoneBook ON SERVICE /test
    FROM `MobilePhoneBook`
    AUTHENTICATION NOT REQUIRED;

CREATE OR REPLACE REST VIEW /Contacts
    ON SERVICE /test SCHEMA /MobilePhoneBook
    AS `MobilePhoneBook`.`Contacts` CLASS MyServiceAnalogPhoneBookContacts {
        id: id @KEY @SORTABLE,
        fName: f_name,
        lName: l_name,
        number: number,
        email: email
    }
    AUTHENTICATION REQUIRED;

CREATE OR REPLACE REST SCHEMA /PhoneBook ON SERVICE /test
    FROM `PhoneBook`
    AUTHENTICATION NOT REQUIRED;

CREATE OR REPLACE REST VIEW /Contacts
    ON SERVICE /test SCHEMA /PhoneBook
    AS `PhoneBook`.`Contacts` CLASS MyServiceAnalogPhoneBookContacts {
        id: id @KEY @SORTABLE,
        fName: f_name,
        lName: l_name,
        number: number,
        email: email
    }
    AUTHENTICATION REQUIRED;"""


def test_service_as_project(phone_book, table_contents):
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
    DbObjectCT(
        session,
        **get_default_db_object_init(session, schema1.id, "Contacts", "/Contacts"),
    )
    DbObjectCT(
        session,
        **get_default_db_object_init(session, schema1.id, "Addresses", "/Addresses"),
    )
    DbObjectCT(
        session,
        **get_default_db_object_init(
            session,
            schema1.id,
            "GetAllContacts",
            "/GetAllContacts",
            db_object_type="PROCEDURE",
        ),
    )

    service2 = ServiceCT(session, "/myService2")
    schema2 = SchemaCT(session, service2.id, "MyTestDb2", "/MyTestDb2")
    DbObjectCT(
        session,
        **get_default_db_object_init(session, schema2.id, "Contacts", "/Contacts"),
    )
    DbObjectCT(
        session,
        **get_default_db_object_init(session, schema2.id, "Addresses", "/Addresses"),
    )
    DbObjectCT(
        session,
        **get_default_db_object_init(
            session,
            schema2.id,
            "GetAllContacts",
            "/GetAllContacts",
            db_object_type="PROCEDURE",
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

        dump_service_as_project(
            destination=directory_1,
            services=services,
            schemas=schemas,
            settings=project_settings,
            zip=False,
            overwrite=True,
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
        dump_service_as_project(
            destination=zip_path,
            services=services,
            schemas=schemas,
            settings=project_settings,
            zip=True,
            overwrite=True,
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

    assert os.path.isdir(directory_1)

    load_service_project(source=directory_1)

    with tempfile.TemporaryDirectory(delete=False) as directory_2:
        dump_service_as_project(
            destination=directory_2,
            services=services,
            schemas=schemas,
            settings=project_settings,
            zip=False,
            overwrite=True,
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


def test_dump_and_load_service(phone_book):
    session = phone_book["session"]

    script = session.run_sql(
        "SHOW CREATE REST SERVICE /test INCLUDING DATABASE ENDPOINTS"
    ).fetch_one()[0]

    with tempfile.TemporaryDirectory() as directory:
        file_path = os.path.join(directory, "test.mrs.sql")

        dump_service("/test", file_path, endpoints="database", session=session)
        with open(file_path) as f:
            assert f.read() == script + "\n"

        with pytest.raises(Exception, match="already exists"):
            dump_service("/test", file_path, session=session)

        with pytest.raises(Exception, match="Invalid endpoints"):
            dump_service(
                "/test", file_path, endpoints="STATIC", overwrite=True, session=session
            )

        # Only the CREATE REST SERVICE statement
        dump_service("/test", file_path, endpoints="", overwrite=True, session=session)
        with open(file_path) as f:
            assert f.read().startswith("CREATE OR REPLACE REST SERVICE /test\n")

        # Loaded under another request path
        dump_service("/test", file_path, overwrite=True, session=session)
        load_service(file_path, as_path="/testCopy", session=session)
        try:
            copy = session.run_sql(
                "SHOW CREATE REST SERVICE /testCopy INCLUDING DATABASE ENDPOINTS"
            ).fetch_one()[0]
            # Only the CREATE and USE statements name the service
            assert copy.count("/testCopy") == 2
            assert copy == script.replace(
                "REST SERVICE /test\n", "REST SERVICE /testCopy\n", 1
            ).replace("USE REST SERVICE /test;", "USE REST SERVICE /testCopy;", 1)
        finally:
            session.run_sql("DROP REST SERVICE /testCopy")


def test_load_service_in_development_as_path(phone_book):
    session = phone_book["session"]

    # The script of a service in development names it with its developers;
    # as_path replaces that, and the endpoints follow
    session.run_sql("CREATE REST SERVICE mike@/devSvc")
    session.run_sql(
        "CREATE REST SCHEMA /phoneBook ON SERVICE mike@/devSvc FROM `PhoneBook`"
    )
    try:
        with tempfile.TemporaryDirectory() as directory:
            file_path = os.path.join(directory, "devSvc.mrs.sql")
            dump_service("mike@/devSvc", file_path, session=session)
            load_service(file_path, as_path="/devCopy", session=session)
        try:
            schemas = session.run_sql(
                "SHOW REST SCHEMAS FROM SERVICE /devCopy"
            ).fetch_all()
            assert [row[0] for row in schemas] == ["/phoneBook"]
        finally:
            session.run_sql("DROP REST SERVICE /devCopy")
    finally:
        session.run_sql("DROP REST SERVICE mike@/devSvc")
