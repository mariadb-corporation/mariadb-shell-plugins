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

import { DEFAULT_PORT, parseConnectionUri } from "../connections/connectionUri.js";
import type { IRestdDeployment } from "../mcp/restApi.js";
import { DEFAULT_METADATA_SCHEMA } from "../mrs/mrsDialogs.js";

/**
 * Which local REST Daemon serves a connection's REST services, so that a
 * URL opened from the tree goes to the right port.
 *
 * A daemon serves a server - the REST metadata of one host and port - not
 * a connection: every connection to that server, whatever its account,
 * reaches the same services through it. Each deployment records the
 * server it was set up for (`host`, `server_port`) and the metadata schema
 * it serves, which is what a connection is matched on.
 */

/** The names of this machine a connection can use: one server on all. */
const LOOPBACK = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/**
 * @param host A host name or address.
 *
 * @returns It in the form two names of one host share: `localhost` for
 *          every loopback name, lower case otherwise.
 */
const sameHost = (host: string): string => {
    const lower = host.trim().toLowerCase();

    return LOOPBACK.has(lower) ? "localhost" : lower.replace(/^\[|\]$/gu, "");
};

/**
 * @param deployments The local REST Daemons, as `rest.get_restd_status`
 *                    lists them.
 * @param uri A connection.
 * @param metadataSchema The metadata schema the tree row is on. Undefined
 *                       where the server has one only, whatever its name -
 *                       which is when a tree row names none - so any daemon
 *                       of the server serves it, one of the default
 *                       schema's first.
 *
 * @returns The daemon serving that server's metadata schema - a running one
 *          before a stopped one - or undefined where none here does.
 */
export const restdFor = (
    deployments: readonly IRestdDeployment[],
    uri: string,
    metadataSchema?: string,
): IRestdDeployment | undefined => {
    const server = parseConnectionUri(uri);
    const port = Number(server.port || DEFAULT_PORT);
    const socket = server.socket.trim();
    const matching = deployments.filter((deployment) => {
        // A server reached through a socket is that socket's, whatever
        // host and port its deployment was given.
        const sameServer = socket !== ""
            ? deployment.socket === socket
            : deployment.host !== null
                && sameHost(deployment.host) === sameHost(server.host)
                && deployment.server_port === port;

        return sameServer && (metadataSchema === undefined
            || deployment.metadata_schema === metadataSchema);
    }).sort((first, second) => {
        return Number(second.metadata_schema === DEFAULT_METADATA_SCHEMA)
            - Number(first.metadata_schema === DEFAULT_METADATA_SCHEMA);
    });

    return matching.find((deployment) => {
        return deployment.status === "running";
    }) ?? matching[0];
};
