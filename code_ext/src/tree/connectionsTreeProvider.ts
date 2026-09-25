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
    UI_BACKEND_SESSION,
    type ConnectionManager,
} from "../connections/connectionManager.js";
import { showErrorWithLog } from "../errorMessages.js";
import type { IServerStatus } from "../mcp/serverStarter.js";
import {
    ConnectionsModel,
    type ConnectionsNode,
    type IConnectionNode,
    type IConnectionStatusNode,
    type IOpenAttempt,
} from "./connectionsModel.js";
import { createTreeItem, type IconResolver } from "./treeItems.js";

/** The id the Connections view is contributed under. */
export const CONNECTIONS_VIEW_ID = "mariadb.connections";

/**
 * The context key saying what the Connections view is waiting for, which
 * is what picks its welcome content.
 *
 * The list comes from the MCP server, which has to be found - or
 * downloaded - and started first, so an empty tree means "not asked yet"
 * for as long as that takes, and telling the user in that window that
 * nothing is configured would be telling them something untrue. It lives
 * here rather than with the settings keys because nothing configures it:
 * the tree is what knows.
 */
export const CONNECTIONS_VIEW_STATE_CONTEXT_KEY = "mariadb.connectionsView";

/**
 * What the Connections view is showing when it has no rows.
 *
 * - `looking`: the server is being found or started.
 * - `installing`: the MariaDB Shell is being downloaded and installed.
 * - `failed`: the last attempt to list the connections failed.
 * - `listed`: the connections were listed - an empty tree now means there
 *   are none.
 */
export type ConnectionsViewState =
    | "looking"
    | "installing"
    | "failed"
    | "listed";

/**
 * Feeds the Connections view in the primary sidebar.
 *
 * The shape of the tree lives in ConnectionsModel; this only turns its
 * nodes into VS Code items and refreshes when the connection state changes.
 */
export class ConnectionsTreeProvider
    implements vscode.TreeDataProvider<ConnectionsNode>, vscode.Disposable {

    readonly #model: ConnectionsModel;
    readonly #onDidChangeTreeData =
        new vscode.EventEmitter<ConnectionsNode | undefined>();
    readonly #unsubscribe: () => void;
    readonly #unsubscribeStatus: () => void;
    /** How the last attempt at listing the roots went, if there was one. */
    #listing: "unasked" | "listed" | "failed" = "unasked";
    /** The state last handed to the context key. */
    #shownState?: ConnectionsViewState;
    /**
     * The tree's attempts at opening a connection, by URI, while they are
     * under way and after they failed. Cleared once one succeeds.
     */
    readonly #attempts = new Map<string, IOpenAttempt>();

    public readonly onDidChangeTreeData = this.#onDidChangeTreeData.event;

    /**
     * @param connections The open connections and the default one.
     * @param resolveIcon The icon lookup the items share.
     * @param log Where to write a failure the tree cannot show.
     * @param connectOnOpen Whether expanding a closed connection opens it.
     * @param status How the server's startup is going, which is what the
     *               view says while it has nothing to list yet.
     */
    public constructor(
        private readonly connections: ConnectionManager,
        private readonly resolveIcon: IconResolver,
        private readonly log: (message: string) => void,
        private readonly connectOnOpen: () => boolean,
        private readonly status?: IServerStatus,
    ) {
        this.#model = new ConnectionsModel(connections, connectOnOpen,
            (uri) => { return this.#attempts.get(uri); });
        this.#unsubscribe = connections.onDidChange(() => {
            this.refresh();
        });
        this.#unsubscribeStatus = status?.onDidChangePhase((phase) => {
            // A new attempt is under way, so the last one's failure is no
            // longer what the view should be saying.
            if (phase === "locating" && this.#listing === "failed") {
                this.#listing = "unasked";
            }
            this.#showState();
        }) ?? (() => { /* nothing to stop */ });
        this.#showState();
    }

    /**
     * Redraws the tree, or one subtree of it.
     *
     * @param node The node to redraw, or undefined for the whole tree.
     *
     * @returns Nothing.
     */
    public refresh(node?: ConnectionsNode): void {
        this.#onDidChangeTreeData.fire(node);
    }

    /**
     * @param node The node to show.
     *
     * @returns The item VS Code should draw for it.
     */
    public getTreeItem(node: ConnectionsNode): vscode.TreeItem {
        return createTreeItem(node, this.resolveIcon);
    }

    /**
     * @param node The node to expand, or undefined for the root.
     *
     * @returns Its children.
     */
    public async getChildren(
        node?: ConnectionsNode,
    ): Promise<ConnectionsNode[]> {
        try {
            if (node !== undefined) {
                return await this.#model.getChildren(node);
            }

            const roots = await this.#model.getRoots();
            this.#listing = "listed";
            this.#showState();

            return roots;
        } catch (error) {
            this.#report("list the connections", error);
            // The attempt is over. A view left saying it is still looking
            // would be as wrong as one saying nothing is configured.
            this.#listing = "failed";
            this.#showState();

            return [];
        }
    }

    /**
     * @returns What the view should say while it has no rows.
     */
    public get state(): ConnectionsViewState {
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
     * Hands the view's state to its welcome content, when it changed.
     *
     * @returns Nothing.
     */
    #showState(): void {
        const state = this.state;
        if (state === this.#shownState) {
            return;
        }

        this.#shownState = state;
        void vscode.commands.executeCommand(
            "setContext",
            CONNECTIONS_VIEW_STATE_CONTEXT_KEY,
            state,
        );
    }

    /**
     * Opens the connection the tree browses with, for a node the user
     * just expanded.
     *
     * This hangs off the user's expand rather than off `getChildren`, which
     * the tree also calls on every refresh: a closed connection that opened
     * itself whenever it was asked for its children could never be
     * disconnected. The children come from the refresh that opening fires,
     * so there is nothing to return here.
     *
     * A connection something else already has open is expanded even in
     * the explicit mode. What that mode is about is not connecting to a
     * server behind the user's back, and the server has been connected
     * to; all this adds is the connection the tree browses on, without
     * which the row would expand to nothing and stay that way.
     *
     * @param node The node that was expanded.
     *
     * @returns Nothing.
     */
    public async expanded(node: ConnectionsNode): Promise<void> {
        if (node.kind !== "connection") {
            return;
        }

        if (!this.connectOnOpen() && !this.connections.isConnected(node.uri)) {
            return;
        }

        await this.#open(node);
    }

    /**
     * Tries again to open the connection a failed status row stands under.
     *
     * @param node The failed status row.
     *
     * @returns Nothing.
     */
    public async retry(node: IConnectionStatusNode): Promise<void> {
        await this.#open(node.parent);
    }

    /**
     * Opens the tree's own connection on a connection row, showing how it
     * goes under the row: a spinner while it is under way, the reason if it
     * fails. The failure is shown there rather than in a notification - the
     * user is looking at the row, and it has a Retry button beside it.
     *
     * @param node The connection row, as the tree holds it.
     *
     * @returns Nothing.
     */
    async #open(node: IConnectionNode): Promise<void> {
        if (this.connections.isConnected(node.uri, UI_BACKEND_SESSION)
            || this.#attempts.get(node.uri)?.state === "connecting") {
            return;
        }

        this.#attempts.set(node.uri, { state: "connecting" });
        this.refresh(node);
        try {
            await this.connections.connect(node.uri, UI_BACKEND_SESSION);
            // Opening fired the refresh that lists the schemas already.
            this.#attempts.delete(node.uri);
        } catch (error) {
            const message = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to open '${node.uri}': ${message}`);
            this.#attempts.set(node.uri, { state: "failed", message });
            this.refresh(node);
        }
    }

    /**
     * Surfaces a failure the tree itself cannot show.
     *
     * A tree that throws shows nothing and says nothing, so the reason
     * goes to the log and to a notification.
     *
     * @param what The attempt that failed, as a verb phrase.
     * @param error What went wrong.
     *
     * @returns Nothing.
     */
    #report(what: string, error: unknown): void {
        const message = error instanceof Error
            ? error.message
            : String(error);
        this.log(`Failed to ${what}: ${message}`);
        void showErrorWithLog(message);
    }

    /**
     * Stops listening to the connection manager.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#unsubscribe();
        this.#unsubscribeStatus();
        this.#onDidChangeTreeData.dispose();
    }
}
