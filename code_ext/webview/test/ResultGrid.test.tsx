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
    BINARY_DIGITS_SHOWN,
    blobCell,
    buildColumns,
    cellActionsOf,
    editableAsText,
    formatCell,
    formatValue,
    LONG_TEXT,
    openableCell,
    ROW_INDEX_FIELD,
    worthOpening,
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

describe("formatValue", () => {
    it("shows a binary value as 0x and its hex", () => {
        expect(formatValue("00ff", "binary")).toBe("0x00ff");
        expect(formatValue("", "binary")).toBe("0x");
    });

    it("cuts a long binary value short, as the MySQL Shell does", () => {
        const long = "ab".repeat(BINARY_DIGITS_SHOWN);

        expect(formatValue(long, "binary"))
            .toBe(`0x${long.slice(0, 64)}\u2026`);
        expect(formatValue("ab".repeat(32), "binary"))
            .toBe(`0x${"ab".repeat(32)}`);
    });

    it("shows a BLOB, a spatial value and a vector by an icon", () => {
        for (const [display, className, text] of [
            ["blob", "blobValue", "BLOB"],
            ["geometry", "geometryValue", "GEOMETRY"],
            ["vector", "vectorValue", "VECTOR"],
        ] as const) {
            const rendered = formatValue("00ff", display) as HTMLElement;

            expect(rendered.classList.contains(className)).toBe(true);
            // The word stays, for copying and for a screen reader.
            expect(rendered.textContent).toBe(text);
        }
    });

    it("shows NULL as NULL whatever the column holds", () => {
        const rendered = formatValue(null, "blob") as HTMLElement;

        expect(rendered.classList.contains("nullValue")).toBe(true);
    });

    it("leaves every other value as text", () => {
        expect(formatValue("{\"a\": 1}")).toBe("{\"a\": 1}");
    });
});

describe("cellActionsOf", () => {
    const blob = { name: "image", display: "blob" as const, nullable: true };

    it("saves and loads a BLOB of an editable row", () => {
        expect(cellActionsOf(resultSet(), blob, "89504e47", false)).toEqual({
            canSave: true, canLoad: true, canSetNull: true, openReadOnly: false,
        });
    });

    it("has nothing to save from a NULL, but can load into it", () => {
        expect(cellActionsOf(resultSet(), blob, null, false)).toEqual({
            canSave: false, canLoad: true, canSetNull: false, openReadOnly: false,
        });
    });

    it("only saves from a result set that cannot be edited", () => {
        expect(cellActionsOf(resultSet({ editable: false }), blob, "00",
            false)).toEqual({
            canSave: true, canLoad: false, canSetNull: false, openReadOnly: true,
        });
    });

    it("changes nothing in a row marked for deletion", () => {
        expect(cellActionsOf(resultSet(), blob, "00", true)).toMatchObject({
            canLoad: false, canSetNull: false,
        });
    });

    it("keeps saving and loading to BLOBs, as the MySQL Shell does", () => {
        const binary = { name: "b", display: "binary" as const };

        expect(cellActionsOf(resultSet(), binary, "00ff", false))
            .toMatchObject({ canSave: false, canLoad: false });
    });

    it("opens a spatial value or a vector read only", () => {
        for (const display of ["geometry", "vector"] as const) {
            expect(cellActionsOf(resultSet(), { name: "g", display }, "0101",
                false).openReadOnly).toBe(true);
        }
        expect(cellActionsOf(resultSet(), { name: "j", display: "json" },
            "{}", false).openReadOnly).toBe(false);
    });

    it("sets only a nullable column to NULL", () => {
        const required = { name: "Name", nullable: false };

        expect(cellActionsOf(resultSet(), required, "Kabul", false).canSetNull)
            .toBe(false);
    });
});

describe("blobCell", () => {
    it("puts Save and Load over the icon", () => {
        const onSave = vi.fn();
        const onLoad = vi.fn();
        const host = blobCell("89504e47",
            { canSave: true, canLoad: true, canSetNull: true, openReadOnly: false },
            onSave, onLoad);

        expect(host.querySelector(".blobValue")?.textContent).toBe("BLOB");
        const buttons = [...host.querySelectorAll("button")];
        expect(buttons.map((button) => {
            return button.getAttribute("aria-label");
        })).toEqual(["Save Value to File...", "Load Value from File..."]);

        // A click is the button's alone: the cell would open its editor.
        const cellClick = vi.fn();
        const cell = document.createElement("div");
        cell.addEventListener("click", cellClick);
        cell.append(host);
        buttons[0].click();
        buttons[1].click();

        expect(onSave).toHaveBeenCalledOnce();
        expect(onLoad).toHaveBeenCalledOnce();
        expect(cellClick).not.toHaveBeenCalled();
    });

    it("offers only what can be done", () => {
        const host = blobCell(null,
            { canSave: false, canLoad: false, canSetNull: false,
                openReadOnly: true },
            vi.fn(), vi.fn());

        expect(host.querySelector(".cellOverlay")).toBeNull();
        expect(host.querySelector(".nullValue")).not.toBeNull();
    });
});

describe("the cell menu", () => {
    it("offers the MySQL Shell's value items for a cell", () => {
        const callbacks = {
            onCellEdited: vi.fn(),
            onToggleDeleted: vi.fn(),
            onSelectionChanged: vi.fn(),
            onSaveValue: vi.fn(),
            onLoadValue: vi.fn(),
        };
        const columns = buildColumns(resultSet({
            columns: [
                { name: "ID", isPrimary: true },
                { name: "image", display: "blob", nullable: true },
            ],
        }), callbacks);
        const image = columns.find((column) => {
            return column.field === "image";
        })!;

        const event = new MouseEvent("contextmenu", {
            clientX: 10, clientY: 10, cancelable: true,
        });
        image.cellContext!(event, cell("89504e47",
            { [ROW_INDEX_FIELD]: 4 }) as never);

        const items = [...document.querySelectorAll(".copyMenu .copyMenuItem")]
            .map((item) => {
                return [item.textContent,
                    (item as HTMLButtonElement).disabled];
            });
        // The MySQL Shell's order.
        expect(items).toEqual([
            ["Open Value in Editor", false],
            ["Set Field to Null", false],
            ["Save Value to File...", false],
            ["Load Value from File...", false],
            ["Delete Row", false],
        ]);
        expect(document.querySelector(".copyMenuSeparator")).not.toBeNull();
        expect(event.defaultPrevented).toBe(true);

        (document.querySelectorAll(".copyMenuItem")[1] as HTMLElement).click();
        expect(callbacks.onCellEdited).toHaveBeenCalledWith(4, "image", null);
        // A pick closes it.
        expect(document.querySelector(".copyMenu")).toBeNull();
    });
});

describe("opening a value in an editor", () => {
    it("offers it for JSON and for text a cell cannot show whole", () => {
        expect(worthOpening("{}", "json")).toBe(true);
        expect(worthOpening("one\ntwo")).toBe(true);
        expect(worthOpening("x".repeat(LONG_TEXT + 1))).toBe(true);
        expect(worthOpening("short")).toBe(false);
        expect(worthOpening(null, "json")).toBe(false);
    });

    it("puts the button over such a cell, and keeps its text", () => {
        const onOpen = vi.fn();
        const host = openableCell("line 1\nline 2", undefined, onOpen);

        expect(host.querySelector(".cellText")?.textContent)
            .toBe("line 1\nline 2");
        const button = host.querySelector("button")!;
        expect(button.getAttribute("aria-label")).toBe("Open Value in Editor");
        button.click();
        expect(onOpen).toHaveBeenCalledOnce();
    });

    it("puts it first over a BLOB with a value", () => {
        const host = blobCell("89504e47",
            { canSave: true, canLoad: false, canSetNull: false,
                openReadOnly: true },
            vi.fn(), vi.fn(), vi.fn());

        expect([...host.querySelectorAll("button")].map((button) => {
            return button.getAttribute("aria-label");
        })).toEqual(["Open Value in Editor", "Save Value to File..."]);
    });
});

describe("editableAsText", () => {
    it("keeps a spatial value and a vector out of the text editor", () => {
        expect(editableAsText("geometry")).toBe(false);
        expect(editableAsText("vector")).toBe(false);
        expect(editableAsText("binary")).toBe(true);
        expect(editableAsText("blob")).toBe(true);
        expect(editableAsText(undefined)).toBe(true);
    });

    it("gives such a column no editor in the grid", () => {
        const columns = buildColumns(resultSet({
            columns: [
                { name: "ID", isPrimary: true },
                { name: "shape", display: "geometry" },
                { name: "data", display: "blob" },
            ],
        }), {
            onCellEdited: vi.fn(),
            onToggleDeleted: vi.fn(),
            onSelectionChanged: vi.fn(),
        });

        const byField = new Map(columns.map((column) => {
            return [column.field, column];
        }));
        expect(byField.get("shape")?.editor).toBeUndefined();
        expect(byField.get("data")?.editor).toBe("input");
    });
});

describe("formatCell", () => {
    it("marks NULL rather than showing an empty cell", () => {
        const rendered = formatCell(cell(null)) as HTMLElement;

        expect(rendered.classList.contains("nullValue")).toBe(true);
        expect(rendered.classList.contains("dataIcon")).toBe(true);
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

    /**
     * @param columns The grid's columns.
     *
     * @returns Their titles, in order.
     */
    const titles = (columns: Array<{ title?: string }>): unknown[] => {
        return columns.map((column) => { return column.title; });
    };

    it("has no row header: a row is deleted from its cell menu", () => {
        expect(titles(buildColumns(resultSet(), callbacks())))
            .toEqual(["ID", "Name"]);
        expect(titles(buildColumns(resultSet({ editable: false }),
            callbacks()))).toEqual(["ID", "Name"]);
    });

    it("marks the primary key column for the header styling", () => {
        const columns = buildColumns(resultSet(), callbacks());

        expect(columns[0].cssClass).toBe("pkColumn");
        expect(columns[1].cssClass).toBeUndefined();
    });

    it("gives an editor to every column but a generated one", () => {
        const columns = buildColumns(resultSet({
            columns: [
                { name: "ID", isPrimary: true, isAutoIncrement: true },
                { name: "Name" },
                { name: "Label", isGenerated: true },
            ],
        }), callbacks());

        // An auto-increment key is written like any column; a generated
        // one the server computes, and refuses to be given.
        expect(columns.map((column) => {
            return column.editor;
        })).toEqual(["input", "input", undefined]);
    });

    it("gives no editor at all to a read-only result", () => {
        const columns = buildColumns(
            resultSet({ editable: false }), callbacks());

        expect(columns[0].editor).toBeUndefined();
        expect(columns[1].editor).toBeUndefined();
    });

    it("freezes the cells of a row marked for deletion", () => {
        const columns = buildColumns(resultSet(), callbacks());
        const editableOf = columns[1].editable as
            (cell: CellComponent) => boolean;

        expect(editableOf(cell("Kabul", { __deleted: false }))).toBe(true);
        expect(editableOf(cell("Kabul", { __deleted: true }))).toBe(false);
    });

    it("reports an edited cell with its row and column", () => {
        const handlers = callbacks();
        const columns = buildColumns(resultSet(), handlers);
        const edited = columns[1].cellEdited as
            (cell: CellComponent) => void;

        edited(cell("Kabul City", { [ROW_INDEX_FIELD]: 3 }));

        expect(handlers.onCellEdited)
            .toHaveBeenCalledWith(3, "Name", "Kabul City");
    });

    it("shows a column's type as its header tooltip", () => {
        const columns = buildColumns(resultSet(), callbacks());

        expect(columns[0].headerTooltip).toBe("int(11)");
    });

    it("freezes nothing unless asked to", () => {
        expect(buildColumns(resultSet(), callbacks()).map((column) => {
            return column.frozen;
        })).toEqual([false, false]);
    });

    it("freezes the key columns, moved to the front", () => {
        const keyLast = resultSet({
            columns: [
                { name: "Name" },
                { name: "Code", isPrimary: true },
                { name: "Population" },
                { name: "ID", isPrimary: true },
            ],
        });

        const columns = buildColumns(keyLast, callbacks(), true);

        expect(titles(columns)).toEqual(["Code", "ID", "Name", "Population"]);
        expect(columns.map((column) => {
            return column.frozen;
        })).toEqual([true, true, false, false]);
        // Unfrozen, the result's own order stands.
        expect(titles(buildColumns(keyLast, callbacks(), false)))
            .toEqual(["Name", "Code", "Population", "ID"]);
    });

    it("offers Delete Row, and Restore Row on a row marked already", () => {
        const handlers = callbacks();
        const columns = buildColumns(resultSet(), handlers);
        const menu = (deleted: boolean): HTMLElement[] => {
            columns[1].cellContext!(new MouseEvent("contextmenu"),
                cell("Kabul", {
                    [ROW_INDEX_FIELD]: 5, __deleted: deleted,
                }) as never);

            return [...document.querySelectorAll<HTMLElement>(
                ".copyMenu .copyMenuItem")];
        };

        const item = menu(false).at(-1)!;
        expect(item.textContent).toBe("Delete Row");
        item.click();
        expect(handlers.onToggleDeleted).toHaveBeenCalledWith(5);

        expect(menu(true).at(-1)?.textContent).toBe("Restore Row");
    });

    it("offers no Delete Row in a result that cannot be edited", () => {
        const columns = buildColumns(resultSet({ editable: false }),
            callbacks());
        columns[1].cellContext!(new MouseEvent("contextmenu"),
            cell("Kabul", { [ROW_INDEX_FIELD]: 0 }) as never);

        const item = [...document.querySelectorAll<HTMLButtonElement>(
            ".copyMenu .copyMenuItem")].at(-1);
        expect(item?.textContent).toBe("Delete Row");
        expect(item?.disabled).toBe(true);
    });
});

