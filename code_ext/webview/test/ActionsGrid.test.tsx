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
    buildActionColumns,
    clickedOn,
    formatElapsed,
    formatInformationCell,
    formatMessageCell,
    createSeverityIcon,
    informationOf,
    latestRunOf,
    runHolding,
    startsExpanded,
    timeOf,
} from "../src/ActionsGrid.js";
import type { IActionRow } from "../../src/webview/protocol.js";

/**
 * Tabulator needs real layout and never builds under jsdom, so what it
 * would call - the formatters and the jump handler - is exercised
 * directly, with a stand-in for the cell it passes them.
 */

/**
 * @param overrides The fields that differ from a plain info row.
 *
 * @returns An action row.
 */
const row = (overrides: Partial<IActionRow> = {}): IActionRow => {
    return {
        id: "run1-0",
        time: "12:00:00.123",
        connection: "dba@localhost:3310",
        role: "statement",
        statement: "SELECT 1",
        message: "1 row in set",
        kind: "info",
        elapsedMs: 4,
        ...overrides,
    };
};

/**
 * @param overrides The fields that differ from a finished run.
 *
 * @returns The row one execution reads as.
 */
const run = (overrides: Partial<IActionRow> = {}): IActionRow => {
    return {
        id: "run1",
        time: "12:00:00.123",
        connection: "dba@localhost:3310",
        role: "run",
        statement: "",
        message: "Ran 1 statement on dba@localhost:3310",
        summary: "Finished 1 statement successfully",
        kind: "info",
        elapsedMs: 6,
        children: [row()],
        ...overrides,
    };
};

/**
 * @param data The row the cell belongs to.
 * @param value The cell's value.
 *
 * @returns A stand-in for Tabulator's cell component.
 */
const cell = (data: IActionRow, value: unknown = undefined) => {
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
        expect(formatElapsed(0)).toBe("0ms");
        expect(formatElapsed(999)).toBe("999ms");
    });

    it("shows seconds above one", () => {
        expect(formatElapsed(1000)).toBe("1.000s");
        expect(formatElapsed(1234)).toBe("1.234s");
    });
});

describe("timeOf", () => {
    it("puts how long it took after when it started", () => {
        expect(timeOf(row({ time: "20:44:13.431", elapsedMs: 3 })))
            .toBe("20:44:13.431 (3ms)");
    });

    it("says only when a run that is still going started", () => {
        // A (0ms) would claim it had finished.
        expect(timeOf(run({ time: "20:44:13.431", elapsedMs: undefined })))
            .toBe("20:44:13.431");
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

describe("formatMessageCell", () => {
    it("offers a go-to arrow for a row that knows where its statement is",
        () => {
            const rendered = formatMessageCell(cell(row({
                source: { uri: "file:///q.sql", line: 3, character: 0 },
            })), new Set());
            const button = rendered.querySelector("button");

            expect(button?.className).toBe("goToStatement");
            expect(button?.title).toBe("Go to this statement in the editor");
        });

    it("leads with the severity marker", () => {
        const rendered = formatMessageCell(cell(row({ kind: "error" })),
            new Set());

        // In the cell, after the twistie or the branch Tabulator puts
        // in front of it, rather than in a column of its own.
        expect(rendered.firstElementChild?.className)
            .toBe("markerIcon error codicon codicon-error");
    });

    it("stands in for the twistie an event does not have", () => {
        // A top level row with no children gets neither twistie nor
        // branch, so without this its marker would sit where every
        // other row's message does.
        const rendered = formatMessageCell(cell(row({ role: "event" })),
            new Set());

        expect(rendered.firstElementChild?.className).toBe("treeSpacer");
        expect(rendered.querySelector(".markerIcon")).not.toBeNull();
    });

    it("offers a jump where the result set is still on show", () => {
        const rendered = formatMessageCell(
            cell(row({ resultId: "run1-result-0" })),
            new Set(["run1-result-0"]),
        );
        const button = rendered.querySelector("button");

        expect(button?.className).toBe("jumpToResult");
        expect(button?.title).toBe("Show this result set");
    });

    it("offers nothing where the result set has been replaced", () => {
        // A later run replaced the tabs, so this row's result is gone.
        expect(formatMessageCell(
            cell(row({ resultId: "run1-result-0" })),
            new Set(["run2-result-0"]),
        ).querySelector("button")).toBeNull();
    });

    it("offers both arrows, in one place, when there are both", () => {
        const rendered = formatMessageCell(cell(row({
            resultId: "run1-result-0",
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        })), new Set(["run1-result-0"]));

        expect([...rendered.querySelectorAll(".actionArrows button")]
            .map((button) => { return button.className; }))
            .toEqual(["jumpToResult", "goToStatement"]);
    });

    it("offers nothing for a row with neither", () => {
        const rendered = formatMessageCell(cell(row()), new Set());

        expect(rendered.querySelector("button")).toBeNull();
        expect(rendered.textContent).toBe("1 row in set");
        // No arrows, no room given up for them.
        expect(rendered.querySelector(".actionArrows")).toBeNull();
    });

    it("says it goes to the first error on a failed run's row", () => {
        const rendered = formatMessageCell(cell(run({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
            jumpToRowId: "run1-2",
        })), new Set());

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

describe("createSeverityIcon", () => {
    it.each([
        ["info", "codicon-info"],
        ["warning", "codicon-warning"],
        ["error", "codicon-error"],
    ] as const)("draws %s with the Problems panel's glyph",
        (kind, codicon) => {
            const rendered = createSeverityIcon(row({ kind }));

            expect(rendered.className)
                .toBe(`markerIcon ${kind} codicon ${codicon}`);
            expect(rendered.title).toBe(kind);
        });

    it("spins the marker of a run that has not reported back", () => {
        const rendered = createSeverityIcon(
            run({ kind: "pending", summary: "Running\u2026" }));

        expect(rendered.className).toBe(
            "markerIcon pending codicon codicon-loading "
            + "codicon-modifier-spin");
        expect(rendered.title).toBe("running");
    });
});

describe("informationOf", () => {
    it("shows the call an event row stands for", () => {
        // An event has no statement and is no run, so what it did is
        // what the Information column has to hold.
        expect(informationOf(row({
            role: "event",
            statement: "db.list_schemas()",
        }))).toBe("db.list_schemas()");
    });


    it("shows a statement the statement it ran", () => {
        expect(informationOf(row())).toBe("SELECT 1");
    });

    it("shows a run what it came to", () => {
        expect(informationOf(run())).toBe("Finished 1 statement successfully");
        expect(informationOf(run({ summary: undefined }))).toBe("");
    });

    it("writes it as text, not as markup", () => {
        // Tabulator puts a formatter's string into the cell as HTML, and
        // this column holds SQL and server messages.
        const rendered = formatInformationCell(
            cell(row({ statement: "SELECT a <> b FROM t" })));

        expect(rendered.textContent).toBe("SELECT a <> b FROM t");
        expect(rendered.children).toHaveLength(0);
    });
});

describe("latestRunOf", () => {
    it("finds the newest run, not merely the first row", () => {
        // The row in front may be an event: the connection a run opens
        // on the way is reported after the run's own row went up.
        expect(latestRunOf([
            row({ id: "event1", role: "event" }),
            run({ id: "run2" }),
            run({ id: "run1" }),
        ])).toBe("run2");
    });

    it("finds nothing in a log of events alone", () => {
        expect(latestRunOf([row({ id: "event1", role: "event" })]))
            .toBeUndefined();
        expect(latestRunOf([])).toBeUndefined();
    });
});

describe("startsExpanded", () => {
    it("opens the newest run and closes the rest", () => {
        expect(startsExpanded("run2", "run2")).toBe(true);
        expect(startsExpanded("run1", "run2")).toBe(false);
        // A statement row is never a parent, and an empty grid has no
        // newest run to open.
        expect(startsExpanded("run1-0", "run2")).toBe(false);
        expect(startsExpanded("run1", undefined)).toBe(false);
    });
});

describe("runHolding", () => {
    it("finds the run a statement belongs to", () => {
        const rows = [
            run({ id: "run1", children: [row({ id: "run1-0" })] }),
            run({ id: "run2", children: [row({ id: "run2-0" })] }),
        ];

        expect(runHolding(rows, "run2-0")).toBe("run2");
        expect(runHolding(rows, "run3-0")).toBeUndefined();
        // A run that never reported back holds nothing.
        expect(runHolding([run({ children: [] })], "run1-0"))
            .toBeUndefined();
    });
});

describe("buildActionColumns", () => {
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

    it("leads with what happened", () => {
        const columns = buildActionColumns(new Set(), onJump, onGoTo);

        expect(columns.map((column) => {
            return column.title;
        })).toEqual(["Actions", "Time", "Information"]);
        // The twistie, the branch and the marker all ride in this one
        // cell, in front of the message; there is no column of icons.
        expect(columns[0].field).toBe("message");
    });

    it("names the connection last, and only when several are on show",
        () => {
            const withConnection = buildActionColumns(
                new Set(), onJump, onGoTo, true);

            expect(withConnection.map((column) => {
                return column.title;
            })).toEqual(["Actions", "Time", "Information", "Conn"]);
            // Last, where it labels the row without standing between
            // the marker and what it says.
            expect(withConnection.at(-1)?.field).toBe("connectionLabel");
            expect(withConnection.at(-1)?.cssClass).toBe("actionConnection");

            // With one connection picked, a column repeating its name
            // the whole way down says nothing.
            expect(buildActionColumns(new Set(), onJump, onGoTo)
                .some((column) => { return column.title === "Conn"; }))
                .toBe(false);
        });

    it("puts how long it took in the Time column", () => {
        const columns = buildActionColumns(new Set(), onJump, onGoTo);
        const format = columns[1].formatter as
            (cell: CellComponent) => string;

        expect(format(cell(row({ time: "20:44:13.431", elapsedMs: 4 }))))
            .toBe("20:44:13.431 (4ms)");
        expect(format(cell(run({
            time: "20:44:13.431", elapsedMs: undefined,
        })))).toBe("20:44:13.431");
    });

    it("jumps to the result set a row produced", () => {
        const handler = vi.fn();
        const columns = buildActionColumns(
            new Set(["run1-result-0"]), handler, onGoTo);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).toHaveBeenCalledWith("run1-result-0");
    });

    it("does not jump to a result set that is gone", () => {
        const handler = vi.fn();
        const columns = buildActionColumns(
            new Set(["run2-result-0"]), handler, onGoTo);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not jump from a row with no result set", () => {
        const handler = vi.fn();
        const columns = buildActionColumns(new Set(), handler, onGoTo);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("jumpToResult"), cell(row()));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not jump when the click missed the arrow", () => {
        // Both arrows now share the message's cell.
        const handler = vi.fn();
        const columns = buildActionColumns(
            new Set(["run1-result-0"]), handler, onGoTo);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("actionMessageContent"),
            cell(row({ resultId: "run1-result-0" })));

        expect(handler).not.toHaveBeenCalled();
    });

    it("goes to the statement a row came from", () => {
        const handler = vi.fn();
        const columns = buildActionColumns(new Set(), onJump, handler);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;
        const data = row({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        });

        onClick(clickOn("goToStatement"), cell(data));

        expect(handler).toHaveBeenCalledWith(data);
    });

    it("does not go anywhere from a row with no source", () => {
        const handler = vi.fn();
        const columns = buildActionColumns(new Set(), onJump, handler);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("goToStatement"), cell(row()));

        expect(handler).not.toHaveBeenCalled();
    });

    it("does not go anywhere when the click missed the arrow", () => {
        // The arrow now shares its cell with the message.
        const handler = vi.fn();
        const columns = buildActionColumns(new Set(), onJump, handler);
        const onClick = columns[0].cellClick as
            (event: unknown, cell: CellComponent) => void;

        onClick(clickOn("actionMessageContent"), cell(row({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        })));

        expect(handler).not.toHaveBeenCalled();
    });
});
