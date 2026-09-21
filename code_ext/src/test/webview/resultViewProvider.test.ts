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

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExecutionService } from "../../sql/executionService.js";
import type {
    IExecutionReport,
    IOutputRow,
    IViewState,
} from "../../webview/protocol.js";
import {
    MAX_OUTPUT_ROWS,
    ResultViewProvider,
    RESULT_VIEW_ID,
} from "../../webview/resultViewProvider.js";
import { createFakeApi, createRecordingLog } from "../helpers.js";
import {
    env,
    MockWebviewView,
    resetVscodeMock,
    Uri,
    webviewViews,
    window as mockWindow,
} from "../mocks/vscode.js";

const extensionUri = Uri.file("/ext") as never;

/**
 * @param overrides The fields that differ from the default report.
 *
 * @returns A report holding one editable result set over world.city.
 */
const editableReport = (
    overrides: Partial<IExecutionReport> = {},
): IExecutionReport => {
    return {
        connection: "dba@localhost:3310",
        startedAt: "12:00:00.123",
        elapsedMs: 4,
        output: [{
            ...pendingRun(),
            message: "Ran 1 statement on dba@localhost:3310",
            summary: "Finished 1 statement successfully",
            kind: "info",
            elapsedMs: 4,
            children: [{
                id: "run1-0",
                time: "12:00:00.123",
                connection: "dba@localhost:3310",
                role: "statement",
                statement: "SELECT ID, Name FROM world.city",
                message: "1 row in set",
                kind: "info",
                rows: 1,
                elapsedMs: 4,
                resultId: "run1-result-0",
            }],
        }],
        ...overrides,
        resultSets: overrides.resultSets ?? [{
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
            rows: [{ ID: 1, Name: "Kabul" }],
            editable: true,
            target: { schema: "world", table: "city" },
            status: "1 row in set",
        }],
    };
};

/**
 * @param overrides The fields that differ from a plain pending run.
 *
 * @returns The row a run is opened with.
 */
const pendingRun = (
    overrides: Partial<IOutputRow> = {},
): IOutputRow => {
    return {
        id: "run1",
        time: "12:00:00.123",
        connection: "dba@localhost:3310",
        role: "run",
        statement: "",
        message: "Running 1 statement on dba@localhost:3310",
        summary: "Running\u2026",
        kind: "pending",
        children: [],
        ...overrides,
    };
};

/**
 * @param view The view to read from.
 *
 * @returns The most recent state the provider sent.
 */
const lastState = (
    view: { webview: { posted: unknown[] } },
): IViewState | undefined => {
    const states = view.webview.posted.filter((message) => {
        return (message as { type: string }).type === "state";
    }) as Array<{ state: IViewState }>;

    return states.at(-1)?.state;
};

/**
 * Registers a provider and resolves its view, as VS Code would.
 *
 * @returns The provider, its view and the fake server behind it.
 */
const createResolvedView = () => {
    const api = createFakeApi();
    const log = createRecordingLog();
    const provider = new ResultViewProvider(extensionUri, log);
    mockWindow.registerWebviewViewProvider(
        RESULT_VIEW_ID, provider as never);
    const view = new MockWebviewView(RESULT_VIEW_ID);
    provider.resolveWebviewView(view as never);

    return { api, log, provider, view };
};

describe("ResultViewProvider", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("is contributed under the documented view id", () => {
        expect(RESULT_VIEW_ID).toBe("mariadb.results");
    });

    it("serves the frontend under a strict content security policy", () => {
        const { view } = createResolvedView();

        const { html } = view.webview;

        expect(html).toContain("default-src 'none'");
        expect(html).toContain("dist/webview/main.js");
        expect(html).toContain("dist/webview/main.css");
        const policyNonce = /script-src 'nonce-([A-Za-z0-9]{32})'/
            .exec(html)?.[1];
        expect(policyNonce).toBeDefined();
        expect(html).toContain(`nonce="${policyNonce}"`);
    });

    it("focuses the view when nothing has resolved it yet", async () => {
        const provider = new ResultViewProvider(
            extensionUri, createRecordingLog());
        mockWindow.registerWebviewViewProvider(
        RESULT_VIEW_ID, provider as never);

        await provider.reveal();

        // The focus command is what makes VS Code create the view.
        expect(webviewViews).toHaveLength(1);
        expect(webviewViews[0].viewType).toBe(RESULT_VIEW_ID);
    });

    it("reveals without stealing focus from the editor", async () => {
        const { provider, view } = createResolvedView();

        await provider.reveal();

        expect(view.shown).toBe(1);
        expect(view.preserveFocus).toBe(true);
    });

    it("holds messages back until the frontend says it is listening",
        async () => {
            const { provider, view } = createResolvedView();

            await provider.startRun("dba@localhost:3310", pendingRun());
            expect(view.webview.posted).toEqual([]);

            view.webview.receive({ type: "ready" });

            // The state of the connection being run on, which a freshly
            // resolved view has not seen yet. It comes a tick later,
            // since listing the connections is async.
            await vi.waitFor(() => {
                expect(lastState(view)?.output.map((row) => {
                    return row.kind;
                })).toEqual(["pending"]);
            });
        });

    it("re-sends the report when the view comes back", async () => {
        const { api, provider, view } = createResolvedView();
        view.webview.receive({ type: "ready" });
        const report = editableReport();
        await provider.showResults(report, {
            connectionUri: "dba@localhost:3310",
            connectionId: "uuid",
            service: new ExecutionService(api),
        });

        // VS Code throws a hidden view's content away and resolves a new
        // one; the results have to come back with it.
        const second = new MockWebviewView(RESULT_VIEW_ID);
        provider.resolveWebviewView(second as never);
        second.webview.receive({ type: "ready" });
        await vi.waitFor(() => {
            expect(lastState(second)).toBeDefined();
        });

        expect(lastState(second)?.resultSets).toEqual(report.resultSets);
        expect(lastState(second)?.output).toEqual(report.output);
    });

    it("applies a grid's changes and reports the statements", async () => {
        const { api, provider, view } = createResolvedView();
        view.webview.receive({ type: "ready" });
        await provider.showResults(editableReport(), {
            connectionUri: "dba@localhost:3310",
            connectionId: "uuid",
            service: new ExecutionService(api),
        });

        view.webview.receive({
            type: "applyChanges",
            resultId: "run1-result-0",
            changes: [{
                kind: "update",
                rowIndex: 0,
                keys: { ID: 1 },
                values: { Name: "Kabul City" },
            }],
        });
        await vi.waitFor(() => {
            expect(view.webview.posted.some((message) => {
                return (message as { type: string }).type === "applied";
            })).toBe(true);
        });

        expect(api.scripts).toEqual([
            "UPDATE `world`.`city` SET `Name` = 'Kabul City' "
            + "WHERE `ID` = 1;",
        ]);
        expect(view.webview.posted.at(-1)).toEqual({
            type: "applied",
            resultId: "run1-result-0",
            statements: [
                "UPDATE `world`.`city` SET `Name` = 'Kabul City' "
                + "WHERE `ID` = 1",
            ],
        });
    });

    it("reports a failed apply back to the grid", async () => {
        const { api, log, provider, view } = createResolvedView();
        api.executeScript = () => {
            return Promise.reject(new Error("Duplicate entry '1'"));
        };
        view.webview.receive({ type: "ready" });
        await provider.showResults(editableReport(), {
            connectionUri: "dba@localhost:3310",
            connectionId: "uuid",
            service: new ExecutionService(api),
        });

        view.webview.receive({
            type: "applyChanges",
            resultId: "run1-result-0",
            changes: [{ kind: "delete", rowIndex: 0, keys: { ID: 1 } }],
        });
        await vi.waitFor(() => {
            expect(view.webview.posted.some((message) => {
                return (message as { type: string }).type === "applied";
            })).toBe(true);
        });

        expect(view.webview.posted.at(-1)).toMatchObject({
            type: "applied",
            error: "Duplicate entry '1'",
        });
        expect(log.lines.join("\n")).toContain("Duplicate entry");
    });

    it("says so when the edited result set is gone", async () => {
        const { view } = createResolvedView();
        view.webview.receive({ type: "ready" });

        view.webview.receive({
            type: "applyChanges",
            resultId: "run1-result-0",
            changes: [],
        });
        await vi.waitFor(() => {
            expect(view.webview.posted).toHaveLength(1);
        });

        expect(view.webview.posted[0]).toMatchObject({
            error: "The result set is no longer available.",
        });
    });

    it("re-runs a result set on request", async () => {
        const { api, provider, view } = createResolvedView();
        view.webview.receive({ type: "ready" });
        const reloaded: string[] = [];
        provider.setRefreshHandler((resultSet) => {
            reloaded.push(resultSet.statement);

            return Promise.resolve();
        });
        await provider.showResults(editableReport(), {
            connectionUri: "dba@localhost:3310",
            connectionId: "uuid",
            service: new ExecutionService(api),
        });

        view.webview.receive({ type: "refresh", resultId: "run1-result-0" });
        await vi.waitFor(() => {
            expect(reloaded).toEqual(["SELECT ID, Name FROM world.city"]);
        });
    });

    it("logs, rather than throws, when a refresh fails", async () => {
        const { api, log, provider, view } = createResolvedView();
        view.webview.receive({ type: "ready" });
        provider.setRefreshHandler(() => {
            return Promise.reject(new Error("the connection went away"));
        });
        await provider.showResults(editableReport(), {
            connectionUri: "dba@localhost:3310",
            connectionId: "uuid",
            service: new ExecutionService(api),
        });

        view.webview.receive({ type: "refresh", resultId: "run1-result-0" });
        await vi.waitFor(() => {
            expect(log.lines.join("\n"))
                .toContain("the connection went away");
        });
    });

    describe("a run under way", () => {
        it("puts the run up before anything has been run", async () => {
            const { provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });

            await provider.startRun("dba@localhost:3310", pendingRun());

            expect(lastState(view)?.output).toEqual([pendingRun()]);
        });

        it("replaces it with what the run produced", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });

            await provider.startRun("dba@localhost:3310", pendingRun());
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            });

            // One row, not two: the report carries the same id.
            const output = lastState(view)?.output ?? [];
            expect(output).toHaveLength(1);
            expect(output[0]).toMatchObject({
                id: "run1",
                kind: "info",
                summary: "Finished 1 statement successfully",
            });
            expect(output[0].children).toHaveLength(1);
        });

        it("clears the tabs of the run before it", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            });

            await provider.startRun(
                "dba@localhost:3310", pendingRun({ id: "run2" }));

            // The tabs stand for the last run, and this is no longer it;
            // nothing can be written back through them either.
            expect(lastState(view)?.resultSets).toEqual([]);
            expect(provider.applyContext).toBeUndefined();
        });

        it("keeps each connection's runs apart", async () => {
            const { provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });

            await provider.startRun("dba@localhost:3310", pendingRun());
            await provider.startRun("app@localhost:3311", pendingRun({
                id: "run2",
                connection: "app@localhost:3311",
            }));

            expect(provider.outputFor("dba@localhost:3310")).toHaveLength(1);
            expect(lastState(view)?.connection).toBe("app@localhost:3311");
        });
    });

    describe("accumulating output", () => {
        it("keeps the output of earlier runs", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const context = {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            };

            await provider.showResults(editableReport(), context);
            await provider.showResults(editableReport({
                output: [pendingRun({ id: "run2", kind: "info" })],
                resultSets: [],
            }), context);

            expect(lastState(view)?.output.map((row) => {
                return row.id;
            })).toEqual(["run1", "run2"]);
        });

        it("replaces the result sets on each run", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const context = {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            };

            await provider.showResults(editableReport(), context);
            await provider.showResults(
                editableReport({ output: [], resultSets: [] }), context);

            // The tabs stand for the last run only.
            expect(lastState(view)?.resultSets).toEqual([]);
        });

        it("keeps each connection's output apart", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const service = new ExecutionService(api);

            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid-dba",
                service,
            });
            await provider.showResults(editableReport({
                connection: "app@localhost:3311",
                output: [pendingRun({
                    id: "run2",
                    connection: "app@localhost:3311",
                    kind: "info",
                })],
                resultSets: [],
            }), {
                connectionUri: "app@localhost:3311",
                connectionId: "uuid-app",
                service,
            });

            expect(provider.outputFor("dba@localhost:3310")).toHaveLength(1);
            expect(provider.outputFor("app@localhost:3311")).toHaveLength(1);
            expect(lastState(view)?.connection).toBe("app@localhost:3311");
        });

        it("caps how much output one connection keeps", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const context = {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            };

            // Output lives for as long as the window does, so it needs a
            // ceiling; the oldest runs go first, whole. Three runs of
            // half of it are over it.
            const perRun = Math.floor(MAX_OUTPUT_ROWS / 2) - 1;
            for (let run = 0; run < 3; run += 1) {
                await provider.showResults(editableReport({
                    output: [pendingRun({
                        id: `run${run}`,
                        kind: "info",
                        children: Array.from({ length: perRun }, (_v, i) => {
                            return {
                                id: `run${run}-${i}`,
                                time: "12:00:00.000",
                                connection: "dba@localhost:3310",
                                role: "statement" as const,
                                statement: "SELECT 1",
                                message: "ok",
                                kind: "info" as const,
                                elapsedMs: 1,
                            };
                        }),
                    })],
                    resultSets: [],
                }), context);
            }

            // The first run goes whole: the tree cannot keep half of it.
            const output = provider.outputFor("dba@localhost:3310");
            expect(output.map((row) => {
                return row.id;
            })).toEqual(["run1", "run2"]);
        });
    });

    describe("the connection picker", () => {
        /**
         * @returns A provider that knows two connections.
         */
        const createWithConnections = () => {
            const created = createResolvedView();
            created.provider.setConnectionLister(() => {
                return Promise.resolve([
                    "dba@localhost:3310", "app@localhost:3311",
                ]);
            });
            created.view.webview.receive({ type: "ready" });

            return created;
        };

        it("names the connection on show beside the view's title",
            async () => {
                const { api, provider, view } = createWithConnections();

                await provider.showResults(editableReport(), {
                    connectionUri: "dba@localhost:3310",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                });

                // The Output panel names its channel the same way.
                expect(view.description).toBe("dba@localhost:3310");
            });

        it("offers the configured connections plus any run on",
            async () => {
                const { api, provider, view } = createWithConnections();

                await provider.showResults(editableReport({
                    connection: "adhoc@localhost:3312",
                    output: [],
                    resultSets: [],
                }), {
                    connectionUri: "adhoc@localhost:3312",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                });

                expect(lastState(view)?.connections).toEqual([
                    "adhoc@localhost:3312",
                    "app@localhost:3311",
                    "dba@localhost:3310",
                ]);
            });

        it("switches to the connection the page picked", async () => {
            const { api, provider, view } = createWithConnections();
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            });

            view.webview.receive({
                type: "selectConnection",
                connection: "app@localhost:3311",
            });
            await vi.waitFor(() => {
                expect(lastState(view)?.connection)
                    .toBe("app@localhost:3311");
            });

            // A connection nothing has run on yet shows empty, and the
            // other connection's results are left untouched.
            expect(lastState(view)?.output).toEqual([]);
            expect(lastState(view)?.resultSets).toEqual([]);
            expect(provider.outputFor("dba@localhost:3310"))
                .toHaveLength(1);
            expect(view.description).toBe("app@localhost:3311");
        });

        it("still shows the results when the list cannot be fetched",
            async () => {
                const { api, log, provider, view } = createResolvedView();
                provider.setConnectionLister(() => {
                    return Promise.reject(new Error("the shell is down"));
                });
                view.webview.receive({ type: "ready" });

                await provider.showResults(editableReport(), {
                    connectionUri: "dba@localhost:3310",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                });

                expect(lastState(view)?.resultSets).toHaveLength(1);
                // The connection already run on is still offered, even
                // though the server could not list it.
                expect(lastState(view)?.connections)
                    .toEqual(["dba@localhost:3310"]);
                expect(log.lines.join("\n")).toContain("the shell is down");
            });
    });

    describe("clearing the view", () => {
        it("discards the output and the result tabs together",
            async () => {
                const { api, provider, view } = createResolvedView();
                view.webview.receive({ type: "ready" });
                await provider.showResults(editableReport(), {
                    connectionUri: "dba@localhost:3310",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                });
                expect(lastState(view)?.output).toHaveLength(1);
                expect(lastState(view)?.resultSets).toHaveLength(1);

                await provider.clear();

                expect(lastState(view)?.output).toEqual([]);
                expect(lastState(view)?.resultSets).toEqual([]);
                expect(provider.outputFor("dba@localhost:3310"))
                    .toEqual([]);
                expect(provider.resultSetsFor("dba@localhost:3310"))
                    .toEqual([]);
            });

        it("stops the cleared tabs being written back to", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            });

            await provider.clear();
            view.webview.receive({
                type: "applyChanges",
                resultId: "run1-result-0",
                changes: [{ kind: "delete", rowIndex: 0, keys: { ID: 1 } }],
            });
            await vi.waitFor(() => {
                expect(view.webview.posted.some((message) => {
                    return (message as { type: string }).type === "applied";
                })).toBe(true);
            });

            expect(view.webview.posted.at(-1)).toMatchObject({
                error: "The result set is no longer available.",
            });
            expect(api.scripts).toEqual([]);
        });

        it("only clears the connection on show", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const service = new ExecutionService(api);
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid-dba",
                service,
            });
            await provider.showResults(editableReport({
                connection: "app@localhost:3311",
                resultSets: [],
            }), {
                connectionUri: "app@localhost:3311",
                connectionId: "uuid-app",
                service,
            });

            await provider.clear();

            expect(provider.outputFor("app@localhost:3311")).toEqual([]);
            expect(provider.outputFor("dba@localhost:3310"))
                .toHaveLength(1);
            expect(provider.resultSetsFor("dba@localhost:3310"))
                .toHaveLength(1);
        });
    });

    it("copies text to the clipboard on request", async () => {
        const { view } = createResolvedView();

        view.webview.receive({
            type: "copyToClipboard",
            text: "SELECT 1",
        });

        await vi.waitFor(() => {
            expect(env.clipboard.text).toBe("SELECT 1");
        });
    });

    it("forgets its view when it is closed", async () => {
        const { provider, view } = createResolvedView();
        view.dispose();

        await provider.reveal();

        // With no view left, revealing falls back to the focus command.
        expect(webviewViews).toHaveLength(1);
    });
});
