/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

/** How a value of a column has to be written into a SQL statement. */
export type SqlLiteralKind =
    | "numeric"
    | "string"
    | "binary"
    | "bit"
    | "temporal";

/** Column types written without quotes. */
const NUMERIC = new Set([
    "tinyint", "smallint", "mediumint", "int", "integer", "bigint",
    "decimal", "dec", "numeric", "fixed", "float", "double", "real",
    "boolean", "bool",
]);

/** Column types whose value is a hex blob rather than a quoted string. */
const BINARY = new Set([
    "binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob",
]);

/** Column types whose value is a date or a time. */
const TEMPORAL = new Set([
    "date", "datetime", "timestamp", "time", "year",
]);

/**
 * Reduces a MariaDB column type to its base name: `int(11) unsigned`
 * becomes `int`, `varchar(50)` becomes `varchar`.
 *
 * @param columnType The COLUMN_TYPE as INFORMATION_SCHEMA reports it.
 *
 * @returns The lower case base type name.
 */
export const baseTypeName = (columnType: string): string => {
    return columnType.trim().toLowerCase().split(/[\s(]/, 1)[0];
};

/**
 * Classifies a column type by how its values have to be written.
 *
 * @param columnType The COLUMN_TYPE as INFORMATION_SCHEMA reports it.
 *
 * @returns The literal kind to use for that column.
 */
export const literalKind = (columnType: string): SqlLiteralKind => {
    const base = baseTypeName(columnType);

    if (NUMERIC.has(base)) {
        return "numeric";
    }

    if (BINARY.has(base)) {
        return "binary";
    }

    if (TEMPORAL.has(base)) {
        return "temporal";
    }

    if (base === "bit") {
        return "bit";
    }

    // Everything left - char, varchar, the text family, enum, set, json,
    // uuid, inet6 and the spatial types - is written as a quoted string.
    return "string";
};
