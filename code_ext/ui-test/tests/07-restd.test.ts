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
import { existsSync } from "node:fs";
import { request } from "node:https";

import { By, type WebView } from "vscode-extension-tester";

import {
    CONNECTION,
    DEPLOY_TIMEOUT,
    inDialog,
    inResults,
    isDialogOpen,
    menuAction,
    menuConfirm,
    openSection,
    runCommand,
    SANDBOX_PORT,
    SANDBOX_PASSWORD,
    screenshot,
    SECOND_PORT,
    SERVER_TIMEOUT,
    setChecked,
    setFieldByCaption,
    submitDialog,
    treeItem,
    hasTreeItem,
    waitFor,
    waitForAction,
} from "../lib/ui";

/**
 * The MariaDB REST Daemon: a second sandbox deployed with Configure
 * MariaDB REST Service, its daemon serving and following the sandbox, a
 * daemon set up for the suite's sandbox from its connection's menu, and
 * both deleted again.
 *
 * Needs a mariadb-restd where the MCP server looks for one (the PATH,
 * ~/.local/bin, ~/.local/share/mariadb-restd): its install script is not
 * published yet.
 */

/** How long a setup may take: it configures the REST metadata too. */
const SETUP_TIMEOUT = 2 * DEPLOY_TIMEOUT;

const REST_SANDBOX = `Sandbox localhost:${String(SECOND_PORT)}`;
const SECOND_SANDBOX_ROW = `localhost:${String(SECOND_PORT)}`;

/**
 * Unticks a dialog's box to register the HTTPS certificate, where it is
 * offered: ticked, it would bring up the system's own dialog, which no
 * test can answer. It is offered only while the system does not trust the
 * certificate yet.
 *
 * @param view The dialog.
 * @param name The box's name.
 *
 * @returns Whether it was offered.
 */
const declineCertificate = async (
    view: WebView,
    name: string,
): Promise<boolean> => {
    const boxes = await view.findWebElements(By.css(`input[name='${name}']`));
    if (boxes.length === 0) {
        return false;
    }
    await setChecked(view, name, false);

    return true;
};

/** Shows General Actions in the panel, where the sandbox rows go. */
const showGeneralActions = async (): Promise<void> => {
    await inResults(async (view) => {
        const option = await view.findWebElement(By.xpath(
            "//select[contains(@class,'connectionPicker')]"
            + "/option[@value='General Actions']"));
        await option.click();
    });
};

/**
 * @param url The daemon's address.
 *
 * @returns The HTTP status it answers with, or undefined where it does
 *          not answer. Its certificate is its own, so it is not checked.
 */
const httpStatus = (url: string): Promise<number | undefined> => {
    return new Promise((resolve) => {
        const req = request(url, { rejectUnauthorized: false, timeout: 5000 },
            (response) => {
                response.resume();
                resolve(response.statusCode);
            });
        req.on("error", () => { resolve(undefined); });
        req.on("timeout", () => { req.destroy(); resolve(undefined); });
        req.end();
    });
};

/** @returns A REST Daemons row's description, "port 8443 · running". */
const daemonState = async (label: string): Promise<string> => {
    const daemons = await openSection("REST Daemons");

    return await (await treeItem(daemons, [label])).getDescription() ?? "";
};

/** @returns The port a REST Daemons row says its daemon serves on. */
const daemonPort = async (label: string): Promise<number> => {
    return Number(/port (\d+)/u.exec(await daemonState(label))![1]);
};

describe("The MariaDB REST Daemon", () => {
    it("deploys a sandbox with the MariaDB REST Service", async () => {
        await openSection("Sandboxes");
        await runCommand("MariaDB: New Sandbox…");
        await inDialog("New Sandbox", async (view) => {
            await setFieldByCaption(view, "Port", String(SECOND_PORT));
            await setChecked(view, "generatePassword", false);
            await setFieldByCaption(view, "Root Password", SANDBOX_PASSWORD);
            await setFieldByCaption(view, "Confirm Root Password",
                SANDBOX_PASSWORD);
            await setChecked(view, "configureRest", true);
            // MRS wants a special character, which _ is not.
            await setFieldByCaption(view, "REST User Password", "UiTest-dba-1");
            await declineCertificate(view, "trustRestCertificate");
            await screenshot("07-new-sandbox-rest");
            await submitDialog(view, "Create", DEPLOY_TIMEOUT,
                "Deploying the sandbox. A server version this machine does "
                + "not have is downloaded first, which can take a few "
                + "minutes.");
        });

        // One action, its steps under it, ending with where the service
        // answers.
        await showGeneralActions();
        await waitForAction(new RegExp(
            `^Created Sandbox on port ${String(SECOND_PORT)}.*`
            + "https://localhost:\\d+/myService/v1"), SETUP_TIMEOUT);
        await screenshot("07-rest-actions");

        await waitFor(async () => {
            return (await daemonState(REST_SANDBOX)).includes("running");
        }, "the sandbox's REST Daemon to be listed", SERVER_TIMEOUT);
    });

    it("serves the REST endpoints", async () => {
        const port = await daemonPort(REST_SANDBOX);
        const status = await waitFor(async () => {
            return await httpStatus(`https://localhost:${String(port)}`
                + "/myService/v1/");
        }, "the REST Daemon to answer", SERVER_TIMEOUT);
        assert.ok(status !== undefined);
    });

    it("stops and starts with its sandbox", async () => {
        const sandboxes = await openSection("Sandboxes");
        const sandboxState = async (): Promise<string> => {
            return await (await treeItem(sandboxes, [SECOND_SANDBOX_ROW]))
                .getDescription() ?? "";
        };
        await menuAction(sandboxes, [SECOND_SANDBOX_ROW], ["Stop Sandbox"],
            async () => { return !(await sandboxState()).includes("running"); },
            "the sandbox to begin stopping");
        await waitFor(async () => {
            return (await daemonState(REST_SANDBOX)).includes("stopped");
        }, "the REST Daemon to stop with the sandbox", SERVER_TIMEOUT);

        const again = await openSection("Sandboxes");
        await menuAction(again, [SECOND_SANDBOX_ROW], ["Start Sandbox"],
            async () => {
                return (await sandboxState()).includes("running");
            }, "the sandbox to start");
        await waitFor(async () => {
            return (await daemonState(REST_SANDBOX)).includes("running");
        }, "the REST Daemon to start with the sandbox", SERVER_TIMEOUT);
    });

    it("sets a REST Daemon up from a connection's menu", async () => {
        const connections = await openSection("Connections");
        // Opened, so its menu offers the setup.
        await treeItem(connections, [...CONNECTION, "mysql"]);
        await menuAction(connections, CONNECTION,
            ["Set Up MariaDB REST Daemon…"], async () => {
                return await isDialogOpen("Set Up MariaDB REST Daemon");
            }, "the setup dialog");
        await inDialog("Set Up MariaDB REST Daemon", async (view) => {
            await declineCertificate(view, "trustCertificate");
            await screenshot("07-restd-setup");
            // The stored password, as the field's hint says.
            await submitDialog(view, "Set Up", SETUP_TIMEOUT);
        });

        const label = `127.0.0.1:${String(SANDBOX_PORT)}`;
        await waitFor(async () => {
            return (await daemonState(label)).includes("running");
        }, "the connection's REST Daemon to be listed", SERVER_TIMEOUT);
    });

    it("deletes the connection's REST Daemon from its row", async () => {
        const label = `127.0.0.1:${String(SANDBOX_PORT)}`;
        const daemons = await openSection("REST Daemons");
        const question = await menuConfirm(daemons, [label],
            ["Delete REST Daemon"], "Delete");
        assert.match(question, /Delete the REST Daemon serving/u);
        await waitFor(async () => {
            return !await hasTreeItem(await openSection("REST Daemons"),
                [label]);
        }, "the REST Daemon to go", SERVER_TIMEOUT);
        assert.equal(existsSync(`${process.env.HOME ?? ""}/.mariadb-restd/`
            + `127.0.0.1_${String(SANDBOX_PORT)}`), false);
    });

    it("goes with its sandbox", async () => {
        const sandboxes = await openSection("Sandboxes");
        await menuConfirm(sandboxes, [SECOND_SANDBOX_ROW], ["Delete Sandbox"],
            "Delete");
        await waitFor(async () => {
            return !await hasTreeItem(await openSection("Sandboxes"),
                [SECOND_SANDBOX_ROW]);
        }, "the sandbox to go", SERVER_TIMEOUT);
        await waitFor(async () => {
            return !await hasTreeItem(await openSection("REST Daemons"),
                [REST_SANDBOX]);
        }, "its REST Daemon to go", SERVER_TIMEOUT);
    });
});
