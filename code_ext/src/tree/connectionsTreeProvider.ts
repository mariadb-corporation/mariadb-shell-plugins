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
import {
    ConnectionsModel,
    type ConnectionsNode,
} from "./connectionsModel.js";
import { createTreeItem, type IconResolver } from "./treeItems.js";

/** The id the Connections view is contributed under. */
export const CONNECTIONS_VIEW_ID = "mariadb.connections";

/**
 * The context key saying the configured connections have been listed.
 *
 * The view's welcome content is two messages and this is what picks
 * between them. The list comes from the MCP server, which has to be
 * found and started first, so an empty tree means "not asked yet" for
 * as long as that takes - and telling the user in that window that
 * nothing is configured would be telling them something untrue. It
 * lives here rather than with the settings keys because nothing
 * configures it: the tree is what knows.
 */
export const CONNECTIONS_LISTED_CONTEXT_KEY = "mariadb.connectionsListed";

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
    /** Whether the roots have been asked for and answered, once. */
    #listed = false;

    public readonly onDidChangeTreeData = this.#onDidChangeTreeData.event;

    /**
     * @param connections The open connections and the default one.
     * @param resolveIcon The icon lookup the items share.
     * @param log Where to write a failure the tree cannot show.
     * @param connectOnOpen Whether expanding a closed connection opens it.
     */
    public constructor(
        private readonly connections: ConnectionManager,
        private readonly resolveIcon: IconResolver,
        private readonly log: (message: string) => void,
        private readonly connectOnOpen: () => boolean,
    ) {
        this.#model = new ConnectionsModel(connections, connectOnOpen);
        this.#unsubscribe = connections.onDidChange(() => {
            this.refresh();
        });
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
            this.#markListed();

            return roots;
        } catch (error) {
            this.#report("populate the Connections view", error);
            // The attempt is over, however it went. A view left saying
            // it is still looking would be as wrong as one saying
            // nothing is configured, and the failure has been reported.
            this.#markListed();

            return [];
        }
    }

    /**
     * Says, once, that the configured connections have been listed.
     *
     * @returns Nothing.
     */
    #markListed(): void {
        if (this.#listed) {
            return;
        }

        this.#listed = true;
        void vscode.commands.executeCommand(
            "setContext",
            CONNECTIONS_LISTED_CONTEXT_KEY,
            true,
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

        if (this.connections.isConnected(node.uri, UI_BACKEND_SESSION)) {
            return;
        }

        try {
            await this.connections.connect(node.uri, UI_BACKEND_SESSION);
        } catch (error) {
            this.#report(`open '${node.uri}'`, error);
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
        void vscode.window.showErrorMessage(`MariaDB: ${message}`);
    }

    /**
     * Stops listening to the connection manager.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#unsubscribe();
        this.#onDidChangeTreeData.dispose();
    }
}
