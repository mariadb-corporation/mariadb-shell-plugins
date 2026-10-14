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
import { decodeList, decodeObject, decodeScalar } from "./protocol.js";

/** Whether a REST Daemon is serving. */
export type RestdStatus = "running" | "stopped";

/** One REST Daemon deployment, as `rest.get_restd_status` lists it. */
export interface IRestdDeployment {
    /** The deployment's folder, which names it in every other call. */
    directory: string;
    /** The server it serves. */
    host: string | null;
    server_port: number | null;
    /** The port the daemon serves HTTPS on. */
    port: number;
    /** `https://localhost:<port>`. */
    url: string;
    status: RestdStatus;
    pid: number | null;
    /** The daemon's own log. */
    log_path: string;
    metadata_schema: string;
    /** The connection it was set up with. */
    connection_uri: string | null;
    /** The sandbox it belongs to, or null. */
    sandbox_port: number | null;
}

/** What `rest.configure_restd` answers with. */
export interface IRestdSetup extends IRestdDeployment {
    /** Whether mariadb-restd was installed for it. */
    installed: boolean;
    /** What `mariadb-restd --setup` printed. */
    setup_output: string;
}

/** What `rest.configure_restd` is asked to set up. */
export interface IRestdConfigureOptions {
    /** A connection to the server, opened with `db.connect`. */
    connectionId: string;
    /** The account's password; its stored one when left out. */
    password?: string;
    /** The sandbox the server is, for a sandbox's daemon. */
    sandboxPort?: number;
    /** The port to serve on; the server picks one when left out. */
    port?: number;
    /** Needed only where the server has several metadata schemas. */
    metadataSchema?: string;
}

/**
 * How long a setup may take. Where mariadb-restd is not installed, it is
 * downloaded first, which takes far more than the transport's minute.
 */
export const RESTD_SETUP_TIMEOUT_MS = 20 * 60 * 1000;

/**
 * How long a start, stop or delete may take: the server waits up to 30
 * seconds for a daemon to listen or to end, and then kills it.
 */
export const RESTD_LIFECYCLE_TIMEOUT_MS = 2 * 60 * 1000;

/** The `rest.*` tools, as typed calls. */
export interface IRestApi {
    getStatus(): Promise<IRestdDeployment[]>;
    configure(options: IRestdConfigureOptions): Promise<IRestdSetup>;
    start(directory: string): Promise<IRestdDeployment>;
    stop(directory: string): Promise<IRestdDeployment>;
    delete(directory: string, connectionId?: string): Promise<string>;
}

/** The REST Daemon tools of the MariaDB MCP server. */
export class RestApi implements IRestApi {
    public constructor(private readonly caller: IToolCaller) { }

    /**
     * @returns Every deployment the server knows of: the sandboxes' and
     *          those set up for other servers, running or not.
     */
    public async getStatus(): Promise<IRestdDeployment[]> {
        const name = "rest.get_restd_status";

        return decodeList<IRestdDeployment>(name,
            await this.caller.callTool(name, {}));
    }

    /**
     * Sets a daemon up for a server and starts it: configuring the REST
     * metadata where there is none, and installing mariadb-restd where it
     * is missing.
     *
     * @param options What to set up.
     *
     * @returns The new deployment.
     */
    public async configure(
        options: IRestdConfigureOptions,
    ): Promise<IRestdSetup> {
        const name = "rest.configure_restd";

        return decodeObject<IRestdSetup>(name, await this.caller.callTool(name, {
            connection_id: options.connectionId,
            ...(options.password === undefined
                ? {} : { password: options.password }),
            ...(options.sandboxPort === undefined
                ? {} : { sandbox_port: options.sandboxPort }),
            ...(options.port === undefined ? {} : { port: options.port }),
            ...(options.metadataSchema === undefined
                ? {} : { metadata_schema: options.metadataSchema }),
        }, RESTD_SETUP_TIMEOUT_MS));
    }

    /**
     * Starts a deployment's daemon; nothing happens where it runs already.
     *
     * @param directory The deployment.
     *
     * @returns It, started.
     */
    public async start(directory: string): Promise<IRestdDeployment> {
        const name = "rest.start_restd";

        return decodeObject<IRestdDeployment>(name, await this.caller.callTool(
            name, { directory }, RESTD_LIFECYCLE_TIMEOUT_MS));
    }

    /**
     * Stops a deployment's daemon; nothing happens where it is stopped.
     *
     * @param directory The deployment.
     *
     * @returns It, stopped.
     */
    public async stop(directory: string): Promise<IRestdDeployment> {
        const name = "rest.stop_restd";

        return decodeObject<IRestdDeployment>(name, await this.caller.callTool(
            name, { directory }, RESTD_LIFECYCLE_TIMEOUT_MS));
    }

    /**
     * Stops a daemon and deletes its deployment.
     *
     * @param directory The deployment.
     * @param connectionId A connection to its server, to drop the account
     *                     the setup created with. Left out, only the
     *                     folder goes: for a sandbox about to be deleted.
     *
     * @returns The server's confirmation.
     */
    public async delete(
        directory: string,
        connectionId?: string,
    ): Promise<string> {
        const name = "rest.delete_restd_config";

        return decodeScalar(name, await this.caller.callTool(name, {
            directory,
            ...(connectionId === undefined
                ? { drop_accounts: false } : { connection_id: connectionId }),
        }, RESTD_LIFECYCLE_TIMEOUT_MS));
    }
}
