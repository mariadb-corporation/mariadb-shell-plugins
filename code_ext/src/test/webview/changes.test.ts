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
    blankRow,
    collectChanges,
    initialRows,
    type IEditableRow,
} from "../../webview/changes.js";
import type { IResultSet } from "../../webview/protocol.js";

/**
 * @param rows The rows the result set holds.
 *
 * @returns An editable result set over world.city.
 */
const citySet = (rows: Array<Record<string, unknown>> = []): IResultSet => {
    return {
        id: "result-0",
        caption: "Result #1",
        statement: "SELECT ID, Name FROM world.city",
        columns: [
            {
                name: "ID",
                datatype: "int(11)",
                isPrimary: true,
                isAutoIncrement: true,
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
        rows,
        editable: true,
        target: { schema: "world", table: "city" },
        status: `${rows.length} rows in set`,
    };
};

describe("initialRows", () => {
    it("starts with the current values equal to the original ones", () => {
        const rows = initialRows(citySet([{ ID: 1, Name: "Kabul" }]));

        expect(rows).toEqual([{
            original: { ID: 1, Name: "Kabul" },
            current: { ID: 1, Name: "Kabul" },
            added: false,
            deleted: false,
        }]);
    });

    it("copies the values, so editing one does not touch the other", () => {
        const row = { ID: 1, Name: "Kabul" };
        const [editable] = initialRows(citySet([row]));

        editable.current.Name = "Edited";

        expect(editable.original.Name).toBe("Kabul");
        expect(row.Name).toBe("Kabul");
    });
});

describe("blankRow", () => {
    it("adds a row with every column null", () => {
        expect(blankRow(citySet())).toEqual({
            original: { ID: null, Name: null },
            current: { ID: null, Name: null },
            added: true,
            deleted: false,
        });
    });
});

describe("collectChanges on a changed key", () => {
    it("addresses the row by the key it had", () => {
        const set = {
            id: "r", caption: "", statement: "", editable: true, status: "",
            columns: [{ name: "ID", isPrimary: true, isAutoIncrement: true },
                { name: "Name" }],
            rows: [{ ID: 1, Name: "Kabul" }],
        };
        const rows = initialRows(set);
        rows[0] = { ...rows[0], current: { ID: 4080, Name: "Kabul" } };

        expect(collectChanges(set, rows)).toEqual([{
            kind: "update", rowIndex: 0, keys: { ID: 1 }, values: { ID: 4080 },
        }]);
    });
});

describe("collectChanges", () => {
    it("finds nothing in an untouched grid", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);

        expect(collectChanges(set, initialRows(set))).toEqual([]);
    });

    it("reports an edited cell as an update", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);
        const rows = initialRows(set);
        rows[0].current.Name = "Kabul City";

        expect(collectChanges(set, rows)).toEqual([{
            kind: "update",
            rowIndex: 0,
            keys: { ID: 1 },
            values: { Name: "Kabul City" },
        }]);
    });

    it("reports only the columns that changed", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);
        const rows = initialRows(set);
        rows[0].current.Name = "Kabul";

        expect(collectChanges(set, rows)).toEqual([]);
    });

    it("addresses the row by the key it had before the edit", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);
        const rows = initialRows(set);
        rows[0].current.ID = 99;

        expect(collectChanges(set, rows)).toEqual([{
            kind: "update",
            rowIndex: 0,
            keys: { ID: 1 },
            values: { ID: 99 },
        }]);
    });

    it("reports an added row as an insert", () => {
        const set = citySet();
        const rows = [blankRow(set)];
        rows[0].current.Name = "Springfield";

        expect(collectChanges(set, rows)).toEqual([{
            kind: "insert",
            rowIndex: 0,
            values: { ID: null, Name: "Springfield" },
        }]);
    });

    it("reports a removed row as a delete", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);
        const rows = initialRows(set);
        rows[0].deleted = true;

        expect(collectChanges(set, rows)).toEqual([{
            kind: "delete",
            rowIndex: 0,
            keys: { ID: 1 },
        }]);
    });

    it("ignores a row that was added and then removed", () => {
        const set = citySet();
        const rows = [blankRow(set)];
        rows[0].deleted = true;

        expect(collectChanges(set, rows)).toEqual([]);
    });

    it("ignores edits to a row that is being removed", () => {
        const set = citySet([{ ID: 1, Name: "Kabul" }]);
        const rows = initialRows(set);
        rows[0].current.Name = "pointless";
        rows[0].deleted = true;

        expect(collectChanges(set, rows)).toEqual([{
            kind: "delete",
            rowIndex: 0,
            keys: { ID: 1 },
        }]);
    });

    it("collects a composite key", () => {
        const set: IResultSet = {
            ...citySet([{ a: 1, b: "x", c: "old" }]),
            columns: [
                { name: "a", isPrimary: true },
                { name: "b", isPrimary: true },
                { name: "c", isPrimary: false },
            ],
        };
        const rows = initialRows(set);
        rows[0].current.c = "new";

        expect(collectChanges(set, rows)).toEqual([{
            kind: "update",
            rowIndex: 0,
            keys: { a: 1, b: "x" },
            values: { c: "new" },
        }]);
    });

    it("reports several rows in row order", () => {
        const set = citySet([
            { ID: 1, Name: "Kabul" },
            { ID: 2, Name: "Qandahar" },
            { ID: 3, Name: "Herat" },
        ]);
        const rows: IEditableRow[] = initialRows(set);
        rows[0].deleted = true;
        rows[2].current.Name = "Herat City";

        expect(collectChanges(set, rows)).toEqual([
            { kind: "delete", rowIndex: 0, keys: { ID: 1 } },
            {
                kind: "update",
                rowIndex: 2,
                keys: { ID: 3 },
                values: { Name: "Herat City" },
            },
        ]);
    });

    it("tells an empty string from a null", () => {
        const set = citySet([{ ID: 1, Name: null }]);
        const rows = initialRows(set);
        rows[0].current.Name = "";

        expect(collectChanges(set, rows)).toEqual([{
            kind: "update",
            rowIndex: 0,
            keys: { ID: 1 },
            values: { Name: "" },
        }]);
    });
});
