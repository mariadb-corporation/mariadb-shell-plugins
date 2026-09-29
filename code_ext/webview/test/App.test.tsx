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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { posted } from "./setup.js";
import { App, errorsOf, pagingOf } from "../src/App.js";
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
 * @param label The accessible name of an icon-only button.
 *
 * @returns The button, if it is on screen.
 */
const iconButton = (label: string): HTMLButtonElement | undefined => {
    return host.querySelector<HTMLButtonElement>(
        `button[aria-label="${label}"]`) ?? undefined;
};

/**
 * Clicks an icon-only button by its accessible name.
 *
 * @param label The button's aria-label.
 *
 * @returns Nothing.
 */
const clickIcon = async (label: string): Promise<void> => {
    const target = iconButton(label);
    if (!target) {
        throw new Error(`No "${label}" button.`);
    }

    await act(async () => {
        target.click();
        await Promise.resolve();
    });
};

/**
 * Adds a row with the toolbar's button, as the user would.
 *
 * @returns Nothing.
 */
const addRow = async (): Promise<void> => {
    await clickIcon("Add New Row");
};

/**
 * @returns What the result set's bar says on its left.
 */
const statusText = (): string | null | undefined => {
    return host.querySelector(".statusBar .status")?.textContent;
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

describe("errorsOf", () => {
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

    /**
     * @param id The statement row's id.
     * @param message What the server said about it.
     *
     * @returns A failed statement of a run.
     */
    const failed = (id: string, message: string): IActionRow => {
        return {
            ...run({ id, role: "statement" }),
            kind: "error",
            message,
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        };
    };

    it("says nothing about a run that worked", () => {
        expect(errorsOf([])).toEqual([]);
        expect(errorsOf([run()])).toEqual([]);
    });

    it("reports what the server said, not the count of errors", () => {
        expect(errorsOf([run({
            kind: "error",
            summary: "Finished with 1 error",
            children: [failed("run1-0", "Table 'nope.nope' doesn't exist")],
        })])).toEqual([{
            message: "Table 'nope.nope' doesn't exist",
            rowId: "run1-0",
            source: { uri: "file:///q.sql", line: 3, character: 0 },
        }]);
    });

    it("reports every failure, in the order the script hit them", () => {
        // Which is the order they are worth being walked in: the first
        // is often what caused the rest.
        expect(errorsOf([run({
            kind: "error",
            summary: "Finished with 3 errors",
            children: [
                failed("run1-0", "Unknown database 'nope'"),
                { ...run({ id: "run1-1", role: "statement" }), kind: "info" },
                failed("run1-2", "Table 'nope.a' doesn't exist"),
                failed("run1-3", "Table 'nope.b' doesn't exist"),
            ],
        })]).map((error) => {
            return [error.rowId, error.message];
        })).toEqual([
            ["run1-0", "Unknown database 'nope'"],
            ["run1-2", "Table 'nope.a' doesn't exist"],
            ["run1-3", "Table 'nope.b' doesn't exist"],
        ]);
    });

    it("falls back to the run's own summary", () => {
        // A run that failed before it could blame a statement: the
        // connection was never opened.
        expect(errorsOf([run({
            kind: "error",
            summary: "Execution failed: Access denied",
            children: [],
        })])).toEqual([{
            message: "Execution failed: Access denied",
            rowId: "run1",
            source: undefined,
        }]);
    });

    it("finds the run under the event that opening a connection logs", () => {
        // The first execution on a connection has to open it, and that
        // is logged AFTER the run's own row went up - so it lands above
        // it. Reading only the newest row meant the bar stayed empty on
        // a first run and appeared on the second, the connection being
        // open by then.
        expect(errorsOf([
            { ...run({ id: "connect", role: "event" }), kind: "info" },
            run({
                kind: "error",
                summary: "Finished with 1 error",
                children: [failed("run1-0", "You have an error in your SQL")],
            }),
        ]).map((error) => {
            return error.message;
        })).toEqual(["You have an error in your SQL"]);
    });

    it("reports an event that failed on its own", () => {
        // A connection that could not be opened has no run to blame and
        // says it itself.
        expect(errorsOf([{
            ...run({ id: "connect", role: "event" }),
            kind: "error",
            message: "Access denied for user 'dba'",
        }])).toEqual([{
            message: "Access denied for user 'dba'",
            rowId: "connect",
            source: undefined,
        }]);
    });

    it("leaves an earlier run's errors behind", () => {
        // The bar says what just happened; a run that worked clears
        // it. The newest is the first, so that is the one it reads.
        expect(errorsOf([
            run({ id: "run2" }),
            run({ kind: "error", summary: "Finished with 1 error" }),
        ])).toEqual([]);
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

    it("does not drag the active tab back while the strip is paged",
        async () => {
            await mount();
            const state = report();
            const first = state.resultSets[0];
            await send({
                type: "state",
                state: {
                    ...state,
                    resultSets: [first, {
                        ...first,
                        id: "run1-result-1",
                        caption: "Result #2",
                    }],
                },
            });

            const scrolledTo: string[] = [];
            const original = Element.prototype.scrollIntoView;
            Element.prototype.scrollIntoView = function record(
                this: Element,
            ): void {
                scrolledTo.push(this.textContent ?? "");
            };

            try {
                const strip = host.querySelector<HTMLDivElement>(
                    ".resultTabs");
                // A page is a scroll, and a scroll re-renders the bar.
                // The tab the page started from must not be brought
                // back in on that render, which would undo the page.
                await measure(strip!, { scrollLeft: 60, scrollWidth: 500 });

                expect(scrolledTo).toEqual([]);

                // Switching tabs is what does bring one in, since the
                // one switched to may be off the end of the strip.
                await click((label) => {
                    return label === "Result #2";
                });

                expect(scrolledTo).toEqual(["Result #2"]);
            } finally {
                Element.prototype.scrollIntoView = original;
            }
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
            await addRow();
            expect(iconButton("Apply Changes")?.disabled).toBe(false);

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

            expect(iconButton("Apply Changes")?.disabled).toBe(false);
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

        // Nothing to preview yet, so the button is off.
        expect(iconButton("Preview Changes")?.disabled).toBe(true);
        await addRow();

        await clickIcon("Preview Changes");

        expect(host.querySelector(".sqlPreview")).not.toBeNull();
        expect(iconButton("Preview Changes")?.getAttribute("aria-pressed"))
            .toBe("true");

        // The View dropdown goes back as well as the button does.
        await clickIcon("View");
        await click((label) => {
            return label === "Data Grid";
        });

        expect(host.querySelector(".sqlPreview")).toBeNull();
    });

    it("lays the bar out as the MySQL Shell does", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const order = [...host.querySelectorAll(
            ".toolbar > .toolbarLabel, .toolbar > button, "
            + ".toolbar > .toolbarMenuHost > button, .toolbar > .toolbarDivider",
        )].map((node) => {
            return node.getAttribute("aria-label")
                ?? (node.classList.contains("toolbarDivider")
                    ? "|"
                    : node.textContent);
        });
        expect(order).toEqual([
            "View:", "View", "|",
            "Pages:", "Previous Page", "Next Page", "|",
            "Edit:", "Start Editing", "Add New Row", "Preview Changes",
            "Apply Changes",
            "Rollback Changes", "Refresh", "|",
            "Maximize", "|",
            "Show Action Menu",
        ]);
    });

    describe("paging", () => {
        /**
         * @param index Which page is on show.
         * @param hasMore Whether there are rows after it.
         * @param loads How often it was fetched.
         *
         * @returns `report()` with its result set paged.
         */
        const paged = (index: number, hasMore: boolean, loads = 1) => {
            const state = report();
            state.resultSets[0] = {
                ...state.resultSets[0],
                page: { index, size: 2, hasMore, loads },
            };

            return state;
        };

        it("offers no paging for a result set that holds every row",
            async () => {
                await mount();
                await send({ type: "state", state: report() });

                expect(iconButton("Previous Page")?.disabled).toBe(true);
                expect(iconButton("Next Page")?.disabled).toBe(true);
            });

        it("offers the next page while there is one", async () => {
            await mount();
            await send({ type: "state", state: paged(0, true) });

            expect(iconButton("Previous Page")?.disabled).toBe(true);
            expect(iconButton("Next Page")?.disabled).toBe(false);
            expect(iconButton("Next Page")?.classList
                .contains("pageNextIcon")).toBe(true);

            await clickIcon("Next Page");

            expect(posted.at(-1)).toEqual({
                type: "page", resultId: "run1-result-0", page: 1,
            });
        });

        it("goes back from a later page, and not on from the last",
            async () => {
                await mount();
                await send({ type: "state", state: paged(2, false) });

                expect(iconButton("Next Page")?.disabled).toBe(true);
                await clickIcon("Previous Page");

                expect(posted.at(-1)).toEqual({
                    type: "page", resultId: "run1-result-0", page: 1,
                });
            });

        it("keeps paging off while edits would be lost", async () => {
            await mount();
            await send({ type: "state", state: paged(1, true) });

            await addRow();

            expect(iconButton("Previous Page")?.disabled).toBe(true);
            expect(iconButton("Next Page")?.disabled).toBe(true);
        });

        it("rebuilds the grid's edits from a new page", async () => {
            await mount();
            await send({ type: "state", state: paged(0, true) });
            await addRow();
            expect(iconButton("Apply Changes")?.disabled).toBe(false);

            // The same result set, the same id: only the page differs.
            await send({ type: "state", state: paged(1, true, 2) });

            expect(iconButton("Apply Changes")?.disabled).toBe(true);
        });

        it("reloads the page it is on after an apply", async () => {
            await mount();
            await send({ type: "state", state: paged(3, true) });

            await send({
                type: "applied",
                resultId: "run1-result-0",
                statements: ["DELETE FROM t"],
            });

            expect(posted.at(-1)).toEqual({
                type: "page", resultId: "run1-result-0", page: 3,
            });
        });

        it("shows a page that could not be fetched in the error bar",
            async () => {
                await mount();
                await send({ type: "state", state: paged(0, true) });

                await send({
                    type: "pageFailed",
                    resultId: "run1-result-0",
                    error: "gone away",
                });

                expect(host.querySelector(".errorBar")?.textContent)
                    .toContain("gone away");
            });
    });

    describe("a value saved in an editor", () => {
        /**
         * @param overrides What differs from the first row of `report()`.
         *
         * @returns The message a save sends.
         */
        const edited = (overrides: Record<string, unknown> = {}) => {
            return {
                type: "valueEdited" as const,
                requestId: "edit1",
                resultId: "run1-result-0",
                rowIndex: 0,
                column: "Name",
                pageKey: "",
                value: "Kabul City",
                ...overrides,
            };
        };

        it("goes into its cell as a pending edit", async () => {
            await mount();
            await send({ type: "state", state: report() });

            await send(edited());

            expect(posted.at(-1)).toEqual({
                type: "valueEditResult", requestId: "edit1",
            });
            expect(iconButton("Apply Changes")?.disabled).toBe(false);
        });

        it("is no edit where it says what the cell said", async () => {
            await mount();
            await send({ type: "state", state: report() });

            // The ID is the number 1; saved back, it is the text "1".
            await send(edited({ column: "ID", value: "1" }));

            expect(iconButton("Apply Changes")?.disabled).toBe(true);
        });

        it("is refused once its result set or page is gone", async () => {
            await mount();
            await send({ type: "state", state: report() });

            await send(edited({ resultId: "run0-result-0" }));
            expect(posted.at(-1)).toMatchObject({
                error: expect.stringContaining("result set"),
            });

            await send(edited({ pageKey: "3/1" }));
            expect(posted.at(-1)).toMatchObject({
                error: expect.stringContaining("page"),
            });
            expect(iconButton("Apply Changes")?.disabled).toBe(true);
        });
    });

    describe("a BLOB's file", () => {
        /**
         * @returns `report()` with a BLOB column in its result set.
         */
        const withBlob = () => {
            const state = report();
            state.resultSets[0] = {
                ...state.resultSets[0],
                columns: [
                    ...state.resultSets[0].columns,
                    { name: "image", display: "blob", nullable: true },
                ],
                rows: [
                    { ID: 1, Name: "Kabul", image: "89504e47" },
                    { ID: 2, Name: "Herat", image: null },
                ],
            };

            return state;
        };

        it("ignores an answer to a load it did not ask for", async () => {
            // The grid's Load button starts a load, and Tabulator never
            // builds under jsdom, so only the answering half is here.
            await mount();
            await send({ type: "state", state: withBlob() });

            await send({
                type: "valueLoaded",
                requestId: "load99",
                value: "ffd8ff",
            });

            expect(iconButton("Apply Changes")?.disabled).toBe(true);
            expect(host.querySelector(".errorBar")).toBeNull();
        });

        it("shows a file that could not be read in the error bar",
            async () => {
                await mount();
                await send({ type: "state", state: withBlob() });

                await send({
                    type: "valueLoaded",
                    requestId: "load1",
                    error: "permission denied",
                });

                expect(host.querySelector(".errorBar")?.textContent)
                    .toContain("Could not load the file: permission denied");
            });
    });

    it("says what the pending edits come to", async () => {
        await mount();
        await send({ type: "state", state: report() });
        expect(statusText()).toBe("2 rows in set");

        await addRow();

        expect(statusText()).toBe(
            "Editing, 1 row affected (0 fields changed, 1 row added)");
    });

    it("keeps Refresh off while there are edits it would lose",
        async () => {
            await mount();
            await send({ type: "state", state: report() });
            expect(iconButton("Refresh")?.disabled).toBe(false);

            await addRow();

            expect(iconButton("Refresh")?.disabled).toBe(true);
        });

    describe("freezing the primary key columns", () => {
        /**
         * @returns The action menu's Freeze item, the menu opened.
         */
        const freezeItem = async (): Promise<HTMLButtonElement | undefined> => {
            await clickIcon("Show Action Menu");

            return [...host.querySelectorAll<HTMLButtonElement>(
                ".toolbarMenuItem")].find((item) => {
                return item.textContent === "Freeze Primary Key Columns";
            });
        };

        it("starts from the extension's setting", async () => {
            await mount();
            await send({
                type: "state",
                state: { ...report(), freezeKeyColumns: false },
            });

            const item = await freezeItem();

            expect(item?.getAttribute("role")).toBe("menuitemcheckbox");
            expect(item?.getAttribute("aria-checked")).toBe("false");
        });

        it("is switched for the result set from its menu", async () => {
            await mount();
            await send({
                type: "state",
                state: { ...report(), freezeKeyColumns: true },
            });
            expect((await freezeItem())?.getAttribute("aria-checked"))
                .toBe("true");

            await act(async () => {
                (await freezeItem())?.click();
            });
            // The menu closed on the pick; opened again, it says so.
            const again = await freezeItem();

            expect(again?.getAttribute("aria-checked")).toBe("false");
        });

        it("is off where no primary key is known", async () => {
            await mount();
            const keyless = report();
            keyless.resultSets[0] = {
                ...keyless.resultSets[0],
                columns: keyless.resultSets[0].columns.map((column) => {
                    return { ...column, isPrimary: false };
                }),
            };
            await send({ type: "state", state: keyless });

            expect((await freezeItem())?.disabled).toBe(true);
        });
    });

    it("leaves the icon slot of an item without one blank", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await clickIcon("Show Action Menu");
        const close = [...host.querySelectorAll(".toolbarMenuItem")]
            .find((item) => {
                return item.textContent === "Close Result Set";
            });

        // noIcon is what keeps the mask from drawing a filled square.
        expect(close?.querySelector(".toolbarMenuIcon")?.classList
            .contains("noIcon")).toBe(true);
    });

    it("asks the host to close the result set from the menu", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await clickIcon("Show Action Menu");
        await click((label) => {
            return label === "Close Result Set";
        });

        expect(posted.at(-1))
            .toEqual({ type: "closeResult", resultId: "run1-result-0" });
    });

    it("previews the SQL an added row would run", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await addRow();
        await clickIcon("Preview Changes");

        // Generated by the same builder the extension executes with.
        expect(host.querySelector("code")?.textContent)
            .toBe("INSERT INTO `world`.`city` (`Name`) VALUES (NULL);");
    });

    it("enables Apply only once there is something to apply", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const applyOf = () => {
            return iconButton("Apply Changes");
        };
        expect(applyOf()?.disabled).toBe(true);

        await addRow();

        expect(applyOf()?.disabled).toBe(false);
    });

    it("sends the changes when Apply is pressed", async () => {
        await mount();
        await send({ type: "state", state: report() });
        await addRow();
        await clickIcon("Apply Changes");

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
        await addRow();
        await clickIcon("Rollback Changes");

        expect(iconButton("Apply Changes")?.disabled).toBe(true);
    });

    it("asks for a reload when Refresh is pressed", async () => {
        await mount();
        await send({ type: "state", state: report() });

        await clickIcon("Refresh");

        expect(posted.at(-1))
            .toEqual({ type: "refresh", resultId: "run1-result-0" });
    });

    it("draws the buttons as icons, not labels", async () => {
        await mount();
        await send({ type: "state", state: report() });

        const refresh = iconButton("Refresh");
        expect(refresh?.classList.contains("refreshIcon")).toBe(true);
        expect(refresh?.textContent).toBe("");
        expect(iconButton("Apply Changes")?.classList
            .contains("commitIcon")).toBe(true);
        expect(iconButton("Rollback Changes")?.classList
            .contains("rollbackIcon")).toBe(true);
    });

    describe("maximizing", () => {
        /**
         * @returns The result set of `report()` in its own editor tab.
         */
        const maximizedState = (): IViewState => {
            return {
                connections: [],
                connection: "dba@localhost:3310",
                sessions: [],
                actions: [],
                resultSets: report().resultSets,
                maximized: true,
            };
        };

        it("offers Maximize right of Refresh in the panel", async () => {
            await mount();
            await send({ type: "state", state: report() });

            const buttons = [...host.querySelectorAll(".toolbar button")];
            const refresh = buttons.indexOf(iconButton("Refresh")!);
            expect(buttons[refresh + 1]).toBe(iconButton("Maximize"));
            expect(iconButton("Maximize")?.classList
                .contains("maximizeIcon")).toBe(true);
            expect(iconButton("Minimize")).toBeUndefined();
        });

        it("moves the result set out with its pending edits", async () => {
            await mount();
            await send({ type: "state", state: report() });
            await addRow();

            await clickIcon("Maximize");

            const message = posted.at(-1) as {
                type: string;
                resultId: string;
                rows: Array<{ added: boolean }>;
            };
            expect(message.type).toBe("maximize");
            expect(message.resultId).toBe("run1-result-0");
            expect(message.rows.map((row) => {
                return row.added;
            })).toEqual([false, false, true]);
        });

        it("shows one result set, with nothing to pick", async () => {
            await mount();
            await send({ type: "state", state: maximizedState() });

            expect(host.querySelector(".contentSelectionBar")).toBeNull();
            expect(host.querySelector(".statusBar")).not.toBeNull();
            expect(iconButton("Minimize")?.classList
                .contains("minimizeIcon")).toBe(true);
            expect(iconButton("Maximize")).toBeUndefined();
        });

        it("starts from the edits it was sent", async () => {
            await mount();
            await send({
                type: "state",
                state: maximizedState(),
                editing: {
                    "run1-result-0": [{
                        original: { ID: 1, Name: "Kabul" },
                        current: { ID: 1, Name: "Kabul City" },
                        added: false,
                        deleted: false,
                    }],
                },
            });

            expect(statusText()).toBe(
                "Editing, 1 row affected (1 field changed)");
        });

        it("keeps every feature of the tab", async () => {
            await mount();
            await send({ type: "state", state: maximizedState() });

            for (const label of ["View", "Start Editing", "Add New Row",
                "Preview Changes",
                "Apply Changes", "Rollback Changes", "Show Action Menu"]) {
                expect(iconButton(label)).toBeDefined();
            }
            await clickIcon("Refresh");
            expect(posted.at(-1))
                .toEqual({ type: "refresh", resultId: "run1-result-0" });
        });

        it("goes back to the panel with its pending edits", async () => {
            await mount();
            await send({ type: "state", state: maximizedState() });
            await addRow();

            await clickIcon("Minimize");

            expect(posted.at(-1)).toMatchObject({
                type: "minimize",
                resultId: "run1-result-0",
            });
            expect(posted.at(-1)?.rows)
                .toHaveLength(3);
        });

        it("keeps the other tabs' edits when one leaves", async () => {
            await mount();
            const two = report();
            two.resultSets.push({
                ...two.resultSets[0],
                id: "run1-result-1",
                caption: "Result #2",
            });
            await send({ type: "state", state: two });
            await click((label) => {
                return label === "Result #2";
            });
            await addRow();

            const left = report();
            left.resultSets = [two.resultSets[1]];
            await send({ type: "state", state: left });

            expect(iconButton("Apply Changes")?.disabled).toBe(false);
        });

        it("shows a result set that comes back", async () => {
            await mount();
            await send({ type: "state", state: report() });
            await click((label) => {
                return label.startsWith("Actions");
            });

            const back = report();
            back.resultSets.push({
                ...back.resultSets[0],
                id: "run1-result-1",
                caption: "Result #2",
            });
            await send({ type: "state", state: back });

            expect(host.querySelector(".tab.active")?.textContent)
                .toBe("Result #2");
        });

        it("stays on the result set when an error is stepped to",
            async () => {
                await mount();
                await send({
                    type: "state",
                    state: {
                        ...maximizedState(),
                        actions: [{
                            ...report().actions[0],
                            kind: "error",
                            summary: "Execution failed: gone away",
                        }],
                    },
                });

                await click((label) => {
                    return label.includes("gone away");
                });

                expect(host.querySelector(".statusBar")).not.toBeNull();
            });
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
            expect(first?.querySelector(".errorBarText")?.textContent)
                .toBe("Duplicate entry '1' for key 'PRIMARY'");
            // One error, so nothing to step between.
            expect(first?.querySelector(".errorBarNav")).toBeNull();
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

        expect(host.querySelector(".errorBarText")?.textContent)
            .toBe("Table 'nope.nope' doesn't exist");
    });

    /**
     * Clicks one of the error bar's two chevrons.
     *
     * By class rather than by label: they are codicons and carry no
     * text, so the button helper cannot tell them from any other.
     *
     * @param which 0 for back, 1 for on.
     *
     * @returns Nothing.
     */
    const stepErrors = async (which: number): Promise<void> => {
        await act(async () => {
            host.querySelectorAll<HTMLButtonElement>(".errorBarStep")
                .item(which).click();
            await Promise.resolve();
        });
    };

    /**
     * @param messages What the server said about each failed statement.
     *
     * @returns A run that failed that many times.
     */
    const failedRun = (messages: string[]): IViewState => {
        return {
            connections: ["dba@localhost:3310"],
            connection: "dba@localhost:3310",
            sessions: [{ label: "1", open: true }],
            actions: [{
                id: "run1",
                time: "12:00:00.123",
                connection: "dba@localhost:3310",
                role: "run",
                statement: "",
                message: "Ran 3 statements on dba@localhost:3310",
                summary: `Finished with ${messages.length} errors`,
                kind: "error",
                elapsedMs: 2,
                children: messages.map((message, index) => {
                    return {
                        id: `run1-${index}`,
                        time: "12:00:00.123",
                        connection: "dba@localhost:3310",
                        role: "statement" as const,
                        statement: "SELECT 1",
                        message,
                        kind: "error" as const,
                        elapsedMs: 2,
                        source: {
                            uri: "file:///q.sql",
                            line: index,
                            character: 0,
                        },
                    };
                }),
            }],
            resultSets: [],
        };
    };

    it("shows the bar on a first run, under the connect event", async () => {
        await mount();
        const failing = failedRun(["You have an error in your SQL syntax"]);

        await send({
            type: "state",
            state: {
                ...failing,
                // What the first execution on a connection looks like:
                // the run's row went up before the connection existed,
                // so opening it is logged above the run.
                actions: [{
                    id: "connect-1",
                    time: "12:00:00.100",
                    connection: "dba@localhost:3310",
                    role: "event",
                    statement: "db.connect",
                    message: "Connected to dba@localhost:3310",
                    kind: "info",
                }, ...failing.actions],
            },
        });

        expect(host.querySelector(".errorBarText")?.textContent)
            .toBe("You have an error in your SQL syntax");
    });

    it("opens on the first error and says how many there are", async () => {
        await mount();

        await send({
            type: "state",
            state: failedRun(["first went wrong", "then this", "and this"]),
        });

        // The first, not the last: it is usually what caused the rest,
        // and it is where a reader working through them starts.
        expect(host.querySelector(".errorBarText")?.textContent)
            .toBe("first went wrong");
        expect(host.querySelector(".errorBarCount")?.textContent)
            .toBe("1 of 3");
    });

    it("steps through the errors, taking the user to each", async () => {
        await mount();
        await send({
            type: "state",
            state: failedRun(["first went wrong", "then this", "and this"]),
        });
        posted.length = 0;

        await stepErrors(1);

        expect(host.querySelector(".errorBarText")?.textContent)
            .toBe("then this");
        expect(host.querySelector(".errorBarCount")?.textContent)
            .toBe("2 of 3");
        // Stepping is for fixing, so it puts the cursor on the statement
        // rather than only changing the text.
        expect(posted).toEqual([{
            type: "revealStatement",
            source: { uri: "file:///q.sql", line: 1, character: 0 },
        }]);
    });

    it("stops at either end of the errors", async () => {
        await mount();
        await send({
            type: "state",
            state: failedRun(["first went wrong", "then this"]),
        });

        const steps = [...host.querySelectorAll<HTMLButtonElement>(
            ".errorBarStep")];
        expect(steps).toHaveLength(2);
        // Nothing before the first; the count says what is left.
        expect(steps[0].disabled).toBe(true);
        expect(steps[1].disabled).toBe(false);
    });

    it("goes to the statement when the message itself is clicked",
        async () => {
            await mount();
            await send({
                type: "state",
                state: failedRun(["first went wrong", "then this"]),
            });
            posted.length = 0;

            await act(async () => {
                host.querySelector<HTMLButtonElement>(".errorBarText")
                    ?.click();
                await Promise.resolve();
            });

            expect(posted).toEqual([{
                type: "revealStatement",
                source: { uri: "file:///q.sql", line: 0, character: 0 },
            }]);
        });

    it("copies the error on show, and says so for a moment", async () => {
        vi.useFakeTimers();
        try {
            await mount();
            await send({
                type: "state",
                state: failedRun(["first went wrong", "then this"]),
            });
            const copy = (): HTMLButtonElement => {
                return host.querySelector<HTMLButtonElement>(".errorBarCopy")!;
            };
            // Beside the close button, before it.
            expect(copy().nextElementSibling?.classList
                .contains("errorBarClose")).toBe(true);
            expect(copy().classList.contains("codicon-copy")).toBe(true);
            posted.length = 0;

            await act(async () => {
                copy().click();
                await Promise.resolve();
            });

            expect(posted).toEqual([{
                type: "copyToClipboard", text: "first went wrong",
            }]);
            expect(copy().classList.contains("codicon-check")).toBe(true);
            expect(copy().title).toBe("Copied");

            await act(async () => {
                vi.advanceTimersByTime(1200);
                await Promise.resolve();
            });
            expect(copy().classList.contains("codicon-copy")).toBe(true);

            // The next error along is copied as itself.
            await act(async () => {
                host.querySelectorAll<HTMLButtonElement>(".errorBarStep")[1]!
                    .click();
                await Promise.resolve();
            });
            posted.length = 0;
            await act(async () => {
                copy().click();
                await Promise.resolve();
            });
            expect(posted.find((message) => {
                return message.type === "copyToClipboard";
            })).toEqual({ type: "copyToClipboard", text: "then this" });
        } finally {
            vi.useRealTimers();
        }
    });

    it("closes the bar, and keeps it closed until the next failure",
        async () => {
            await mount();
            await send({
                type: "state",
                state: failedRun(["first went wrong", "then this"]),
            });

            await act(async () => {
                host.querySelector<HTMLButtonElement>(".errorBarClose")
                    ?.click();
                await Promise.resolve();
            });

            expect(host.querySelector(".errorBar")).toBeNull();

            // State arrives whenever anything happens on the connection,
            // and none of it should bring a closed bar back.
            await send({
                type: "state",
                state: failedRun(["first went wrong", "then this"]),
            });

            expect(host.querySelector(".errorBar")).toBeNull();

            await send({ type: "state", state: failedRun(["something new"]) });

            expect(host.querySelector(".errorBarText")?.textContent)
                .toBe("something new");
        });

    it("keeps its place while the connection carries on", async () => {
        await mount();
        const failing = failedRun(["first went wrong", "then this"]);
        await send({ type: "state", state: failing });

        await stepErrors(1);
        // A schema listed, a connection opened - anything at all sends
        // state again, and it must not put the reader back at the first.
        await send({ type: "state", state: { ...failing } });

        expect(host.querySelector(".errorBarCount")?.textContent)
            .toBe("2 of 2");
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

        // Why is said on the Edit button, as the MySQL Shell does.
        const start = iconButton("Start Editing");
        expect(start?.disabled).toBe(true);
        expect(start?.title).toBe("Read only: not a single table.");
        expect(iconButton("Add New Row")?.disabled).toBe(true);
        expect(iconButton("Add New Row")?.title)
            .toBe("Read only: not a single table.");
        expect(iconButton("Refresh")?.disabled).toBe(false);
    });
});
