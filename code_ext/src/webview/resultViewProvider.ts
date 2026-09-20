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

import type { ExecutionService } from "../sql/executionService.js";
import type {
    HostMessage,
    IExecutionReport,
    IOutputRow,
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

/** How many output rows one connection keeps before the oldest go. */
export const MAX_OUTPUT_ROWS = 2000;

/** What the view holds for one connection. */
interface IConnectionResults {
    /** Every run's output, oldest first. */
    output: IOutputRow[];
    /** The last run's result sets; a run replaces them. */
    resultSets: IResultSet[];
    applyContext?: IApplyContext;
}

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
    /** The connection whose output and results are on show. */
    #active?: string;
    /** Set once the frontend says it is listening. */
    #ready = false;
    #pending: HostMessage[] = [];
    /** Re-runs the statement behind a result set. */
    #onRefresh?: (resultSet: IResultSet) => Promise<void>;
    /** Lists the configured connections, for the toolbar's dropdown. */
    #listConnections: () => Promise<string[]> = () => {
        return Promise.resolve([]);
    };
    /** Puts the cursor on the statement an output row came from. */
    #onReveal?: (source: IStatementSource) => Promise<void>;

    public constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * Sets what happens when a grid asks to be reloaded.
     *
     * @param handler Re-runs the result set's statement.
     *
     * @returns Nothing.
     */
    public setRefreshHandler(
        handler: (resultSet: IResultSet) => Promise<void>,
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
     * Sets what happens when an output row asks to be shown in the
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
     * Tells the view that an execution has started.
     *
     * @param connection The connection it runs on.
     *
     * @returns Nothing.
     */
    public async showRunning(connection: string): Promise<void> {
        await this.reveal();
        this.#active = connection;
        this.#send({ type: "running", connection });
    }

    /**
     * Shows what an execution produced.
     *
     * The output is appended to whatever that connection has gathered so
     * far; only its result sets are replaced, since their tabs stand for
     * the last run.
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
        await this.reveal();

        const results = this.#resultsFor(report.connection);
        results.output.push(...report.output);
        if (results.output.length > MAX_OUTPUT_ROWS) {
            // Output accumulates for as long as a window is open, so it
            // needs a ceiling; the oldest rows go first.
            results.output.splice(
                0, results.output.length - MAX_OUTPUT_ROWS);
        }
        results.resultSets = report.resultSets;
        results.applyContext = applyContext;

        this.#active = report.connection;
        await this.#sendState();
    }

    /**
     * Shows another connection's output and results.
     *
     * @param connection The connection to show.
     *
     * @returns Nothing.
     */
    public async selectConnection(connection: string): Promise<void> {
        this.#active = connection;
        await this.#sendState();
    }

    /**
     * Empties the view for the connection on show: its gathered output
     * and its result set tabs both go.
     *
     * @returns Nothing.
     */
    public async clear(): Promise<void> {
        if (this.#active !== undefined) {
            const results = this.#resultsFor(this.#active);
            results.output = [];
            results.resultSets = [];
            // The tabs it belonged to are gone, so nothing can be
            // written back through it any more.
            results.applyContext = undefined;
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
     * @param connection The connection to look up.
     *
     * @returns Its output rows, oldest first.
     */
    public outputFor(connection: string): IOutputRow[] {
        return this.#byConnection.get(connection)?.output ?? [];
    }

    /**
     * @param connection The connection to look up.
     *
     * @returns Its result sets.
     */
    public resultSetsFor(connection: string): IResultSet[] {
        return this.#byConnection.get(connection)?.resultSets ?? [];
    }

    /**
     * @returns What the view needs to write grid edits back, for the
     *          connection on show.
     */
    public get applyContext(): IApplyContext | undefined {
        return this.#active === undefined
            ? undefined
            : this.#byConnection.get(this.#active)?.applyContext;
    }

    /**
     * Drops the view's state.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#view = undefined;
        this.#byConnection.clear();
        this.#active = undefined;
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

            case "applyChanges": {
                await this.#applyChanges(message.resultId, message.changes);
                break;
            }

            case "refresh": {
                await this.#refresh(message.resultId);
                break;
            }

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
        }
    }

    /**
     * @param connection The connection to look up.
     *
     * @returns Its state, created empty if it has none yet.
     */
    #resultsFor(connection: string): IConnectionResults {
        let results = this.#byConnection.get(connection);
        if (!results) {
            results = { output: [], resultSets: [] };
            this.#byConnection.set(connection, results);
        }

        return results;
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

        // Shown beside the view's title, the way the Output panel names
        // the channel it is showing.
        if (this.#view) {
            this.#view.description = connection;
        }

        const state: IViewState = {
            connections: await this.#knownConnections(),
            connection,
            output: results.output,
            resultSets: results.resultSets,
        };

        this.#send({ type: "state", state });
    }

    /**
     * Every connection worth offering: the configured ones, plus any
     * already run on, so a connection cannot go missing from its own
     * output.
     *
     * @returns The connection URIs, sorted.
     */
    async #knownConnections(): Promise<string[]> {
        let configured: string[] = [];
        try {
            configured = await this.#listConnections();
        } catch (error) {
            // The list is worth less than what is already gathered, so a
            // server that cannot produce it is not fatal here.
            this.log("Could not list the connections for the result "
                + `view: ${error instanceof Error
                    ? error.message
                    : String(error)}`);
        }

        const known = new Set([
            ...configured,
            ...this.#byConnection.keys(),
        ]);
        if (this.#active !== undefined) {
            known.add(this.#active);
        }

        return [...known].sort();
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
        if (!resultSet || !this.#onRefresh) {
            return;
        }

        try {
            await this.#onRefresh(resultSet);
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
     * @param resultId The result set that was edited.
     * @param changes The changes to apply.
     *
     * @returns Nothing.
     */
    async #applyChanges(
        resultId: string,
        changes: RowChange[],
    ): Promise<void> {
        const resultSet = this.#resultSet(resultId);
        const applyContext = this.applyContext;

        if (!resultSet || !applyContext) {
            this.#send({
                type: "applied",
                resultId,
                statements: [],
                error: "The result set is no longer available.",
            });

            return;
        }

        try {
            const statements = await applyContext.service.applyChanges(
                applyContext.connectionId,
                resultSet,
                changes,
            );
            for (const statement of statements) {
                this.log(`Applied: ${statement}`);
            }
            this.#send({ type: "applied", resultId, statements });
        } catch (error) {
            const text = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to apply changes: ${text}`);
            this.#send({
                type: "applied",
                resultId,
                statements: [],
                error: text,
            });
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

        return this.#byConnection.get(this.#active)?.resultSets
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
