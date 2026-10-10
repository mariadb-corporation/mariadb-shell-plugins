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
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { By, CustomTreeSection, Workbench } from "vscode-extension-tester";

import {
    childLabels,
    clearNotifications,
    CONNECTION,
    DEPLOY_TIMEOUT,
    inDialog,
    inlineAction,
    FILES,
    hasTreeItem,
    menuConfirm,
    openDialog,
    openSection,
    SANDBOX_PASSWORD,
    SANDBOX_PORT,
    runCommand,
    screenshot,
    SECOND_PORT,
    selectTab,
    SERVER_TIMEOUT,
    setField,
    setFieldByCaption,
    submitDialog,
    treeItem,
    waitFor,
    waitForNotification,
    waitGone,
} from "../lib/ui";

/**
 * Dump and Load: a schema dumped to disk and loaded back under another
 * name, a table exported to a file and imported into another table, and a
 * slow dump cancelled from the Tasks view. Each runs as a task in the MCP
 * server, followed by a notification and a row in the Tasks view.
 *
 * What the server ends up holding is checked with the shell on the
 * command line, outside the UI under test. Runs on the sandbox and the
 * `uitest` schema of `01-sandbox`.
 */

const SUBMENU = "Dump and Load";
const SECOND_CONNECTION = ["Sandboxes", `root@127.0.0.1:${String(SECOND_PORT)}`];
const SCHEMA = ["uitest"];
const DUMP = join(FILES, "uitest-dump");
const EXPORT = join(FILES, "country.tsv");

/** The connection's label in dialog titles, as the extension names it. */
const CONNECTION_LABEL = `root@127.0.0.1:${SANDBOX_PORT}`;

/**
 * Runs SQL on the sandbox with the shell the run put first on the PATH.
 *
 * @param statement One statement.
 * @param port The sandbox's port.
 *
 * @returns Its rows, a line each, the columns tab separated.
 */
const sql = (statement: string, port = SANDBOX_PORT): string => {
    return execFileSync("mariadb-shell", [
        `--uri=mariadb://root@127.0.0.1:${String(port)}/mysql`,
        `--password=${SANDBOX_PASSWORD}`,
        "--py", "-e",
        "import os\n"
        + "rows = session.run_sql(os.environ['UI_TEST_SQL']).fetch_all()\n"
        + "print('\\n'.join('\\t'.join(str(v) for v in r) for r in rows))",
    ], {
        // stderr kept: it warns about the password on the command line.
        encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, UI_TEST_SQL: statement },
    }).trim();
};

let connections: CustomTreeSection;

/**
 * Refreshes the Connections view until a table shows up: one made outside
 * the extension is listed once the tree reads its schema again.
 *
 * @param table The table, in `uitest`.
 *
 * @returns Nothing.
 */
const waitForTable = async (table: string): Promise<void> => {
    await waitFor(async () => {
        // The view's own button: the palette has two "MariaDB: Refresh",
        // the Sandboxes view's as well.
        connections = await openSection("Connections");
        await (await connections.getAction("Refresh"))?.click();

        return (await childLabels(connections,
            [...CONNECTION, ...SCHEMA, "Tables"])).includes(table);
    }, `the table ${table} in the tree`, SERVER_TIMEOUT);
};

/**
 * Waits for the notification that says how a task ended.
 *
 * @param title The task's title, which is its dialog's.
 * @param timeout How long it may take.
 *
 * @returns The notification's message.
 */
const waitEnded = async (
    title: string,
    timeout = DEPLOY_TIMEOUT,
): Promise<string> => {
    return await waitFor(async () => {
        for (const notification of await new Workbench().getNotifications()) {
            const message = await notification.getMessage();
            if (message.startsWith(`${title} `)) {
                await notification.dismiss().catch(() => { /* gone */ });

                return message;
            }
        }

        return undefined;
    }, `"${title}" to end`, timeout);
};

/**
 * Waits for a task to complete, failing with how it ended otherwise.
 *
 * @param title The task's title, which is its dialog's.
 *
 * @returns Nothing.
 */
const waitCompleted = async (title: string): Promise<void> => {
    let ended: string | undefined;
    try {
        ended = await waitEnded(title);
    } finally {
        if (ended !== `${title} completed.`) {
            await screenshot(`06-not-completed-${title}`
                .replace(/[^A-Za-z0-9-]+/g, "_"));
        }
    }
    assert.equal(ended, `${title} completed.`);
};

/**
 * @param title A task's title.
 *
 * @returns Its row's description in the Tasks view: its state or stage.
 */
const taskState = async (title: string): Promise<string> => {
    const tasks = await openSection("Tasks");
    const row = await treeItem(tasks, [title]);

    return await row.getDescription() ?? "";
};

describe("Dump and Load", () => {
    before(async () => {
        connections = await openSection("Connections");
        await clearNotifications();
    });

    it("dumps a schema to disk from its Dump and Load submenu", async () => {
        const title = "Dump uitest to Disk";
        await openDialog(connections, [...CONNECTION, ...SCHEMA],
            [SUBMENU, "Dump Schema to Disk..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", DUMP);
            await screenshot("06-dump-dialog");
            await submitDialog(view, "Dump", SERVER_TIMEOUT * 2);
        });
        await waitCompleted(title);

        assert.equal(await taskState(title), "completed");
        const files = readdirSync(DUMP);
        assert.ok(files.includes("@.done.json"), files.join(", "));
        assert.ok(files.some((name) => {
            return name.startsWith("uitest@country");
        }), files.join(", "));
        await screenshot("06-dumped");
    });

    it("loads the dump into another schema from the connection's submenu",
        async () => {
            const title = `Load a Dump into ${CONNECTION_LABEL}`;
            connections = await openSection("Connections");
            await openDialog(connections, CONNECTION,
                [SUBMENU, "Load Dump from Disk..."], title);
            await inDialog(title, async (view) => {
                await setField(view, "path", DUMP);
                await setField(view, "schema", "uitest_copy");
                await submitDialog(view, "Load", SERVER_TIMEOUT * 2);
            });
            await waitCompleted(title);

            assert.equal(sql("SELECT name FROM uitest_copy.country "
                + "ORDER BY id"), "Austria\nGermany");
            // A load refreshes the tree once it is done.
            connections = await openSection("Connections");
            await waitFor(async () => {
                return (await childLabels(connections, CONNECTION))
                    .includes("uitest_copy");
            }, "uitest_copy in the tree", SERVER_TIMEOUT);
        });

    it("exports a table to a file", async () => {
        const title = "Export uitest.country to a File";
        connections = await openSection("Connections");
        await openDialog(connections,
            [...CONNECTION, ...SCHEMA, "Tables", "country"],
            [SUBMENU, "Export Table Data to File..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", EXPORT);
            await submitDialog(view, "Export", SERVER_TIMEOUT * 2);
        });
        await waitCompleted(title);

        assert.ok(existsSync(EXPORT));
        assert.equal(readFileSync(EXPORT, "utf8"), "1\tAustria\n2\tGermany\n");
    });

    it("imports the file into another table", async () => {
        sql("CREATE TABLE uitest.country_import LIKE uitest.country");
        await waitForTable("country_import");
        const title = "Import Data into uitest.country_import";
        connections = await openSection("Connections");
        await openDialog(connections,
            [...CONNECTION, ...SCHEMA, "Tables", "country_import"],
            [SUBMENU, "Import Data from File..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", EXPORT);
            await submitDialog(view, "Import", SERVER_TIMEOUT * 2);
        });
        await waitCompleted(title);

        assert.equal(sql("SELECT name FROM uitest.country_import "
            + "ORDER BY id"), "Austria\nGermany");
    });

    it("cancels a slow dump from the Tasks view", async () => {
        // Enough rows that a dump held to 1 KB/s is still running when the
        // Cancel button is pressed.
        sql("CREATE TABLE uitest.big (id INT PRIMARY KEY, pad CHAR(100)) "
            + "AS SELECT seq AS id, REPEAT('x', 100) AS pad "
            + "FROM uitest.seq_1_to_20000");
        await waitForTable("big");
        const title = "Dump big to Disk";
        connections = await openSection("Connections");
        await openDialog(connections,
            [...CONNECTION, ...SCHEMA, "Tables", "big"],
            [SUBMENU, "Dump Table to Disk..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", join(FILES, "big-dump"));
            await selectTab(view, "Advanced");
            await setField(view, "maxRate", "1k");
            await submitDialog(view, "Dump", SERVER_TIMEOUT * 2);
        });

        // The row follows the stage and, while data moves, its percent.
        const tasks = await openSection("Tasks");
        await waitFor(async () => {
            return /^Dumping data \d+%$/.test(await taskState(title));
        }, "the row to show the dump's percent", SERVER_TIMEOUT);
        await screenshot("06-running");
        // One click, as a user's: the list redraws at most once a second
        // while the dump runs, so the click is not lost under a redraw.
        await inlineAction(await treeItem(tasks, [title]), "Cancel Task");
        await waitForNotification(`${title} was cancelled.`, SERVER_TIMEOUT);
        assert.equal(await taskState(title), "cancelled");
    });

    it("says why a dump into a folder that is not empty failed", async () => {
        const title = "Dump uitest to Disk";
        connections = await openSection("Connections");
        await openDialog(connections, [...CONNECTION, ...SCHEMA],
            [SUBMENU, "Dump Schema to Disk..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", DUMP);
            await submitDialog(view, "Dump", SERVER_TIMEOUT * 2);
        });
        const message = await waitEnded(title);
        await screenshot("06-not-empty");
        assert.match(message, /failed/);
        assert.match(message, /not empty|already exists|exists/i);
    });

    it("copies a schema to another server", async () => {
        await runCommand("MariaDB: New Sandbox…");
        await inDialog("New Sandbox", async (view) => {
            await setFieldByCaption(view, "Port", String(SECOND_PORT));
            await setFieldByCaption(view, "Root Password", SANDBOX_PASSWORD);
            await setFieldByCaption(view, "Confirm Root Password",
                SANDBOX_PASSWORD);
            await submitDialog(view, "Create", DEPLOY_TIMEOUT);
        });
        connections = await openSection("Connections");
        // Out of sight below the first connection's rows otherwise, and
        // ExTester sees only the rows drawn.
        await (await treeItem(connections, CONNECTION)).collapse();
        await treeItem(connections, [...SECOND_CONNECTION, "mysql"]);

        const title = "Copy uitest to Another Server";
        await openDialog(connections, [...CONNECTION, ...SCHEMA],
            [SUBMENU, "Copy Schema to Another Server..."], title);
        await inDialog(title, async (view) => {
            const target = await view.findWebElement(By.xpath(
                "//select[@name='target']/option[contains(., "
                + `':${String(SECOND_PORT)}')]`));
            await target.click();
            await screenshot("06-copy-dialog");
            await submitDialog(view, "Copy", SERVER_TIMEOUT * 2);
        });
        await waitCompleted(title);

        assert.equal(sql("SELECT name FROM uitest.country ORDER BY id",
            SECOND_PORT), "Austria\nGermany");
    });

    it("deletes the second sandbox", async () => {
        const sandboxes = await openSection("Sandboxes");
        await menuConfirm(sandboxes, [`localhost:${String(SECOND_PORT)}`],
            ["Delete Sandbox"], "Delete");
        connections = await openSection("Connections");
        await waitGone(connections, SECOND_CONNECTION);
    });

    it("clears the finished tasks from the Tasks view", async () => {
        const tasks = await openSection("Tasks");
        const action = await waitFor(async () => {
            return await tasks.getAction("Clear Finished Tasks");
        }, "the Clear Finished Tasks button");
        await action?.click();
        // Looked for by title: ExTester cannot read the rows of an empty
        // tree ("element not interactable").
        for (const title of ["Dump uitest to Disk", "Dump big to Disk",
            "Export uitest.country to a File",
            "Copy uitest to Another Server"]) {
            await waitFor(async () => {
                return !await hasTreeItem(tasks, [title]);
            }, `${title} to leave the Tasks view`);
        }
        sql("DROP SCHEMA uitest_copy");
    });
});
