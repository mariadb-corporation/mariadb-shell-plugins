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

import type { IColumnDetails } from "../../mcp/types.js";
import {
    escapeString,
    QueryBuilder,
    quoteIdentifier,
} from "../../sql/queryBuilder.js";

/**
 * @param overrides The fields that differ from a plain nullable text
 *                  column.
 *
 * @returns A column description.
 */
const column = (overrides: Partial<IColumnDetails>): IColumnDetails => {
    return {
        name: "c",
        datatype: "varchar(50)",
        not_null: 0,
        is_primary: 0,
        is_unique: 0,
        is_generated: 0,
        id_generation: null,
        comment: "",
        column_default: null,
        ...overrides,
    };
};

const CITY_COLUMNS: IColumnDetails[] = [
    column({
        name: "ID",
        datatype: "int(11)",
        not_null: 1,
        is_primary: 1,
        id_generation: "auto_inc",
    }),
    column({ name: "Name", datatype: "char(35)", not_null: 1 }),
    column({ name: "Population", datatype: "int(11)" }),
];

/**
 * @returns A builder over `world`.`city`.
 */
const cityBuilder = (): QueryBuilder => {
    return new QueryBuilder("world", "city", CITY_COLUMNS);
};

describe("quoteIdentifier", () => {
    it("wraps a name in back quotes", () => {
        expect(quoteIdentifier("city")).toBe("`city`");
    });

    it("doubles a back quote inside the name", () => {
        expect(quoteIdentifier("we`ird")).toBe("`we``ird`");
    });
});

describe("escapeString", () => {
    it("escapes quotes, backslashes and control characters", () => {
        expect(escapeString("it's")).toBe("it''s");
        expect(escapeString("a\\b")).toBe("a\\\\b");
        expect(escapeString("a\nb\tc\rd")).toBe("a\\nb\\tc\\rd");
    });
});

describe("QueryBuilder.formatValue", () => {
    const builder = new QueryBuilder(undefined, "t", [
        column({ name: "n", datatype: "int(11)" }),
        column({ name: "d", datatype: "decimal(10,2)" }),
        column({ name: "s", datatype: "varchar(20)" }),
        column({ name: "b", datatype: "varbinary(16)" }),
        column({ name: "flag", datatype: "bit(1)" }),
        column({ name: "at", datatype: "datetime" }),
        column({ name: "j", datatype: "json" }),
    ]);

    it("writes NULL for a missing value", () => {
        expect(builder.formatValue("s", null)).toBe("NULL");
        expect(builder.formatValue("s", undefined)).toBe("NULL");
    });

    it("writes numbers unquoted", () => {
        expect(builder.formatValue("n", 42)).toBe("42");
        expect(builder.formatValue("n", "-7")).toBe("-7");
        expect(builder.formatValue("d", "10.25")).toBe("10.25");
        expect(builder.formatValue("d", "1e3")).toBe("1e3");
    });

    it("quotes a non-numeric value in a numeric column", () => {
        // Unquoted it would silently become 0; quoted, the server rejects
        // it and says so.
        expect(builder.formatValue("n", "abc")).toBe("'abc'");
    });

    it("quotes strings and escapes them", () => {
        expect(builder.formatValue("s", "it's")).toBe("'it''s'");
    });

    it("writes binary values as hex", () => {
        expect(builder.formatValue("b", "cafe00")).toBe("0xcafe00");
        // As the grid shows it, 0x and all.
        expect(builder.formatValue("b", "0xCAFE")).toBe("0xCAFE");
        expect(builder.formatValue("b", "")).toBe("0x00");
    });

    it("writes bit values in bit notation", () => {
        expect(builder.formatValue("flag", "1")).toBe("b'1'");
        expect(builder.formatValue("flag", "0101")).toBe("b'0101'");
    });

    it("quotes temporal values", () => {
        expect(builder.formatValue("at", "2026-09-20 12:00:00"))
            .toBe("'2026-09-20 12:00:00'");
    });

    it("quotes an unknown column as a string", () => {
        expect(builder.formatValue("nope", "x")).toBe("'x'");
    });
});

describe("QueryBuilder", () => {
    it("knows whether a result can be written back", () => {
        expect(cityBuilder().isUpdatable).toBe(true);
        expect(new QueryBuilder(undefined, "t", [
            column({ name: "a" }),
        ]).isUpdatable).toBe(false);
    });

    it("names the key columns", () => {
        expect(cityBuilder().keyColumnNames).toEqual(["ID"]);
    });

    it("builds an UPDATE addressed by the primary key", () => {
        expect(cityBuilder().buildUpdate(
            { ID: 1 },
            { Name: "Kabul", Population: 1780000 },
        )).toBe(
            "UPDATE `world`.`city` SET `Name` = 'Kabul', "
            + "`Population` = 1780000 WHERE `ID` = 1",
        );
    });

    it("refuses an UPDATE with nothing to set", () => {
        expect(() => {
            return cityBuilder().buildUpdate({ ID: 1 }, {});
        }).toThrow(/at least one changed column/);
    });

    it("writes an auto-increment value the user gave", () => {
        expect(cityBuilder().buildInsert({
            ID: 5000,
            Name: "Springfield",
            Population: 30000,
        })).toBe(
            "INSERT INTO `world`.`city` (`ID`, `Name`, `Population`) "
            + "VALUES (5000, 'Springfield', 30000)",
        );
        // An emptied cell is no value either: the server assigns one.
        expect(cityBuilder().buildInsert({ ID: "", Name: "x" }))
            .toBe("INSERT INTO `world`.`city` (`Name`) VALUES ('x')");
    });

    it("changes a primary key with an UPDATE addressed by the old one", () => {
        expect(cityBuilder().buildUpdate({ ID: 1 }, { ID: 4080 }))
            .toBe("UPDATE `world`.`city` SET `ID` = 4080 WHERE `ID` = 1");
    });

    it("leaves auto-increment columns out of an INSERT", () => {
        expect(cityBuilder().buildInsert({
            ID: null,
            Name: "Springfield",
            Population: 30000,
        })).toBe(
            "INSERT INTO `world`.`city` (`Name`, `Population`) "
            + "VALUES ('Springfield', 30000)",
        );
    });

    it("leaves generated columns out of an INSERT", () => {
        const builder = new QueryBuilder(undefined, "t", [
            column({ name: "a" }),
            column({ name: "b", is_generated: 1 }),
        ]);

        expect(builder.buildInsert({ a: "x", b: "computed" }))
            .toBe("INSERT INTO `t` (`a`) VALUES ('x')");
    });

    it("builds an all-defaults INSERT when nothing is left to set", () => {
        const builder = new QueryBuilder(undefined, "t", [
            column({ name: "id", is_primary: 1, id_generation: "auto_inc" }),
        ]);

        expect(builder.buildInsert({ id: null }))
            .toBe("INSERT INTO `t` () VALUES ()");
    });

    it("builds a DELETE addressed by the primary key", () => {
        expect(cityBuilder().buildDelete({ ID: 42 }))
            .toBe("DELETE FROM `world`.`city` WHERE `ID` = 42");
    });

    it("builds a composite key WHERE clause", () => {
        const builder = new QueryBuilder(undefined, "link", [
            column({ name: "a", datatype: "int(11)", is_primary: 1 }),
            column({ name: "b", datatype: "varchar(10)", is_primary: 1 }),
        ]);

        expect(builder.buildDelete({ a: 1, b: "x" }))
            .toBe("DELETE FROM `link` WHERE `a` = 1 AND `b` = 'x'");
    });

    it("addresses a NULL key with IS NULL", () => {
        const builder = new QueryBuilder(undefined, "t", [
            column({ name: "k", is_primary: 1 }),
        ]);

        expect(builder.buildDelete({ k: null }))
            .toBe("DELETE FROM `t` WHERE `k` IS NULL");
    });

    it("refuses to address a row without a primary key", () => {
        const builder = new QueryBuilder(undefined, "t", [
            column({ name: "a" }),
        ]);

        expect(() => {
            return builder.buildDelete({ a: 1 });
        }).toThrow(/no primary key/);
    });

    it("omits the schema when there is none", () => {
        expect(new QueryBuilder(undefined, "city", CITY_COLUMNS)
            .buildDelete({ ID: 1 }))
            .toBe("DELETE FROM `city` WHERE `ID` = 1");
    });

    it("orders a script as updates, inserts, then deletes", () => {
        const statements = cityBuilder().buildStatements([
            { kind: "delete", rowIndex: 2, keys: { ID: 3 } },
            { kind: "insert", rowIndex: 3, values: { ID: null, Name: "New" } },
            {
                kind: "update",
                rowIndex: 0,
                keys: { ID: 1 },
                values: { Name: "Edited" },
            },
        ]);

        expect(statements.map((entry) => {
            return entry.sql;
        })).toEqual([
            "UPDATE `world`.`city` SET `Name` = 'Edited' WHERE `ID` = 1",
            "INSERT INTO `world`.`city` (`Name`) VALUES ('New')",
            "DELETE FROM `world`.`city` WHERE `ID` = 3",
        ]);
    });

    it("keeps the grid row each statement came from", () => {
        // The SQL preview uses this to take the user back to the row.
        const statements = cityBuilder().buildStatements([
            { kind: "delete", rowIndex: 2, keys: { ID: 3 } },
            {
                kind: "update",
                rowIndex: 7,
                keys: { ID: 1 },
                values: { Name: "Edited" },
            },
        ]);

        expect(statements.map((entry) => {
            return entry.rowIndex;
        })).toEqual([7, 2]);
    });

    it("builds nothing from no changes", () => {
        expect(cityBuilder().buildStatements([])).toEqual([]);
    });
});
