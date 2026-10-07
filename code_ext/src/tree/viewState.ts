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

import type { IServerStatus } from "../mcp/serverStarter.js";

/**
 * What a view fed by the MCP server is showing when it has no rows.
 *
 * - `looking`: the server is being found or started.
 * - `installing`: the MariaDB Shell is being downloaded and installed.
 * - `failed`: the last attempt to list the rows failed.
 * - `listed`: the rows were listed - an empty view now means there are
 *   none.
 */
export type ViewState =
    | "looking"
    | "installing"
    | "failed"
    | "listed";

/**
 * Keeps a view's welcome content in step with the server's startup and
 * the view's own attempts at listing its rows.
 *
 * Both the Connections and the Sandboxes view are fed by the MCP server,
 * which has to be found - or downloaded - and started first, so an empty
 * view means "not asked yet" for as long as that takes. Each view hands
 * its state to a context key of its own, which is what picks the welcome
 * content; this does the bookkeeping behind that key for either of them.
 */
export class ViewStateTracker implements vscode.Disposable {
    readonly #unsubscribeStatus: () => void;
    /** How the last attempt at listing went, if there was one. */
    #listing: "unasked" | "listed" | "failed" = "unasked";
    /** The state last handed to the context key. */
    #shownState?: ViewState;

    /**
     * @param contextKey The context key the view's welcome content
     *                   switches on.
     * @param status How the server's startup is going, which is what the
     *               view says while it has nothing to list yet.
     */
    public constructor(
        private readonly contextKey: string,
        private readonly status?: IServerStatus,
    ) {
        this.#unsubscribeStatus = status?.onDidChangePhase((phase) => {
            // A new attempt is under way, so the last one's failure is no
            // longer what the view should be saying.
            if (phase === "locating" && this.#listing === "failed") {
                this.#listing = "unasked";
            }
            this.#show();
        }) ?? (() => { /* nothing to stop */ });
        this.#show();
    }

    /**
     * @returns What the view should say while it has no rows.
     */
    public get state(): ViewState {
        if (this.status?.phase === "installing") {
            return "installing";
        }

        // A server that did not start is a failure whoever asked for it;
        // rows listed earlier still hide the welcome content, so this only
        // shows where there is nothing else to show.
        if (this.#listing === "failed" || this.status?.phase === "failed") {
            return "failed";
        }

        return this.#listing === "listed" ? "listed" : "looking";
    }

    /**
     * Notes that the rows were listed.
     *
     * @returns Nothing.
     */
    public listed(): void {
        this.#listing = "listed";
        this.#show();
    }

    /**
     * Notes that listing the rows failed. The attempt is over: a view left
     * saying it is still looking would be as wrong as one saying nothing
     * is configured.
     *
     * @returns Nothing.
     */
    public failed(): void {
        this.#listing = "failed";
        this.#show();
    }

    /**
     * Hands the view's state to its welcome content, when it changed.
     *
     * @returns Nothing.
     */
    #show(): void {
        const state = this.state;
        if (state === this.#shownState) {
            return;
        }

        this.#shownState = state;
        void vscode.commands.executeCommand(
            "setContext",
            this.contextKey,
            state,
        );
    }

    /**
     * Stops listening to the server's startup.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#unsubscribeStatus();
    }
}
