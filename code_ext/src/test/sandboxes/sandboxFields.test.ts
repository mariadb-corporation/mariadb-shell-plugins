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
    emptySandboxFields,
    defaultServerVersion,
    isServerOnPath,
    localConnectionPorts,
    mariadbdOptionLines,
    newestFirst,
    serverVersionChoices,
    serverVersionProblem,
    sandboxConnectionUri,
    sandboxDeployOptions,
    sandboxFieldProblem,
    suggestSandboxPort,
    type ISandboxFields,
} from "../../sandboxes/sandboxFields.js";

const fields = (overrides: Partial<ISandboxFields> = {}): ISandboxFields => {
    return { ...emptySandboxFields("3310"), ...overrides };
};

describe("suggestSandboxPort", () => {
    it("starts at 3310, clear of a server on 3306", () => {
        expect(suggestSandboxPort([])).toBe(3310);
    });

    it("takes the next port up past the ones in use", () => {
        expect(suggestSandboxPort([3310])).toBe(3311);
        expect(suggestSandboxPort([3310, 3311, 3313])).toBe(3312);
        // A port below the start is no reason to move.
        expect(suggestSandboxPort([3306])).toBe(3310);
    });
});

describe("newestFirst", () => {
    it("orders versions by their numbers, not their text", () => {
        expect(newestFirst(["11.9.4", "12.3.2", "11.10.1", "10.6.21"]))
            .toEqual(["12.3.2", "11.10.1", "11.9.4", "10.6.21"]);
        expect(newestFirst([])).toEqual([]);
    });
});

describe("emptySandboxFields", () => {
    it("starts on the server on the PATH unless given a version", () => {
        expect(emptySandboxFields("3310").serverVersion)
            .toBe("Server on the PATH");
        expect(emptySandboxFields("3310", "12").serverVersion).toBe("12");
    });
});

describe("the server version", () => {
    it("starts on the highest version", () => {
        expect(defaultServerVersion(["11.8.9", "12.3.2", "10.6.21"]))
            .toBe("12.3.2");
        expect(defaultServerVersion(["11.9.4", "11.10.1"])).toBe("11.10.1");
        expect(defaultServerVersion([])).toBe("Server on the PATH");
    });

    it("offers the versions newest first, then the PATH", () => {
        expect(serverVersionChoices(["11.8.9", "12.3.2"])).toEqual([
            "12.3.2", "11.8.9", "Server on the PATH",
        ]);
        expect(serverVersionChoices([])).toEqual(["Server on the PATH"]);
    });

    it("reads the server on the PATH in any case", () => {
        expect(isServerOnPath(" server on the path ")).toBe(true);
        expect(isServerOnPath("12")).toBe(false);
    });

    it.each(["12", "11.8", "11.8.9", "v11.8", "Server on the PATH"])(
        "accepts %j", (text) => {
            expect(serverVersionProblem(text)).toBeUndefined();
        });

    it("asks for a version when the field is empty", () => {
        expect(serverVersionProblem("  ")).toBe("Enter a server version, "
            + "such as 12 or 11.8.9, or pick Server on the PATH.");
    });

    it.each(["latest", "11.8.9.1", "11.x", "Server on PATH"])(
        "refuses %j, naming it", (text) => {
            expect(serverVersionProblem(text))
                .toContain(`'${text}' is not a server version.`);
        });
});

describe("localConnectionPorts", () => {
    it("reads the port of a connection to this machine", () => {
        expect(localConnectionPorts(["mariadb://dba@localhost:3310"]))
            .toEqual([3310]);
    });

    it("counts localhost, 127.0.0.1 and ::1 as the one machine, in any case",
        () => {
            expect(localConnectionPorts([
                "mariadb://dba@localhost:3310",
                "mariadb://root@127.0.0.1:3310",
                "mariadb://root@[::1]:3312",
                "dba@LOCALHOST:3311",
            ])).toEqual([3310, 3311, 3312]);
        });

    it("puts a local connection with no port on 3306", () => {
        expect(localConnectionPorts(["mariadb://root@127.0.0.1"]))
            .toEqual([3306]);
    });

    it("leaves out other hosts, sockets and tunnels", () => {
        expect(localConnectionPorts([
            "mariadb://dba@db.example.com:3310",
            "mariadb://u@/world?socket=/tmp/mysql.sock",
            // Through a tunnel, localhost is the SSH host's own loopback.
            "mariadb+ssh://u@localhost:3311",
            "mysql+ssh://u@127.0.0.1:3312",
        ])).toEqual([]);
    });
});

describe("sandboxFieldProblem", () => {
    it("accepts a new dialog with a port", () => {
        expect(sandboxFieldProblem(fields())).toBeUndefined();
    });

    it.each([
        ["", "Enter the port"],
        ["33a0", "Enter the port"],
        ["3310.5", "Enter the port"],
        ["80", "between 1024 and 65535"],
        ["65536", "between 1024 and 65535"],
    ])("refuses the port %j", (port, message) => {
        expect(sandboxFieldProblem(fields({ port }))).toEqual({
            field: "port", message: expect.stringContaining(message),
        });
    });

    it("refuses a port a sandbox is already on", () => {
        expect(sandboxFieldProblem(fields(), [3310])?.message)
            .toBe("There is a sandbox on port 3310 already.");
    });

    it.each(["11", "11.8", "11.8.9", "v11.8", " 12.3 ",
        "Server on the PATH"])(
        "accepts the version %j", (serverVersion) => {
            expect(sandboxFieldProblem(fields({ serverVersion })))
                .toBeUndefined();
        });

    it.each(["latest", "11.8.9.1", "11.x", ""])(
        "refuses the version %j", (serverVersion) => {
            expect(sandboxFieldProblem(fields({ serverVersion }))?.field)
                .toBe("serverVersion");
        });

    it("refuses a confirmation that does not match the password", () => {
        expect(sandboxFieldProblem(fields({
            password: "secret", passwordConfirmation: "secert",
        }))).toEqual({
            field: "passwordConfirmation",
            message: "The passwords do not match.",
        });
        // Spaces are characters of a password: not trimmed away.
        expect(sandboxFieldProblem(fields({
            password: "a ", passwordConfirmation: "a",
        }))?.field).toBe("passwordConfirmation");
        expect(sandboxFieldProblem(fields({
            password: "a b", passwordConfirmation: "a b",
        }))).toBeUndefined();
    });

    it("refuses a server ID or timeout that is not a whole number", () => {
        expect(sandboxFieldProblem(fields({ serverId: "-1" }))?.field)
            .toBe("serverId");
        expect(sandboxFieldProblem(fields({ timeout: "1.5" }))?.field)
            .toBe("timeout");
        // No wait at all is not a timeout anyone means.
        expect(sandboxFieldProblem(fields({ timeout: "0" }))?.field)
            .toBe("timeout");
    });
});

describe("sandboxDeployOptions", () => {
    it("sends only the port, password, root host and MCP access when "
        + "nothing else changed", () => {
        // The root host always: the dialog's 127.0.0.1 is not the shell's %.
        expect(sandboxDeployOptions(fields())).toEqual({
            options: {
                port: 3310, password: "", allowRootFrom: "127.0.0.1",
                mcpAccess: true,
            },
        });
    });

    it("sends % and empty for the root host as given", () => {
        expect(sandboxDeployOptions(fields({ allowRootFrom: "%" })))
            .toMatchObject({ options: { allowRootFrom: "%" } });
        expect(sandboxDeployOptions(fields({ allowRootFrom: " " })))
            .toMatchObject({ options: { allowRootFrom: "" } });
    });

    it("sends MCP access off when it was unticked", () => {
        expect(sandboxDeployOptions(fields({ mcpAccess: false })))
            .toMatchObject({ options: { mcpAccess: false } });
    });

    it("sends no version for the server on the PATH, in any case", () => {
        expect(sandboxDeployOptions(fields({
            serverVersion: "server on the path",
        }))).toEqual({
            options: {
                port: 3310, password: "", allowRootFrom: "127.0.0.1",
                mcpAccess: true,
            },
        });
    });

    it("sends everything that was changed", () => {
        expect(sandboxDeployOptions(fields({
            port: " 3320 ",
            password: "secret",
            passwordConfirmation: "secret",
            serverVersion: " 11.8 ",
            allowRootFrom: "",
            serverId: "7",
            ssl: true,
            mariadbdOptions: "innodb_buffer_pool_size=64M\n\n  skip-log-bin \n",
            timeout: "120",
            mcpAccess: false,
        }))).toEqual({
            options: {
                port: 3320,
                password: "secret",
                serverVersion: "11.8",
                allowRootFrom: "",
                serverId: 7,
                ssl: true,
                mariadbdOptions: ["innodb_buffer_pool_size=64M", "skip-log-bin"],
                timeout: 120,
                mcpAccess: false,
            },
        });
    });

    it("keeps the password exactly as typed", () => {
        // Spaces are characters of a password like any other.
        const built = sandboxDeployOptions(fields({
            password: " a b ", passwordConfirmation: " a b ",
        }));

        expect("options" in built && built.options.password).toBe(" a b ");
    });

    it("builds nothing from fields with a problem", () => {
        expect(sandboxDeployOptions(fields(), [3310])).toEqual({
            problem: {
                field: "port",
                message: "There is a sandbox on port 3310 already.",
            },
        });
    });
});

describe("mariadbdOptionLines", () => {
    it("reads one option per line, Windows line ends included", () => {
        expect(mariadbdOptionLines("a=1\r\nb=2\n")).toEqual(["a=1", "b=2"]);
        expect(mariadbdOptionLines("   ")).toEqual([]);
    });
});

describe("sandboxConnectionUri", () => {
    it("spells the connection as the plugin registers it", () => {
        expect(sandboxConnectionUri("3310"))
            .toBe("mariadb://root@127.0.0.1:3310");
        expect(sandboxConnectionUri(" ")).toBe("mariadb://root@127.0.0.1:<port>");
    });
});
