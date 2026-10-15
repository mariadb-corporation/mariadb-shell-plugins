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
import { describe, expect, it } from "vitest";

import "./setup.js";
import {
    DB_OBJECT_COLUMNS,
    formatDbObjectCell,
    withKeys,
} from "../src/mrs/DbObjectsTable.js";

const cell = (data: Record<string, unknown>): CellComponent => {
    return {
        getRow: () => { return { getData: () => { return data; } }; },
    } as unknown as CellComponent;
};

describe("the REST Objects table", () => {
    it("has the Database Object, Type and Details columns", () => {
        expect(DB_OBJECT_COLUMNS.map((column) => { return column.title; }))
            .toEqual(["Database Object", "Type", "Details"]);
    });

    it("leads a row with its checkbox, then the type's icon and the name",
        () => {
            const rendered = formatDbObjectCell(cell({
                name: "actor", type: "TABLE", selected: true, key: "TABLE:actor",
            }));
            const [box, icon, name] = [...rendered.children] as HTMLElement[];

            expect((box as HTMLInputElement).type).toBe("checkbox");
            expect((box as HTMLInputElement).checked).toBe(true);
            expect(icon!.className).toBe("dbObjectIcon table");
            expect(name!.textContent).toBe("actor");
            expect(formatDbObjectCell(cell({ name: "p", type: "PROCEDURE",
                selected: false, key: "PROCEDURE:p" })).querySelector("input")
                ?.checked).toBe(false);
        });

    it("ticks and unticks keys, each once", () => {
        expect(withKeys(["a"], ["a", "b"], true).sort()).toEqual(["a", "b"]);
        expect(withKeys(["a", "b"], ["b", "c"], false)).toEqual(["a"]);
    });
});
