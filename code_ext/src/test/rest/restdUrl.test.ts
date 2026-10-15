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
import { restdFor } from "../../rest/restdUrl.js";

/** A deployment of mariadb-restd, serving the given server on `port`. */
const deployment = (
    host: string | null,
    serverPort: number,
    port: number,
    extra: Partial<IRestdDeployment> = {},
): IRestdDeployment => {
    return {
        directory: `/d/${port}`, host, server_port: serverPort, port,
        url: `https://localhost:${port}`, status: "running", pid: 42,
        log_path: `/d/${port}/log/mariadb-restd.log`,
        metadata_schema: "mariadb_rest_service", connection_uri: null,
        sandbox_port: null, ...extra,
    };
};

describe("restdFor", () => {
    it("matches a connection to its server's daemon", () => {
        const daemons = [
            deployment("127.0.0.1", 3311, 8443),
            deployment("db1.example.com", 3306, 8444),
            deployment("db1.example.com", 3307, 8445),
        ];

        expect(restdFor(daemons, "root@localhost:3311")?.port).toBe(8443);
        expect(restdFor(daemons, "root@[::1]:3311")?.port).toBe(8443);
        expect(restdFor(daemons, "anna@DB1.example.com")?.port).toBe(8444);
        expect(restdFor(daemons, "anna@db1.example.com:3307")?.port)
            .toBe(8445);
        expect(restdFor(daemons, "anna@db2.example.com:3306")).toBeUndefined();
        expect(restdFor(daemons, "root@localhost:3312")).toBeUndefined();
    });

    it("matches the metadata schema the row is on", () => {
        const daemons = [
            deployment("localhost", 3306, 8443),
            deployment("localhost", 3306, 8444,
                { metadata_schema: "dev_mariadb_rest_service" }),
        ];

        expect(restdFor(daemons, "root@localhost")?.port).toBe(8443);
        expect(restdFor(daemons, "root@localhost",
            "dev_mariadb_rest_service")?.port).toBe(8444);
        expect(restdFor(daemons, "root@localhost", "other")).toBeUndefined();
    });

    it("takes a running daemon before a stopped one", () => {
        const daemons = [
            deployment("localhost", 3306, 8443, { status: "stopped" }),
            deployment("localhost", 3306, 8444),
        ];

        expect(restdFor(daemons, "root@localhost")?.port).toBe(8444);
        expect(restdFor(daemons.slice(0, 1), "root@localhost")?.port)
            .toBe(8443);
    });

    it("skips a deployment that does not say its server", () => {
        expect(restdFor([deployment(null, 3306, 8443)], "root@localhost"))
            .toBeUndefined();
    });

    it("matches a socket connection on its socket", () => {
        const daemons = [
            deployment("localhost", 3306, 8443),
            deployment("localhost", 3306, 8444,
                { socket: "/tmp/mysql.sock" }),
        ];

        expect(restdFor(daemons, "root@(/tmp/mysql.sock)")?.port).toBe(8444);
        expect(restdFor(daemons, "root@%2Ftmp%2Fmysql.sock")?.port)
            .toBe(8444);
        expect(restdFor(daemons, "root@(/tmp/other.sock)")).toBeUndefined();
        expect(restdFor(daemons, "root@localhost")?.port).toBe(8443);
    });

    it("takes the one metadata schema a server has, whatever its name",
        () => {
            const daemons = [deployment("localhost", 3306, 8443,
                { metadata_schema: "acme_rest" })];

            expect(restdFor(daemons, "root@localhost")?.port).toBe(8443);
            expect(restdFor(daemons, "root@localhost",
                "mariadb_rest_service")).toBeUndefined();
        });
});
