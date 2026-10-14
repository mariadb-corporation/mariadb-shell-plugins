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

import { ActivityBar, By, VSBrowser } from "vscode-extension-tester";

import {
    openSection,
    screenshot,
    VIEW_CONTAINER,
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

    it("lays the views out for a first start", async () => {
        // package.json's initialSize weights and the collapsed visibility of
        // REST Daemons and Tasks, which VS Code applies only while it has no
        // sizes of its own for the views: a new profile, as every run is.
        const control = await waitFor(async () => {
            return await new ActivityBar().getViewControl(VIEW_CONTAINER);
        }, "the MariaDB activity bar entry");
        const content = (await control.openView()).getContent();
        const heights = async (): Promise<Record<string, number>> => {
            const found: Record<string, number> = {};
            for (const pane of await VSBrowser.instance.driver.findElements(
                By.css(".part.sidebar .split-view-view"))) {
                const titles = await pane.findElements(
                    By.css(".pane-header h3"));
                if (titles.length > 0) {
                    found[(await titles[0].getText()).toLowerCase()] =
                        (await pane.getRect()).height;
                }
            }

            return found;
        };
        const ROW = 22;

        const first = await waitFor(async () => {
            const found = await heights();

            return found.tasks === undefined ? undefined : found;
        }, "the MariaDB views");
        await screenshot("00-first-layout");
        // REST Daemons and Tasks: their headers only.
        assert.ok(first["rest daemons"]! <= 2 * ROW,
            `REST Daemons is ${first["rest daemons"]}px`);
        assert.ok(first.tasks! <= 2 * ROW, `Tasks is ${first.tasks}px`);
        // Sandboxes: room for a handful of rows, Connections the rest.
        assert.ok(first.sandboxes! >= 5 * ROW && first.sandboxes! <= 9 * ROW,
            `Sandboxes is ${first.sandboxes}px`);
        assert.ok(first.connections! > 5 * first.sandboxes!,
            `Connections is ${first.connections}px`);

        // Opened, Tasks gets a few rows' worth too.
        await (await content.getSection("Tasks")).expand();
        const expanded = await waitFor(async () => {
            const found = await heights();

            return found.tasks! > 2 * ROW ? found : undefined;
        }, "the Tasks view to open");
        assert.ok(expanded.tasks! >= 4 * ROW && expanded.tasks! <= 9 * ROW,
            `Tasks is ${expanded.tasks}px`);
        await (await content.getSection("Tasks")).collapse();
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
