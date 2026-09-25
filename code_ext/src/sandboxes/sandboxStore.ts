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

import type {
    ISandboxApi,
    ISandboxDeployOptions,
    ISandboxInstance,
    SandboxStatus,
} from "../mcp/sandboxApi.js";

/**
 * What the Sandboxes view and the New Sandbox dialog work from, so a test
 * can supply its own.
 */
export interface ISandboxStore {
    listInstances(): Promise<ISandboxInstance[]>;
    availableVersions(): Promise<string[]>;
    invalidate(): void;
    refreshInstance(port: number): Promise<ISandboxInstance | undefined>;
    /** The ports of the sandboxes last listed; empty before the first. */
    readonly ports: number[];
    deploy(options: ISandboxDeployOptions): Promise<string>;
    start(port: number): Promise<string>;
    stop(port: number): Promise<string>;
    delete(port: number): Promise<string>;
}

/**
 * The sandboxes, read once and kept.
 *
 * Listing them means the server walking the sandbox directory, asking each
 * instance for its version and probing its port, which takes long enough
 * that doing it on every redraw of the view - and the view redraws twice
 * for every start, stop or delete - is not on. The list is read:
 *
 * - the first time anything needs it;
 * - after `invalidate()`, which the view's Refresh button calls.
 *
 * Nothing else lists them all. A successful start, stop or delete says what
 * it did, so the kept list is updated in place. Where only the server knows
 * - the version a new sandbox runs, or where a start or stop that failed
 * left it (one that timed out may still have got there) - that ONE sandbox
 * is asked about (`refreshInstance`), which is what `sandbox.list_instances`
 * takes a port for. Only if that fails too is the whole list dropped.
 * A sandbox something else deploys - an agent's `sandbox.deploy` - shows at
 * the next Refresh, as a connection added elsewhere does.
 *
 * The available versions are kept for good once read: the server reads
 * them from a file that ships with its plugin, so they cannot change while
 * it runs.
 */
export class SandboxStore implements ISandboxStore {
    /** The listing, in flight or done, so concurrent callers share one. */
    #instances?: Promise<ISandboxInstance[]>;
    /** The last listing that came back, for what needs an answer now. */
    #listed: ISandboxInstance[] = [];
    /**
     * Whether `#instances` is a listing that came back and is kept, as
     * against one still in flight - which a change must not replace, since
     * it brings its own answer.
     */
    #kept = false;
    #versions?: Promise<string[]>;

    /**
     * @param api The sandbox API, starting the MCP server if it is not up.
     */
    public constructor(private readonly api: () => Promise<ISandboxApi>) { }

    /**
     * @returns The sandboxes, by port: kept, or read if there are none kept.
     */
    public async listInstances(): Promise<ISandboxInstance[]> {
        const listing = this.#instances ??= (async () => {
            return await (await this.api()).listInstances();
        })();

        try {
            const listed = await listing;
            if (this.#instances === listing) {
                this.#listed = listed;
                this.#kept = true;
            }

            return [...listed];
        } catch (error) {
            // Not kept, so the next look tries again - but only if nothing
            // replaced the failed listing meanwhile.
            if (this.#instances === listing) {
                this.#instances = undefined;
            }

            throw error;
        }
    }

    /**
     * @returns The latest release of every series a deploy can download.
     */
    public async availableVersions(): Promise<string[]> {
        const listing = this.#versions ??= (async () => {
            return await (await this.api()).listAvailableVersions();
        })();

        try {
            return [...await listing];
        } catch (error) {
            if (this.#versions === listing) {
                this.#versions = undefined;
            }

            throw error;
        }
    }

    /**
     * Drops the kept sandboxes, so the next look reads them again.
     *
     * @returns Nothing.
     */
    public invalidate(): void {
        this.#instances = undefined;
        this.#kept = false;
    }

    /**
     * Asks the server about one sandbox and puts the answer in the kept
     * list: updated, added, or taken out when it is not there any more.
     *
     * @param port The sandbox.
     *
     * @returns It, or undefined when there is no sandbox on the port.
     */
    public async refreshInstance(
        port: number,
    ): Promise<ISandboxInstance | undefined> {
        // Picked out of the answer, not trusted to be all of it: a server
        // that predates the argument answers with every sandbox.
        const instance = (await (await this.api()).listInstances(port))
            .find((candidate) => { return candidate.port === port; });

        this.#listed = [
            ...this.#listed.filter((kept) => { return kept.port !== port; }),
            ...(instance === undefined ? [] : [instance]),
        ].sort((a, b) => { return a.port - b.port; });
        this.#keep();

        return instance;
    }

    /**
     * @returns The ports of the sandboxes last listed.
     */
    public get ports(): number[] {
        return this.#listed.map((instance) => { return instance.port; });
    }

    /**
     * Deploys a sandbox, and asks the server about it afterwards.
     *
     * @param options What to deploy.
     *
     * @returns The server's confirmation.
     */
    public async deploy(options: ISandboxDeployOptions): Promise<string> {
        try {
            return await (await this.api()).deploy(options);
        } finally {
            // Success or failure: only the server knows which version the
            // new sandbox runs, and a deploy that failed late may have left
            // one behind.
            await this.#settle(options.port);
        }
    }

    /**
     * @param port The sandbox to start.
     *
     * @returns The server's confirmation.
     */
    public async start(port: number): Promise<string> {
        return await this.#change(port, async (api) => {
            return await api.start(port);
        }, "running");
    }

    /**
     * @param port The sandbox to stop.
     *
     * @returns The server's confirmation.
     */
    public async stop(port: number): Promise<string> {
        return await this.#change(port, async (api) => {
            return await api.stop(port);
        }, "stopped");
    }

    /**
     * @param port The sandbox to delete.
     *
     * @returns The server's confirmation.
     */
    public async delete(port: number): Promise<string> {
        return await this.#change(port, async (api) => {
            return await api.delete(port);
        }, undefined);
    }

    /**
     * Makes one call on a sandbox and updates the kept list to match.
     *
     * @param port The sandbox.
     * @param work The call.
     * @param status What the sandbox is afterwards, or undefined for gone.
     *
     * @returns What the call returned.
     */
    async #change(
        port: number,
        work: (api: ISandboxApi) => Promise<string>,
        status: SandboxStatus | undefined,
    ): Promise<string> {
        let message: string;
        try {
            message = await work(await this.api());
        } catch (error) {
            // Where it was left is the server's to say.
            await this.#settle(port);

            throw error;
        }

        this.#listed = status === undefined
            ? this.#listed.filter((instance) => { return instance.port !== port; })
            : this.#listed.map((instance) => {
                return instance.port === port
                    ? { ...instance, status }
                    : instance;
            });
        this.#keep();

        return message;
    }

    /**
     * Makes `#listed` what the next look is answered with - only where a
     * listing is kept: one in flight or dropped brings its own answer, and
     * replacing it here would hide that.
     *
     * @returns Nothing.
     */
    #keep(): void {
        if (this.#kept) {
            this.#instances = Promise.resolve(this.#listed);
        }
    }

    /**
     * Asks about one sandbox after the call on it said nothing reliable,
     * dropping the whole list only when that cannot be answered either.
     *
     * @param port The sandbox.
     *
     * @returns Nothing. It never rejects: it runs where another failure is
     *          already on its way out, and that is the one to report.
     */
    async #settle(port: number): Promise<void> {
        try {
            await this.refreshInstance(port);
        } catch {
            this.invalidate();
        }
    }
}
