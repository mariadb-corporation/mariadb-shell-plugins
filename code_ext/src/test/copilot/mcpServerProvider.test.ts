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

import { beforeEach, describe, expect, it } from "vitest";

import {
    COPILOT_MCP_PROVIDER_ID,
    CopilotMcpServerProvider,
    registerCopilotMcpServer,
} from "../../copilot/mcpServerProvider.js";
import type { BootstrapResult } from "../../shell/bootstrap.js";
import {
    shellBinaryName,
    type ShellLocation,
} from "../../shell/locator.js";
import { ShellResolver } from "../../shell/shellResolver.js";
import { createRecordingLog } from "../helpers.js";
import {
    configuration,
    fireConfigurationChange,
    McpStdioServerDefinition,
    mcpServerDefinitionProviders,
    resetVscodeMock,
} from "../mocks/vscode.js";

const SHELL: ShellLocation = {
    binaryPath: "/home/me/.local/share/mariadb-shell/26.10.1/bin/mariadb-shell",
    version: { major: 26, minor: 10, patch: 1 },
    source: "managed",
};

const resolverFinding = (
    found: ShellLocation = SHELL,
): { resolver: ShellResolver; lookups: () => number } => {
    let lookups = 0;
    const resolver = new ShellResolver((): Promise<BootstrapResult> => {
        lookups += 1;

        return Promise.resolve({ location: found, installed: false });
    });

    return { resolver, lookups: () => { return lookups; } };
};

describe("CopilotMcpServerProvider", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    const on = (): boolean => { return true; };

    it("lists the server on the bare name, without looking for a shell",
        () => {
            const { resolver, lookups } = resolverFinding();
            const provider = new CopilotMcpServerProvider(resolver, on,
                createRecordingLog(), undefined, "linux");

            const [server, ...rest] = provider.provideMcpServerDefinitions();

            expect(rest).toEqual([]);
            expect(server.label).toBe("MariaDB");
            expect(server.command).toBe("mariadb-shell");
            expect(lookups()).toBe(0);
        });

    it("names the Windows executable", () => {
        const { resolver } = resolverFinding();
        const provider = new CopilotMcpServerProvider(resolver, on,
            createRecordingLog(), undefined, "win32");

        expect(provider.provideMcpServerDefinitions()[0].command)
            .toBe("mariadb-shell.exe");
    });

    it("finds the shell before the start, and runs it without --gui",
        async () => {
            const { resolver, lookups } = resolverFinding();
            const log = createRecordingLog();
            const provider = new CopilotMcpServerProvider(resolver, on, log);
            const [listed] = provider.provideMcpServerDefinitions();

            const server = await provider.resolveMcpServerDefinition(listed);

            expect(lookups()).toBe(1);
            expect(server?.command).toBe(SHELL.binaryPath);
            expect(server?.args).toEqual(
                ["--", "mcp", "start-server", "--transport=stdio"]);
            expect(log.lines.join("\n")).toContain("for Copilot");
            // Listed again, it is listed as before: nothing to restart for.
            expect(provider.provideMcpServerDefinitions()[0].command)
                .toBe(shellBinaryName(process.platform));
        });

    it("lists nothing and starts nothing while switched off", async () => {
        const { resolver, lookups } = resolverFinding();
        const provider = new CopilotMcpServerProvider(resolver, () => {
            return false;
        }, createRecordingLog());

        expect(provider.provideMcpServerDefinitions()).toEqual([]);
        expect(await provider.resolveMcpServerDefinition(
            new McpStdioServerDefinition("MariaDB", "mariadb-shell")))
            .toBeUndefined();
        expect(lookups()).toBe(0);
    });

    it("lets the lookup's failure reach VS Code", async () => {
        const resolver = new ShellResolver(() => {
            return Promise.reject(new Error("installer failed"));
        });
        const provider = new CopilotMcpServerProvider(resolver, on,
            createRecordingLog());

        await expect(provider.resolveMcpServerDefinition(
            new McpStdioServerDefinition("MariaDB", "mariadb-shell")))
            .rejects.toThrow("installer failed");
    });
});

describe("registerCopilotMcpServer", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    const registered = (): CopilotMcpServerProvider => {
        return mcpServerDefinitionProviders.get(COPILOT_MCP_PROVIDER_ID) as
            CopilotMcpServerProvider;
    };

    it("registers under the id package.json declares, until disposed", () => {
        const { resolver } = resolverFinding();
        const registration = registerCopilotMcpServer(resolver,
            createRecordingLog());

        expect(registered()).toBeInstanceOf(CopilotMcpServerProvider);
        registration.dispose();
        expect(mcpServerDefinitionProviders.size).toBe(0);
    });

    it("follows the setting, which is on by default", () => {
        const { resolver } = resolverFinding();
        registerCopilotMcpServer(resolver, createRecordingLog());
        let changes = 0;
        registered().onDidChangeMcpServerDefinitions?.(() => {
            changes += 1;
        });

        expect(registered().provideMcpServerDefinitions()).toHaveLength(1);

        configuration.set("mariadb.copilot.enabled", false);
        fireConfigurationChange("mariadb.copilot.enabled");
        fireConfigurationChange("mariadb.execute.pageSize");

        expect(changes).toBe(1);
        expect(registered().provideMcpServerDefinitions()).toEqual([]);
    });
});
