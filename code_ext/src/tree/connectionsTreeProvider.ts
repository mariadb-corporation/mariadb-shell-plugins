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

import type { ConnectionManager } from "../connections/connectionManager.js";
import {
    ConnectionsModel,
    type ConnectionsNode,
} from "./connectionsModel.js";
import { createTreeItem, type IconResolver } from "./treeItems.js";

/** The id the Connections view is contributed under. */
export const CONNECTIONS_VIEW_ID = "mariadb.connections";

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
            return node === undefined
                ? await this.#model.getRoots()
                : await this.#model.getChildren(node);
        } catch (error) {
            this.#report("populate the Connections view", error);

            return [];
        }
    }

    /**
     * Opens the connection a node the user just expanded stands for, where
     * that is the mode in force.
     *
     * This hangs off the user's expand rather than off `getChildren`, which
     * the tree also calls on every refresh: a closed connection that opened
     * itself whenever it was asked for its children could never be
     * disconnected. The children come from the refresh that opening fires,
     * so there is nothing to return here.
     *
     * @param node The node that was expanded.
     *
     * @returns Nothing.
     */
    public async expanded(node: ConnectionsNode): Promise<void> {
        if (node.kind !== "connection" || !this.connectOnOpen()) {
            return;
        }

        if (this.connections.isConnected(node.uri)) {
            return;
        }

        try {
            await this.connections.connect(node.uri);
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
