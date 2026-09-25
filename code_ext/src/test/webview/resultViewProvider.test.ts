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

import type {
    IActivityEvent,
} from "../../connections/connectionActivity.js";
import { ExecutionService } from "../../sql/executionService.js";
import type {
    IExecutionReport,
    IActionRow,
    IViewState,
} from "../../webview/protocol.js";
import {
    MAX_ACTION_ROWS,
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
        actions: [{
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
    overrides: Partial<IActionRow> = {},
): IActionRow => {
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
                expect(lastState(view)?.actions.map((row) => {
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
        expect(lastState(second)?.actions).toEqual(report.actions);
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

            expect(lastState(view)?.actions).toEqual([pendingRun()]);
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
            const output = lastState(view)?.actions ?? [];
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

            expect(provider.actionsFor("dba@localhost:3310")).toHaveLength(1);
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
                actions: [pendingRun({ id: "run2", kind: "info" })],
                resultSets: [],
            }), context);

            // Newest first: what has just happened is at the top.
            expect(lastState(view)?.actions.map((row) => {
                return row.id;
            })).toEqual(["run2", "run1"]);
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
                editableReport({ actions: [], resultSets: [] }), context);

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
                actions: [pendingRun({
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

            expect(provider.actionsFor("dba@localhost:3310")).toHaveLength(1);
            expect(provider.actionsFor("app@localhost:3311")).toHaveLength(1);
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
            const perRun = Math.floor(MAX_ACTION_ROWS / 2) - 1;
            for (let run = 0; run < 3; run += 1) {
                await provider.showResults(editableReport({
                    actions: [pendingRun({
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

            // The oldest run goes whole: the tree cannot keep half of
            // it, and the oldest is the one at the end.
            const output = provider.actionsFor("dba@localhost:3310");
            expect(output.map((row) => {
                return row.id;
            })).toEqual(["run2", "run1"]);
        });

        it("counts a statement's warnings against that cap too", async () => {
            const { api, provider, view } = createResolvedView();
            view.webview.receive({ type: "ready" });
            const context = {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            };

            // Half the cap of statements, but every one of them carrying
            // a warning of its own, so two runs are over it. Counting
            // only the statements would have kept all three.
            const perRun = Math.floor(MAX_ACTION_ROWS / 4);
            for (let run = 0; run < 3; run += 1) {
                await provider.showResults(editableReport({
                    actions: [pendingRun({
                        id: `run${run}`,
                        kind: "warning",
                        children: Array.from({ length: perRun }, (_v, i) => {
                            return {
                                id: `run${run}-${i}`,
                                time: "12:00:00.000",
                                connection: "dba@localhost:3310",
                                role: "statement" as const,
                                statement: "SELECT 1",
                                message: "1 row in set, 1 warning",
                                kind: "warning" as const,
                                elapsedMs: 1,
                                children: [{
                                    id: `run${run}-${i}-warning-0`,
                                    time: "12:00:00.000",
                                    connection: "dba@localhost:3310",
                                    role: "warning" as const,
                                    statement: "Warning 1292",
                                    message: "Truncated incorrect value",
                                    kind: "warning" as const,
                                }],
                            };
                        }),
                    })],
                    resultSets: [],
                }), context);
            }

            expect(provider.actionsFor("dba@localhost:3310").map((row) => {
                return row.id;
            })).toEqual(["run2"]);
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
                    actions: [],
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
            expect(lastState(view)?.actions).toEqual([]);
            expect(lastState(view)?.resultSets).toEqual([]);
            expect(provider.actionsFor("dba@localhost:3310"))
                .toHaveLength(1);
            expect(view.description).toBe("app@localhost:3311");
        });

        it("does not ask the server for the list on every row",
            async () => {
                const { api, provider, view } = createResolvedView();
                let listed = 0;
                provider.setConnectionLister(() => {
                    listed += 1;

                    return Promise.resolve(["dba@localhost:3310"]);
                });
                view.webview.receive({ type: "ready" });

                const context = {
                    connectionUri: "dba@localhost:3310",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                };
                await provider.showResults(editableReport(), context);
                await provider.showResults(editableReport({
                    actions: [pendingRun({ id: "run2", kind: "info" })],
                    resultSets: [],
                }), context);

                // State is sent for every row that appears, and the
                // configured list only changes when one is edited.
                expect(listed).toBe(1);
                expect(lastState(view)?.connections)
                    .toEqual(["dba@localhost:3310"]);
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
                expect(lastState(view)?.actions).toHaveLength(1);
                expect(lastState(view)?.resultSets).toHaveLength(1);

                await provider.clear();

                expect(lastState(view)?.actions).toEqual([]);
                expect(lastState(view)?.resultSets).toEqual([]);
                expect(provider.actionsFor("dba@localhost:3310"))
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

            expect(provider.actionsFor("app@localhost:3311")).toEqual([]);
            expect(provider.actionsFor("dba@localhost:3310"))
                .toHaveLength(1);
            expect(provider.resultSetsFor("dba@localhost:3310"))
                .toHaveLength(1);
        });
    });

    describe("several connections on one URI", () => {
        /**
         * @param overrides The fields that differ from a plain event.
         *
         * @returns One event on a connection.
         */
        const anEvent = (
            overrides: Partial<IActivityEvent> = {},
        ): IActivityEvent => {
            return {
                connection: "dba@localhost:3310",
                label: "UI Backend",
                call: "db.list_schemas()",
                message: "Listed 2 schemas",
                when: new Date(2026, 8, 21, 12, 0, 0, 500),
                elapsedMs: 3,
                ...overrides,
            };
        };

        /**
         * @returns A view knowing one open connection per label.
         */
        const createWithSessions = () => {
            const created = createResolvedView();
            created.provider.setSessionLister(() => {
                return ["1", "UI Backend"];
            });
            created.view.webview.receive({ type: "ready" });

            return created;
        };

        it("offers General Actions first, showing it until a connection "
            + "reports", async () => {
                const { provider, view } = createWithSessions();
                provider.setConnectionLister(() => {
                    return Promise.resolve(["dba@localhost:3310"]);
                });

                await provider.appendEvent(anEvent({
                    connection: "General Actions",
                    label: "",
                    call: "db.list_connections(kind=gui)",
                    message: "Listed 1 connection",
                }));

                // With nothing else to show, the first action logged is
                // shown rather than an empty panel.
                expect(view.description).toBe("General Actions");
                expect(lastState(view)?.actions).toMatchObject([{
                    statement: "db.list_connections(kind=gui)",
                }]);

                // A connection reporting takes the view over: General
                // Actions is there to be looked up, not to stand in front.
                await provider.appendEvent(anEvent());
                const state = lastState(view);
                expect(state?.connections).toEqual([
                    "General Actions", "dba@localhost:3310",
                ]);
                expect(state?.connection).toBe("dba@localhost:3310");

                await provider.selectConnection("General Actions");
                expect(lastState(view)?.actions).toMatchObject([{
                    role: "event",
                    statement: "db.list_connections(kind=gui)",
                    message: "Listed 1 connection",
                }]);
            });

        it("keeps General Actions on show once it was picked", async () => {
            const { provider, view } = createWithSessions();
            await provider.appendEvent(anEvent({
                connection: "General Actions", label: "",
                call: "sandbox.list_instances()", message: "Listed 0 sandboxes",
            }));
            await provider.selectConnection("General Actions");

            await provider.appendEvent(anEvent());

            expect(view.description).toBe("General Actions");
        });

        it("leaves a connection on show when a general action comes in",
            async () => {
                const { provider, view } = createWithSessions();
                await provider.appendEvent(anEvent());

                await provider.appendEvent(anEvent({
                    connection: "General Actions", label: "",
                    call: "sandbox.start(port=3310)", message: "Started.",
                }));

                expect(view.description).toBe("dba@localhost:3310");
            });

        it("gathers what happens outside a run", async () => {
            const { provider, view } = createWithSessions();

            await provider.appendEvent(anEvent());

            const output = lastState(view)?.actions ?? [];
            expect(output).toHaveLength(1);
            expect(output[0]).toMatchObject({
                role: "event",
                connectionLabel: "UI Backend",
                message: "Listed 2 schemas",
                statement: "db.list_schemas()",
                kind: "info",
            });
        });

        it("shows every connection's rows together by default",
            async () => {
                const { provider, view } = createWithSessions();

                await provider.appendEvent(anEvent());
                await provider.startRun("dba@localhost:3310", pendingRun({
                    connectionLabel: "1",
                }));

                const state = lastState(view);
                expect(state?.session).toBeUndefined();
                expect(state?.actions.map((row) => {
                    return row.connectionLabel;
                })).toEqual(["1", "UI Backend"]);
                // Both, whether or not anything has run on them.
                expect(state?.sessions).toEqual([
                    { label: "1", open: true },
                    { label: "UI Backend", open: true },
                ]);
            });

        it("puts what a run does on the way above the run", async () => {
            const { provider, view } = createWithSessions();

            // The run's row goes up before its connection is opened,
            // so the opening arrives after it - and the newest is what
            // the top of the log holds.
            await provider.startRun("dba@localhost:3310", pendingRun({
                connectionLabel: "1",
            }));
            await provider.appendEvent(anEvent({
                label: "1",
                call: "db.connect(dba@localhost:3310)",
                message: "Opened Session 1 for dba@localhost:3310",
            }));

            expect(lastState(view)?.actions.map((row) => {
                return [row.role, row.message];
            })).toEqual([
                ["event", "Opened Session 1 for dba@localhost:3310"],
                ["run", "Running 1 statement on dba@localhost:3310"],
            ]);
        });

        it("puts what happens after a run has finished above it",
            async () => {
                const { api, provider, view } = createWithSessions();
                await provider.showResults(editableReport(), {
                    connectionUri: "dba@localhost:3310",
                    connectionId: "uuid",
                    service: new ExecutionService(api),
                });

                await provider.appendEvent(anEvent());

                expect(lastState(view)?.actions.map((row) => {
                    return row.role;
                })).toEqual(["event", "run"]);
            });

        it("narrows the output to the connection the page picked",
            async () => {
                const { provider, view } = createWithSessions();
                await provider.appendEvent(anEvent());
                await provider.startRun("dba@localhost:3310", pendingRun({
                    connectionLabel: "1",
                }));

                view.webview.receive({
                    type: "selectSession",
                    session: "UI Backend",
                });
                await vi.waitFor(() => {
                    expect(lastState(view)?.session).toBe("UI Backend");
                });

                expect(lastState(view)?.actions.map((row) => {
                    return row.id;
                })).toEqual(["event1"]);
                // Nothing was thrown away: the other rows are still
                // there to come back to.
                expect(provider.actionsFor("dba@localhost:3310"))
                    .toHaveLength(2);
            });

        it("drops a filter that would hide the run just started",
            async () => {
                const { provider, view } = createWithSessions();
                await provider.selectSession("UI Backend");

                await provider.startRun("dba@localhost:3310", pendingRun({
                    connectionLabel: "1",
                }));

                // Putting a run up the moment it starts is pointless if
                // the filter in force hides it.
                expect(lastState(view)?.session).toBeUndefined();
                expect(lastState(view)?.actions).toHaveLength(1);
            });

        it("keeps the filter when the run is on the connection shown",
            async () => {
                const { provider, view } = createWithSessions();
                await provider.selectSession("1");

                await provider.startRun("dba@localhost:3310", pendingRun({
                    connectionLabel: "1",
                }));

                expect(lastState(view)?.session).toBe("1");
            });

        it("offers a connection that has been closed, and says so",
            async () => {
                const { provider, view } = createResolvedView();
                provider.setSessionLister(() => { return []; });
                view.webview.receive({ type: "ready" });

                await provider.appendEvent(anEvent());

                // The log outlives the connection it was gathered on.
                expect(lastState(view)?.sessions)
                    .toEqual([{ label: "UI Backend", open: false }]);
            });

        it("shows all of them again when the connection changes",
            async () => {
                const { provider, view } = createWithSessions();
                await provider.appendEvent(anEvent());
                await provider.selectSession("UI Backend");

                view.webview.receive({
                    type: "selectConnection",
                    connection: "app@localhost:3311",
                });
                await vi.waitFor(() => {
                    expect(lastState(view)?.connection)
                        .toBe("app@localhost:3311");
                });

                expect(lastState(view)?.session).toBeUndefined();
            });

        it("does not reveal the view for an event", async () => {
            const { provider, view } = createWithSessions();
            const shown = view.shown;

            await provider.appendEvent(anEvent());

            // Browsing the tree is no reason to throw the panel open
            // over whatever is being read.
            expect(view.shown).toBe(shown);
        });

        it("leaves another connection's view alone", async () => {
            const { api, provider, view } = createWithSessions();
            await provider.showResults(editableReport(), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid",
                service: new ExecutionService(api),
            });
            const before = view.webview.posted.length;

            await provider.appendEvent(anEvent({
                connection: "app@localhost:3311",
            }));

            expect(view.webview.posted).toHaveLength(before);
            expect(provider.actionsFor("app@localhost:3311"))
                .toHaveLength(1);
        });

        it("shows the tabs of whichever connection ran last", async () => {
            const { api, provider, view } = createWithSessions();
            const service = new ExecutionService(api);

            await provider.showResults(editableReport({
                actions: [pendingRun({
                    kind: "info",
                    connectionLabel: "UI Backend",
                })],
            }), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid-ui",
                service,
            });

            expect(lastState(view)?.resultSets).toHaveLength(1);

            // A run on the other connection takes the tabs over; the
            // first one's are kept, and come back when it is picked.
            await provider.showResults(editableReport({
                actions: [pendingRun({
                    id: "run2",
                    kind: "info",
                    connectionLabel: "1",
                })],
                resultSets: [],
            }), {
                connectionUri: "dba@localhost:3310",
                connectionId: "uuid-editor",
                service,
            });
            expect(lastState(view)?.resultSets).toEqual([]);

            await provider.selectSession("UI Backend");
            expect(lastState(view)?.resultSets).toHaveLength(1);
        });

        it("clears only the connection on show", async () => {
            const { provider, view } = createWithSessions();
            await provider.appendEvent(anEvent());
            await provider.startRun("dba@localhost:3310", pendingRun({
                connectionLabel: "1",
            }));
            await provider.selectSession("1");

            await provider.clear();

            expect(lastState(view)?.actions).toEqual([]);
            expect(provider.actionsFor("dba@localhost:3310").map((row) => {
                return row.connectionLabel;
            })).toEqual(["UI Backend"]);
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
