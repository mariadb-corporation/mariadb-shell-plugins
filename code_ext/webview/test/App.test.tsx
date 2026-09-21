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

import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { posted } from "./setup.js";
import { App, lastErrorOf } from "../src/App.js";
import type {
    HostMessage,
    IOutputRow,
    IViewState,
} from "../../src/webview/protocol.js";

let host: HTMLDivElement;

/**
 * Mounts the app and lets its effects run.
 *
 * `act` is what flushes Preact's effect queue; without it the mount
 * effect that posts `ready` would not have run by the time a test looks.
 *
 * @returns Nothing.
 */
const mount = async (): Promise<void> => {
    await act(async () => {
        render(<App />, host);
        await Promise.resolve();
    });
};

/**
 * Delivers a message from the extension, the way the webview receives it.
 *
 * @param message The message to deliver.
 *
 * @returns Nothing.
 */
const send = async (message: HostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await Promise.resolve();
    });
};

/**
 * Clicks the first button whose label matches.
 *
 * @param matches Picks the button to click.
 *
 * @returns Nothing.
 */
const click = async (
    matches: (label: string) => boolean,
): Promise<void> => {
    const button = [...host.querySelectorAll("button")].find((node) => {
        return matches(node.textContent ?? "");
    });
    if (!button) {
        throw new Error("No button matched.");
    }

    await act(async () => {
        button.click();
        await Promise.resolve();
    });
};

/**
 * @param matches Picks the button to look up.
 *
 * @returns The matching button, if it is on screen.
 */
const button = (
    matches: (label: string) => boolean,
): HTMLButtonElement | undefined => {
    return [...host.querySelectorAll("button")].find((node) => {
        return matches(node.textContent ?? "");
    });
};

/**
 * @returns A view state with one output row and an editable result set.
 */
const report = (): IViewState => {
    return {
        connections: ["dba@localhost:3310", "app@localhost:3311"],
        connection: "dba@localhost:3310",
        output: [{
            id: "run1",
            time: "12:00:00.123",
            connection: "dba@localhost:3310",
            role: "run",
            statement: "",
            message: "Ran 1 statement on dba@localhost:3310",
            summary: "Finished 1 statement successfully",
            kind: "info",
            elapsedMs: 6,
            source: { uri: "file:///q.sql", line: 0, character: 0 },
            children: [{
                id: "run1-0",
                time: "12:00:00.123",
                connection: "dba@localhost:3310",
                role: "statement",
                statement: "CREATE SCHEMA demo",
                message: "Query OK, 1 row affected",
                kind: "info",
                rows: 1,
                elapsedMs: 4,
                source: { uri: "file:///q.sql", line: 0, character: 0 },
            }],
        }],
        resultSets: [{
            id: "run1-result-0",
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
            rows: [{ ID: 1, Name: "Kabul" }, { ID: 2, Name: "Herat" }],
            editable: true,
            target: { schema: "world", table: "city" },
            status: "2 rows in set",
        }],
    };
};

beforeEach(() => {
    posted.length = 0;
    document.body.innerHTML = "";
    host = document.createElement("div");
    document.body.append(host);
});

afterEach(() => {
    render(null, host);
});

describe("lastErrorOf", () => {
    /**
     * @param overrides The fields that differ from a clean run.
     *
     * @returns One run of the output.
     */
    const run = (overrides: Partial<IOutputRow> = {}): IOutputRow => {
        return {
            id: "run1",
            time: "12:00:00.123",
            connection: "dba@localhost:3310",
            role: "run",
            statement: "",
            message: "Ran 1 statement on dba@localhost:3310",
            summary: "Finished 1 statement successfully",
            kind: "info",
            children: [],
            ...overrides,
        };
    };

    it("says nothing about a run that worked", () => {
        expect(lastErrorOf([])).toBeUndefined();
        expect(lastErrorOf([run()])).toBeUndefined();
    });

    it("reports what the server said, not the count of errors", () => {
        expect(lastErrorOf([run({
            kind: "error",
            summary: "Finished with 1 error",
            children: [{
                ...run({ id: "run1-0", role: "statement" }),
                kind: "error",
                message: "Table 'nope.nope' doesn't exist",
            }],
        })])).toBe("Table 'nope.nope' doesn't exist");
    });

    it("falls back to the run's own summary", () => {
        // A run that failed before it could blame a statement: the
        // connection was never opened.
        expect(lastErrorOf([run({
            kind: "error",
            summary: "Execution failed: Access denied",
            children: [],
        })])).toBe("Execution failed: Access denied");
    });

    it("leaves an earlier run's error behind", () => {
        // The bar says what just happened; a run that worked clears it.
        expect(lastErrorOf([
            run({ kind: "error", summary: "Finished with 1 error" }),
            run({ id: "run2" }),
        ])).toBeUndefined();
    });
});

describe("App", () => {
    it("tells the extension it is listening", async () => {
        await mount();

        expect(posted).toEqual([{ type: "ready" }]);
    });

    it("invites the user to run something before anything has run",
        async () => {
            await mount();

            expect(host.textContent).toContain("Run a .sql file");
        });

    it("shows a run that has only just started on the output tab",
        async () => {
            // The run is a row of the output from the moment it starts,
            // rather than a placeholder over the whole view, so what the
            // connection did before it is still there to read.
            await mount();
            const pending = report();
            pending.output.push({
                id: "run2",
                time: "12:00:01.000",
                connection: "dba@localhost:3310",
                role: "run",
                statement: "",
                message: "Running 1 statement on dba@localhost:3310",
                summary: "Running\u2026",
                kind: "pending",
                children: [],
            });
            pending.resultSets = [];

            await send({ type: "state", state: pending });

            expect(host.querySelector(".tab.active")?.textContent)
                .toContain("Output");
            expect(host.querySelector(".errorBar")).toBeNull();
        });

    it("puts the tabs at the bottom, after the content", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const panel = host.querySelector(".panel");
        const children = [...(panel?.children ?? [])];
        // content, then the bar holding the tabs and the toolbar.
        expect(children.map((node) => {
            return node.tagName.toLowerCase();
        })).toEqual(["section", "footer"]);
        expect(children[1].querySelector(".tabs")).not.toBeNull();
    });

    it("names one tab per result set, beside the output tab", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const tabs = [...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        });
        // The badge counts runs, which is what the output now holds.
        expect(tabs).toEqual(["Output1", "Result #1"]);
    });

    it("opens on the result set when the script produced rows", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const active = host.querySelector(".tab.active");
        expect(active?.textContent).toBe("Result #1");
    });

    it("stays on the output tab when nothing returned rows", async () => {
        await mount();
        await send({
            type: "state",
            state: { ...report(), resultSets: [] },
        });

        expect(host.querySelector(".tab.active")?.textContent)
            .toBe("Output1");
        // Tabulator renders nothing under jsdom, so only the mount is
        // checkable here; the rows themselves are covered by the
        // OutputGrid tests.
        expect(host.querySelector(".outputGridHost")).not.toBeNull();
    });

    it("puts the connection picker at the far left of the bottom bar",
        async () => {
            await mount();
            await send({ type: "state", state: report() });

            const footer = host.querySelector(".statusBar");
            expect(footer?.firstElementChild?.className)
                .toBe("connectionPicker");
            expect([...host.querySelectorAll<HTMLOptionElement>(
                ".connectionPicker option")].map((option) => {
                return option.value;
            })).toEqual(["dba@localhost:3310", "app@localhost:3311"]);
        });

    it("asks the host to switch connections when one is picked",
        async () => {
            await mount();
            await send({ type: "state", state: report() });
            const picker = host.querySelector<HTMLSelectElement>(
                ".connectionPicker");

            await act(async () => {
                picker!.value = "app@localhost:3311";
                picker!.dispatchEvent(new Event("change", {
                    bubbles: true,
                }));
                await Promise.resolve();
            });

            expect(posted.at(-1)).toEqual({
                type: "selectConnection",
                connection: "app@localhost:3311",
            });
        });

    it("leaves clearing to the view's toolbar", async () => {
        await mount();
        await send({ type: "state", state: report() });

        // Clear Output is a button in the panel's own title bar now.
        expect(button((label) => {
            return label === "Clear Output";
        })).toBeUndefined();
    });

    it("shows the connection the host switched to", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await send({
            type: "state",
            state: {
                connections: [
                    "dba@localhost:3310", "app@localhost:3311",
                ],
                connection: "app@localhost:3311",
                output: [],
                resultSets: [],
            },
        });

        // The other connection's tabs went with it.
        expect([...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        })).toEqual(["Output"]);
    });

    it("shows nothing once the host has cleared the view", async () => {
        await mount();
        await send({ type: "state", state: report() });
        expect(host.querySelectorAll(".tab")).toHaveLength(2);

        // What clearing sends back: no output and no result sets.
        await send({
            type: "state",
            state: { ...report(), output: [], resultSets: [] },
        });

        // Only the output tab is left, and it has no badge.
        expect([...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        })).toEqual(["Output"]);
    });

    it("mounts the grid for a result set", async () => {
        await mount();
        await send({ type: "state", state: report() });

        // Only that the grid is mounted and Tabulator was constructed
        // without throwing can be checked here: Tabulator measures the
        // DOM to lay itself out, and jsdom reports every element as zero
        // sized, so it never finishes building and renders nothing.
        expect(host.querySelector(".resultGridHost")).not.toBeNull();
        expect(host.querySelector(".sqlPreview")).toBeNull();
    });

    it("switches to the SQL preview and back", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await click((label) => {
            return label.includes("Preview SQL");
        });

        expect(host.querySelector(".sqlPreview")).not.toBeNull();
        expect(host.textContent).toContain("No changes to preview.");

        await click((label) => {
            return label.includes("Grid");
        });

        expect(host.querySelector(".sqlPreview")).toBeNull();
    });

    it("previews the SQL an added row would run", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await click((label) => {
            return label.includes("+ Row");
        });
        await click((label) => {
            return label.includes("Preview SQL");
        });

        // Generated by the same builder the extension executes with.
        expect(host.querySelector("code")?.textContent)
            .toBe("INSERT INTO `world`.`city` (`Name`) VALUES (NULL);");
    });

    it("enables Apply only once there is something to apply", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const applyOf = () => {
            return button((label) => {
                return label.startsWith("Apply");
            });
        };
        expect(applyOf()?.disabled).toBe(true);

        await click((label) => {
            return label.includes("+ Row");
        });

        expect(applyOf()?.disabled).toBe(false);
        expect(applyOf()?.textContent).toBe("Apply (1)");
    });

    it("sends the changes when Apply is pressed", async () => {
        await mount();
        await send({ type: "state", state: report() });
        await click((label) => {
            return label.includes("+ Row");
        });
        await click((label) => {
            return label.startsWith("Apply");
        });

        expect(posted.at(-1)).toEqual({
            type: "applyChanges",
            resultId: "run1-result-0",
            changes: [{
                kind: "insert",
                rowIndex: 2,
                values: { ID: null, Name: null },
            }],
        });
    });

    it("discards the pending changes on Revert", async () => {
        await mount();
        await send({ type: "state", state: report() });
        await click((label) => {
            return label.includes("+ Row");
        });
        await click((label) => {
            return label === "Revert";
        });

        expect(button((label) => {
            return label.startsWith("Apply");
        })?.disabled).toBe(true);
    });

    it("asks for a reload when Refresh is pressed", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await click((label) => {
            return label === "Refresh";
        });

        expect(posted.at(-1))
            .toEqual({ type: "refresh", resultId: "run1-result-0" });
    });

    it("shows an apply failure at the very bottom and opens the preview",
        async () => {
            await mount();
            await send({ type: "state", state: report() });

            await send({
                type: "applied",
                resultId: "run1-result-0",
                statements: [],
                error: "Duplicate entry '1' for key 'PRIMARY'",
            });

            const panel = host.querySelector(".panel");
            const last = panel?.lastElementChild;
            expect(last?.classList.contains("errorBar")).toBe(true);
            expect(last?.textContent)
                .toBe("Duplicate entry '1' for key 'PRIMARY'");
            // The failing statement is in the preview, so that is where
            // the user is taken.
            expect(host.querySelector(".sqlPreview")).not.toBeNull();
        });

    it("reloads the grid after a successful apply", async () => {
        await mount();
        await send({ type: "state", state: report() });
        posted.length = 0;

        await send({
            type: "applied",
            resultId: "run1-result-0",
            statements: ["UPDATE `world`.`city` SET `Name` = 'x'"],
        });

        expect(host.textContent).toContain("Applied 1 statement.");
        expect(posted).toEqual([
            { type: "refresh", resultId: "run1-result-0" },
        ]);
        expect(host.querySelector(".errorBar")).toBeNull();
    });

    it("shows a failed execution in the error bar", async () => {
        await mount();

        await send({
            type: "state",
            state: {
                connections: ["dba@localhost:3310"],
                connection: "dba@localhost:3310",
                output: [{
                    id: "run1",
                    time: "12:00:00.123",
                    connection: "dba@localhost:3310",
                    role: "run",
                    statement: "",
                    message: "Ran 1 statement on dba@localhost:3310",
                    summary: "Finished with 1 error",
                    kind: "error",
                    elapsedMs: 2,
                    children: [{
                        id: "run1-0",
                        time: "12:00:00.123",
                        connection: "dba@localhost:3310",
                        role: "statement",
                        statement: "SELECT * FROM nope.nope",
                        message: "Table 'nope.nope' doesn't exist",
                        kind: "error",
                        elapsedMs: 2,
                    }],
                }],
                resultSets: [],
            },
        });

        expect(host.querySelector(".errorBar")?.textContent)
            .toBe("Table 'nope.nope' doesn't exist");
    });

    it("marks a read-only result set and offers no editing", async () => {
        await mount();
        const readOnly = report();
        readOnly.resultSets[0] = {
            ...readOnly.resultSets[0],
            editable: false,
            target: undefined,
            readOnlyReason: "Read only: not a single table.",
        };
        await send({ type: "state", state: readOnly });

        expect(host.querySelector(".readOnly")?.textContent).toBe("read only");
        const labels = [...host.querySelectorAll("button")].map((node) => {
            return node.textContent;
        });
        expect(labels.some((label) => {
            return label?.startsWith("Apply");
        })).toBe(false);
        expect(labels).toContain("Refresh");
    });
});
