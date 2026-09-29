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

import type { IColumnMetadata } from "../mcp/types.js";

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

/**
 * How a column's values are shown in the grid, where that is not plain
 * text: binary values as hex, and a BLOB, a spatial value or a vector as
 * an icon standing for it, as the MySQL Shell's result view shows them.
 * JSON is shown as text, as it is there, and is marked so that its value
 * opens in an editor as JSON.
 */
export type ValueDisplay = "binary" | "blob" | "geometry" | "vector" | "json";

/** Column types holding a spatial value. */
const SPATIAL = new Set([
    "geometry", "point", "linestring", "polygon", "multipoint",
    "multilinestring", "multipolygon", "geometrycollection",
]);

/** The BLOB family, whose values are shown by an icon. */
const BLOBS = new Set(["tinyblob", "blob", "mediumblob", "longblob"]);

/**
 * Decides how a column's values are shown.
 *
 * The table's own column type decides where it is known, since it is
 * the only thing that tells a VECTOR from a VARBINARY: the server reports
 * both alike. Otherwise it is the metadata the server reported for the
 * result's column, which every result has - a join, a view, a CALL.
 *
 * @param metadata The result column's metadata, as the MCP server's
 *                 `column_metadata` reports it.
 * @param columnType The table's COLUMN_TYPE, where it was looked up.
 *
 * @returns How the values are shown, or undefined for plain text.
 */
export const valueDisplayOf = (
    metadata?: IColumnMetadata,
    columnType?: string,
): ValueDisplay | undefined => {
    if (columnType !== undefined) {
        const base = baseTypeName(columnType);
        if (base === "vector") {
            return "vector";
        }
        if (BLOBS.has(base)) {
            return "blob";
        }
        if (base === "binary" || base === "varbinary") {
            return "binary";
        }
        if (SPATIAL.has(base)) {
            return "geometry";
        }
        if (base === "json") {
            return "json";
        }
    }

    switch (metadata?.type) {
        // BINARY, VARBINARY and every BLOB are all BYTES; only the BLOB
        // flag tells them apart.
        case "BYTES": {
            return metadata.flags?.includes("BLOB") ? "blob" : "binary";
        }

        case "GEOMETRY": {
            return "geometry";
        }

        case "JSON": {
            return "json";
        }

        default: {
            return undefined;
        }
    }
};

/**
 * The family a column's type belongs to, which picks the colour its type
 * name is shown in under the column's name.
 */
export type TypeCategory =
    | "number"
    | "text"
    | "temporal"
    | "binary"
    | "json"
    | "other";

/** The server's `mysql.Type` names, by the family each belongs to. */
const METADATA_CATEGORIES: Record<string, TypeCategory> = {
    integer: "number",
    uinteger: "number",
    float: "number",
    double: "number",
    decimal: "number",
    bit: "number",
    string: "text",
    enum: "text",
    set: "text",
    date: "temporal",
    time: "temporal",
    datetime: "temporal",
    bytes: "binary",
    geometry: "binary",
    json: "json",
};

/** Text column types that `literalKind` lumps in with every string. */
const TEXT = new Set([
    "char", "varchar", "tinytext", "text", "mediumtext", "longtext",
    "enum", "set", "uuid", "inet4", "inet6",
]);

/**
 * Decides which family a type name belongs to. It takes either form a
 * result column's type can come in: the table's COLUMN_TYPE, or the name
 * the server reports in the result's metadata.
 *
 * @param typeName The type name, in either form and any case.
 *
 * @returns Its family; "other" for one that is none of them.
 */
export const typeCategoryOf = (typeName: string): TypeCategory => {
    const base = baseTypeName(typeName);
    const reported = METADATA_CATEGORIES[base];
    if (reported !== undefined) {
        return reported;
    }

    if (NUMERIC.has(base)) {
        return "number";
    }
    if (TEXT.has(base)) {
        return "text";
    }
    if (TEMPORAL.has(base)) {
        return "temporal";
    }
    if (BINARY.has(base) || SPATIAL.has(base) || base === "vector") {
        return "binary";
    }

    return "other";
};
