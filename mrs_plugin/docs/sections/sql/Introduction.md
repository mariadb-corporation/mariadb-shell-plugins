<!-- Copyright (c) 2022, 2025, Oracle and/or its affiliates.
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

# Introduction

It has been an essential goal of the MySQL REST Service (MRS) to provide a management interface that feels familiar to MySQL developers and DBAs and integrates well into their existing processes.

For this purpose the MySQL REST Service plugins directly into the [MariaDB Shell](https://dev.mysql.com/downloads/shell/) and [MariaDB Shell for VS Code](https://marketplace.visualstudio.com/items?itemName=Oracle.mysql-shell-for-vs-code). It extends the available SQL commands to include DDL (Data Definition Language) statements that allow managing the MySQL REST Service in an easy and seamless way.

This makes the process of creating a MySQL REST Service for your application as easy as creating a database schema or table.

**_Example_**

The following script configures the MySQL REST Service, creates a new REST service `/myService` and adds a REST schema `/sakila` and a REST data mapping view `/actor` that lists all actors and their film titles.

```sql
CONFIGURE REST METADATA;

CREATE REST SERVICE /myService;
USE REST SERVICE /myService;

CREATE REST SCHEMA /sakila FROM `sakila`;
USE REST SCHEMA /sakila;

CREATE REST VIEW /actor
AS `sakila`.`actor` {
    actorId: actor_id @SORTABLE,
    firstName: first_name,
    lastName: last_name,
    lastUpdate: last_update,
    filmActor: sakila.film_actor @UNNEST {
        film: sakila.film @UNNEST {
            title: title
        }
    }
}
AUTHENTICATION REQUIRED;
```

> Note: Please ensure to install the [MySQL sakila example database schema](https://downloads.mysql.com/docs/sakila-db.zip) before running the MRS DDL script above.

## Syntax Conventions

The REST SQL statements follow the lexical rules of MariaDB SQL statements.

- **Separators.** Statements are separated by `;`. Leading, trailing and repeated semicolons are ignored, so `;SHOW REST SERVICES;;` is a valid script.
- **Comments.** `-- ` (two dashes followed by a space), `#` to the end of the line, and `/* ... */`. A `/*! ... */` version comment is not supported.
- **Request paths.** An unquoted request path is a sequence of `/segment` parts, e.g. `/myService/v1`. Each segment is an identifier: it may contain letters, digits, `_` and `$`, but it must not consist of digits only or look like a number (`/2024`, `/1e5`). Such paths, and paths with other characters, are written in back ticks, e.g. `` CREATE REST SERVICE `/2024`; ``. A quoted request path has to start with `/`, or with a wildcard (`*`, `?`) where wildcards are allowed.
- **Identifiers.** Names of database schemas, tables, views, routines and columns, and class names, are written unquoted or in back ticks. Inside back ticks a backslash is an ordinary character and a back tick is written twice (`` `a``b` ``). With the `ANSI_QUOTES` SQL mode, a double quoted string is an identifier as well.
- **Text.** Comments, passwords and similar values are written in single quotes, or in double quotes unless `ANSI_QUOTES` is set. A quote character is escaped by doubling it (`'it''s'`) or, unless `NO_BACKSLASH_ESCAPES` is set, with a backslash (`'it\'s'`). Names of REST users, roles and authentication apps accept double quotes in every SQL mode.
- **Keywords as names.** Keywords used as names have to be quoted, e.g. `` `role` ``. `FILES` and `VENDORS` are exceptions and can be used unquoted, e.g. `` AS `sakila`.files ``.
- **REST users.** A REST user is written as `name@app`, e.g. `admin@myApp` or `"admin"@"MRS"`; each part is quoted as needed (`MRS` is a keyword).
- **JSON values.** `OPTIONS`, `METADATA`, `APP OPTIONS` and `JSON SCHEMA` take a JSON value. Its keys and strings are written in double quotes in every SQL mode; numbers may be negative and may have a decimal part, e.g. `{"maxItems": -1, "ratio": 0.5}`.
