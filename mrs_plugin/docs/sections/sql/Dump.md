<!-- Copyright (c) 2022, 2025, Oracle and/or its affiliates.

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

# DUMP

## REST Projects

A REST project bundles one or more REST services with the database schemas they are based on, so they can be deployed together. REST projects are not handled by REST SQL statements; they are dumped and loaded with the `mrs.dump.serviceProject()` and `mrs.load.serviceProject()` functions of the mrs_plugin.

A dumped project is a directory containing the following:

- ```mrs.package.json``` containing the project details
- ```*.service.mrs.sql``` containing the REST SQL for each service
- other SQL files containing schema dumps
- directories containing schema dumps
- ```appIcon.*``` being the icon for this project

This directory may also be written as a ZIP file.

`mrs.dump.serviceProject()` takes the following options:

- `services`, a list of the REST services to include. Each one gives its request path as `name` and selects the endpoints to include:
    - `include_database_endpoints`: REST objects like VIEW, PROCEDURE and FUNCTION
    - `include_static_endpoints`: content sets that are not of SCRIPT type
    - `include_dynamic_endpoints`: content sets that are of SCRIPT type

  Each REST service is written to its own REST SQL file containing the statements to recreate it.
- `schemas`, a list of the database schemas to include. Each one gives its `name`, and optionally a `file_path` of an SQL file or directory holding a dump of it. Without a `file_path`, the schema is dumped.
- `settings`, the project details stored in ```mrs.package.json```: `name` and `version` (in any format, e.g. `'v1.0'` or `'1.0.0b'`), and optionally a `description`, the `publisher` and an `icon_path` to copy the project icon from.
- `destination`, the directory or ZIP file to write, and `zip` to write a ZIP file.

Paths may start with `~`.

**_Examples_**

The following example dumps the REST service with the request path `/myService` to a REST project, including the database schema `sakila` it is based on.

```py
mrs.dump.service_project(
    services=[{"name": "/myService",
               "include_database_endpoints": True,
               "include_static_endpoints": True,
               "include_dynamic_endpoints": True}],
    schemas=[{"name": "sakila"}],
    settings={"name": "myServiceProject", "version": "1.0.0",
              "description": "My first REST project",
              "publisher": "MariaDB"},
    destination="~/myServiceProject.zip",
    zip=True)
```

`mrs.load.serviceProject()` loads a project from a directory, a ZIP file, a URL or a GitHub shortcut.

```py
mrs.load.service_project(source="~/myServiceProject.zip")
```

## Dumping and Loading REST Services

A single REST service is dumped as a REST SQL script, which recreates the service when run. The script is the result of [SHOW CREATE REST SERVICE](#show-create-rest-service) with an `INCLUDING ... ENDPOINTS` clause. The dump does not include the database schemas the service is based on. To create a fully consistent dump that also includes them, dump a [REST project](#rest-projects) instead.

```sql
SHOW CREATE REST SERVICE /myService INCLUDING ALL ENDPOINTS;
```

From the service, you choose which endpoints to include:

- `DATABASE`: REST objects like TABLE, VIEW, PROCEDURE and FUNCTION
- `DATABASE AND STATIC`: also the content sets that do not hold MRS scripts
- `DATABASE AND STATIC AND DYNAMIC`: also the content sets that hold MRS scripts
- `ALL`: short for `DATABASE AND STATIC AND DYNAMIC`

Each of these settings is a superset of the former.

Reading and writing files on the client machine is not part of REST SQL. The mrs plugin of MariaDB Shell provides functions for this, see [Deploying REST Services](index.html#deploying-rest-services) for a guide:

- `mrs.dump.service()` writes the script of a service to a file.
- `mrs.load.service()` runs such a script, optionally creating the service under another request path.
- `mrs.load.contentSet()` uploads the files of a directory to a content set, sending one `CREATE REST CONTENT FILE` statement per file, and registers the MRS scripts held by the files with `ALTER REST CONTENT SET ... LOAD TYPESCRIPT SCRIPTS`. By default, the scripts are registered if the directory holds any; `load_scripts` turns this on or off. Files matching the `ignore_list` patterns, by default `*node_modules/*, */.*`, are skipped.

**_Examples_**

The following example dumps the REST service with the request path `/myService` and loads it again as `/myCopy`.

```py
mrs.dump.service(service_path="/myService", file_path="~/myService.mrs.sql",
                 endpoints="ALL")
mrs.load.service(file_path="~/myService.mrs.sql", as_path="/myCopy")
```

The following example uploads a directory to a new content set and registers its MRS scripts.

```py
mrs.load.content_set(directory="~/myScripts", content_set_path="/scripts",
                     service_path="/myService")
```
