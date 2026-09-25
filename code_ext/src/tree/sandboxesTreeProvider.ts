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

import type { ISandboxInstance } from "../mcp/sandboxApi.js";
import type { IServerStatus } from "../mcp/serverStarter.js";
import type { ISandboxStore } from "../sandboxes/sandboxStore.js";

/** The id the Sandboxes view is contributed under. */
export const SANDBOXES_VIEW_ID = "mariadb.sandboxes";

/**
 * The context key the Sandboxes view's welcome content switches on, for
 * the same reason the Connections view has one: an empty list means
 * "not asked yet" for as long as the server takes to come up.
 */
export const SANDBOXES_VIEW_STATE_CONTEXT_KEY = "mariadb.sandboxesView";

/**
 * What the Sandboxes view is showing when it has no rows.
 *
 * - `looking`: the server is being found or started.
 * - `installing`: the MariaDB Shell is being downloaded and installed.
 * - `failed`: the last attempt to list the sandboxes failed.
 * - `listed`: the sandboxes were listed - an empty view now means there
 *   are none.
 */
export type SandboxesViewState =
    | "looking"
    | "installing"
    | "failed"
    | "listed";

/** What is being done to a sandbox while its row shows a spinner. */
export type SandboxAction = "starting" | "stopping" | "deleting";

/** A row of the Sandboxes view. */
export interface ISandboxNode extends ISandboxInstance {
    kind: "sandbox";
    /** Set while a start, stop or delete of it is under way. */
    action?: SandboxAction;
}

/** How each action reads on the row while it runs. */
const ACTION_CAPTIONS: Record<SandboxAction, string> = {
    starting: "starting…",
    stopping: "stopping…",
    deleting: "deleting…",
};

/**
 * The item drawn for one sandbox.
 *
 * Its `contextValue` is `mariadbSandbox.<running|stopped|busy>`, which is
 * what the inline buttons switch on: Start on a stopped one, Stop on a
 * running one, Delete on both, and nothing while an action is under way -
 * a second click would only be refused by the shell.
 */
export class SandboxTreeItem extends vscode.TreeItem {
    public constructor(public readonly node: ISandboxNode) {
        super(`localhost:${node.port}`,
            vscode.TreeItemCollapsibleState.None);

        const version = node.version ?? "unknown version";
        const state = node.action === undefined
            ? node.status
            : ACTION_CAPTIONS[node.action];
        this.description = `${version} · ${state}`;
        this.tooltip = `Sandbox on port ${node.port}\n`
            + `Server version: ${version}\n`
            + `Status: ${state}`;
        this.contextValue = node.action === undefined
            ? `mariadbSandbox.${node.status}`
            : "mariadbSandbox.busy";

        if (node.action !== undefined) {
            this.iconPath = new vscode.ThemeIcon("loading~spin");
        } else if (node.status === "running") {
            this.iconPath = new vscode.ThemeIcon("vm-running",
                new vscode.ThemeColor("testing.iconPassed"));
        } else {
            this.iconPath = new vscode.ThemeIcon("vm-outline");
        }

        // A port names one sandbox, so it tells rows apart across refreshes.
        this.id = `sandbox:${node.port}`;
    }
}

/**
 * Feeds the Sandboxes view: the instances in the default sandbox path, as
 * `sandbox.list_instances` reports them, and the start, stop and delete
 * run from their rows.
 *
 * Everything goes through the store, which keeps the list: the view redraws
 * on every action, and a redraw must not cost a listing. `refresh` redraws
 * from what is kept; `reload` is the Refresh button, and reads it again.
 */
export class SandboxesTreeProvider
    implements vscode.TreeDataProvider<ISandboxNode>, vscode.Disposable {

    readonly #onDidChangeTreeData =
        new vscode.EventEmitter<ISandboxNode | undefined>();
    readonly #unsubscribeStatus: () => void;
    /** How the last attempt at listing went, if there was one. */
    #listing: "unasked" | "listed" | "failed" = "unasked";
    /** The state last handed to the context key. */
    #shownState?: SandboxesViewState;
    /** The actions under way, by port. */
    readonly #actions = new Map<number, SandboxAction>();

    public readonly onDidChangeTreeData = this.#onDidChangeTreeData.event;

    /**
     * @param store The sandboxes, kept, and the calls that change them.
     * @param log Where to write a failure.
     * @param status How the server's startup is going, which is what the
     *               view says while it has nothing to list yet.
     */
    public constructor(
        private readonly store: ISandboxStore,
        private readonly log: (message: string) => void,
        private readonly status?: IServerStatus,
    ) {
        this.#unsubscribeStatus = status?.onDidChangePhase((phase) => {
            if (phase === "locating" && this.#listing === "failed") {
                this.#listing = "unasked";
            }
            this.#showState();
        }) ?? (() => { /* nothing to stop */ });
        this.#showState();
    }

    /**
     * Redraws the view from the sandboxes kept.
     *
     * @returns Nothing.
     */
    public refresh(): void {
        this.#onDidChangeTreeData.fire(undefined);
    }

    /**
     * Reads the sandboxes again and redraws the view: the Refresh button.
     *
     * @returns Nothing.
     */
    public reload(): void {
        this.store.invalidate();
        this.refresh();
    }

    /**
     * @param node The node to show.
     *
     * @returns The item VS Code should draw for it.
     */
    public getTreeItem(node: ISandboxNode): vscode.TreeItem {
        return new SandboxTreeItem(node);
    }

    /**
     * @param node Undefined for the root; a sandbox has no children.
     *
     * @returns The sandboxes, by port.
     */
    public async getChildren(node?: ISandboxNode): Promise<ISandboxNode[]> {
        if (node !== undefined) {
            return [];
        }

        try {
            const instances = await this.store.listInstances();
            this.#listing = "listed";
            this.#showState();

            return instances.map((instance): ISandboxNode => {
                const action = this.#actions.get(instance.port);

                return {
                    kind: "sandbox",
                    ...instance,
                    ...(action === undefined ? {} : { action }),
                };
            });
        } catch (error) {
            // Logged, not notified: the view's welcome content says the
            // listing failed and offers the log, and the Connections view
            // has already put up a notification when the server itself
            // could not be started.
            this.log("Failed to list the sandboxes: "
                + `${error instanceof Error ? error.message : String(error)}`);
            this.#listing = "failed";
            this.#showState();

            return [];
        }
    }

    /**
     * @returns What the view should say while it has no rows.
     */
    public get state(): SandboxesViewState {
        if (this.status?.phase === "installing") {
            return "installing";
        }

        if (this.#listing === "failed" || this.status?.phase === "failed") {
            return "failed";
        }

        return this.#listing === "listed" ? "listed" : "looking";
    }

    /**
     * Starts a stopped sandbox.
     *
     * @param node Its row.
     *
     * @returns The server's confirmation. Rejects with the shell's reason.
     */
    public async start(node: ISandboxNode): Promise<string> {
        return await this.#run(node.port, "starting", async () => {
            return await this.store.start(node.port);
        });
    }

    /**
     * Stops a running sandbox.
     *
     * @param node Its row.
     *
     * @returns The server's confirmation. Rejects with the shell's reason.
     */
    public async stop(node: ISandboxNode): Promise<string> {
        return await this.#run(node.port, "stopping", async () => {
            return await this.store.stop(node.port);
        });
    }

    /**
     * Deletes a sandbox, stopping it first if it is running: the shell
     * refuses to delete one that is, and a user who asked for it gone has
     * no use for being told to stop it by hand.
     *
     * @param node Its row.
     *
     * @returns The server's confirmation. Rejects with the shell's reason.
     */
    public async delete(node: ISandboxNode): Promise<string> {
        return await this.#run(node.port, "deleting", async () => {
            if (node.status === "running") {
                await this.store.stop(node.port);
            }

            return await this.store.delete(node.port);
        });
    }

    /**
     * Runs an action on one sandbox, showing it on the row while it runs.
     * The view is redrawn afterwards either way: the store has updated its
     * list to match a success, and dropped it after a failure - a failed
     * stop may still have stopped the server - so the redraw asks again.
     *
     * @param port The sandbox's port.
     * @param action What is being done.
     * @param work The calls to make.
     *
     * @returns What the work returned.
     */
    async #run(
        port: number,
        action: SandboxAction,
        work: () => Promise<string>,
    ): Promise<string> {
        if (this.#actions.has(port)) {
            throw new Error(`The sandbox on port ${port} is already `
                + `${ACTION_CAPTIONS[this.#actions.get(port)!]}`);
        }

        this.#actions.set(port, action);
        this.refresh();
        try {
            const message = await work();
            this.log(message);

            return message;
        } finally {
            this.#actions.delete(port);
            this.refresh();
        }
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
            SANDBOXES_VIEW_STATE_CONTEXT_KEY,
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
        this.#onDidChangeTreeData.dispose();
    }
}
