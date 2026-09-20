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

import { baseTypeName, literalKind } from "../../sql/dataTypes.js";

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
