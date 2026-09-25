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

import * as vscode from "vscode";

import {
    GENERAL_ACTIONS,
    activityRow,
    type IActivityEvent,
} from "../connections/connectionActivity.js";
import { freezePrimaryKeyColumns } from "../connections/settings.js";
import type { ExecutionService } from "../sql/executionService.js";
import type { IEditableRow } from "./changes.js";
import { loadValueFromFile, saveValueToFile } from "./valueFiles.js";
import { ValueDocuments } from "./valueDocuments.js";

/** How long a saved value waits for the grid to take it. */
export const VALUE_EDIT_TIMEOUT_MS = 10000;
import {
    MaximizedResult,
    type IMaximizedOrigin,
} from "./maximizedResult.js";
import type {
    HostMessage,
    IConnectionSession,
    IExecutionReport,
    IActionRow,
    IResultSet,
    IStatementSource,
    IViewState,
    RowChange,
    WebviewMessage,
} from "./protocol.js";

/** The id the result view is contributed under. */
export const RESULT_VIEW_ID = "mariadb.results";

/** What the view needs in order to write a grid's edits back. */
export interface IApplyContext {
    connectionUri: string;
    connectionId: string;
    service: ExecutionService;
}

/** What a refresh is to re-run on, and where its result goes. */
export interface IRefreshTarget {
    /** The connection the result set was produced on. */
    connectionUri?: string;
    /**
     * The maximized result set to put the new result in, rather than the
     * panel's tabs. Passed back to `startRun`.
     */
    into?: string;
}

/**
 * Where a run's result goes instead of the panel's tabs: the key of a
 * maximized result set to refresh, or a new editor tab to open under the
 * title given.
 */
export type RunInto = string | { title: string };

/**
 * How many action rows one connection keeps before the oldest go. A run
 * and its statements are counted together, since they stand or fall as
 * one row of the tree.
 */
export const MAX_ACTION_ROWS = 2000;

/**
 * How long the configured connection list is reused before the server is
 * asked for it again.
 */
export const CONNECTION_LIST_TTL_MS = 5000;

/** What the view holds for one of the connections open on a URI. */
interface ISessionResults {
    /** The last run's result sets; a run replaces them. */
    resultSets: IResultSet[];
    applyContext?: IApplyContext;
}

/** What the view holds for one connection URI. */
interface IConnectionResults {
    /**
     * Everything that happened on it, newest first: a row per run, each
     * holding its statements, and a row per other event.
     *
     * One list for every connection open on the URI rather than one
     * apiece, because that is what the view shows by default: the rows
     * carry the label of the connection they happened on, and picking one
     * in the page filters this list rather than switching to another.
     */
    actions: IActionRow[];
    /** Connection label -> the tabs that connection's last run left. */
    sessions: Map<string, ISessionResults>;
    /** The connection that ran last, whose tabs are on show for "All". */
    lastRun?: string;
}

/**
 * The key a row's connection is held under. A row from before there were
 * several - a test's, or an older report's - has none, and is shown only
 * when all of them are.
 *
 * @param row The row to place.
 *
 * @returns Its connection label, or the empty key.
 */
const sessionOf = (row: IActionRow | undefined): string => {
    return row?.connectionLabel ?? "";
};

/**
 * Builds a nonce for one load of the webview.
 *
 * @returns 32 random alphanumeric characters.
 */
const createNonce = (): string => {
    const alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let nonce = "";
    for (let index = 0; index < 32; index += 1) {
        nonce += alphabet[Math.floor(Math.random() * alphabet.length)];
    }

    return nonce;
};

/**
 * Builds the HTML shell the webview loads.
 *
 * Everything is served from the extension's own folder under a strict
 * content security policy, with a per-load nonce on the one script tag, so
 * the view cannot pull anything off the network.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 *
 * @returns The HTML document.
 */
export const buildViewHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
): string => {
    const asset = (...parts: string[]): string => {
        return webview.asWebviewUri(
            vscode.Uri.joinPath(extensionUri, ...parts),
        ).toString();
    };

    const script = asset("dist", "webview", "main.js");
    const style = asset("dist", "webview", "main.css");
    const nonce = createNonce();

    return `<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; ${""
        }style-src ${webview.cspSource} 'unsafe-inline'; ${""
        }img-src ${webview.cspSource} data:; ${""
        }font-src ${webview.cspSource}; ${""
        }script-src 'nonce-${nonce}';" />
    <link rel="stylesheet" href="${style}" />
    <title>MariaDB</title>
</head>

<body>
    <div id="root"></div>
    <script type="module" nonce="${nonce}" src="${script}"></script>
</body>

</html>`;
};

/**
 * The MariaDB result view, docked in the bottom panel beside Problems,
 * Output and the Debug Console.
 *
 * It is a `WebviewView` rather than a `WebviewPanel` because only a view
 * can live in the panel area; a panel would open as an editor tab.
 */
export class ResultViewProvider
    implements vscode.WebviewViewProvider, vscode.Disposable {

    #view?: vscode.WebviewView;
    /** Connection URI -> everything the view holds for it. */
    readonly #byConnection = new Map<string, IConnectionResults>();
    /** The connection whose actions and results are on show. */
    #active?: string;
    /**
     * Whether General Actions is on show only because it was the first
     * thing logged, rather than because the user picked it - in which case
     * the first connection to report anything takes the view over.
     */
    #generalByDefault = false;
    /**
     * Which connection open on it is on show, or undefined for all of
     * them together - which is what the view opens on.
     */
    #activeSession?: string;
    /** Set once the frontend says it is listening. */
    #ready = false;
    #pending: HostMessage[] = [];
    /** Counts the events, so each row put up for one has its own id. */
    #eventCount = 0;
    /** The configured connections, as last listed, and when. */
    #configured?: { at: number; uris: string[] };
    /** Re-runs the statement behind a result set. */
    #onRefresh?: (
        resultSet: IResultSet,
        target: IRefreshTarget,
    ) => Promise<void>;
    /** The result sets moved into editor tabs of their own, by key. */
    readonly #maximized = new Map<string, MaximizedResult>();
    /** Counts the maximized tabs, so each has its own key. */
    #maximizedCount = 0;
    /**
     * Run id -> the maximized tab its result goes to, or the title of the
     * one it is to open, while it runs.
     */
    readonly #runInto = new Map<string, RunInto>();
    /**
     * Pending edits for result sets coming back from an editor tab, sent
     * with the next state the view is there to receive.
     */
    #seed: Record<string, IEditableRow[]> = {};
    /** Lists the configured connections, for the toolbar's dropdown. */
    #listConnections: () => Promise<string[]> = () => {
        return Promise.resolve([]);
    };
    /** Lists the connections open on one URI, for the second dropdown. */
    #listSessions: (connection: string) => string[] = () => {
        return [];
    };
    /** Puts the cursor on the statement an action row came from. */
    #onReveal?: (source: IStatementSource) => Promise<void>;
    /** Grid values opened in editors, served as files. */
    public readonly valueDocuments = new ValueDocuments();
    /** Saved values waiting for their grid to take them, by request. */
    readonly #pendingEdits = new Map<string, {
        resolve: () => void;
        reject: (error: Error) => void;
    }>();
    #editCount = 0;
    readonly #onClose: vscode.Disposable;

    public constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly log: (message: string) => void,
    ) {
        // A closed value editor's value is not needed any more.
        this.#onClose = vscode.workspace.onDidCloseTextDocument((document) => {
            this.valueDocuments.forget(document.uri);
        });
    }

    /**
     * Sets what happens when a grid asks to be reloaded.
     *
     * @param handler Re-runs the result set's statement, on the
     *                connection the target names.
     *
     * @returns Nothing.
     */
    public setRefreshHandler(
        handler: (
            resultSet: IResultSet,
            target: IRefreshTarget,
        ) => Promise<void>,
    ): void {
        this.#onRefresh = handler;
    }

    /**
     * Sets where the toolbar's connection list comes from.
     *
     * @param lister Lists the configured connections.
     *
     * @returns Nothing.
     */
    public setConnectionLister(
        lister: () => Promise<string[]>,
    ): void {
        this.#listConnections = lister;
    }

    /**
     * Sets where the second dropdown's list of open connections comes
     * from.
     *
     * @param lister Names the connections open on one URI.
     *
     * @returns Nothing.
     */
    public setSessionLister(
        lister: (connection: string) => string[],
    ): void {
        this.#listSessions = lister;
    }

    /**
     * Sets what happens when an action row asks to be shown in the
     * editor it came from.
     *
     * @param handler Puts the cursor on the statement.
     *
     * @returns Nothing.
     */
    public setRevealHandler(
        handler: (source: IStatementSource) => Promise<void>,
    ): void {
        this.#onReveal = handler;
    }

    /**
     * Called by VS Code when the view first becomes visible.
     *
     * @param view The view to fill.
     *
     * @returns Nothing.
     */
    public resolveWebviewView(view: vscode.WebviewView): void {
        this.#view = view;
        this.#ready = false;

        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this.extensionUri, "dist"),
            ],
        };
        view.webview.html = buildViewHtml(view.webview, this.extensionUri);

        view.webview.onDidReceiveMessage((message: WebviewMessage) => {
            void this.#onMessage(message);
        });

        view.onDidDispose(() => {
            this.#view = undefined;
            this.#ready = false;
            this.#pending = [];
        });
    }

    /**
     * Brings the result view to the front of the bottom panel.
     *
     * @returns Nothing.
     */
    public async reveal(): Promise<void> {
        if (this.#view) {
            // preserveFocus: the editor keeps the caret, which matters
            // when this is triggered by running the file.
            this.#view.show(true);

            return;
        }

        // Nothing has resolved the view yet, so it has to be focused by
        // command; that is what makes VS Code create it.
        await vscode.commands.executeCommand(`${RESULT_VIEW_ID}.focus`);
    }

    /**
     * Opens a run in the actions, before anything has been run.
     *
     * The row goes in the moment the user asks for the run rather than
     * once the server answers, so a script that takes its time - or a
     * connection that has to be opened first - is visibly under way.
     * `showResults` then replaces it, matching on its id.
     *
     * The result set tabs of the previous run go with it: they stand for
     * the last run, and this is no longer it.
     *
     * A run whose result goes to an editor tab - a maximized result
     * set's Refresh, or one opened maximized to begin with - leaves the
     * panel's tabs as they are, and does not bring the panel up over the
     * editor the user is working in.
     *
     * @param connection The connection it runs on.
     * @param run The run's row, from `pendingRunRow()`.
     * @param into The editor tab its result goes to, if any.
     *
     * @returns Nothing.
     */
    public async startRun(
        connection: string,
        run: IActionRow,
        into?: RunInto,
    ): Promise<void> {
        const maximized = typeof into === "string"
            ? this.#maximized.get(into)
            : undefined;
        if (maximized || typeof into === "object") {
            this.#runInto.set(run.id, maximized?.key ?? into!);
            const results = this.#resultsFor(connection);
            results.actions.unshift(run);
            this.#trim(results);
            if (maximized) {
                maximized.run = run;
                this.#sendMaximized(maximized);
            }
            // Where nothing is on show yet - or only General Actions, by
            // default - the run is what the panel has to show, without
            // being brought up for it.
            if (this.#active === undefined || this.#generalByDefault) {
                this.#active = connection;
                this.#generalByDefault = false;
            }
            if (connection === this.#active) {
                await this.#sendState();
            }

            return;
        }

        await this.reveal();

        const results = this.#resultsFor(connection);
        const session = this.#sessionFor(results, sessionOf(run));
        results.actions.unshift(run);
        this.#trim(results);
        session.resultSets = [];
        session.applyContext = undefined;
        results.lastRun = sessionOf(run);

        this.#show(connection, sessionOf(run));
        await this.#sendState();
    }

    /**
     * Shows what an execution produced.
     *
     * A run that was opened with `startRun` is updated in place - its row
     * carries the same id - and one that was not is appended, so a report
     * that arrives on its own still shows. Only the result sets are
     * replaced outright, since their tabs stand for the last run.
     *
     * @param report What the execution produced.
     * @param applyContext What is needed to write grid edits back. Left
     *                     out when the execution failed before it could
     *                     produce anything editable.
     *
     * @returns Nothing.
     */
    public async showResults(
        report: IExecutionReport,
        applyContext?: IApplyContext,
    ): Promise<void> {
        const runId = report.actions[0]?.id ?? "";
        const into = this.#runInto.get(runId);
        this.#runInto.delete(runId);
        const maximized = typeof into === "string"
            ? this.#maximized.get(into)
            : undefined;
        if (maximized) {
            this.#fillMaximized(maximized, report, applyContext);

            return;
        }
        if (typeof into === "object") {
            await this.#openMaximizedRun(report, into.title, applyContext);

            return;
        }

        await this.reveal();

        const results = this.#resultsFor(report.connection);
        this.#mergeActions(results, report.actions);

        // Which connection ran is read off the run's own row, so a report
        // cannot say one thing and the rows it carries another.
        const label = sessionOf(report.actions[0]);
        const session = this.#sessionFor(results, label);
        session.resultSets = report.resultSets;
        session.applyContext = applyContext;
        results.lastRun = label;

        this.#show(report.connection, label);
        await this.#sendState();
    }

    /**
     * Puts up what happened on a connection outside a run: it being
     * opened or closed, and every `db.*` call made on it.
     *
     * It goes to the top, as everything does: the row of a run that is
     * still going was put up before the connection it needed was even
     * opened, so the opening of that connection lands above it - which
     * is the order the two happened in.
     *
     * Unlike a run this does not reveal the view. Browsing the schema
     * tree is not a reason to throw the panel open over what the user is
     * reading; the rows are simply there when they next look.
     *
     * @param event What happened.
     *
     * @returns Nothing.
     */
    public async appendEvent(event: IActivityEvent): Promise<void> {
        const results = this.#resultsFor(event.connection);
        this.#eventCount += 1;
        results.actions.unshift(
            activityRow(event, `event${this.#eventCount}`));
        this.#trim(results);

        // The first thing to happen at all is what the view comes up on,
        // so a tree that was browsed before anything was run is not
        // looking at an empty panel. General Actions only holds the view
        // until a connection reports something: with the logging on, it
        // is often first - listing the connections is - and an empty panel
        // is no better for it, but it is there to be looked up rather than
        // to stand in front of the connection being worked on.
        if (this.#active === undefined) {
            this.#active = event.connection;
            this.#generalByDefault = event.connection === GENERAL_ACTIONS;
        } else if (this.#generalByDefault
            && event.connection !== GENERAL_ACTIONS) {
            this.#active = event.connection;
            this.#generalByDefault = false;
        }

        if (event.connection === this.#active) {
            await this.#sendState();
        }
    }

    /**
     * Shows another connection's actions and results.
     *
     * @param connection The connection to show.
     *
     * @returns Nothing.
     */
    public async selectConnection(connection: string): Promise<void> {
        this.#active = connection;
        this.#generalByDefault = false;
        // Another connection's connections are not this one's, so the
        // filter goes back to showing all of them.
        this.#activeSession = undefined;
        await this.#sendState();
    }

    /**
     * Narrows the actions to one of the connections open on the URI on
     * show, or widens it to all of them.
     *
     * @param session The connection to show, or undefined for all.
     *
     * @returns Nothing.
     */
    public async selectSession(session?: string): Promise<void> {
        this.#activeSession = session;
        await this.#sendState();
    }

    /**
     * Empties the view for the connection on show: its gathered actions
     * and its result set tabs both go.
     *
     * @returns Nothing.
     */
    public async clear(): Promise<void> {
        if (this.#active !== undefined) {
            const results = this.#resultsFor(this.#active);
            const session = this.#activeSession;

            if (session === undefined) {
                results.actions = [];
                results.sessions.clear();
                results.lastRun = undefined;
            } else {
                // Only what is on show goes: the rows of the other
                // connections are not what was being read.
                results.actions = results.actions.filter((row) => {
                    return sessionOf(row) !== session;
                });
                results.sessions.delete(session);
                if (results.lastRun === session) {
                    results.lastRun = undefined;
                }
            }
        }

        await this.#sendState();
    }

    /**
     * @returns The connection whose results are on show.
     */
    public get activeConnection(): string | undefined {
        return this.#active;
    }

    /**
     * @returns Which connection open on it is on show, or undefined when
     *          all of them are.
     */
    public get activeSession(): string | undefined {
        return this.#activeSession;
    }

    /**
     * @param connection The connection to look up.
     *
     * @returns What happened on it, newest first, across every
     *          connection open on it.
     */
    public actionsFor(connection: string): IActionRow[] {
        return this.#byConnection.get(connection)?.actions ?? [];
    }

    /**
     * @param connection The connection to look up.
     *
     * @returns The result sets on show for it.
     */
    public resultSetsFor(connection: string): IResultSet[] {
        return this.#sessionResults(connection)?.resultSets ?? [];
    }

    /**
     * @returns What the view needs to write grid edits back, for the
     *          connection on show.
     */
    public get applyContext(): IApplyContext | undefined {
        return this.#active === undefined
            ? undefined
            : this.#sessionResults(this.#active)?.applyContext;
    }

    /**
     * Drops the view's state.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#onClose.dispose();
        for (const pending of this.#pendingEdits.values()) {
            pending.reject(new Error("The result view was closed."));
        }
        this.#pendingEdits.clear();
        for (const maximized of this.#maximized.values()) {
            maximized.dispose();
        }
        this.#maximized.clear();
        this.#runInto.clear();
        this.#view = undefined;
        this.#byConnection.clear();
        this.#active = undefined;
        this.#generalByDefault = false;
        this.#activeSession = undefined;
        this.#pending = [];
    }

    /**
     * Handles a message from the frontend.
     *
     * @param message The message that arrived.
     *
     * @returns Nothing.
     */
    async #onMessage(message: WebviewMessage): Promise<void> {
        switch (message.type) {
            case "ready": {
                this.#ready = true;
                const pending = this.#pending;
                this.#pending = [];
                for (const queued of pending) {
                    this.#send(queued);
                }

                // A view VS Code re-created after being hidden starts
                // empty, so what it was showing is sent again.
                if (this.#active !== undefined) {
                    await this.#sendState();
                }
                break;
            }

            case "selectConnection": {
                await this.selectConnection(message.connection);
                break;
            }

            case "selectSession": {
                await this.selectSession(message.session);
                break;
            }

            case "applyChanges": {
                const active = this.#active;
                await this.#applyChanges(
                    this.#resultSet(message.resultId),
                    this.applyContext,
                    active === undefined
                        ? ""
                        : this.#shownSession(active) ?? "",
                    message.resultId,
                    message.changes,
                    (reply) => { this.#send(reply); },
                );
                break;
            }

            case "refresh": {
                await this.#refresh(message.resultId);
                break;
            }

            case "maximize": {
                await this.#maximize(message.resultId, message.rows);
                break;
            }

            case "page": {
                const held = this.#active === undefined
                    ? undefined
                    : this.#sessionResults(this.#active);
                await this.#fetchPage(
                    this.#resultSet(message.resultId),
                    this.applyContext,
                    this.#active === undefined
                        ? ""
                        : this.#shownSession(this.#active) ?? "",
                    message.page,
                    (next) => {
                        if (held) {
                            held.resultSets = held.resultSets.map((set) => {
                                return set.id === next.id ? next : set;
                            });
                        }
                        void this.#sendState();
                    },
                    (reply) => { this.#send(reply); },
                );
                break;
            }

            case "closeResult": {
                const held = this.#active === undefined
                    ? undefined
                    : this.#sessionResults(this.#active);
                if (held) {
                    held.resultSets = held.resultSets.filter((set) => {
                        return set.id !== message.resultId;
                    });
                    await this.#sendState();
                }
                break;
            }

            case "revealStatement":
            case "copyToClipboard":
            case "saveValue":
            case "loadValue":
            case "openValue":
            case "valueEditResult": {
                await this.#onShared(message, (reply) => {
                    this.#send(reply);
                }, false);
                break;
            }
        }
    }

    /**
     * Handles what the panel and a maximized tab do alike.
     *
     * @param message The message that arrived.
     * @param send Replies to the page that sent it.
     * @param maximized Whether it came from a maximized result set, whose
     *                  value editors open beside it rather than in the
     *                  active editor group.
     *
     * @returns Nothing.
     */
    async #onShared(
        message: WebviewMessage,
        send: (reply: HostMessage) => void,
        maximized: boolean,
    ): Promise<void> {
        switch (message.type) {
            case "revealStatement": {
                try {
                    await this.#onReveal?.(message.source);
                } catch (error) {
                    this.log("Could not show the statement: "
                        + `${error instanceof Error
                            ? error.message
                            : String(error)}`);
                }
                break;
            }

            case "copyToClipboard": {
                await vscode.env.clipboard.writeText(message.text);
                break;
            }

            case "saveValue": {
                try {
                    const saved = await saveValueToFile(
                        message.value, message.name);
                    if (saved) {
                        this.log(`Saved a value to ${saved.fsPath}`);
                    }
                } catch (error) {
                    const text = error instanceof Error
                        ? error.message
                        : String(error);
                    this.log(`Could not save the value: ${text}`);
                    void vscode.window.showErrorMessage(
                        `Could not save the value: ${text}`);
                }
                break;
            }

            case "openValue": {
                const { resultId, rowIndex, column, pageKey } = message;
                const uri = this.valueDocuments.put(
                    `${resultId}/${rowIndex}/${column}`,
                    {
                        value: message.value,
                        kind: message.kind,
                        name: message.name,
                        readOnly: message.readOnly,
                        writeBack: (value) => {
                            return this.#writeBack(send, {
                                resultId, rowIndex, column, pageKey,
                            }, value);
                        },
                    },
                );
                try {
                    // `vscode.open` picks the editor the file's type calls
                    // for: text for text, the image preview for a picture.
                    await vscode.commands.executeCommand("vscode.open", uri, {
                        viewColumn: maximized
                            ? vscode.ViewColumn.Beside
                            : vscode.ViewColumn.Active,
                        preview: false,
                    });
                } catch (error) {
                    this.log("Could not open the value: "
                        + `${error instanceof Error
                            ? error.message
                            : String(error)}`);
                }
                break;
            }

            case "valueEditResult": {
                const pending = this.#pendingEdits.get(message.requestId);
                this.#pendingEdits.delete(message.requestId);
                if (message.error === undefined) {
                    pending?.resolve();
                } else {
                    pending?.reject(new Error(message.error));
                }
                break;
            }

            case "loadValue": {
                try {
                    send({
                        type: "valueLoaded",
                        requestId: message.requestId,
                        value: await loadValueFromFile(),
                    });
                } catch (error) {
                    const text = error instanceof Error
                        ? error.message
                        : String(error);
                    this.log(`Could not load the value: ${text}`);
                    send({
                        type: "valueLoaded",
                        requestId: message.requestId,
                        error: text,
                    });
                }
                break;
            }

            default:
        }
    }

    /**
     * Handles a message from a maximized result set's tab.
     *
     * @param maximized The tab it came from.
     * @param message The message that arrived.
     *
     * @returns Nothing.
     */
    async #onMaximizedMessage(
        maximized: MaximizedResult,
        message: WebviewMessage,
    ): Promise<void> {
        switch (message.type) {
            case "ready": {
                // A tab VS Code re-created starts empty, as the panel does.
                this.#sendMaximized(maximized);
                break;
            }

            case "applyChanges": {
                const matches = message.resultId === maximized.resultSet.id;
                await this.#applyChanges(
                    matches ? maximized.resultSet : undefined,
                    maximized.applyContext,
                    maximized.origin.session,
                    message.resultId,
                    message.changes,
                    (reply) => { maximized.send(reply); },
                );
                break;
            }

            case "refresh": {
                await this.#runRefresh(maximized.resultSet, {
                    connectionUri: maximized.applyContext?.connectionUri
                        ?? maximized.origin.connection,
                    into: maximized.key,
                });
                break;
            }

            case "minimize": {
                await this.#minimize(maximized, message.rows);
                break;
            }

            case "page": {
                await this.#fetchPage(
                    message.resultId === maximized.resultSet.id
                        ? maximized.resultSet
                        : undefined,
                    maximized.applyContext,
                    maximized.origin.session,
                    message.page,
                    (next) => {
                        maximized.replace(next);
                        this.#sendMaximized(maximized);
                    },
                    (reply) => { maximized.send(reply); },
                );
                break;
            }

            case "closeResult": {
                // As the tab's own close button does: it goes with it.
                this.#maximized.delete(maximized.key);
                maximized.dispose();
                break;
            }

            case "revealStatement":
            case "copyToClipboard":
            case "saveValue":
            case "loadValue":
            case "openValue":
            case "valueEditResult": {
                await this.#onShared(message, (reply) => {
                    maximized.send(reply);
                }, true);
                break;
            }

            default:
        }
    }

    /**
     * Moves a result set out of the panel into an editor tab of its own.
     *
     * The rows it already holds go with it, and so do the edits it has
     * pending: nothing is run again to fill the tab. It leaves the panel's
     * tabs while it is away, as a tab dragged out of a group would.
     *
     * @param resultId The result set to move.
     * @param rows Its rows as the grid holds them, pending edits and all.
     *
     * @returns Nothing.
     */
    async #maximize(resultId: string, rows: IEditableRow[]): Promise<void> {
        if (this.#active === undefined) {
            return;
        }

        const connection = this.#active;
        const session = this.#shownSession(connection);
        const held = this.#sessionResults(connection);
        const position = held?.resultSets.findIndex((set) => {
            return set.id === resultId;
        }) ?? -1;
        if (!held || session === undefined || position === -1) {
            return;
        }

        const [resultSet] = held.resultSets.splice(position, 1);
        this.#openMaximized(
            { connection, session, position },
            resultSet,
            undefined,
            held.applyContext,
            { [resultSet.id]: rows },
        );

        await this.#sendState();
    }

    /**
     * Opens an editor tab for a result set.
     *
     * @param origin Where it goes back to when minimized.
     * @param resultSet The result set to show.
     * @param title The tab's title, in place of the statement's start.
     * @param applyContext What its edits are written back through.
     * @param editing Pending edits to start it from.
     *
     * @returns The tab.
     */
    #openMaximized(
        origin: IMaximizedOrigin,
        resultSet: IResultSet,
        title: string | undefined,
        applyContext: IApplyContext | undefined,
        editing?: Record<string, IEditableRow[]>,
    ): MaximizedResult {
        this.#maximizedCount += 1;
        const key = `maximized${this.#maximizedCount}`;
        const maximized: MaximizedResult = new MaximizedResult(
            key,
            origin,
            resultSet,
            title,
            (webview) => { return buildViewHtml(webview, this.extensionUri); },
            this.extensionUri,
            (message) => {
                void this.#onMaximizedMessage(maximized, message);
            },
            () => {
                // Closed by the user: the result set goes with the tab,
                // as a tab closed in the panel would. Minimize is how it
                // comes back.
                this.#maximized.delete(key);
            },
        );
        maximized.applyContext = applyContext;
        this.#maximized.set(key, maximized);
        this.#sendMaximized(maximized, editing);

        return maximized;
    }

    /**
     * Shows what a run that was to open maximized came to.
     *
     * The run goes into the actions like any other, and its first result
     * set opens in an editor tab, minimizing to the front of its
     * connection's tabs. One that produced no result set - it failed -
     * has nothing to open, so the panel is brought up on the run instead,
     * which is where its error is; the tabs there are left alone, since
     * this run was never going to replace them.
     *
     * @param report What the run produced.
     * @param title The tab's title.
     * @param applyContext What is needed to write grid edits back.
     *
     * @returns Nothing.
     */
    async #openMaximizedRun(
        report: IExecutionReport,
        title: string,
        applyContext?: IApplyContext,
    ): Promise<void> {
        const results = this.#resultsFor(report.connection);
        this.#mergeActions(results, report.actions);

        const label = sessionOf(report.actions[0]);
        const [resultSet] = report.resultSets;
        if (resultSet) {
            const maximized = this.#openMaximized(
                { connection: report.connection, session: label, position: 0 },
                resultSet,
                title,
                applyContext,
            );
            maximized.run = report.actions[0];
            this.#sendMaximized(maximized);
            if (report.connection === this.#active) {
                await this.#sendState();
            }

            return;
        }

        await this.reveal();
        this.#show(report.connection, label);
        await this.#sendState();
    }

    /**
     * Puts a report's rows into a connection's actions: a run that was
     * started is replaced where it is, anything else goes on top.
     *
     * @param results The connection's state.
     * @param rows The report's rows.
     *
     * @returns Nothing.
     */
    #mergeActions(results: IConnectionResults, rows: IActionRow[]): void {
        for (const row of rows) {
            const at = results.actions.findIndex((existing) => {
                return existing.id === row.id;
            });
            if (at === -1) {
                results.actions.unshift(row);
            } else {
                results.actions[at] = row;
            }
        }
        this.#trim(results);
    }

    /**
     * Puts a maximized result set back among the panel's tabs, where it
     * was, and closes its editor tab.
     *
     * @param maximized The tab to put back.
     * @param rows Its rows as the grid holds them, pending edits and all.
     *
     * @returns Nothing.
     */
    async #minimize(
        maximized: MaximizedResult,
        rows: IEditableRow[],
    ): Promise<void> {
        const { connection, session: label, position } = maximized.origin;
        const results = this.#resultsFor(connection);
        const session = this.#sessionFor(results, label);
        session.resultSets.splice(
            Math.min(position, session.resultSets.length), 0,
            maximized.resultSet);
        // A later run on the connection has its own; one that was cleared
        // away has none, and the result set is edited through its own.
        session.applyContext ??= maximized.applyContext;

        this.#maximized.delete(maximized.key);
        maximized.dispose();

        // It has to be on show once it is back, so the view goes to its
        // connection - and to the connection open on it that produced it,
        // where that is not the one whose tabs are on show.
        this.#active = connection;
        this.#generalByDefault = false;
        results.lastRun ??= label;
        if (this.#shownSession(connection) !== label) {
            this.#activeSession = label;
        }

        this.#seed[maximized.resultSet.id] = rows;
        await this.reveal();
        await this.#sendState();
    }

    /**
     * Shows what a maximized result set's refresh came to, in its tab.
     *
     * The run goes into the actions like any other; only its result set
     * goes to the tab instead of the panel. A statement can produce more
     * than one - a CALL does - and the tab holds one, so it takes the
     * first. A run that produced none leaves the old rows on show, its
     * error in the tab's bar.
     *
     * @param maximized The tab the refresh was started from.
     * @param report What the run produced.
     * @param applyContext What is needed to write grid edits back.
     *
     * @returns Nothing.
     */
    #fillMaximized(
        maximized: MaximizedResult,
        report: IExecutionReport,
        applyContext?: IApplyContext,
    ): void {
        const results = this.#resultsFor(report.connection);
        this.#mergeActions(results, report.actions);

        maximized.run = report.actions[0];
        const [resultSet] = report.resultSets;
        if (resultSet) {
            maximized.replace(resultSet);
        }
        if (applyContext) {
            maximized.applyContext = applyContext;
        }
        this.#sendMaximized(maximized);

        if (report.connection === this.#active) {
            void this.#sendState();
        }
    }

    /**
     * Sends a maximized tab what it shows: its one result set, and the
     * run of its last refresh for the error bar.
     *
     * @param maximized The tab to send to.
     * @param editing Pending edits to start the result set from.
     *
     * @returns Nothing.
     */
    #sendMaximized(
        maximized: MaximizedResult,
        editing?: Record<string, IEditableRow[]>,
    ): void {
        maximized.send({
            type: "state",
            state: {
                connections: [],
                connection: maximized.origin.connection,
                sessions: [],
                actions: maximized.run ? [maximized.run] : [],
                resultSets: [maximized.resultSet],
                maximized: true,
                freezeKeyColumns: freezePrimaryKeyColumns(),
            },
            editing,
        });
    }

    /**
     * @param connection The connection to look up.
     *
     * @returns Its state, created empty if it has none yet.
     */
    #resultsFor(connection: string): IConnectionResults {
        let results = this.#byConnection.get(connection);
        if (!results) {
            results = { actions: [], sessions: new Map() };
            this.#byConnection.set(connection, results);
        }

        return results;
    }

    /**
     * @param results The connection's state.
     * @param label The connection open on it.
     *
     * @returns What the view holds for it, created empty if it has none.
     */
    #sessionFor(
        results: IConnectionResults,
        label: string,
    ): ISessionResults {
        let session = results.sessions.get(label);
        if (!session) {
            session = { resultSets: [] };
            results.sessions.set(label, session);
        }

        return session;
    }

    /**
     * Which connection's tabs are on show: the one picked, and otherwise
     * the one that ran last - the tabs stand for the last run, whichever
     * connection it was on.
     *
     * @param connection The connection URI on show.
     *
     * @returns Its label, or undefined where nothing has run yet.
     */
    #shownSession(connection: string): string | undefined {
        return this.#activeSession
            ?? this.#byConnection.get(connection)?.lastRun;
    }

    /**
     * @param connection The connection URI on show.
     *
     * @returns The result sets and apply context on show for it.
     */
    #sessionResults(connection: string): ISessionResults | undefined {
        const shown = this.#shownSession(connection);

        return shown === undefined
            ? undefined
            : this.#byConnection.get(connection)?.sessions.get(shown);
    }

    /**
     * Brings a run into view: its connection, and its own rows.
     *
     * A filter that would hide the run that is starting is dropped
     * rather than left in force, since the point of putting a run up the
     * moment it starts is that it can be watched.
     *
     * @param connection The connection the run is on.
     * @param label The connection open on it that the run is on.
     *
     * @returns Nothing.
     */
    #show(connection: string, label: string): void {
        this.#active = connection;
        this.#generalByDefault = false;
        if (this.#activeSession !== undefined
            && this.#activeSession !== label) {
            this.#activeSession = undefined;
        }
    }

    /**
     * Drops the oldest runs once a connection has gathered too much.
     *
     * The oldest are at the end, since the newest are what the log is
     * read from. A run counts as its own row plus its statements: the
     * tree cannot keep half a run, so the ceiling is enforced a whole
     * run at a time, and the newest one is kept however long it is.
     *
     * @param results The connection's state, trimmed in place.
     *
     * @returns Nothing.
     */
    #trim(results: IConnectionResults): void {
        // Counted through the whole tree, not one level of it: a
        // statement's warnings are rows of the log as much as the
        // statement is, and a run of them would otherwise push the
        // history well past the cap without the cap noticing.
        const sizeOf = (row: IActionRow): number => {
            return (row.children ?? []).reduce((sum, child) => {
                return sum + sizeOf(child);
            }, 1);
        };

        let total = results.actions.reduce((sum, row) => {
            return sum + sizeOf(row);
        }, 0);

        while (total > MAX_ACTION_ROWS && results.actions.length > 1) {
            total -= sizeOf(results.actions[results.actions.length - 1]);
            results.actions.pop();
        }
    }

    /**
     * Sends the frontend everything it shows for the active connection.
     *
     * @returns Nothing.
     */
    async #sendState(): Promise<void> {
        if (this.#active === undefined) {
            return;
        }

        const connection = this.#active;
        const results = this.#resultsFor(connection);
        const sessions = this.#sessionsOf(connection);

        // A connection that has been picked and has since gone - closed,
        // and its actions cleared - leaves the view showing all of them
        // rather than showing nothing at all.
        if (this.#activeSession !== undefined && !sessions.some((session) => {
            return session.label === this.#activeSession;
        })) {
            this.#activeSession = undefined;
        }
        const session = this.#activeSession;

        // Shown beside the view's title, the way the Output panel names
        // the channel it is showing.
        if (this.#view) {
            this.#view.description = connection;
        }

        const state: IViewState = {
            connections: await this.#knownConnections(),
            connection,
            sessions,
            session,
            // Filtered here rather than in the page: what the page shows
            // is what it is given, so a row cannot be counted in one
            // place and hidden in another.
            actions: session === undefined
                ? results.actions
                : results.actions.filter((row) => {
                    return sessionOf(row) === session;
                }),
            resultSets: this.#sessionResults(connection)?.resultSets ?? [],
            freezeKeyColumns: freezePrimaryKeyColumns(),
        };

        // Held until there is a view to take them: one that is still
        // being created would otherwise drop them.
        const editing = this.#view ? this.#seed : {};
        if (this.#view) {
            this.#seed = {};
        }

        this.#send({
            type: "state",
            state,
            ...Object.keys(editing).length > 0 ? { editing } : {},
        });
    }

    /**
     * The connections to offer for one URI: the ones open on it, plus any
     * that only its gathered actions still remember.
     *
     * A connection that has been closed stays in the list for as long as
     * its rows do - the log outlives the connection - and says that it is
     * closed.
     *
     * @param connection The connection URI on show.
     *
     * @returns Its connections, numbered ones first.
     */
    #sessionsOf(connection: string): IConnectionSession[] {
        const open = new Set(this.#listSessions(connection));
        const logged = new Set(this.#resultsFor(connection).actions
            .map((row) => { return sessionOf(row); })
            // The empty key stands for rows that named no connection,
            // which is nothing to offer a filter for.
            .filter((label) => { return label !== ""; }));

        return [...new Set([...open, ...logged])]
            .sort((first, second) => {
                const asNumbers = Number(first) - Number(second);

                return Number.isNaN(asNumbers)
                    ? first.localeCompare(second)
                    : asNumbers;
            })
            .map((label) => {
                return { label, open: open.has(label) };
            });
    }

    /**
     * Every connection worth offering: the configured ones, plus any
     * already run on, so a connection cannot go missing from its own
     * actions.
     *
     * @returns The connection URIs, sorted.
     */
    async #knownConnections(): Promise<string[]> {
        let configured = this.#configured?.uris ?? [];

        // Asking the server costs two tool calls, and state is sent for
        // every row that appears - including one per `db.*` call on the
        // connection. The configured list only changes when the user
        // edits one, so a few seconds stale in the picker is worth more
        // than a round trip per row.
        if (this.#configured === undefined
            || Date.now() - this.#configured.at > CONNECTION_LIST_TTL_MS) {
            try {
                configured = await this.#listConnections();
                this.#configured = { at: Date.now(), uris: configured };
            } catch (error) {
                // The list is worth less than what is already gathered,
                // so a server that cannot produce it is not fatal here.
                this.log("Could not list the connections for the result "
                    + `view: ${error instanceof Error
                        ? error.message
                        : String(error)}`);
            }
        }

        const known = new Set([
            ...configured,
            ...this.#byConnection.keys(),
        ]);
        if (this.#active !== undefined) {
            known.add(this.#active);
        }

        // Not a connection, so it goes first rather than among them - and
        // only once something is filed under it.
        const general = known.delete(GENERAL_ACTIONS);

        return [...(general ? [GENERAL_ACTIONS] : []), ...[...known].sort()];
    }

    /**
     * Re-runs the statement behind a result set.
     *
     * @param resultId The result set to reload.
     *
     * @returns Nothing.
     */
    async #refresh(resultId: string): Promise<void> {
        const resultSet = this.#resultSet(resultId);
        if (!resultSet) {
            return;
        }

        await this.#runRefresh(resultSet, {
            connectionUri: this.applyContext?.connectionUri,
        });
    }

    /**
     * Hands a result set to the refresh handler, logging a failure rather
     * than letting it escape into the message loop.
     *
     * @param resultSet The result set to reload.
     * @param target Where it runs, and where its result goes.
     *
     * @returns Nothing.
     */
    async #runRefresh(
        resultSet: IResultSet,
        target: IRefreshTarget,
    ): Promise<void> {
        if (!this.#onRefresh) {
            return;
        }

        try {
            await this.#onRefresh(resultSet, target);
        } catch (error) {
            const text = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to refresh the result set: ${text}`);
        }
    }

    /**
     * Writes a grid's edits back and reports what happened.
     *
     * @param resultSet The result set that was edited, if it is still
     *                  held.
     * @param applyContext What it is written back through.
     * @param label The connection open on the URI that produced it.
     * @param resultId The id the grid knows it by.
     * @param changes The changes to apply.
     * @param send Replies to the grid that asked.
     *
     * @returns Nothing.
     */
    async #applyChanges(
        resultSet: IResultSet | undefined,
        applyContext: IApplyContext | undefined,
        label: string,
        resultId: string,
        changes: RowChange[],
        send: (message: HostMessage) => void,
    ): Promise<void> {
        if (!resultSet || !applyContext) {
            send({
                type: "applied",
                resultId,
                statements: [],
                error: "The result set is no longer available.",
            });

            return;
        }

        // Writing a grid back runs SQL on the connection like anything
        // else, so it is a row of that connection's actions too - the one
        // place the statements it ran can be read afterwards.
        const when = new Date();
        const startedMs = Date.now();
        const event = {
            connection: applyContext.connectionUri,
            label,
            call: "db.execute_sql_script()",
            when,
        };

        try {
            const statements = await applyContext.service.applyChanges(
                applyContext.connectionId,
                resultSet,
                changes,
            );
            for (const statement of statements) {
                this.log(`Applied: ${statement}`);
            }
            await this.appendEvent({
                ...event,
                message: `Applied ${statements.length} statement`
                    + `${statements.length === 1 ? "" : "s"}`,
                elapsedMs: Date.now() - startedMs,
            });
            send({ type: "applied", resultId, statements });
        } catch (error) {
            const text = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to apply changes: ${text}`);
            await this.appendEvent({
                ...event,
                message: "",
                error: text,
                elapsedMs: Date.now() - startedMs,
            });
            send({
                type: "applied",
                resultId,
                statements: [],
                error: text,
            });
        }
    }

    /**
     * Hands a saved value to the grid it came from and waits for it to be
     * taken, so a save whose cell is gone fails rather than vanishing.
     *
     * @param send Reaches the page the value was opened from.
     * @param cell Which cell, and the page it was opened from.
     * @param value The saved value, as the grid holds it.
     *
     * @returns Once the grid has taken it; rejects with its reason.
     */
    #writeBack(
        send: (message: HostMessage) => void,
        cell: {
            resultId: string;
            rowIndex: number;
            column: string;
            pageKey: string;
        },
        value: string,
    ): Promise<void> {
        this.#editCount += 1;
        const requestId = `edit${this.#editCount}`;

        return new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => {
                this.#pendingEdits.delete(requestId);
                reject(new Error("The result set did not answer; it may have "
                    + "been closed."));
            }, VALUE_EDIT_TIMEOUT_MS);
            this.#pendingEdits.set(requestId, {
                resolve: () => {
                    clearTimeout(timer);
                    resolve();
                },
                reject: (error) => {
                    clearTimeout(timer);
                    reject(error);
                },
            });
            send({ type: "valueEdited", requestId, ...cell, value });
        });
    }

    /**
     * Fetches another page of a result set's rows and puts it on show.
     *
     * Like an apply, it runs SQL on the connection, so it is a row of the
     * connection's actions. A page that cannot be fetched leaves the one
     * on show, and the grid is told why.
     *
     * @param resultSet The result set to page, if it is still held.
     * @param applyContext The connection it is fetched on.
     * @param label The connection open on the URI that produced it.
     * @param page Which page, from 0.
     * @param show Puts the new page where the result set is held.
     * @param send Replies to the grid that asked.
     *
     * @returns Nothing.
     */
    async #fetchPage(
        resultSet: IResultSet | undefined,
        applyContext: IApplyContext | undefined,
        label: string,
        page: number,
        show: (next: IResultSet) => void,
        send: (message: HostMessage) => void,
    ): Promise<void> {
        if (!resultSet || !applyContext) {
            return;
        }

        const startedMs = Date.now();
        const event = {
            connection: applyContext.connectionUri,
            label,
            call: "db.execute_sql()",
            when: new Date(),
        };

        try {
            const next = await applyContext.service.fetchPage(
                applyContext.connectionId, resultSet, page);
            show(next);
            await this.appendEvent({
                ...event,
                message: `Page ${page + 1}: ${next.status}`,
                elapsedMs: Date.now() - startedMs,
            });
        } catch (error) {
            const text = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to fetch page ${page + 1}: ${text}`);
            await this.appendEvent({
                ...event,
                message: "",
                error: text,
                elapsedMs: Date.now() - startedMs,
            });
            send({ type: "pageFailed", resultId: resultSet.id, error: text });
        }
    }

    /**
     * @param resultId The result set to look up.
     *
     * @returns It, if the connection on show still holds it.
     */
    #resultSet(resultId: string): IResultSet | undefined {
        if (this.#active === undefined) {
            return undefined;
        }

        return this.#sessionResults(this.#active)?.resultSets
            .find((set) => {
                return set.id === resultId;
            });
    }

    /**
     * Sends a message, holding it back until the frontend is listening.
     *
     * @param message The message to send.
     *
     * @returns Nothing.
     */
    #send(message: HostMessage): void {
        if (!this.#view) {
            return;
        }

        if (!this.#ready) {
            this.#pending.push(message);

            return;
        }

        void this.#view.webview.postMessage(message);
    }
}
