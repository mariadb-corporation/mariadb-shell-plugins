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
    buildOutputColumns,
    clickedOn,
    formatElapsed,
    formatMessageCell,
    formatRowsCell,
    formatSeverityCell,
} from "../src/OutputGrid.js";
import type { IOutputRow } from "../../src/webview/protocol.js";

/**
 * Tabulator needs real layout and never builds under jsdom, so what it
 * would call - the formatters and the jump handler - is exercised
 * directly, with a stand-in for the cell it passes them.
 */

/**
 * @param overrides The fields that differ from a plain info row.
 *
 * @returns An output row.
 */
const row = (overrides: Partial<IOutputRow> = {}): IOutputRow => {
    return {
        id: "run1-0",
        time: "12:00:00.123",
        connection: "dba@localhost:3310",
        role: "statement",
        statement: "SELECT 1",
        message: "1 row in set",
        kind: "info",
        rows: 1,
        elapsedMs: 4,
        ...overrides,
    };
};

/**
 * @param data The row the cell belongs to.
 * @param value The cell's value.
 *
 * @returns A stand-in for Tabulator's cell component.
 */
const cell = (data: IOutputRow, value: unknown = undefined) => {
    return {
        getValue: () => {
            return value;
        },
        getRow: () => {
            return { getData: () => {
                return data;
            } };
        },
    } as unknown as CellComponent;
};

describe("formatElapsed", () => {
    it("shows milliseconds below a second", () => {
        expect(formatElapsed(0)).toBe("0 ms");
        expect(formatElapsed(999)).toBe("999 ms");
    });

    it("shows seconds above one", () => {
        expect(formatElapsed(1000)).toBe("1.000 s");
        expect(formatElapsed(1234)).toBe("1.234 s");
    });
});

/**
 * @param target The element the click landed on.
 *
 * @returns A stand-in for the click Tabulator reports.
 */
const click = (target: Element): Event => {
    return { target } as unknown as Event;
};

describe("formatRowsCell", () => {
    it("offers a jump where the result set is still on show", () => {
        const rendered = formatRowsCell(
            cell(row({ resultId: "run1-result-0" })),
            new Set(["run1-result-0"]),
        );
        const button = rendered.querySelector("button");

        expect(button?.className).toBe("jumpToResult");
        expect(button?.title).toBe("Show this result set");
    });

    it("offers nothing where the result set has been replaced", () => {
        // A later run replaced the tabs, so this row's result is gone.
        expect(formatRowsCell(
            cell(row({ resultId: "run1-result-0" })),
            new Set(["run2-result-0"]),
        ).querySelector("button")).toBeNull();
    });

    it("offers nothing for a row that produced no result set", () => {
        expect(formatRowsCell(cell(row()), new Set(["run1-result-0"]))
            .querySelector("button")).toBeNull();
    });

    it("holds the arrow's place so the counts stay in a column", () => {
        // Without the slot a row with no jump would let its count slide
        // right, out of line with the rows above and below it.
        const rendered = formatRowsCell(cell(row()), new Set());

        expect(rendered.querySelector(".outputJumpSlot")).not.toBeNull();
    });

    it("shows the row count, and nothing where there is none", () => {
        expect(formatRowsCell(cell(row({ rows: 3 })), new Set())
            .textContent).toBe("3");
        expect(formatRowsCell(cell(row({ rows: undefined })), new Set())
            .textContent).toBe("");
    });
});

describe("formatMessageCell", () => {
    it("offers a go-to arrow for a row that knows where its statement is",
        () => {
            const rendered = formatMessageCell(cell(row({
                source: { uri: "file:///q.sql", line: 3, character: 0 },
            })));
            const button = rendered.querySelector("button");

            expect(button?.className).toBe("goToStatement");
            expect(button?.title).toBe("Go to this statement in the editor");
            // The message gives up the corner the arrow sits in.
            expect(rendered.classList.contains("hasGoTo")).toBe(true);
        });

    it("offers nothing for a row with no source", () => {
        const rendered = formatMessageCell(cell(row()));

        expect(rendered.querySelector("button")).toBeNull();
        expect(rendered.textContent).toBe("1 row in set");
        expect(rendered.classList.contains("hasGoTo")).toBe(false);
    });

    it("says it goes to the first error on a run's closing line", () => {
        const rendered = formatMessageCell(cell(row({
            role: "finish",
            source: { uri: "file:///q.sql", line: 3, character: 0 },
            jumpToRowId: "run1-2",
        })));

        expect(rendered.querySelector("button")?.title)
            .toBe("Go to the first error of this run");
    });
});

describe("clickedOn", () => {
    it("recognises a click on the control itself", () => {
        const button = document.createElement("button");
        button.className = "jumpToResult";

        expect(clickedOn(click(button), ".jumpToResult")).toBe(true);
    });

    it("recognises a click inside the control", () => {
        const button = document.createElement("button");
        button.className = "jumpToResult";
        const inner = document.createElement("span");
        button.appendChild(inner);

        expect(clickedOn(click(inner), ".jumpToResult")).toBe(true);
    });

    it("ignores a click elsewhere in the cell", () => {
        // Both arrows share their cell with the value beside them, so a
        // click on the value must not count as a click on the arrow.
        expect(clickedOn(click(document.createElement("div")),
            ".jumpToResult")).toBe(false);
        expect(clickedOn(undefined, ".jumpToResult")).toBe(false);
    });
});

describe("formatSeverityCell", () => {
    it.each([
        ["info", "codicon-info"],
        ["warning", "codicon-warning"],
        ["error", "codicon-error"],
    ] as const)("draws %s with the Problems panel's glyph",
        (kind, codicon) => {
            const rendered = formatSeverityCell(cell(row({ kind })));

            expect(rendered.className)
                .toBe(`markerIcon ${kind} codicon ${codicon}`);
            expect(rendered.title).toBe(kind);
        });
});

describe("buildOutputColumns", () => {
    const onJump = vi.fn();
    const onGoTo = vi.fn();

    /**
     * @returns A click on a control of the given class.
     */
    const clickOn = (className: string): Event => {
        const button = document.createElement("button");
        button.className = className;

        return click(button);
    };

    it("leads with the marker and the message", () => {
        const columns = buildOutputColumns(new Set(), onJump, onGoTo);

        expect(columns.map((column) => {
            return column.title;
        })).toEqual(["", "Output", "Time", "Elapsed", "Rows", "Statement"]);
        // The marker and the message lead together, as they do in the
        // Problems panel; the details follow.
        expect(columns[0].field).toBe("kind");
        expect(columns[1].field).toBe("message");
    });

    it("renders the elapsed time in the largest readable unit", () => {
        const columns = buildOutputColumns(new Set(), onJump, onGoTo);
        const format = columns[3].formatter as
            (cell: CellComponent) => string;

        expect(format(cell(row(), 4))).toBe("4 ms");
        expect(format(cell(row(), 2500))).toBe("2.500 s");
    });

    it("jumps to the result set a row produced", () => {
        const handler = vi.fn();
        const columns = buildOutputColumns(
            new Set(["run1-result-0"]), handler, onGoTo);
        const onClick = columns[4].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).toHaveBeenCalledWith("run1-result-0");
    });

    it("does not jump to a result set that is gone", () => {
        const handler = vi.fn();
        const columns = buildOutputColumns(
            new Set(["run2-result-0"]), handler, onGoTo);
        const onClick = columns[4].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not jump from a row with no result set", () => {
        const handler = vi.fn();
        const columns = buildOutputColumns(new Set(), handler, onGoTo);
        const onClick = columns[4].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"), cell(row()));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not jump when the click missed the arrow", () => {
        // The arrow now shares its cell with the row count.
        const handler = vi.fn();
        const columns = buildOutputColumns(
            new Set(["run1-result-0"]), handler, onGoTo);
        const onClick = columns[4].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("outputJumpSlot"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).not.toHaveBeenCalled();
    });

    it("goes to the statement a row came from", () => {
        const handler = vi.fn();
        const columns = buildOutputColumns(new Set(), onJump, handler);
        const onClick = columns[1].cellClick as
            (event: unknown, cell: CellComponent) => void;
        const data = row({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        });

        onClick(clickOn("goToStatement"), cell(data));

        expect(handler).toHaveBeenCalledWith(data);
    });

    it("does not go anywhere from a row with no source", () => {
        const handler = vi.fn();
        const columns = buildOutputColumns(new Set(), onJump, handler);
        const onClick = columns[1].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("goToStatement"), cell(row()));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not go anywhere when the click missed the arrow", () => {
        // The arrow now shares its cell with the message.
        const handler = vi.fn();
        const columns = buildOutputColumns(new Set(), onJump, handler);
        const onClick = columns[1].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("outputMessageContent"), cell(row({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        })));

        expect(handler).not.toHaveBeenCalled();
    });
});
