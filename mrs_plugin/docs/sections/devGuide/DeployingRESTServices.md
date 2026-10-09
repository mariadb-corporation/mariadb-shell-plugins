<!-- Copyright (c) 2022, 2026, Oracle and/or its affiliates.
Copyright (c) 2026, MariaDB plc.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License, version 2.0,
as published by the Free Software Foundation.

This program is designed to work with certain software (including
but not limited to OpenSSL) that is licensed under separate terms, as
designated in a particular file or component or in included license
documentation.  The authors of MySQL hereby grant you an additional
permission to link the program and your derivative works with the
separately licensed software that they have either included with
the program or referenced in the documentation.

This program is distributed in the hope that it will be useful,  but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
the GNU General Public License, version 2.0, for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA -->

# Deploying REST Services

A REST service built on a development server is moved to another server, e.g. for testing or production, by dumping it on the first server and loading it on the second. The mrs plugin of MariaDB Shell provides functions for both steps.

## Dumping and Loading a REST Service

`mrs.dump.service()` writes a REST SQL script that recreates a REST service, and `mrs.load.service()` runs such a script:

```py
mrs.dump.service(service_path="/myService", file_path="~/myService.mrs.sql",
                 endpoints="ALL")
```

```py
mrs.load.service(file_path="~/myService.mrs.sql")
```

`mrs.dump.service()` takes the following options:

- `service_path`: the request path of the REST service.
- `file_path`: the file to write.
- `endpoints`: the endpoints to include, each choice a superset of the former:
    - `DATABASE` (the default): the REST schemas and their REST objects like TABLE, VIEW, PROCEDURE and FUNCTION
    - `DATABASE AND STATIC`: also the content sets without MRS scripts, with their files
    - `DATABASE AND STATIC AND DYNAMIC` or `ALL`: also the content sets with MRS scripts
    - an empty string: only the REST service itself
- `overwrite`: overwrite the file if it exists.

`mrs.load.service()` takes the `file_path` of the script and optionally an `as_path`, to create the REST service under another request path:

```py
mrs.load.service(file_path="~/myService.mrs.sql", as_path="/myServiceTest")
```

The script does not include the database schemas the REST service is based on, they must already exist on the target server. To deploy them together with the REST service, use a REST project.

## REST Projects

A REST project bundles one or more REST services with the database schemas they are based on. `mrs.dump.serviceProject()` (`mrs.dump.service_project()` in Python mode) writes a project to a directory or a ZIP file, and `mrs.load.serviceProject()` loads it from a directory, a ZIP file, a URL or a GitHub repository:

```py
mrs.dump.service_project(
    services=[{"name": "/myService",
               "include_database_endpoints": True,
               "include_static_endpoints": True,
               "include_dynamic_endpoints": True}],
    schemas=[{"name": "sakila"}],
    settings={"name": "myServiceProject", "version": "1.0.0"},
    destination="~/myServiceProject.zip",
    zip=True)
```

```py
mrs.load.service_project(source="~/myServiceProject.zip")
```

See [REST Projects](sql.html#rest-projects) in the SQL reference for all options.

## Using REST SQL

The script `mrs.dump.service()` writes is the result of the `SHOW CREATE REST SERVICE` statement with an `INCLUDING ... ENDPOINTS` clause, which any client can send to MariaDB Shell:

```sql
SHOW CREATE REST SERVICE /myService INCLUDING ALL ENDPOINTS;
```

Saved to a file, the script is run like any SQL script, e.g. with `mariadb-shell dba@localhost --sql -f myService.mrs.sql` or with `\source myService.mrs.sql` in SQL mode. See [SHOW CREATE REST SERVICE](sql.html#show-create-rest-service).
