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

import type { IRestdDeployment } from "../mcp/restApi.js";
import type { IServerStatus } from "../mcp/serverStarter.js";
import { errorText } from "../text.js";
import { ViewStateTracker, type ViewState } from "./viewState.js";

/** The id the REST Daemons view is contributed under. */
export const RESTD_VIEW_ID = "mariadb.restDaemons";

/**
 * The context key the REST Daemons view's welcome content switches on, as
 * the Sandboxes view's does.
 */
export const RESTD_VIEW_STATE_CONTEXT_KEY = "mariadb.restDaemonsView";

/** What is being done to a daemon while its row shows a spinner. */
export type RestdAction = "starting" | "stopping" | "deleting";

/** A row of the REST Daemons view: one deployment. */
export interface IRestdNode extends IRestdDeployment {
    kind: "restd";
    /** Set while a start, stop or delete of it is under way. */
    action?: RestdAction;
}


/**
 * @param node A deployment.
 *
 * @returns The server it serves, as its row is labelled: the sandbox's
 *          port for a sandbox's, the server's address otherwise.
 */
export const restdServerLabel = (node: IRestdDeployment): string => {
    if (node.sandbox_port !== null) {
        return `Sandbox localhost:${String(node.sandbox_port)}`;
    }

    return `${node.host ?? "unknown host"}:${String(node.server_port ?? "")}`;
};

/**
 * The item drawn for one deployment. Its `contextValue` is
 * `mariadbRestd.<running|stopped|busy>`, which the buttons switch on.
 */
export class RestdTreeItem extends vscode.TreeItem {
    public constructor(public readonly node: IRestdNode) {
        super(restdServerLabel(node), vscode.TreeItemCollapsibleState.None);

        const state = node.action === undefined
            ? node.status
            : `${node.action}…`;
        this.description = `port ${String(node.port)} · ${state}`;
        this.tooltip = `REST Daemon serving ${restdServerLabel(node)}\n`
            + `URL: ${node.url}\n`
            + `Metadata schema: ${node.metadata_schema}\n`
            + `Status: ${state}\n`
            + `Deployment: ${node.directory}`;
        this.contextValue = node.action === undefined
            ? `mariadbRestd.${node.status}`
            : "mariadbRestd.busy";

        if (node.action !== undefined) {
            this.iconPath = new vscode.ThemeIcon("loading~spin");
        } else if (node.status === "running") {
            this.iconPath = new vscode.ThemeIcon("server-process",
                new vscode.ThemeColor("testing.iconPassed"));
        } else {
            this.iconPath = new vscode.ThemeIcon("server-process");
        }

        this.id = `restd:${node.directory}`;
    }
}

/**
 * Feeds the REST Daemons view: every deployment `rest.get_restd_status`
 * lists - the sandboxes' and those set up for other servers - running or
 * stopped, and the start, stop and delete run from their rows.
 */
export class RestdTreeProvider
    implements vscode.TreeDataProvider<IRestdNode>, vscode.Disposable {

    readonly #onDidChangeTreeData =
        new vscode.EventEmitter<IRestdNode | undefined>();
    readonly #viewState: ViewStateTracker;
    /** The actions under way, by deployment. */
    readonly #actions = new Map<string, RestdAction>();
    /** The deployments as last listed, for whoever needs them in between. */
    #listed?: IRestdDeployment[];
    /** The next redraw shows #listed again rather than listing afresh. */
    #reuseListed = false;

    public readonly onDidChangeTreeData = this.#onDidChangeTreeData.event;

    /**
     * @param list Lists the deployments, asking the server each time.
     * @param log Where to write a failure.
     * @param status How the server's startup is going.
     */
    public constructor(
        private readonly list: () => Promise<IRestdDeployment[]>,
        private readonly log: (message: string) => void,
        status?: IServerStatus,
    ) {
        this.#viewState =
            new ViewStateTracker(RESTD_VIEW_STATE_CONTEXT_KEY, status);
    }

    /**
     * Lists the deployments again and redraws the view.
     *
     * @returns Nothing.
     */
    public refresh(): void {
        // A collapsed view lists nothing on its own, so the next lookup
        // reads the list afresh rather than one from before the change.
        this.#listed = undefined;
        this.#reuseListed = false;
        this.#onDidChangeTreeData.fire(undefined);
    }

    public getTreeItem(node: IRestdNode): vscode.TreeItem {
        return new RestdTreeItem(node);
    }

    /**
     * @param node Undefined for the root; a deployment has no children.
     *
     * @returns The deployments.
     */
    public async getChildren(node?: IRestdNode): Promise<IRestdNode[]> {
        if (node !== undefined) {
            return [];
        }

        try {
            const reuse = this.#reuseListed ? this.#listed : undefined;
            this.#reuseListed = false;
            const deployments = reuse ?? await this.list();
            this.#listed = deployments;
            this.#viewState.listed();

            return deployments.map((deployment): IRestdNode => {
                const action = this.#actions.get(deployment.directory);

                return {
                    kind: "restd",
                    ...deployment,
                    ...(action === undefined ? {} : { action }),
                };
            });
        } catch (error) {
            this.log(`Failed to list the REST Daemons: ${errorText(error)}`);
            this.#viewState.failed();

            return [];
        }
    }

    /**
     * @returns The deployments as the view last listed them, or a fresh
     *          list where it has not listed them yet - it starts collapsed.
     */
    public async deployments(): Promise<IRestdDeployment[]> {
        if (this.#listed === undefined) {
            this.#listed = await this.list();
        }

        return this.#listed;
    }

    /** @returns What the view should say while it has no rows. */
    public get state(): ViewState {
        return this.#viewState.state;
    }

    /**
     * Runs an action on one deployment, showing it on the row while it
     * runs, and lists them again afterwards either way.
     *
     * @param node Its row.
     * @param action What is being done.
     * @param work The calls to make.
     *
     * @returns What the work returned.
     */
    public async run<T>(
        node: IRestdNode,
        action: RestdAction,
        work: () => Promise<T>,
    ): Promise<T> {
        if (this.#actions.has(node.directory)) {
            throw new Error(`The REST Daemon is already `
                + `${this.#actions.get(node.directory)!}…`);
        }

        this.#actions.set(node.directory, action);
        // Only the row's caption changes: no need to ask the server.
        this.#reuseListed = true;
        this.#onDidChangeTreeData.fire(undefined);
        try {
            return await work();
        } finally {
            this.#actions.delete(node.directory);
            this.refresh();
        }
    }

    public dispose(): void {
        this.#viewState.dispose();
        this.#onDidChangeTreeData.dispose();
    }
}
