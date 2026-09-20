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

import { createQueryBuilder } from "../../sql/resultSetQueryBuilder.js";
import type { IResultSet } from "../../webview/protocol.js";

/**
 * @param overrides The fields that differ from an editable city result.
 *
 * @returns A result set.
 */
const resultSet = (overrides: Partial<IResultSet> = {}): IResultSet => {
    return {
        id: "result-0",
        caption: "Result #1",
        statement: "SELECT ID, Name FROM world.city",
        columns: [
            {
                name: "ID",
                datatype: "int(11)",
                isPrimary: true,
                isGenerated: true,
                nullable: false,
            },
            {
                name: "Name",
                datatype: "char(35)",
                isPrimary: false,
                isGenerated: false,
                nullable: false,
            },
        ],
        rows: [],
        editable: true,
        target: { schema: "world", table: "city" },
        status: "0 rows in set",
        ...overrides,
    };
};

describe("createQueryBuilder", () => {
    it("builds a builder over the result set's table", () => {
        const builder = createQueryBuilder(resultSet());

        expect(builder?.buildDelete({ ID: 1 }))
            .toBe("DELETE FROM `world`.`city` WHERE `ID` = 1");
    });

    it("carries the primary key through", () => {
        expect(createQueryBuilder(resultSet())?.keyColumnNames)
            .toEqual(["ID"]);
    });

    it("carries the generated columns through", () => {
        // The webview previews the same INSERT the extension will run,
        // so the auto-increment column has to be left out on both sides.
        expect(createQueryBuilder(resultSet())
            ?.buildInsert({ ID: null, Name: "New" }))
            .toBe("INSERT INTO `world`.`city` (`Name`) VALUES ('New')");
    });

    it("carries the data types through", () => {
        const builder = createQueryBuilder(resultSet());

        expect(builder?.formatValue("ID", 7)).toBe("7");
        expect(builder?.formatValue("Name", "it's")).toBe("'it''s'");
    });

    it("treats a column with no type as text", () => {
        const builder = createQueryBuilder(resultSet({
            columns: [
                { name: "k", isPrimary: true },
                { name: "v" },
            ],
        }));

        expect(builder?.formatValue("v", 5)).toBe("'5'");
    });

    it("returns nothing for a result set with no table", () => {
        expect(createQueryBuilder(resultSet({ target: undefined })))
            .toBeUndefined();
    });

    it("omits the schema when the target has none", () => {
        const builder = createQueryBuilder(resultSet({
            target: { table: "city" },
        }));

        expect(builder?.buildDelete({ ID: 1 }))
            .toBe("DELETE FROM `city` WHERE `ID` = 1");
    });
});
