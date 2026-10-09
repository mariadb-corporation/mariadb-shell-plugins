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

import { VSBrowser } from "vscode-extension-tester";

import {
    openSection,
    screenshot,
    DEPLOY_TIMEOUT,
    SERVER_TIMEOUT,
    waitFor,
} from "../lib/ui";

/**
 * The extension comes up: its activity bar entry and both views, the
 * shell found on the PATH and the MCP server started, so the views say
 * there is nothing yet rather than that they are looking.
 */
describe("Startup", () => {
    before(async () => {
        await VSBrowser.instance.waitForWorkbench();
    });

    it("shows the Connections view, listed and empty", async () => {
        const connections = await openSection("Connections");
        const welcome = await waitFor(async () => {
            const content = await connections.findWelcomeContent();
            const text = await content?.getTextSections();

            return text?.join(" ").includes("No MariaDB connections")
                ? text.join(" ") : undefined;
        }, "the Connections view to list its connections", DEPLOY_TIMEOUT);
        assert.match(welcome, /No MariaDB connections are configured yet/);
        await screenshot("00-connections-empty");
    });

    it("shows the Sandboxes view, listed and empty", async () => {
        const sandboxes = await openSection("Sandboxes");
        const welcome = await waitFor(async () => {
            const content = await sandboxes.findWelcomeContent();
            const text = await content?.getTextSections();

            return text?.join(" ").includes("A sandbox is a MariaDB server")
                ? text.join(" ") : undefined;
        }, "the Sandboxes view to list its sandboxes", SERVER_TIMEOUT);
        assert.match(welcome, /None has/);
    });
});
