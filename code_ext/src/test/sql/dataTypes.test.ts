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

import { describe, expect, it } from "vitest";

import {
    baseTypeName,
    literalKind,
    valueDisplayOf,
} from "../../sql/dataTypes.js";

describe("baseTypeName", () => {
    it.each([
        ["int(11)", "int"],
        ["int(10) unsigned", "int"],
        ["varchar(50)", "varchar"],
        ["DECIMAL(10,2)", "decimal"],
        ["datetime", "datetime"],
        ["enum('a','b')", "enum"],
    ])("reduces %s to %s", (columnType, expected) => {
        expect(baseTypeName(columnType)).toBe(expected);
    });
});

describe("literalKind", () => {
    it.each([
        ["int(11)", "numeric"],
        ["bigint(20) unsigned", "numeric"],
        ["decimal(10,2)", "numeric"],
        ["double", "numeric"],
        ["varchar(50)", "string"],
        ["text", "string"],
        ["enum('a','b')", "string"],
        ["json", "string"],
        ["uuid", "string"],
        ["binary(16)", "binary"],
        ["blob", "binary"],
        ["bit(1)", "bit"],
        ["date", "temporal"],
        ["datetime(6)", "temporal"],
        ["timestamp", "temporal"],
    ] as const)("classifies %s as %s", (columnType, expected) => {
        expect(literalKind(columnType)).toBe(expected);
    });
});

describe("valueDisplayOf", () => {
    it("goes by the server's type where the table's is not known", () => {
        expect(valueDisplayOf("BYTES")).toBe("binary");
        expect(valueDisplayOf("BLOB")).toBe("blob");
        expect(valueDisplayOf("GEOMETRY")).toBe("geometry");
        // Shown as text, as the MySQL Shell shows it, but marked as JSON.
        expect(valueDisplayOf("JSON")).toBe("json");
        expect(valueDisplayOf(undefined, "json")).toBe("json");
        expect(valueDisplayOf("STRING")).toBeUndefined();
        expect(valueDisplayOf(null)).toBeUndefined();
        expect(valueDisplayOf(undefined)).toBeUndefined();
    });

    it("knows a vector only by the table's own column type", () => {
        // The server reports a VECTOR as BYTES, as it does a VARBINARY.
        expect(valueDisplayOf("BYTES", "vector(3)")).toBe("vector");
        expect(valueDisplayOf("BYTES", "varbinary(12)")).toBe("binary");
    });

    it("reads the table's column type for the rest too", () => {
        expect(valueDisplayOf(undefined, "mediumblob")).toBe("blob");
        expect(valueDisplayOf(undefined, "binary(16)")).toBe("binary");
        expect(valueDisplayOf(undefined, "point")).toBe("geometry");
        expect(valueDisplayOf(undefined, "multipolygon")).toBe("geometry");
        expect(valueDisplayOf(undefined, "varchar(10)")).toBeUndefined();
    });
});
