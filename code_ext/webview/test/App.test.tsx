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
import { App, lastErrorOf, pagingOf } from "../src/App.js";
import type {
    HostMessage,
    IActionRow,
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
 * Says what a scrolling strip measures and lets the page read it.
 *
 * jsdom lays nothing out, so the sizes the paging buttons follow have
 * to be stated; the scroll event is what the page listens to.
 *
 * @param strip The strip to measure.
 * @param sizes What it should report.
 *
 * @returns Nothing.
 */
const measure = async (
    strip: HTMLElement,
    sizes: { scrollLeft: number; scrollWidth: number; clientWidth?: number },
): Promise<void> => {
    for (const [name, value] of Object.entries({
        clientWidth: sizes.clientWidth ?? 100,
        scrollWidth: sizes.scrollWidth,
        scrollLeft: sizes.scrollLeft,
    })) {
        Object.defineProperty(strip, name, {
            configurable: true,
            value,
        });
    }

    await act(async () => {
        strip.dispatchEvent(new Event("scroll"));
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
 * @returns A view state with one action row and an editable result set.
 */
const report = (): IViewState => {
    return {
        connections: ["dba@localhost:3310", "app@localhost:3311"],
        connection: "dba@localhost:3310",
        sessions: [{ label: "1", open: true }],
        actions: [{
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
        // The bar says what just happened; a run that worked clears
        // it. The newest is the first, so that is the one it reads.
        expect(lastErrorOf([
            run({ id: "run2" }),
            run({ kind: "error", summary: "Finished with 1 error" }),
        ])).toBeUndefined();
    });
});

describe("pagingOf", () => {
    it("says nothing is to be paged when the tabs all fit", () => {
        expect(pagingOf({
            scrollLeft: 0, scrollWidth: 100, clientWidth: 100,
        })).toEqual({ overflowing: false, atStart: true, atEnd: true });
    });

    it("ignores a fraction of a pixel of overflow", () => {
        // A fractional layout leaves the scrolled width a hair over the
        // visible one, which is not something to offer paging for.
        expect(pagingOf({
            scrollLeft: 0, scrollWidth: 100.5, clientWidth: 100,
        }).overflowing).toBe(false);
    });

    it("says which way there is more to see", () => {
        expect(pagingOf({
            scrollLeft: 0, scrollWidth: 300, clientWidth: 100,
        })).toEqual({ overflowing: true, atStart: true, atEnd: false });
        expect(pagingOf({
            scrollLeft: 100, scrollWidth: 300, clientWidth: 100,
        })).toEqual({ overflowing: true, atStart: false, atEnd: false });
        expect(pagingOf({
            scrollLeft: 200, scrollWidth: 300, clientWidth: 100,
        })).toEqual({ overflowing: true, atStart: false, atEnd: true });
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

    it("shows a run that has only just started on the Actions tab",
        async () => {
            // The run is a row of the output from the moment it starts,
            // rather than a placeholder over the whole view, so what the
            // connection did before it is still there to read.
            await mount();
            const pending = report();
            pending.actions.push({
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
                .toContain("Actions");
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

    it("marks the tab on show with a line under it, not a surface",
        async () => {
            await mount();
            await send({ type: "state", state: report() });

            // The panel's own container tabs are selected by surface,
            // so two selections in one corner cannot be read as one.
            const active = host.querySelector(".tab.active");
            expect(active?.textContent).toBe("Result #1");
            expect([...host.querySelectorAll(".tab.active")])
                .toHaveLength(1);
        });

    it("gives a result set its own bar, at the bottom of it", async () => {
        await mount();
        await send({ type: "state", state: report() });

        // Inside the tab's own content, under the grid - not in the
        // row of tabs, which picks what is on show and nothing else.
        const content = host.querySelector(".content");
        const bar = content?.querySelector(".statusBar");
        expect(bar).not.toBeNull();
        expect(content?.lastElementChild).toBe(bar);
        expect(bar?.querySelector(".status")?.textContent)
            .toBe("2 rows in set");
        expect(bar?.querySelector(".toolbar")).not.toBeNull();
    });

    it("gives the Actions tab no bar of its own", async () => {
        await mount();
        await send({
            type: "state",
            state: { ...report(), resultSets: [] },
        });

        // There is no result set for one to be about.
        expect(host.querySelector(".statusBar")).toBeNull();
    });

    it("keeps the Actions tab out of the strip that scrolls", async () => {
        await mount();
        await send({ type: "state", state: report() });

        // It is what a result tab is gone back to, so it is always
        // there to be clicked.
        const tabs = host.querySelector(".tabs");
        expect(tabs?.firstElementChild?.textContent).toContain("Actions");
        expect(host.querySelector(".resultTabs")?.textContent)
            .toBe("Result #1");
    });

    it("names one tab per result set, beside the Actions tab", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const tabs = [...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        });
        // The badge counts runs, which is what the output now holds.
        expect(tabs).toEqual(["Actions1", "Result #1"]);
    });

    it("opens on the result set when the script produced rows", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const active = host.querySelector(".tab.active");
        expect(active?.textContent).toBe("Result #1");
    });

    it("stays on the Actions tab when nothing returned rows", async () => {
        await mount();
        await send({
            type: "state",
            state: { ...report(), resultSets: [] },
        });

        expect(host.querySelector(".tab.active")?.textContent)
            .toBe("Actions1");
        // Tabulator renders nothing under jsdom, so only the mount is
        // checkable here; the rows themselves are covered by the
        // ActionsGrid tests.
        expect(host.querySelector(".actionsGridHost")).not.toBeNull();
    });

    it("puts the two pickers at the far right of the bottom bar",
        async () => {
            await mount();
            await send({ type: "state", state: report() });

            const footer = host.querySelector(".contentSelectionBar");
            // The bar picks what is on show and holds nothing else:
            // the tabs, then the two pickers at the far right.
            expect([...(footer?.children ?? [])].map((node) => {
                return node.className;
            })).toEqual(["tabs", "connectionPicker", "sessionPicker"]);
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

    it("puts the session picker beside the connection picker", async () => {
        await mount();
        await send({
            type: "state",
            state: {
                ...report(),
                sessions: [
                    { label: "1", open: true },
                    { label: "UI Backend", open: false },
                ],
            },
        });

        const pickers = [...host.querySelectorAll(
            ".contentSelectionBar > select")];
        expect(pickers.map((node) => { return node.className; }))
            .toEqual(["connectionPicker", "sessionPicker"]);
        // The very last things in the bar.
        expect(host.querySelector(".contentSelectionBar")
            ?.lastElementChild?.className).toBe("sessionPicker");
        // All of them together first, which is what the view opens on,
        // and a connection that has been closed says so.
        expect([...host.querySelectorAll<HTMLOptionElement>(
            ".sessionPicker option")].map((option) => {
            return [option.value, option.textContent];
        })).toEqual([
            ["", "All Sessions"],
            ["1", "1"],
            ["UI Backend", "UI Backend (closed)"],
        ]);
        expect(host.querySelector<HTMLSelectElement>(
            ".sessionPicker")?.value).toBe("");
    });

    it("offers paging only when the result tabs do not fit", async () => {
        await mount();
        await send({ type: "state", state: report() });
        const strip = host.querySelector<HTMLDivElement>(".resultTabs");

        // jsdom reports every element as zero sized, so what the strip
        // measures is said here instead.
        expect(host.querySelectorAll(".tabPager")).toHaveLength(0);

        await measure(strip!, { scrollLeft: 0, scrollWidth: 500 });

        const pagers = [...host.querySelectorAll<HTMLButtonElement>(
            ".tabPager")];
        expect(pagers).toHaveLength(2);
        // At the start there is nothing before these.
        expect(pagers[0].disabled).toBe(true);
        expect(pagers[1].disabled).toBe(false);

        await measure(strip!, { scrollLeft: 400, scrollWidth: 500 });

        const atEnd = [...host.querySelectorAll<HTMLButtonElement>(
            ".tabPager")];
        expect(atEnd[0].disabled).toBe(false);
        expect(atEnd[1].disabled).toBe(true);
    });

    it("pages the strip by most of its width", async () => {
        await mount();
        await send({ type: "state", state: report() });
        const strip = host.querySelector<HTMLDivElement>(".resultTabs");
        await measure(strip!, { scrollLeft: 200, scrollWidth: 500 });
        const scrolled: Array<Record<string, unknown>> = [];
        strip!.scrollBy = (options: unknown) => {
            scrolled.push(options as Record<string, unknown>);
        };

        const pagers = [...host.querySelectorAll<HTMLButtonElement>(
            ".tabPager")];
        await act(async () => {
            pagers[1].click();
            await Promise.resolve();
        });
        await act(async () => {
            pagers[0].click();
            await Promise.resolve();
        });

        expect(scrolled).toEqual([
            { left: 80, behavior: "smooth" },
            { left: -80, behavior: "smooth" },
        ]);
    });

    it("offers no session picker where nothing is open", async () => {
        await mount();
        await send({
            type: "state",
            state: { ...report(), sessions: [] },
        });

        expect(host.querySelector(".sessionPicker")).toBeNull();
    });

    it("asks the host to narrow the output to one connection",
        async () => {
            await mount();
            await send({ type: "state", state: report() });
            const picker = host.querySelector<HTMLSelectElement>(
                ".sessionPicker");

            await act(async () => {
                picker!.value = "1";
                picker!.dispatchEvent(new Event("change", {
                    bubbles: true,
                }));
                await Promise.resolve();
            });

            expect(posted.at(-1))
                .toEqual({ type: "selectSession", session: "1" });
        });

    it("asks the host for every connection again", async () => {
        await mount();
        await send({
            type: "state",
            state: { ...report(), session: "1" },
        });
        const picker = host.querySelector<HTMLSelectElement>(
            ".sessionPicker");

        await act(async () => {
            picker!.value = "";
            picker!.dispatchEvent(new Event("change", { bubbles: true }));
            await Promise.resolve();
        });

        expect(posted.at(-1))
            .toEqual({ type: "selectSession", session: undefined });
    });

    it("keeps pending edits when something else happens on the "
        + "connection", async () => {
            await mount();
            await send({ type: "state", state: report() });
            await click((label) => { return label.startsWith("+ Row"); });
            expect(button((label) => {
                return label.startsWith("Apply");
            })?.disabled).toBe(false);

            // A schema listed in the tree sends state like anything
            // else, and the grid is still being edited.
            await send({
                type: "state",
                state: {
                    ...report(),
                    actions: [...report().actions, {
                        id: "event1",
                        time: "12:00:01.000",
                        connection: "dba@localhost:3310",
                        connectionLabel: "UI Backend",
                        role: "event",
                        statement: "db.list_schemas()",
                        message: "Listed 2 schemas",
                        kind: "info",
                    }],
                },
            });

            expect(button((label) => {
                return label.startsWith("Apply");
            })?.disabled).toBe(false);
        });

    it("leaves clearing to the view's toolbar", async () => {
        await mount();
        await send({ type: "state", state: report() });

        // Clear Actions is a button in the panel's own title bar now.
        expect(button((label) => {
            return label === "Clear Actions";
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
                sessions: [],
                actions: [],
                resultSets: [],
            },
        });

        // The other connection's tabs went with it.
        expect([...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        })).toEqual(["Actions"]);
    });

    it("shows nothing once the host has cleared the view", async () => {
        await mount();
        await send({ type: "state", state: report() });
        expect(host.querySelectorAll(".tab")).toHaveLength(2);

        // What clearing sends back: no output and no result sets.
        await send({
            type: "state",
            state: { ...report(), actions: [], resultSets: [] },
        });

        // Only the Actions tab is left, and it has no badge.
        expect([...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        })).toEqual(["Actions"]);
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

    it("shows an apply failure at the very top and opens the preview",
        async () => {
            await mount();
            await send({ type: "state", state: report() });

            await send({
                type: "applied",
                resultId: "run1-result-0",
                statements: [],
                error: "Duplicate entry '1' for key 'PRIMARY'",
            });

            // Above what it is about, where it is read before the eye
            // has gone looking for what went wrong.
            const panel = host.querySelector(".panel");
            const first = panel?.firstElementChild;
            expect(first?.classList.contains("errorBar")).toBe(true);
            expect(first?.textContent)
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
                sessions: [{ label: "1", open: true }],
                actions: [{
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
