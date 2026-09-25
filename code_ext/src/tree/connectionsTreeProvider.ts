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
    ROOT_FOLDER,
    folderNames,
    normalizeFolder,
} from "../connections/connectionFolders.js";
import {
    fileConnections,
    type IFiling,
    type IStoredConnection,
} from "../connections/connectionStore.js";
import { showErrorWithLog } from "../errorMessages.js";
import type { IServerStatus } from "../mcp/serverStarter.js";
import {
    ConnectionsModel,
    type ConnectionsNode,
    type IConnectionNode,
    type IFolderNode,
    type IConnectionStatusNode,
    type IOpenAttempt,
} from "./connectionsModel.js";
import {
    COLLAPSED_FOLDERS_KEY,
    FolderSet,
    isWithin,
    rebase,
} from "./customFolders.js";
import {
    FolderTreeItem,
    createTreeItem,
    type IconResolver,
} from "./treeItems.js";

/** What the tree needs of the folders the user made. */
export interface ICustomFolders {
    list(): string[];
    move(from: string, to: string): Promise<void>;
}

/** The id the Connections view is contributed under. */
export const CONNECTIONS_VIEW_ID = "mariadb.connections";

/**
 * What a drag out of the Connections view carries: the connection and
 * folder rows dragged.
 *
 * Deliberately NOT the tree's own `application/vnd.code.tree.<view id>`:
 * VS Code fills that one in by itself, with the handles of whatever is
 * dragged, so reading it back as rows breaks on anything this did not put
 * there.
 */
export const CONNECTIONS_DRAG_MIME = "application/vnd.mariadb.connections";

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
    implements vscode.TreeDataProvider<ConnectionsNode>,
    vscode.TreeDragAndDropController<ConnectionsNode>,
    vscode.Disposable {

    public readonly dragMimeTypes = [CONNECTIONS_DRAG_MIME];
    public readonly dropMimeTypes = [CONNECTIONS_DRAG_MIME];

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
     * @param customFolders The folders the user made that may be empty.
     * @param collapsedFolders The folders the user collapsed, persisted so a
     *        restart comes back as it was left. Folders are drawn open, so
     *        the ones NOT here are open - which picks each one's state and
     *        icon on the very first draw.
     */
    public constructor(
        private readonly connections: ConnectionManager,
        private readonly resolveIcon: IconResolver,
        private readonly log: (message: string) => void,
        private readonly connectOnOpen: () => boolean,
        private readonly status?: IServerStatus,
        private readonly customFolders: ICustomFolders = new FolderSet(),
        private readonly collapsedFolders =
        new FolderSet(undefined, COLLAPSED_FOLDERS_KEY),
    ) {
        this.#model = new ConnectionsModel(connections, connectOnOpen,
            (uri) => { return this.#attempts.get(uri); },
            () => { return customFolders.list(); });
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
        if (node.kind === "folder") {
            return new FolderTreeItem(node,
                !this.collapsedFolders.has(node.path));
        }

        return createTreeItem(node, this.resolveIcon);
    }

    /**
     * Notes a folder the user collapsed, and redraws it closed.
     *
     * @param node The node that was collapsed.
     *
     * @returns Nothing.
     */
    public async collapsed(node: ConnectionsNode): Promise<void> {
        if (node.kind === "folder") {
            await this.collapsedFolders.add(node.path);
            this.refresh(node);
        }
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
        if (node.kind === "folder") {
            // Redrawn open; only a folder that was closed needs it. Its own
            // entry only: the folders inside it keep theirs.
            if (await this.collapsedFolders.delete(node.path)) {
                this.refresh(node);
            }

            return;
        }

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
     * Starts a drag of the connections and folders selected. What is under
     * an open connection stays: it is the connection's database, not the
     * view's to rearrange.
     *
     * @param source The rows being dragged, all of the selection.
     * @param dataTransfer What the drag carries.
     *
     * @returns Nothing.
     */
    public handleDrag(
        source: readonly ConnectionsNode[],
        dataTransfer: vscode.DataTransfer,
    ): void {
        const dragged = source.filter((node) => {
            return node.kind === "connection" || node.kind === "folder";
        });
        if (dragged.length > 0) {
            dataTransfer.set(CONNECTIONS_DRAG_MIME,
                new vscode.DataTransferItem(dragged));
        }
    }

    /**
     * Files what was dragged where it was dropped: in the folder dropped on,
     * in the folder of the connection dropped on, or at the top level when
     * dropped on empty space.
     *
     * A folder moves whole and keeps its name: every connection in or below
     * it is re-filed under its new path, and so are the empty folders made
     * inside it. A folder dropped into itself or into a folder of its own is
     * left where it is, and so is a connection dragged along with the folder
     * it is in - it moves with that folder.
     *
     * @param target The row dropped on, or undefined for empty space.
     * @param dataTransfer What the drag carries.
     *
     * @returns Nothing.
     */
    public async handleDrop(
        target: ConnectionsNode | undefined,
        dataTransfer: vscode.DataTransfer,
    ): Promise<void> {
        // Checked rather than trusted: anything this did not put there
        // carries no rows, and nothing then moves.
        const carried: unknown = dataTransfer.get(CONNECTIONS_DRAG_MIME)?.value;
        const rows = Array.isArray(carried)
            ? (carried as Array<ConnectionsNode | undefined>)
            : [];
        const into = dropFolder(target);
        if (into === undefined) {
            return;
        }

        // A folder into itself or below itself has nowhere to go, and one
        // already in the target goes nowhere; a folder inside another that
        // is dragged too moves with that one.
        const candidates = rows.filter((node): node is IFolderNode => {
            return node?.kind === "folder" && !isWithin(into, node.path)
                && parentOf(node.path) !== into;
        });
        const folders = candidates.filter((folder) => {
            return !candidates.some((other) => {
                return other !== folder && isWithin(folder.path, other.path);
            });
        });
        const connections = rows.filter((node): node is IConnectionNode => {
            return node?.kind === "connection" && !folders.some((folder) => {
                return isWithin(node.path ?? ROOT_FOLDER, folder.path);
            });
        });
        if (folders.length === 0 && connections.length === 0) {
            return;
        }

        await this.#refile(
            folders.map((folder) => {
                return { from: folder.path, to: movedPath(folder, into) };
            }),
            connections.map((node) => {
                return { connection: storedOf(node), path: into };
            }),
            `move to '${into}'`,
        );
    }

    /**
     * Files connections in a folder - what New Folder with Selection does
     * once the folder is named.
     *
     * @param nodes The connection rows to move.
     * @param path The folder to file them in.
     *
     * @returns Nothing.
     */
    public async fileInFolder(
        nodes: IConnectionNode[],
        path: string,
    ): Promise<void> {
        const folder = normalizeFolder(path);

        await this.#refile([], nodes.map((node) => {
            return { connection: storedOf(node), path: folder };
        }), `move to '${folder}'`);
    }

    /**
     * Renames a folder, re-filing every connection in and below it and the
     * empty folders made inside it. A folder of the new name already there
     * is merged with, as a drop onto it would be.
     *
     * @param folder The folder to rename.
     * @param name Its new name: one folder name, no `/`.
     *
     * @returns Nothing.
     */
    public async renameFolder(folder: IFolderNode, name: string): Promise<void> {
        const renamed = normalizeFolder(`${parentOf(folder.path)}/${name}`);
        if (renamed === folder.path || renamed === ROOT_FOLDER) {
            return;
        }

        await this.#refile(
            [{ from: folder.path, to: renamed }], [],
            `rename '${folder.path}'`);
    }

    /**
     * Moves folders and connections, and redraws the tree.
     *
     * @param folders Each folder to move, from its path to its new one.
     *                Everything in or below it goes along.
     * @param filings Connections to file somewhere of their own.
     * @param what What is being done, for the log if it fails.
     *
     * @returns Nothing.
     */
    async #refile(
        folders: Array<{ from: string; to: string }>,
        filings: IFiling[],
        what: string,
    ): Promise<void> {
        try {
            const api = await this.connections.api();
            const all = [...filings];

            if (folders.length > 0) {
                // Everything in the moved folders, not only what is in
                // sight: a closed folder's connections go too.
                const stored = await this.connections.listStoredConnections();
                for (const { from, to } of folders) {
                    for (const connection of stored) {
                        const path = connection.path ?? ROOT_FOLDER;
                        if (isWithin(path, from)) {
                            all.push({
                                connection, path: rebase(path, from, to),
                            });
                        }
                    }
                }
            }

            for (const connection of await fileConnections(api, all)) {
                this.log(`Moved '${connection.uri}' to '${connection.path}'.`);
            }

            for (const { from, to } of folders) {
                await this.customFolders.move(from, to);
                // A collapsed folder stays collapsed where it went.
                await this.collapsedFolders.move(from, to);
                this.log(`Moved the folder '${from}' to '${to}'.`);
            }
        } catch (error) {
            this.#report(what, error);
        } finally {
            // Whatever made it, including a part-way failure.
            this.refresh();
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

/**
 * The folder a drop on a row files into.
 *
 * @param target The row dropped on, or undefined for empty space.
 *
 * @returns The folder, or undefined where a drop means nothing - on a
 *          schema or an object, say, which belong to a connection's database.
 */
const dropFolder = (target: ConnectionsNode | undefined): string | undefined => {
    if (target === undefined) {
        return ROOT_FOLDER;
    }

    switch (target.kind) {
        case "folder": {
            return target.path;
        }

        case "connection": {
            return target.path ?? ROOT_FOLDER;
        }

        default: {
            return undefined;
        }
    }
};

/**
 * The folder a path sits in.
 *
 * @param path The folder.
 *
 * @returns Its parent; `/` for a top-level folder.
 */
const parentOf = (path: string): string => {
    return normalizeFolder(folderNames(path).slice(0, -1).join("/"));
};

/**
 * Where a folder ends up when dropped into another: inside it, same name.
 *
 * @param folder The folder moved.
 * @param into The folder it is dropped into.
 *
 * @returns Its new path.
 */
const movedPath = (folder: IFolderNode, into: string): string => {
    return normalizeFolder(`${into}/${folder.name}`);
};

/**
 * A connection row as the store knows the connection.
 *
 * @param node The row.
 *
 * @returns The connection.
 */
const storedOf = (node: IConnectionNode): IStoredConnection => {
    return { uri: node.uri, kind: node.connectionKind, path: node.path };
};
