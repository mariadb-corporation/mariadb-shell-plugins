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
    checkConnectionUri,
    connectionLabel,
    emptyConnectionFields,
    parseConnectionUri,
    previewConnectionUri,
    schemeOf,
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
        expect(uriOf({ user: "dba" })).toBe("mariadb://dba@localhost:3306");

        expect(uriOf({ user: "dba", schema: "my db" }))
            .toBe("mariadb://dba@localhost:3306/my%20db");

        expect(uriOf({ user: "my user" }))
            .toBe("mariadb://my%20user@localhost:3306");

        expect(uriOf({ user: "dba", scheme: "mysqlx" }))
            .toBe("mysqlx://dba@localhost:3306");

        expect(uriOf({ user: "dba", host: "::1" }))
            .toBe("mariadb://dba@[::1]:3306");

        expect(uriOf({ user: "dba", host: "", port: "", socket: "/tmp/mysql.sock" }))
            .toBe("mariadb://dba@%2Ftmp%2Fmysql.sock");

        expect(uriOf({
            user: "dba", sslMode: "VERIFY_CA", sslCa: "/etc/my ca.pem",
        })).toBe("mariadb://dba@localhost:3306"
            + "?ssl-ca=%2Fetc%2Fmy%20ca.pem&ssl-mode=VERIFY_CA");

        expect(uriOf({
            user: "dba", compression: "REQUIRED", compressionLevel: "3",
        })).toBe("mariadb://dba@localhost:3306"
            + "?compression=REQUIRED&compression-level=3");

        expect(uriOf({ user: "dba", compressionAlgorithms: ["zstd", "lz4"] }))
            .toBe("mariadb://dba@localhost:3306"
                + "?compression-algorithms=zstd%2Clz4");

        // The tunnel is the scheme, and the ssh-* options ride along with it.
        expect(uriOf({
            user: "dba", host: "db.internal", scheme: "mariadb+ssh",
            sshHost: "bastion.example.com", sshUser: "jump", sshPort: "2222",
        })).toBe("mariadb+ssh://dba@db.internal:3306"
            + "?ssh-host=bastion.example.com&ssh-port=2222&ssh-user=jump");
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
            "mariadb://dba@localhost:3306"
            + "?compression=PREFERRED&connect-timeout=5000&ssl-mode=REQUIRED",
        );
    });

    it("leaves empty fields out entirely", () => {
        // Not `?ssl-mode=&compression=`: an option that is set to nothing is
        // not the same connection as one that is not set at all.
        expect(uriOf({ user: "dba" })).toBe("mariadb://dba@localhost:3306");
        expect(uriOf({ user: "dba", port: "" }))
            .toBe("mariadb://dba@localhost");
    });

    it("prefers a socket over host and port", () => {
        expect(uriOf({
            user: "dba", host: "localhost", port: "3306",
            socket: "/var/run/mysqld/mysqld.sock",
        })).toBe("mariadb://dba@%2Fvar%2Frun%2Fmysqld%2Fmysqld.sock");
    });

    it("lets a typed option row override the field of the same name", () => {
        // The row is the one the user can see, so it wins.
        expect(uriOf({
            user: "dba",
            sslMode: "REQUIRED",
            extraOptions: [{ name: "ssl-mode", value: "DISABLED" }],
        })).toBe("mariadb://dba@localhost:3306?ssl-mode=DISABLED");
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

        expect(buildConnectionUri(fields({ user: "dba", scheme: "postgres" })).error)
            .toMatch(/not a protocol the shell accepts/);

        expect(buildConnectionUri(fields({
            user: "dba", scheme: "mariadb+ssh", sshPort: "0",
        })).error).toMatch(/not an SSH port/);
    });

    it("refuses an option a URI cannot carry", () => {
        // `ssh` and the two SSH passwords are not URI options in the shell,
        // and sql-mode is not a connection option at all - all of them are
        // things the MySQL Shell's editor offers and this one must not.
        for (const name of ["ssh", "ssh-password", "sql-mode", "nonsense"]) {
            expect(buildConnectionUri(fields({
                user: "dba",
                extraOptions: [{ name, value: "x" }],
            })).error).toBe(`'${name}' is not a connection option a URI can carry.`);
        }
    });

    it("refuses an ssh-* option without the tunnel that gives it meaning", () => {
        // The shell refuses it too, but with its own wording about a scheme
        // extension the user never typed.
        expect(buildConnectionUri(fields({
            user: "dba",
            extraOptions: [{ name: "ssh-host", value: "bastion" }],
        })).error).toMatch(/needs an SSH tunnel/);

        expect(buildConnectionUri(fields({
            user: "dba",
            scheme: "mariadb+ssh",
            extraOptions: [{ name: "ssh-host", value: "bastion" }],
        })).error).toBeUndefined();
    });

    it("accepts every option the shell allows in a URI", () => {
        for (const name of URI_OPTIONS) {
            expect(buildConnectionUri(fields({
                user: "dba",
                // The ssh-* ones only mean anything on a tunnelling URI.
                scheme: "mariadb+ssh",
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
        const credentials = uri.slice(
            uri.indexOf("://") + 3, uri.lastIndexOf("@"),
        );
        expect(credentials).toBe("dba");
        expect(credentials).not.toContain(":");
    });
});

describe("parseConnectionUri", () => {
    it("takes the shell's own spellings apart again", () => {
        expect(parseConnectionUri("mariadb://dba@localhost:3306"))
            .toMatchObject({
                user: "dba", host: "localhost", port: "3306",
                scheme: "mariadb",
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

    it("keeps whichever scheme the URI names", () => {
        // The scheme is part of what identifies a connection to the MCP
        // server - mariadb:// and mysql:// are two of them - and the `+ssh`
        // extension is the only way to ask for a tunnel, so none of it may be
        // dropped on the way into the editor.
        for (const scheme of ["mariadb", "mariadb+ssh", "mysql", "mysqlx"]) {
            expect(parseConnectionUri(`${scheme}://dba@localhost:3306`))
                .toMatchObject({ scheme, user: "dba", host: "localhost" });
        }

        // Schemes are case-insensitive, and the shell emits them lowercased.
        expect(parseConnectionUri("MariaDB://dba@localhost:3306"))
            .toMatchObject({ scheme: "mariadb" });

        // A connection stored before the server kept schemes has none, and
        // that means the default - which is the field's starting value.
        expect(parseConnectionUri("dba@localhost:3306"))
            .toMatchObject({ scheme: "mariadb" });
    });

    it("reads the ssh-* options into their own fields", () => {
        expect(parseConnectionUri(
            "mariadb+ssh://dba@db.internal:3306?ssh-config-file=%2Fetc%2Fssh"
            + "&ssh-host=bastion&ssh-identity-file=%2Fk%2Fid&ssh-port=2222"
            + "&ssh-user=jump",
        )).toMatchObject({
            scheme: "mariadb+ssh",
            sshHost: "bastion",
            sshUser: "jump",
            sshPort: "2222",
            sshIdentityFile: "/k/id",
            sshConfigFile: "/etc/ssh",
            extraOptions: [],
        });
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

describe("schemeOf", () => {
    it("reads the scheme, lowercased", () => {
        expect(schemeOf("MySQL+SSH://dba@db")).toBe("mysql+ssh");
        expect(schemeOf("mariadb://dba@db")).toBe("mariadb");
    });

    it("answers the default for a URI without one", () => {
        expect(schemeOf("dba@db:3306")).toBe("mariadb");
    });
});

describe("connectionLabel", () => {
    it("drops the scheme and the options, keeping port and schema", () => {
        expect(connectionLabel(
            "mariadb+ssh://dba@db:3310/world?ssh-host=bastion&ssl-mode=REQUIRED",
        )).toBe("dba@db:3310/world");
        expect(connectionLabel("dba@db")).toBe("dba@db");
    });

    it("decodes a socket path, and survives one that does not decode", () => {
        expect(connectionLabel("mariadb://dba@%2Ftmp%2Fmysql.sock"))
            .toBe("dba@/tmp/mysql.sock");
        expect(connectionLabel("mariadb://dba@db%zz")).toBe("dba@db%zz");
    });
});

describe("previewConnectionUri", () => {
    it("spells out fields that do not name a connection yet", () => {
        // A new connection has no user; the preview shows the gap.
        expect(previewConnectionUri(fields())).toBe("mariadb://@localhost:3306");
    });

    it("agrees with buildConnectionUri where the fields are sound", () => {
        const sound = fields({ user: "dba", schema: "world", sslMode: "REQUIRED" });

        expect(previewConnectionUri(sound))
            .toBe(buildConnectionUri(sound).uri);
    });
});

describe("checkConnectionUri", () => {
    /** The problem with a URI, and the text it points at. */
    const problemOf = (uri: string): { message: string; marked: string } => {
        const { problem } = checkConnectionUri(uri);
        expect(problem).toBeDefined();

        return {
            message: problem!.message,
            marked: uri.slice(problem!.start, problem!.end),
        };
    };

    it("takes a sound URI apart", () => {
        const check = checkConnectionUri(
            "  mysql+ssh://dba@db:3310/world?ssh-host=bastion&ssl-mode=REQUIRED ");

        expect(check.problem).toBeUndefined();
        expect(check.fields).toMatchObject({
            scheme: "mysql+ssh",
            user: "dba",
            host: "db",
            port: "3310",
            schema: "world",
            sshHost: "bastion",
            sslMode: "REQUIRED",
        });
    });

    it("accepts one without a scheme, a socket or an IPv6 host", () => {
        for (const uri of [
            "dba@db",
            "mariadb://dba@%2Ftmp%2Fmysql.sock",
            "mariadb://dba@/tmp/mysql.sock",
            "mariadb://dba@[::1]:3306/world",
        ]) {
            expect(checkConnectionUri(uri).problem, uri).toBeUndefined();
        }
    });

    it("points at an unknown protocol", () => {
        expect(problemOf("postgres://dba@db")).toMatchObject({
            marked: "postgres",
        });
    });

    it("points at a missing user", () => {
        expect(problemOf("mariadb://db:3306").message)
            .toContain("user name is required");
        expect(problemOf("mariadb://@db").message).toContain("empty");
        expect(problemOf("mariadb://:secret@db").message)
            .toContain("before the password is empty");
    });

    it("takes a password out, decoded, and leaves it out of the fields",
        () => {
            const check = checkConnectionUri("mariadb://dba:p%40ss:w@db/world");

            expect(check.problem).toBeUndefined();
            expect(check.password).toBe("p@ss:w");
            expect(check.fields).toMatchObject({
                user: "dba", host: "db", schema: "world",
            });
            expect(buildConnectionUri(check.fields!).uri)
                .toBe("mariadb://dba@db/world");
        });

    it("keeps an empty password, which is a password", () => {
        expect(checkConnectionUri("mariadb://dba:@db").password).toBe("");
        expect(checkConnectionUri("mariadb://dba@db")).not
            .toHaveProperty("password");
    });

    it("points at a password that does not decode", () => {
        expect(problemOf("mariadb://dba:se%zz@db").marked).toBe("se%zz");
    });

    it("points at a missing host and a bad port", () => {
        expect(problemOf("mariadb://dba@").message).toContain("host name");
        expect(problemOf("mariadb://dba@db:33x6/world").marked).toBe("33x6");
        expect(problemOf("mariadb://dba@db:70000").marked).toBe("70000");
        expect(problemOf("mariadb://dba@db:").marked).toBe("");
        expect(problemOf("mariadb://dba@:3306").message)
            .toContain("host name");
    });

    it("points at a broken IPv6 address", () => {
        expect(problemOf("mariadb://dba@[::1:3306").message)
            .toContain("closing");
        expect(problemOf("mariadb://dba@[::1]x").marked).toBe("x");
    });

    it("points at bad percent-encoding", () => {
        expect(problemOf("mariadb://d%zzba@db").marked).toBe("d%zzba");
        expect(problemOf("mariadb://dba@db/wor%zzld").marked).toBe("wor%zzld");
        expect(problemOf("mariadb://dba@db?ssl-mode=%zz").marked)
            .toBe("ssl-mode=%zz");
    });

    it("points at an option a URI cannot carry", () => {
        expect(problemOf("mariadb://dba@db?ssl-mode=REQUIRED&sql-mode=x"))
            .toMatchObject({ marked: "sql-mode" });
        expect(problemOf("mariadb://dba@db?ssl-mode=a&ssl-mode=b").message)
            .toContain("more than once");
    });

    it("points at an ssh option on a URI that does not tunnel", () => {
        expect(problemOf("mariadb://dba@db?ssh-host=bastion")).toEqual({
            message: expect.stringContaining("SSH tunnel") as string,
            marked: "ssh-host",
        });
        expect(problemOf("mariadb+ssh://dba@db?ssh-port=0").marked).toBe("0");
    });

    it("offsets the mark past leading blanks", () => {
        const { problem } = checkConnectionUri("  postgres://dba@db");

        expect(problem).toMatchObject({ start: 2, end: 10 });
    });

    it("refuses an empty URI", () => {
        expect(checkConnectionUri("   ").problem?.message)
            .toContain("Type or paste");
    });
});
