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

import { describe, expect, it } from "vitest";

import type { IRestdDeployment } from "../../mcp/restApi.js";
import { restdServerLabel } from "../../tree/restdTreeProvider.js";

const deployment = (
    extra: Partial<IRestdDeployment> = {},
): IRestdDeployment => {
    return {
        directory: "/d", host: "localhost", server_port: 3306, port: 8443,
        url: "https://localhost:8443", status: "running", pid: 42,
        log_path: "/d/log/mariadb-restd.log",
        metadata_schema: "mariadb_rest_service", connection_uri: null,
        sandbox_port: null, ...extra,
    };
};

describe("restdServerLabel", () => {
    it("names a server by host and port, or its sandbox", () => {
        expect(restdServerLabel(deployment())).toBe("localhost:3306");
        expect(restdServerLabel(deployment({ sandbox_port: 3311 })))
            .toBe("Sandbox localhost:3311");
        // Reached through a socket, but listening on a port too.
        expect(restdServerLabel(deployment({ socket: "/tmp/mysql.sock" })))
            .toBe("localhost:3306");
    });

    it("names a server that listens on no port by its socket", () => {
        expect(restdServerLabel(deployment({ server_port: null,
            socket: "/tmp/mysql.sock" }))).toBe("/tmp/mysql.sock");
    });
});
