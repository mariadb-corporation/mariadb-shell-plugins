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

import type { IToolCaller } from "./mariaDbApi.js";
import { decodeList, decodeScalar } from "./protocol.js";

/** Whether a sandbox's server is up, as `sandbox.list_instances` says. */
export type SandboxStatus = "running" | "stopped";

/** One entry of `sandbox.list_instances`. */
export interface ISandboxInstance {
    port: number;
    /** `major.minor.patch`, or null where the shell cannot tell. */
    version: string | null;
    status: SandboxStatus;
}

/** What `sandbox.deploy` is asked to build. */
export interface ISandboxDeployOptions {
    port: number;
    /** The root password. The shell refuses a deploy without one. */
    password: string;
    /**
     * `11.8.9`, `11.8` or `11`; left out, the server on the PATH is used.
     * A version this machine does not have is downloaded first.
     */
    serverVersion?: string;
    /** The host pattern for a remote root account; `""` makes none. */
    allowRootFrom?: string;
    serverId?: number;
    ssl?: boolean;
    /** `option=value` lines for the `[mysqld]` section. */
    mariadbdOptions?: string[];
    /** Seconds to wait for the server to come up. */
    timeout?: number;
    /**
     * Whether the connection the deploy registers goes into the shared MCP
     * list (any MCP client may open it) or the extension's own. Served by a
     * `--gui` server only; an older one drops it and uses the shared list.
     */
    mcpAccess?: boolean;
}

/**
 * How long a deploy may take. It can download a server - a few hundred
 * megabytes - before it initializes a data directory and starts it, so
 * the transport's one minute would cut short the very case that most
 * needs the wait. The server side bounds its own download.
 */
export const DEPLOY_TIMEOUT_MS = 30 * 60 * 1000;

/**
 * How long a start, stop or delete may take. The shell waits up to a
 * minute for a server to come up or go down, which is exactly the
 * transport's default, so the call needs room above it.
 */
export const LIFECYCLE_TIMEOUT_MS = 3 * 60 * 1000;

/** The `sandbox.*` tools the Sandboxes view uses, as typed calls. */
export interface ISandboxApi {
    listInstances(port?: number): Promise<ISandboxInstance[]>;
    listAvailableVersions(series?: string): Promise<string[]>;
    deploy(options: ISandboxDeployOptions): Promise<string>;
    start(port: number): Promise<string>;
    stop(port: number): Promise<string>;
    delete(port: number): Promise<string>;
}

/**
 * The sandbox tools of the MariaDB MCP server.
 *
 * Only the default sandbox path is worked on: `sandbox.list_instances`
 * lists nothing else, and an instance the view cannot list is one it
 * should not be starting or deleting either, so no call names another.
 */
export class SandboxApi implements ISandboxApi {
    public constructor(private readonly caller: IToolCaller) { }

    /**
     * @param port The one instance to report, which spares the server
     *             asking every other one for its version and probing its
     *             port. Left out, every instance is listed.
     *
     * @returns The instances deployed in the default sandbox path, by port.
     *          With a port, that one instance, or none when there is no
     *          sandbox on it. A server that predates the argument ignores
     *          it and answers with every instance, so a caller asking about
     *          one picks it out of the answer rather than trusting it to be
     *          the only one.
     */
    public async listInstances(port?: number): Promise<ISandboxInstance[]> {
        const name = "sandbox.list_instances";

        return decodeList<ISandboxInstance>(name, await this.caller.callTool(
            name, port === undefined ? {} : { port }));
    }

    /**
     * @param series A `major.minor` or `major` version to list every release
     *               below; left out, the latest release of every series.
     *
     * @returns The versions a deploy can be asked for, oldest first.
     */
    public async listAvailableVersions(series?: string): Promise<string[]> {
        const name = "sandbox.list_available_versions";

        return decodeList<string>(name, await this.caller.callTool(
            name, series === undefined ? {} : { series }));
    }

    /**
     * Deploys and starts a new instance. The server registers a connection
     * to its root account, filed in `/Sandboxes`.
     *
     * @param options What to deploy.
     *
     * @returns The server's confirmation, naming the version where one was
     *          asked for.
     */
    public async deploy(options: ISandboxDeployOptions): Promise<string> {
        const name = "sandbox.deploy";

        return decodeScalar(name, await this.caller.callTool(name, {
            port: options.port,
            password: options.password,
            // Each one left out when not given, so the server's own default
            // applies rather than an explicit nothing.
            ...(options.serverVersion === undefined
                ? {} : { server_version: options.serverVersion }),
            ...(options.allowRootFrom === undefined
                ? {} : { allow_root_from: options.allowRootFrom }),
            ...(options.serverId === undefined
                ? {} : { server_id: options.serverId }),
            ...(options.ssl === undefined ? {} : { ssl: options.ssl }),
            ...(options.mariadbdOptions === undefined
                || options.mariadbdOptions.length === 0
                ? {} : { mariadbd_options: options.mariadbdOptions }),
            ...(options.timeout === undefined
                ? {} : { timeout: options.timeout }),
            ...(options.mcpAccess === undefined
                ? {} : { mcp_access: options.mcpAccess }),
        }, DEPLOY_TIMEOUT_MS));
    }

    /**
     * Starts a stopped instance. No server binary is named: the start
     * script written at deploy time records the one it was built with, a
     * downloaded one included.
     *
     * @param port The instance's port.
     *
     * @returns The server's confirmation.
     */
    public async start(port: number): Promise<string> {
        const name = "sandbox.start";

        return decodeScalar(name, await this.caller.callTool(
            name, { port }, LIFECYCLE_TIMEOUT_MS));
    }

    /**
     * Stops a running instance gracefully.
     *
     * @param port The instance's port.
     *
     * @returns The server's confirmation.
     */
    public async stop(port: number): Promise<string> {
        const name = "sandbox.stop";

        return decodeScalar(name, await this.caller.callTool(
            name, { port }, LIFECYCLE_TIMEOUT_MS));
    }

    /**
     * Deletes a stopped instance, and the connection deploy registered.
     *
     * @param port The instance's port.
     *
     * @returns The server's confirmation.
     */
    public async delete(port: number): Promise<string> {
        const name = "sandbox.delete";

        return decodeScalar(name, await this.caller.callTool(
            name, { port }, LIFECYCLE_TIMEOUT_MS));
    }
}
