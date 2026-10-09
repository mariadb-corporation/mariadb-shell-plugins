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

import { EditorView, TextEditor } from "vscode-extension-tester";

import {
    childLabels,
    CONNECTION,
    inDialog,
    inlineAction,
    openSection,
    runCommand,
    SANDBOX_PASSWORD,
    SANDBOX_PORT,
    SANDBOX_ROW,
    screenshot,
    DEPLOY_TIMEOUT,
    submitDialog,
    SERVER_TIMEOUT,
    setFieldByCaption,
    sleep,
    treeItem,
    waitFor,
} from "../lib/ui";

/**
 * A clean sandbox for the tests that follow, made the way a user makes
 * one: the New Sandbox dialog. Then the `uitest` schema, created by running
 * SQL from an editor opened on the sandbox's connection. The last test
 * file deletes the sandbox again.
 */

const SCHEMA_SQL = [
    "CREATE DATABASE uitest;",
    "CREATE TABLE uitest.country (id INT PRIMARY KEY, name VARCHAR(40) NOT NULL);",
    "CREATE TABLE uitest.city (id INT PRIMARY KEY, name VARCHAR(40) NOT NULL, "
    + "country_id INT, FOREIGN KEY (country_id) REFERENCES uitest.country(id));",
    "INSERT INTO uitest.country VALUES (1, 'Austria'), (2, 'Germany');",
    "INSERT INTO uitest.city VALUES (1, 'Vienna', 1), (2, 'Berlin', 2);",
    "DELIMITER $$",
    "CREATE PROCEDURE uitest.cities(IN prefix VARCHAR(10), OUT n INT) "
    + "BEGIN SELECT id, name FROM uitest.city WHERE name LIKE prefix; "
    + "SET n = 1; END$$",
    "DELIMITER ;",
    "CREATE FUNCTION uitest.twice(x INT) RETURNS INT DETERMINISTIC "
    + "RETURN x * 2;",
].join("\n");

describe("A sandbox to test with", () => {
    it("deploys a sandbox with the New Sandbox dialog", async () => {
        await openSection("Sandboxes");
        await runCommand("MariaDB: New Sandbox…");
        await inDialog("New Sandbox", async (view) => {
            await setFieldByCaption(view, "Port", String(SANDBOX_PORT));
            await setFieldByCaption(view, "Root Password", SANDBOX_PASSWORD);
            await setFieldByCaption(view, "Confirm Root Password",
                SANDBOX_PASSWORD);
            await screenshot("01-new-sandbox");
            await submitDialog(view, "Create", DEPLOY_TIMEOUT,
                "Deploying the sandbox. A server version this machine does "
                + "not have is downloaded first, which can take a few "
                + "minutes.");
        });

        const sandboxes = await openSection("Sandboxes");
        const row = await treeItem(sandboxes, [SANDBOX_ROW]);
        await waitFor(async () => {
            return (await row.getDescription())?.includes("running");
        }, "the sandbox to run");
    });

    it("adds the sandbox's connection to the Sandboxes folder", async () => {
        const connections = await openSection("Connections");
        // Opening it connects, and lists its schemas.
        await treeItem(connections, [...CONNECTION, "mysql"]);
        await screenshot("01-sandbox-connection");
    });

    it("creates the test schema by running SQL from an editor", async () => {
        const connections = await openSection("Connections");
        const connection = await treeItem(connections, CONNECTION);
        await inlineAction(connection, "New SQL Editor");
        const editor = await waitFor(async () => {
            const active = await new EditorView().getActiveTab();
            const title = await active?.getTitle();

            // An untitled editor is named after its first line, which
            // says what it is bound to.
            return title?.includes("MariaDB connection") === true
                ? new TextEditor() : undefined;
        }, "the new SQL editor");
        const header = await editor.getText();
        assert.match(header, new RegExp(`mariadb://root@127\\.0\\.0\\.1:`
            + `${SANDBOX_PORT}`));
        await editor.setText(`${header.trimEnd()}\n${SCHEMA_SQL}\n`);
        await runCommand("MariaDB: Run SQL Script");
        await sleep(2000);
        // Never saved: closing VS Code would ask about it otherwise.
        await runCommand("View: Revert and Close Editor");

        await waitFor(async () => {
            await runCommand("MariaDB: Refresh");

            return (await childLabels(connections, CONNECTION))
                .includes("uitest");
        }, "the uitest schema", SERVER_TIMEOUT);
        assert.deepEqual((await childLabels(connections,
            [...CONNECTION, "uitest", "Tables"])).sort(), ["city", "country"]);
        await screenshot("01-schema-created");
    });
});
