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

import {
    buildConnectionUri,
    emptyConnectionFields,
    parseConnectionUri,
    URI_OPTIONS,
    type IConnectionFields,
} from "../../connections/connectionUri.js";

/** The editor's defaults with the given fields overridden. */
const fields = (
    overrides: Partial<IConnectionFields> = {},
): IConnectionFields => {
    return { ...emptyConnectionFields(), ...overrides };
};

/** The URI those fields build, failing the test if they do not build one. */
const uriOf = (overrides: Partial<IConnectionFields> = {}): string => {
    const result = buildConnectionUri(fields(overrides));
    expect(result.error).toBeUndefined();

    return result.uri!;
};

describe("buildConnectionUri", () => {
    // Every expectation below was produced by the real shell: the fields were
    // handed to `shell.unparse_uri`, and each URI here was then fed back to
    // `shell.parse_uri` and `unparse_uri`, which returned it UNCHANGED. So
    // these are not merely URIs the shell accepts, they are its own canonical
    // spellings - which is what lets a URI built here compare equal to one
    // read back from db.list_connections without a round trip to normalize it.
    it("matches the shell's own spelling", () => {
        expect(uriOf({ user: "dba" })).toBe("dba@localhost:3306");

        expect(uriOf({ user: "dba", schema: "my db" }))
            .toBe("dba@localhost:3306/my%20db");

        expect(uriOf({ user: "my user" }))
            .toBe("my%20user@localhost:3306");

        expect(uriOf({ user: "dba", scheme: "mysqlx" }))
            .toBe("mysqlx://dba@localhost:3306");

        expect(uriOf({ user: "dba", host: "::1" }))
            .toBe("dba@[::1]:3306");

        expect(uriOf({ user: "dba", host: "", port: "", socket: "/tmp/mysql.sock" }))
            .toBe("dba@%2Ftmp%2Fmysql.sock");

        expect(uriOf({
            user: "dba", sslMode: "VERIFY_CA", sslCa: "/etc/my ca.pem",
        })).toBe("dba@localhost:3306?ssl-ca=%2Fetc%2Fmy%20ca.pem&ssl-mode=VERIFY_CA");

        expect(uriOf({
            user: "dba", compression: "REQUIRED", compressionLevel: "3",
        })).toBe("dba@localhost:3306?compression=REQUIRED&compression-level=3");

        expect(uriOf({ user: "dba", compressionAlgorithms: ["zstd", "lz4"] }))
            .toBe("dba@localhost:3306?compression-algorithms=zstd%2Clz4");
    });

    it("sorts the options, as the shell's encoder does", () => {
        // Given out of order on purpose: two URIs naming one connection have
        // to come out identical, or the MCP server sees them as two.
        const uri = uriOf({
            user: "dba",
            sslMode: "REQUIRED",
            connectTimeout: "5000",
            compression: "PREFERRED",
        });

        expect(uri).toBe(
            "dba@localhost:3306"
            + "?compression=PREFERRED&connect-timeout=5000&ssl-mode=REQUIRED",
        );
    });

    it("leaves empty fields out entirely", () => {
        // Not `?ssl-mode=&compression=`: an option that is set to nothing is
        // not the same connection as one that is not set at all.
        expect(uriOf({ user: "dba" })).toBe("dba@localhost:3306");
        expect(uriOf({ user: "dba", port: "" })).toBe("dba@localhost");
    });

    it("prefers a socket over host and port", () => {
        expect(uriOf({
            user: "dba", host: "localhost", port: "3306",
            socket: "/var/run/mysqld/mysqld.sock",
        })).toBe("dba@%2Fvar%2Frun%2Fmysqld%2Fmysqld.sock");
    });

    it("lets a typed option row override the field of the same name", () => {
        // The row is the one the user can see, so it wins.
        expect(uriOf({
            user: "dba",
            sslMode: "REQUIRED",
            extraOptions: [{ name: "ssl-mode", value: "DISABLED" }],
        })).toBe("dba@localhost:3306?ssl-mode=DISABLED");
    });

    it("refuses fields that name no connection", () => {
        expect(buildConnectionUri(fields({ user: "" })).error)
            .toBe("A user name is required.");

        expect(buildConnectionUri(fields({ user: "dba", host: "" })).error)
            .toMatch(/host name, an IP address or a socket/);

        expect(buildConnectionUri(fields({ user: "dba", port: "0" })).error)
            .toMatch(/not a port number/);
        expect(buildConnectionUri(fields({ user: "dba", port: "70000" })).error)
            .toMatch(/not a port number/);
        expect(buildConnectionUri(fields({ user: "dba", port: "x" })).error)
            .toMatch(/not a port number/);

        expect(buildConnectionUri(fields({ user: "dba", scheme: "mariadb" })).error)
            .toMatch(/not a protocol the shell accepts/);
    });

    it("refuses an option a URI cannot carry", () => {
        // ssh-* is a separate set in the shell and never reaches a URI, and
        // sql-mode is not a connection option at all - both are things the
        // MySQL Shell's editor offers and this one must not.
        for (const name of ["ssh", "ssh-identity-file", "sql-mode", "nonsense"]) {
            expect(buildConnectionUri(fields({
                user: "dba",
                extraOptions: [{ name, value: "x" }],
            })).error).toBe(`'${name}' is not a connection option a URI can carry.`);
        }
    });

    it("accepts every option the shell allows in a URI", () => {
        for (const name of URI_OPTIONS) {
            expect(buildConnectionUri(fields({
                user: "dba",
                extraOptions: [{ name, value: "1" }],
            })).error).toBeUndefined();
        }
    });

    it("never puts a password in the URI", () => {
        // The MCP server refuses a URI that carries one, so there is no field
        // for it and nothing can leak into the URI from elsewhere.
        // The credentials are everything before the LAST @, and a password
        // would sit there after a colon. The port's colon is not that.
        const uri = uriOf({ user: "dba", schema: "s", sslCipher: "AES" });
        expect(uri.slice(0, uri.lastIndexOf("@"))).toBe("dba");
        expect(uri.slice(0, uri.lastIndexOf("@"))).not.toContain(":");
    });
});

describe("parseConnectionUri", () => {
    it("takes the shell's own spellings apart again", () => {
        expect(parseConnectionUri("dba@localhost:3306")).toMatchObject({
            user: "dba", host: "localhost", port: "3306", scheme: "",
        });

        expect(parseConnectionUri("mysqlx://dba@localhost:33060"))
            .toMatchObject({ scheme: "mysqlx", port: "33060" });

        expect(parseConnectionUri("dba@localhost:3306/my%20db"))
            .toMatchObject({ schema: "my db" });

        expect(parseConnectionUri("my%20user@localhost:3306"))
            .toMatchObject({ user: "my user" });

        expect(parseConnectionUri("dba@[::1]:3306"))
            .toMatchObject({ host: "::1", port: "3306" });

        expect(parseConnectionUri("dba@localhost"))
            .toMatchObject({ host: "localhost", port: "" });
    });

    it("reads a socket in either spelling the shell emits", () => {
        // The shell leaves the leading separator bare; this one encodes it.
        // Both mean the same path and both have to come back as one.
        expect(parseConnectionUri("dba@/tmp%2Fmysql.sock"))
            .toMatchObject({ socket: "/tmp/mysql.sock", host: "" });
        expect(parseConnectionUri("dba@%2Ftmp%2Fmysql.sock"))
            .toMatchObject({ socket: "/tmp/mysql.sock", host: "" });
    });

    it("splits the options into their own fields", () => {
        const parsed = parseConnectionUri(
            "dba@localhost:3306?compression=REQUIRED&compression-algorithms="
            + "zstd%2Clz4&compression-level=3&connect-timeout=5000"
            + "&ssl-ca=%2Fetc%2Fca.pem&ssl-cert=%2Fetc%2Fc.pem"
            + "&ssl-cipher=AES&ssl-key=%2Fetc%2Fk.pem&ssl-mode=VERIFY_CA",
        );

        expect(parsed).toMatchObject({
            compression: "REQUIRED",
            compressionAlgorithms: ["zstd", "lz4"],
            compressionLevel: "3",
            connectTimeout: "5000",
            sslCa: "/etc/ca.pem",
            sslCert: "/etc/c.pem",
            sslCipher: "AES",
            sslKey: "/etc/k.pem",
            sslMode: "VERIFY_CA",
        });
        expect(parsed.extraOptions).toEqual([]);
    });

    it("puts an option with no field of its own into the table", () => {
        expect(parseConnectionUri(
            "dba@localhost:3306?net-read-timeout=30&auth-method=x",
        ).extraOptions).toEqual([
            { name: "net-read-timeout", value: "30" },
            { name: "auth-method", value: "x" },
        ]);
    });

    it("drops a password rather than showing it in a field", () => {
        // Nothing stores one here; it exists only in the OS secret store.
        const parsed = parseConnectionUri("dba:hunter2@localhost:3306");

        expect(parsed.user).toBe("dba");
        expect(JSON.stringify(parsed)).not.toContain("hunter2");
    });

    it("treats mariadb:// as no scheme, as the MCP server does", () => {
        // The server strips it before parsing, since the shell's own parser
        // rejects that scheme - so it must not come back as a field value the
        // editor would then try to rebuild a URI from.
        expect(parseConnectionUri("mariadb://dba@localhost:3306"))
            .toMatchObject({ scheme: "", user: "dba", host: "localhost" });
    });

    it("opens on a URI it cannot fully make sense of", () => {
        // A dialog that refuses to appear is worse than one showing fields
        // the user can see and correct.
        expect(() => { return parseConnectionUri("nonsense"); }).not.toThrow();
        expect(() => { return parseConnectionUri(""); }).not.toThrow();
    });
});

describe("build and parse together", () => {
    it("round-trips every field the editor has", () => {
        const original = fields({
            scheme: "mysqlx",
            host: "db.example.com",
            port: "33060",
            user: "some user",
            schema: "my db",
            sslMode: "VERIFY_IDENTITY",
            sslCipher: "AES256",
            sslCa: "/etc/ssl/ca.pem",
            sslCert: "/etc/ssl/cert.pem",
            sslKey: "/etc/ssl/key.pem",
            connectTimeout: "5000",
            compression: "REQUIRED",
            compressionLevel: "5",
            compressionAlgorithms: ["zstd", "zlib"],
            extraOptions: [{ name: "net-read-timeout", value: "30" }],
        });

        const built = buildConnectionUri(original);
        expect(built.error).toBeUndefined();

        expect(parseConnectionUri(built.uri!)).toEqual(original);
    });

    it("round-trips a socket connection", () => {
        const original = fields({
            user: "dba", host: "", port: "", socket: "/tmp/mysql.sock",
        });

        expect(parseConnectionUri(buildConnectionUri(original).uri!))
            .toEqual(original);
    });
});
