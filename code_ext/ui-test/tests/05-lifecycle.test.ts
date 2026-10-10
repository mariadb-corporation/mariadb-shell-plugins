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

import * as assert from "node:assert/strict";

import { BottomBarPanel, OutputView } from "vscode-extension-tester";

import {
    CONNECTION,
    hasTreeItem,
    menuAction,
    openSection,
    runCommand,
    SANDBOX_ROW,
    SERVER_TIMEOUT,
    treeItem,
    waitFor,
} from "../lib/ui";

/**
 * The rest of the Sandboxes view, and the MCP server: a sandbox stopped
 * and started again, the server's log shown, and the server restarted with
 * everything still there afterwards.
 */
describe("Sandbox lifecycle and the MCP server", () => {
    it("stops the sandbox and starts it again", async () => {
        const sandboxes = await openSection("Sandboxes");
        const state = async (): Promise<string> => {
            return await (await treeItem(sandboxes, [SANDBOX_ROW]))
                .getDescription() ?? "";
        };
        // The menu step only waits for the work to begin: stopping or
        // starting a server takes longer than a missed click does.
        await menuAction(sandboxes, [SANDBOX_ROW], ["Stop Sandbox"],
            async () => { return !(await state()).includes("running"); },
            "the sandbox to begin stopping");
        await waitFor(async () => {
            return (await state()).includes("stopped");
        }, "the sandbox to stop", SERVER_TIMEOUT);
        await menuAction(sandboxes, [SANDBOX_ROW], ["Start Sandbox"],
            async () => { return !(await state()).includes("stopped"); },
            "the sandbox to begin starting");
        await waitFor(async () => {
            return (await state()).includes("running");
        }, "the sandbox to start", SERVER_TIMEOUT);
        await runCommand("MariaDB: Refresh");
        assert.match(await state(), /running/);
    });

    it("shows the MCP server's log", async () => {
        await runCommand("MariaDB: Show MCP Server Log");
        const text = await waitFor(async () => {
            const output = await new BottomBarPanel().openOutputView();
            const content = await (output as OutputView).getText();

            return content.includes("MCP server ready") ? content : undefined;
        }, "the log in the Output view");
        assert.match(text, /Starting MCP server/);
    });

    it("restarts the MCP server, the connections still listed", async () => {
        await runCommand("MariaDB: Restart MCP Server");
        const connections = await openSection("Connections");
        await waitFor(async () => {
            return await hasTreeItem(connections, CONNECTION);
        }, "the connections after the restart", SERVER_TIMEOUT);
        // And it is usable: the connection opens again.
        await waitFor(async () => {
            return await hasTreeItem(connections, [...CONNECTION, "uitest"]);
        }, "the sandbox's schemas", SERVER_TIMEOUT);
    });
});
