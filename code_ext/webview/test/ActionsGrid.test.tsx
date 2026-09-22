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
    actionClick,
    buildActionColumns,
    clickedOn,
    formatElapsed,
    formatInformationCell,
    formatMessageCell,
    createSeverityIcon,
    informationOf,
    expandedIdsOf,
    runHolding,
    startsExpanded,
    timeOf,
} from "../src/ActionsGrid.js";
import type { IActionRow } from "../../src/webview/protocol.js";
import { closeOverflowPopup } from "../src/overflowPopup.js";

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

/** Everything the cells were asked to copy, newest last. */
const copied: string[] = [];

/**
 * @param text What a cell's copy button was given.
 *
 * @returns Nothing.
 */
const onCopy = (text: string): void => {
    copied.push(text);
};

describe("formatMessageCell", () => {
    it("offers a go-to arrow for a row that knows where its statement is",
        () => {
            const rendered = formatMessageCell(cell(row({
                source: { uri: "file:///q.sql", line: 3, character: 0 },
            })), new Set(), onCopy);
            const button = rendered.querySelector("button");

            expect(button?.className).toBe("goToStatement");
            expect(button?.title).toBe("Go to this statement in the editor");
        });

    it("leads with the severity marker", () => {
        const rendered = formatMessageCell(cell(row({ kind: "error" })),
            new Set(), onCopy);

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
            new Set(), onCopy);

        expect(rendered.firstElementChild?.className).toBe("treeSpacer");
        expect(rendered.querySelector(".markerIcon")).not.toBeNull();
    });

    it("gives a cut-off message a popup with the whole of it", () => {
        const rendered = formatMessageCell(cell(row({
            message: "Query OK, 1 row affected, 3 warnings",
        })), new Set(), onCopy);
        const content = rendered.querySelector<HTMLElement>(
            ".actionMessageContent");
        Object.defineProperty(content, "scrollWidth",
            { configurable: true, value: 400 });
        Object.defineProperty(content, "clientWidth",
            { configurable: true, value: 120 });
        document.body.appendChild(rendered);

        content?.dispatchEvent(new Event("mouseenter"));

        expect(document.querySelector(".overflowPopup")?.textContent)
            .toContain("Query OK, 1 row affected, 3 warnings");

        closeOverflowPopup();
        rendered.remove();
    });

    it("leaves an error to wrap instead of hiding it behind a popup", () => {
        const rendered = formatMessageCell(cell(row({
            kind: "error",
            message: "MySQL Error (1064): You have an error in your syntax",
        })), new Set(), onCopy);
        const content = rendered.querySelector<HTMLElement>(
            ".actionMessageContent");
        // Measured as cut off, which it would not be: the row grows to
        // hold it. Nothing should open even so.
        Object.defineProperty(content, "scrollWidth",
            { configurable: true, value: 400 });
        Object.defineProperty(content, "clientWidth",
            { configurable: true, value: 120 });
        document.body.appendChild(rendered);

        content?.dispatchEvent(new Event("mouseenter"));

        expect(document.querySelector(".overflowPopup")).toBeNull();

        rendered.remove();
    });

    it("offers a jump where the result set is still on show", () => {
        const rendered = formatMessageCell(
            cell(row({ resultId: "run1-result-0" })),
            new Set(["run1-result-0"]),
            onCopy,
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
            onCopy,
        ).querySelector("button")).toBeNull();
    });

    it("offers both arrows, in one place, when there are both", () => {
        const rendered = formatMessageCell(cell(row({
            resultId: "run1-result-0",
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        })), new Set(["run1-result-0"]), onCopy);

        expect([...rendered.querySelectorAll(".actionArrows button")]
            .map((button) => { return button.className; }))
            .toEqual(["jumpToResult", "goToStatement"]);
    });

    it("offers nothing for a row with neither", () => {
        const rendered = formatMessageCell(cell(row()), new Set(), onCopy);

        expect(rendered.querySelector("button")).toBeNull();
        expect(rendered.textContent).toBe("1 row in set");
        // No arrows, no room given up for them.
        expect(rendered.querySelector(".actionArrows")).toBeNull();
    });

    it("says it goes to the first error on a failed run's row", () => {
        const rendered = formatMessageCell(cell(run({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
            jumpToRowId: "run1-2",
        })), new Set(), onCopy);

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
            cell(row({ statement: "SELECT a <> b FROM t" })), onCopy);

        expect(rendered.textContent).toBe("SELECT a <> b FROM t");
        expect(rendered.children).toHaveLength(0);
    });
});

describe("expandedIdsOf", () => {
    it("opens the newest run, not merely the first row", () => {
        // The row in front may be an event: the connection a run opens
        // on the way is reported after the run's own row went up.
        expect([...expandedIdsOf([
            row({ id: "event1", role: "event" }),
            run({ id: "run2" }),
            run({ id: "run1" }),
        ])]).toEqual(["run2"]);
    });

    it("opens the statements of that run that carry warnings", () => {
        const warned = row({
            id: "run2-1",
            kind: "warning",
            children: [row({ id: "run2-1-warning-0", role: "warning" })],
        });

        // The plain statement beside it stays shut: only the ones with
        // something under them open, so the run does not unroll whole.
        expect([...expandedIdsOf([
            run({ id: "run2", children: [row({ id: "run2-0" }), warned] }),
            run({ id: "run1", children: [{ ...warned, id: "run1-1" }] }),
        ])]).toEqual(["run2", "run2-1"]);
    });

    it("opens nothing in a log of events alone", () => {
        expect(expandedIdsOf([row({ id: "event1", role: "event" })]).size)
            .toBe(0);
        expect(expandedIdsOf([]).size).toBe(0);
    });
});

describe("startsExpanded", () => {
    it("opens what expandedIdsOf named and closes the rest", () => {
        const open = new Set(["run2", "run2-1"]);

        expect(startsExpanded("run2", open)).toBe(true);
        expect(startsExpanded("run2-1", open)).toBe(true);
        expect(startsExpanded("run1", open)).toBe(false);
        // Tabulator hands an index of whatever type the data carries.
        expect(startsExpanded(2, open)).toBe(false);
        expect(startsExpanded("run2", new Set())).toBe(false);
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
    /**
     * @returns A click on a control of the given class.
     */
    const clickOn = (className: string): Event => {
        const button = document.createElement("button");
        button.className = className;

        return click(button);
    };

    it("leads with what happened", () => {
        const columns = buildActionColumns(new Set(), onCopy);

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
                new Set(), onCopy, true);

            expect(withConnection.map((column) => {
                return column.title;
            })).toEqual(["Actions", "Time", "Information", "Conn"]);
            // Last, where it labels the row without standing between
            // the marker and what it says.
            expect(withConnection.at(-1)?.field).toBe("connectionLabel");
            expect(withConnection.at(-1)?.cssClass).toBe("actionConnection");

            // With one connection picked, a column repeating its name
            // the whole way down says nothing.
            expect(buildActionColumns(new Set(), onCopy)
                .some((column) => { return column.title === "Conn"; }))
                .toBe(false);
        });

    it("puts how long it took in the Time column", () => {
        const columns = buildActionColumns(new Set(), onCopy);
        const format = columns[1].formatter as
            (cell: CellComponent) => string;

        expect(format(cell(row({ time: "20:44:13.431", elapsedMs: 4 }))))
            .toBe("20:44:13.431 (4ms)");
        expect(format(cell(run({
            time: "20:44:13.431", elapsedMs: undefined,
        })))).toBe("20:44:13.431");
    });

    it("takes a jump arrow as a jump to the result set", () => {
        expect(actionClick(
            clickOn("jumpToResult"),
            row({ resultId: "run1-result-0" }),
            new Set(["run1-result-0"]),
        )).toBe("jump");
    });

    it("does not jump to a result set that is gone", () => {
        // A later run replaced the tabs. The click still counts as one
        // on the row, which is where it landed.
        expect(actionClick(
            clickOn("jumpToResult"),
            row({
                resultId: "run1-result-0",
                source: { uri: "file:///q.sql", line: 3, character: 0 },
            }),
            new Set(["run2-result-0"]),
        )).toBe("goTo");
    });

    it("takes a click anywhere else on the row to the statement", () => {
        // The whole row is the target, as it is in the Problems panel -
        // not the arrow alone, which is only what says so.
        const data = row({
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        });

        expect(actionClick(clickOn("actionMessageContent"), data, new Set()))
            .toBe("goTo");
        expect(actionClick(clickOn("actionTime"), data, new Set()))
            .toBe("goTo");
        expect(actionClick(clickOn("goToStatement"), data, new Set()))
            .toBe("goTo");
    });

    it("takes a run's row to its first error, as its arrow does", () => {
        expect(actionClick(clickOn("actionMessageContent"), run({
            source: { uri: "file:///q.sql", line: 0, character: 0 },
            jumpToRowId: "run1-2",
        }), new Set())).toBe("goTo");
    });

    it("does nothing on a row that knows nowhere to go", () => {
        // An event has no statement behind it.
        expect(actionClick(clickOn("actionMessageContent"),
            row({ role: "event", source: undefined }), new Set()))
            .toBeUndefined();
    });

});
