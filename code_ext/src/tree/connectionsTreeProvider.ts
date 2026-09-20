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

    public constructor(
        connections: ConnectionManager,
        private readonly resolveIcon: IconResolver,
        private readonly log: (message: string) => void,
    ) {
        this.#model = new ConnectionsModel(connections);
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
            // A tree that throws shows nothing and says nothing, so the
            // reason goes to the log and the branch comes back empty.
            const message = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to populate the Connections view: ${message}`);
            void vscode.window.showErrorMessage(
                `MariaDB: ${message}`,
            );

            return [];
        }
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
