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

import type { CellComponent } from "tabulator-tables";
import { describe, expect, it, vi } from "vitest";

import "./setup.js";
import {
    buildColumns,
    formatCell,
    ROW_INDEX_FIELD,
    toTableData,
} from "../src/ResultGrid.js";
import type { IEditableRow } from "../../src/webview/changes.js";
import type { IResultSet } from "../../src/webview/protocol.js";

/**
 * Tabulator needs real layout, so it never builds under jsdom. What it
 * would call - the formatters, the editable predicate and the cell
 * handlers - is exercised here directly, with stand-ins for the cell and
 * row it passes them.
 */

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

/**
 * @param value The value the cell holds.
 * @param rowData The row the cell belongs to.
 *
 * @returns A stand-in for Tabulator's cell component.
 */
const cell = (
    value: unknown,
    rowData: Record<string, unknown> = {},
): CellComponent => {
    return {
        getValue: () => {
            return value;
        },
        getRow: () => {
            return { getData: () => {
                return rowData;
            } };
        },
    } as unknown as CellComponent;
};

/**
 * @param row The row to build editing state for.
 *
 * @returns An editable row holding it unchanged.
 */
const editable = (row: Record<string, unknown>): IEditableRow => {
    return {
        original: { ...row },
        current: { ...row },
        added: false,
        deleted: false,
    };
};

describe("formatCell", () => {
    it("marks NULL rather than showing an empty cell", () => {
        const rendered = formatCell(cell(null)) as HTMLElement;

        expect(rendered.className).toBe("nullValue");
        expect(rendered.textContent).toBe("NULL");
    });

    it("tells an empty string from NULL", () => {
        expect(formatCell(cell(""))).toBe("");
    });

    it("renders scalars as text", () => {
        expect(formatCell(cell(42))).toBe("42");
        expect(formatCell(cell("Kabul"))).toBe("Kabul");
        expect(formatCell(cell(false))).toBe("false");
    });

    it("renders an object as JSON", () => {
        expect(formatCell(cell({ a: 1 }))).toBe('{"a":1}');
    });
});

describe("toTableData", () => {
    it("carries the row index Tabulator keys on", () => {
        const rows = [editable({ ID: 1 }), editable({ ID: 2 })];

        expect(toTableData(rows).map((row) => {
            return row[ROW_INDEX_FIELD];
        })).toEqual([0, 1]);
    });

    it("lists the columns that differ from the stored row", () => {
        const row = editable({ ID: 1, Name: "Kabul" });
        row.current.Name = "Kabul City";

        expect(toTableData([row])[0].__dirty).toEqual(["Name"]);
    });

    it("has nothing dirty in an untouched row", () => {
        expect(toTableData([editable({ ID: 1 })])[0].__dirty).toEqual([]);
    });

    it("marks added and deleted rows", () => {
        const added = { ...editable({ ID: null }), added: true };
        const deleted = { ...editable({ ID: 2 }), deleted: true };

        const data = toTableData([added, deleted]);

        expect(data[0].__added).toBe(true);
        expect(data[0].__deleted).toBe(false);
        expect(data[1].__deleted).toBe(true);
    });

    it("shows the current values, not the stored ones", () => {
        const row = editable({ ID: 1, Name: "Kabul" });
        row.current.Name = "Edited";

        expect(toTableData([row])[0].Name).toBe("Edited");
    });
});

describe("buildColumns", () => {
    const callbacks = () => {
        return {
            onCellEdited: vi.fn(),
            onToggleDeleted: vi.fn(),
            onSelectionChanged: vi.fn(),
        };
    };

    it("puts a row header in front of an editable result", () => {
        const columns = buildColumns(resultSet(), callbacks());

        expect(columns[0].field).toBe(ROW_INDEX_FIELD);
        expect(columns.map((column) => {
            return column.title;
        })).toEqual(["", "ID", "Name"]);
    });

    it("has no row header when the result is read only", () => {
        const columns = buildColumns(
            resultSet({ editable: false }), callbacks());

        expect(columns.map((column) => {
            return column.title;
        })).toEqual(["ID", "Name"]);
    });

    it("marks the primary key column for the header styling", () => {
        const columns = buildColumns(resultSet(), callbacks());

        expect(columns[1].cssClass).toBe("pkColumn");
        expect(columns[2].cssClass).toBeUndefined();
    });

    it("gives an editor to every column the server does not fill in", () => {
        const columns = buildColumns(resultSet(), callbacks());

        // ID is auto-increment, so it is not typed into.
        expect(columns[1].editor).toBeUndefined();
        expect(columns[2].editor).toBe("input");
    });

    it("gives no editor at all to a read-only result", () => {
        const columns = buildColumns(
            resultSet({ editable: false }), callbacks());

        expect(columns[0].editor).toBeUndefined();
        expect(columns[1].editor).toBeUndefined();
    });

    it("freezes the cells of a row marked for deletion", () => {
        const columns = buildColumns(resultSet(), callbacks());
        const editableOf = columns[2].editable as
            (cell: CellComponent) => boolean;

        expect(editableOf(cell("Kabul", { __deleted: false }))).toBe(true);
        expect(editableOf(cell("Kabul", { __deleted: true }))).toBe(false);
    });

    it("reports an edited cell with its row and column", () => {
        const handlers = callbacks();
        const columns = buildColumns(resultSet(), handlers);
        const edited = columns[2].cellEdited as
            (cell: CellComponent) => void;

        edited(cell("Kabul City", { [ROW_INDEX_FIELD]: 3 }));

        expect(handlers.onCellEdited)
            .toHaveBeenCalledWith(3, "Name", "Kabul City");
    });

    it("offers a delete toggle that names what it will do", () => {
        const columns = buildColumns(resultSet(), callbacks());
        const formatter = columns[0].formatter as
            (cell: CellComponent) => HTMLElement;

        const kept = formatter(cell(0, { __deleted: false }));
        expect(kept.title).toBe("Mark this row for deletion");

        const marked = formatter(cell(0, { __deleted: true }));
        expect(marked.title).toBe("Keep this row");
    });

    it("toggles the row it was clicked on", () => {
        const handlers = callbacks();
        const columns = buildColumns(resultSet(), handlers);
        const clicked = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        clicked(undefined, cell(0, { [ROW_INDEX_FIELD]: 5 }));

        expect(handlers.onToggleDeleted).toHaveBeenCalledWith(5);
    });

    it("shows a column's type as its header tooltip", () => {
        const columns = buildColumns(resultSet(), callbacks());

        expect(columns[1].headerTooltip).toBe("int(11)");
    });
});
