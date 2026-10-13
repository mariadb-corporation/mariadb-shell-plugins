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

import { By, CustomTreeSection } from "vscode-extension-tester";

import {
    answerInput,
    childLabels,
    clearClipboard,
    clickButton,
    clipboard,
    contextMenuEntries,
    hasTreeItem,
    inDialog,
    isDialogOpen,
    menuAction,
    menuConfirm,
    openDialog,
    openSection,
    runCommand,
    SANDBOX_PASSWORD,
    SANDBOX_PORT,
    screenshot,
    setFieldByCaption,
    submitDialog,
    treeItem,
    typeInto,
    waitFor,
    waitGone,
} from "../lib/ui";

/**
 * The Connections view's own work: a connection added with the connection
 * editor, tested, edited, made the default, copied, filed in a folder,
 * opened and closed, and deleted again. It connects to the sandbox the
 * tests deployed, under a caption of its own.
 */

const CAPTION = "UI Copy";
const ADDRESS = `root@127.0.0.1:${SANDBOX_PORT}`;
const URI = `mariadb://${ADDRESS}`;

let connections: CustomTreeSection;

/** Fills in the editor's Basic tab for the sandbox. */
const fillBasic = async (view: Parameters<Parameters<typeof inDialog>[1]>[0])
    : Promise<void> => {
    await setFieldByCaption(view, "Caption", CAPTION);
    await typeInto(await view.findWebElement(
        By.css("[aria-label='Host Name/IP']")), "127.0.0.1");
    await setFieldByCaption(view, "Port", String(SANDBOX_PORT));
    await setFieldByCaption(view, "User Name", "root");
    await clickButton(view, "Set Password");
    const password = await view.findWebElement(
        By.css("[aria-label='Password']"));
    await password.sendKeys(SANDBOX_PASSWORD);
};

describe("Connections", () => {
    before(async () => {
        connections = await openSection("Connections");
    });

    it("adds a connection with the connection editor, after a test",
        async () => {
            await runCommand("MariaDB: New Connection…");
            await inDialog("New Database Connection", async (view) => {
                await fillBasic(view);
                await clickButton(view, "Test Connection");
                await waitFor(async () => {
                    const messages = await view.findWebElements(
                        By.css(".message.ok"));

                    return messages.length > 0;
                }, "the connection test to succeed");
                await screenshot("03-connection-editor");
                await submitDialog(view, "Create");
            });
            await waitFor(async () => {
                return await hasTreeItem(connections, [CAPTION]);
            }, "the new connection's row");
            const row = await treeItem(connections, [CAPTION]);
            // The caption replaces the address, which moves beside it.
            assert.match(await row.getDescription() ?? "", /127\.0\.0\.1/);
        });

    it("refuses a connection without a host or user", async () => {
        await runCommand("MariaDB: New Connection…");
        await inDialog("New Database Connection", async (view) => {
            await clickButton(view, "Create");
            await waitFor(async () => {
                return (await view.findWebElements(
                    By.css(".message.error, .invalid"))).length > 0;
            }, "the editor to say what is missing");
            await clickButton(view, "Cancel");
        });
        await waitFor(async () => {
            return !await isDialogOpen("New Database Connection");
        }, "the editor to close");
    });

    it("renames the connection with Edit Connection", async () => {
        await openDialog(connections, [CAPTION], ["Edit Connection"],
            `Edit ${URI}`);
        await inDialog(`Edit ${URI}`, async (view) => {
            await setFieldByCaption(view, "Caption", `${CAPTION} 2`);
            await submitDialog(view, "Save");
        });
        await waitFor(async () => {
            return await hasTreeItem(connections, [`${CAPTION} 2`]);
        }, "the renamed row");
        await waitGone(connections, [CAPTION]);
    });

    it("makes it the default connection, and clears that again", async () => {
        const path = [`${CAPTION} 2`];
        await menuAction(connections, path, ["Set as Default Connection"],
            async () => {
                return (await (await treeItem(connections, path))
                    .getDescription() ?? "").includes("default");
            }, "the default marker");
        await menuAction(connections, path, ["Clear Default Connection"],
            async () => {
                return !(await (await treeItem(connections, path))
                    .getDescription() ?? "").includes("default");
            }, "the default marker to go");
    });

    it("copies the connection's URI", async () => {
        clearClipboard();
        await menuAction(connections, [`${CAPTION} 2`],
            ["Copy Connection URI"], async () => {
                return clipboard().trim() === URI;
            }, "the URI on the clipboard");
    });

    it("files the connection in a new folder, and renames it", async () => {
        await menuAction(connections, [`${CAPTION} 2`],
            ["New Folder with Selection…"], async () => {
                return true;
            }, "the folder prompt");
        await answerInput("Team");
        await waitFor(async () => {
            return await hasTreeItem(connections, ["Team", `${CAPTION} 2`]);
        }, "the connection in the folder");

        await menuAction(connections, ["Team"], ["Rename Folder…"],
            async () => { return true; }, "the rename prompt");
        await answerInput("Crew");
        await waitFor(async () => {
            return await hasTreeItem(connections, ["Crew", `${CAPTION} 2`]);
        }, "the renamed folder");
    });

    it("opens the connection, and closes it with Disconnect", async () => {
        const path = ["Crew", `${CAPTION} 2`];
        assert.ok((await childLabels(connections, path)).includes("uitest"));
        assert.ok((await contextMenuEntries(await treeItem(connections,
            path))).includes("Disconnect"));
        await menuAction(connections, path, ["Disconnect"], async () => {
            return (await contextMenuEntries(await treeItem(connections,
                path))).includes("Connect");
        }, "the connection to close");
        await menuAction(connections, path, ["Connect"], async () => {
            return (await contextMenuEntries(await treeItem(connections,
                path))).includes("Disconnect");
        }, "the connection to open");
    });

    it("deletes the connection, then the empty folder", async () => {
        assert.match(await menuConfirm(connections, ["Crew", `${CAPTION} 2`],
            ["Delete Connection"], "Delete"), /UI Copy 2|127\.0\.0\.1/);
        await waitGone(connections, ["Crew", `${CAPTION} 2`]);
        // The folder stays, empty, until it is removed.
        await menuAction(connections, ["Crew"], ["Remove Folder"],
            async () => {
                return !await hasTreeItem(connections, ["Crew"]);
            }, "the folder to go");
        // The sandbox's own connection is untouched.
        assert.ok(await hasTreeItem(connections, ["Sandboxes", ADDRESS]));
    });
});
