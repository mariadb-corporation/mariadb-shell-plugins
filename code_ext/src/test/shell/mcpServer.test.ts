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

import { buildMcpServerCommand } from "../../shell/mcpServer.js";
import type { ShellLocation } from "../../shell/locator.js";

const location: ShellLocation = {
    binaryPath: "/usr/local/bin/mariadb-shell",
    version: { major: 26, minor: 9, patch: 2 },
    source: "path",
};

describe("buildMcpServerCommand", () => {
    it("starts the MCP server over stdio", () => {
        expect(buildMcpServerCommand(location)).toEqual({
            command: "/usr/local/bin/mariadb-shell",
            args: [
                "--", "mcp", "start-server", "--transport=stdio", "--gui",
            ],
        });
    });

    it("uses the located binary", () => {
        const managed: ShellLocation = {
            binaryPath:
                "/Users/mzinner/.local/share/mariadb-shell/26.9.2/bin"
                + "/mariadb-shell",
            version: { major: 26, minor: 9, patch: 2 },
            source: "managed",
        };

        expect(buildMcpServerCommand(managed).command)
            .toBe(managed.binaryPath);
    });

    it("hands out a fresh argument array each time", () => {
        const first = buildMcpServerCommand(location);
        first.args.push("--extra");

        expect(buildMcpServerCommand(location).args).toHaveLength(5);
    });
});
