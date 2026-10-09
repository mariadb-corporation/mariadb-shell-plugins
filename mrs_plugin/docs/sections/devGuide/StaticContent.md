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

# Static Content and MRS Scripts

Besides REST endpoints for database objects, a REST service can serve files, like the HTML, JavaScript and image files of a web app, and run MRS scripts, TypeScript functions that the MariaDB REST Daemon executes. Both are stored in REST content sets. A content set has a request path on its REST service, and each of its files is served below that path.

## Uploading a Directory

The easiest way to put the files of a directory into a content set is the `mrs.load.contentSet()` function of the mrs plugin of MariaDB Shell (`mrs.load.content_set()` in Python mode). It creates the content set, uploads every file of the directory and, if the files hold MRS scripts, registers the scripts as REST endpoints.

```py
mrs.load.content_set(directory="~/myApp/dist", content_set_path="/app",
                     service_path="/myService")
```

From the command line:

```sh
mariadb-shell dba@localhost --py -e 'mrs.load.content_set(directory="~/myApp/dist", content_set_path="/app", service_path="/myService")'
```

The function takes the following options:

- `directory`: the directory holding the files. Each file is served at its path relative to this directory, e.g. `~/myApp/dist/index.html` at `/myService/app/index.html`.
- `content_set_path`: the request path of the new content set.
- `service_path`: the request path of the REST service. Without it, the current REST service is used.
- `ignore_list`: a comma separated list of file patterns to skip, matched against the path relative to the directory. `*` matches any characters, `?` a single one. The default `*node_modules/*, */.*` skips `node_modules` folders and hidden files and folders like `.git`.
- `load_scripts`: whether to register the MRS scripts of the files. By default, they are registered if the directory holds any.
- `replace`: replace an existing content set with the same request path. Use this to upload a new version of the files.

The function returns the list of uploaded files.

## Serving a Web App

A web app, e.g. a PWA built with a bundler, is uploaded from its build output folder:

```py
mrs.load.content_set(directory="~/myApp/dist", content_set_path="/app",
                     service_path="/myService", replace=True)
```

All files of a content set without MRS scripts are public, so the whole web app is served.

## MRS Scripts

An MRS scripts project is a TypeScript project whose classes and functions carry the `@Mrs.module`, `@Mrs.script` and `@Mrs.trigger` decorators:

```TypeScript
@Mrs.module({ name: "hello", requestPath: "/hello" })
class Hello {
    @Mrs.script({ name: "greet", requiresAuth: false })
    public static async greet(name: string): Promise<string> {
        return "Hello " + name;
    }
}
```

The project is built first, so its build output folder (`build`, `dist`, `out` or `output`) holds the compiled modules the MariaDB REST Daemon executes. Then the whole project directory is uploaded:

```py
mrs.load.content_set(directory="~/myScripts", content_set_path="/scripts",
                     service_path="/myService")
```

The function detects the MRS scripts and registers each `@Mrs.module` as a REST schema and each `@Mrs.script` or `@Mrs.trigger` as a REST endpoint below it, here `/myService/hello/greet`.

Registering the scripts also decides which files of the content set are public:

- Files in a static folder of the project (`static`, `assets`, `media`, `web`, `js`, `css` or `images`) stay public.
- All other files, the TypeScript sources and the build output, become private. The MariaDB REST Daemon still reads them to run the scripts, but no client can download them, so the server-side code is not exposed.

> Note: Do not let a web app build into the build output folder of an MRS scripts project, its files would become private. Upload the web app as its own content set, as shown in [Serving a Web App](#serving-a-web-app), or build it into a static folder of the scripts project, e.g. `web`.

To upload a new version of the scripts, build the project and upload it again with `replace=True`. The scripts registered before are replaced.

## Using REST SQL

`mrs.load.contentSet()` reads the files on the client machine and sends one REST SQL statement per step to MariaDB Shell. Clients other than MariaDB Shell can send the same statements:

```sql
CREATE REST CONTENT SET /scripts ON SERVICE /myService;

CREATE REST CONTENT FILE `/src/hello.mts` ON SERVICE /myService CONTENT SET /scripts
    CONTENT '@Mrs.module({ name: "hello", requestPath: "/hello" }) ...';

CREATE REST CONTENT FILE `/dist/hello.mjs` ON SERVICE /myService CONTENT SET /scripts
    CONTENT 'export class Hello { ... }';

ALTER REST CONTENT SET /scripts ON SERVICE /myService
    LOAD TYPESCRIPT SCRIPTS;
```

Binary files are sent base64 encoded with `BINARY CONTENT`. See [CREATE REST CONTENT SET](sql.html#create-rest-content-set), [CREATE REST CONTENT FILE](sql.html#create-rest-content-file) and [ALTER REST CONTENT SET](sql.html#alter-rest-content-set) in the SQL reference.
