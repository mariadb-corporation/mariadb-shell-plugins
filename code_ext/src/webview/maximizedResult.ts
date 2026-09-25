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

import { captionFor } from "../sql/executionService.js";
import type {
    HostMessage,
    IActionRow,
    IResultSet,
    WebviewMessage,
} from "./protocol.js";
import type { IApplyContext } from "./resultViewProvider.js";

/** The view type a maximized result set's editor tab is created under. */
export const MAXIMIZED_RESULT_VIEW_TYPE = "mariadb.maximizedResult";

/**
 * The longest statement an editor tab's title shows. A tab title is not
 * where a statement is read, and a long one squeezes every other tab.
 */
export const TITLE_LENGTH = 30;

/** Where a maximized result set came from, and what it needs. */
export interface IMaximizedOrigin {
    /** The connection URI it was produced on. */
    connection: string;
    /** Which of the connections open on that URI produced it. */
    session: string;
    /** Where it sat among that connection's tabs, to go back there. */
    position: number;
}

/**
 * One result set moved out of the bottom panel into an editor tab of its
 * own.
 *
 * It is the same frontend the panel runs, told by `maximized` in its state
 * to show the one result set and nothing that picks between others. The
 * result set moves with the rows it already holds, so nothing is run to
 * fill the tab.
 */
export class MaximizedResult implements vscode.Disposable {
    /** The result set on show; a refresh replaces it. */
    public resultSet: IResultSet;
    /** The run of the last refresh, whose errors its bar shows. */
    public run?: IActionRow;
    /** What its edits are written back through. */
    public applyContext?: IApplyContext;

    readonly #panel: vscode.WebviewPanel;
    #ready = false;
    #pending: HostMessage[] = [];
    /** Set once the tab is gone, so a late message is not sent to it. */
    #disposed = false;

    /**
     * @param key What the host knows this tab by. A refresh gives the
     *            result set a new id, so the id cannot be it.
     * @param origin Where the result set came from.
     * @param resultSet The result set to show.
     * @param fixedTitle The tab's title, kept across refreshes, in place of
     *                   the statement's start.
     * @param html Builds the page for the panel's webview.
     * @param extensionUri The root of the installed extension.
     * @param onMessage Handles what the frontend sends.
     * @param onClosed Called when the user closes the tab.
     */
    public constructor(
        public readonly key: string,
        public readonly origin: IMaximizedOrigin,
        resultSet: IResultSet,
        private readonly fixedTitle: string | undefined,
        html: (webview: vscode.Webview) => string,
        extensionUri: vscode.Uri,
        onMessage: (message: WebviewMessage) => void,
        onClosed: () => void,
    ) {
        this.resultSet = resultSet;
        this.#panel = vscode.window.createWebviewPanel(
            MAXIMIZED_RESULT_VIEW_TYPE,
            this.#title(),
            { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
            {
                enableScripts: true,
                // Pending edits have to survive another editor tab being
                // brought to the front, as they do in the panel.
                retainContextWhenHidden: true,
                localResourceRoots: [
                    vscode.Uri.joinPath(extensionUri, "dist"),
                ],
            },
        );
        this.#panel.iconPath = {
            light: vscode.Uri.joinPath(
                extensionUri, "images", "light", "schemaTable.svg"),
            dark: vscode.Uri.joinPath(
                extensionUri, "images", "dark", "schemaTable.svg"),
        };
        this.#panel.webview.html = html(this.#panel.webview);

        this.#panel.webview.onDidReceiveMessage((message: WebviewMessage) => {
            if (message.type === "ready") {
                this.#ready = true;
                const pending = this.#pending;
                this.#pending = [];
                for (const queued of pending) {
                    void this.#panel.webview.postMessage(queued);
                }
            }
            onMessage(message);
        });

        this.#panel.onDidDispose(() => {
            if (!this.#disposed) {
                this.#disposed = true;
                onClosed();
            }
        });
    }

    /**
     * Replaces the result set on show, after a refresh.
     *
     * @param resultSet What the statement came to this time.
     *
     * @returns Nothing.
     */
    public replace(resultSet: IResultSet): void {
        this.resultSet = resultSet;
        this.#panel.title = this.#title();
    }

    /**
     * Sends a message, holding it back until the frontend is listening.
     *
     * @param message The message to send.
     *
     * @returns Nothing.
     */
    public send(message: HostMessage): void {
        if (this.#disposed) {
            return;
        }

        if (!this.#ready) {
            this.#pending.push(message);

            return;
        }

        void this.#panel.webview.postMessage(message);
    }

    /**
     * Closes the tab without reporting it as closed by the user: it is
     * how the result set goes back to the panel.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        if (this.#disposed) {
            return;
        }

        this.#disposed = true;
        this.#panel.dispose();
    }

    /**
     * @returns The tab's title: the one it was opened with, or else the
     *          start of the statement - not the caption the panel's tab
     *          had, since every run numbers its results from one.
     */
    #title(): string {
        if (this.fixedTitle !== undefined) {
            return this.fixedTitle;
        }

        return captionFor(this.resultSet.statement, TITLE_LENGTH)
            || this.resultSet.caption;
    }
}
