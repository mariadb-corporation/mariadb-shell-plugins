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
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync }
    from "node:fs";
import { join } from "node:path";

import { By, CustomTreeSection } from "vscode-extension-tester";

import {
    menuConfirm,
    answerFileDialog,
    childLabels,
    clearClipboard,
    clearNotifications,
    clickButton,
    clickLabelled,
    clipboard,
    contextMenu,
    contextMenuEntries,
    CONNECTION,
    DEPLOY_TIMEOUT,
    dialogErrors,
    FILES,
    hasTreeItem,
    inDialog,
    submitDialog,
    openDialog,
    openSection,
    pickQuick,
    screenshot,
    selectOption,
    selectTab,
    setChecked,
    setField,
    tabNames,
    treeItem,
    waitDialogClosed,
    waitFor,
    waitForNotification,
    waitGone,
} from "../lib/ui";

/**
 * The MariaDB REST Service, as a user works with it: configured on the
 * sandbox, a service with a schema, REST objects of a table and a
 * procedure, a content set, an auth app with a user, and everything taken
 * away again. Each step leans on the ones before it.
 */

const ROOT = [...CONNECTION, "MariaDB REST Service"];
const SERVICE = [...ROOT, "/uitest"];
const SCHEMA = [...SERVICE, "/uitest (uitest)"];
const AUTH_APPS = [...ROOT, "REST Authentication Apps"];

let connections: CustomTreeSection;

/** Waits for a row, redrawing the tree while it is not there yet. */
const appears = async (path: string[]): Promise<void> => {
    await waitFor(async () => {
        return await hasTreeItem(connections, path);
    }, `the row ${path.join(" > ")}`);
};

describe("MariaDB REST Service", () => {
    before(async () => {
        connections = await openSection("Connections");
    });

    it("has no REST Service before it is configured", async () => {
        assert.equal(await hasTreeItem(connections, ROOT), false);
        const entries = await contextMenuEntries(
            await treeItem(connections, CONNECTION));
        assert.ok(entries.includes("Configure MariaDB REST Service..."));
    });

    it("configures the instance with an auth app and a user", async () => {
        await clearNotifications();
        const title = "Configure Instance for MariaDB REST Service Support";
        await openDialog(connections, CONNECTION,
            ["Configure MariaDB REST Service..."], title);
        await inDialog(title, async (view) => {
            assert.deepEqual(await tabNames(view), ["Authentication"]);
            assert.equal(await (await view.findWebElement(
                By.css("[name='metadataSchema']"))).getAttribute("value"),
            "mariadb_rest_service");
            // A password that is too weak is refused.
            await setField(view, "authAppUser", "admin");
            await setField(view, "authAppPassword", "short");
            await clickButton(view, "OK");
            const errors = await dialogErrors(view);
            assert.match(errors.join(" "), /minimum authentication string/);
            await setField(view, "authAppPassword", "Admin!pw12");
            await screenshot("02-configure");
            await submitDialog(view, "OK", DEPLOY_TIMEOUT,
                "Deploying the MariaDB REST Service metadata schema...");
        });
        await waitForNotification("configured successfully");
        await appears(ROOT);
    });

    it("lists the REST Daemons and the auth app with its user", async () => {
        assert.deepEqual(await childLabels(connections, ROOT),
            ["REST Daemons", "REST Authentication Apps"]);
        assert.deepEqual(await childLabels(connections,
            [...ROOT, "REST Daemons"]), []);
        // The metadata brings the MariaDB app; the dialog added MRS.
        assert.deepEqual(await childLabels(connections, AUTH_APPS),
            ["MariaDB", "MRS"]);
        assert.deepEqual(await childLabels(connections, [...AUTH_APPS, "MRS"]),
            ["admin"]);
        await screenshot("02-root");
    });

    it("refuses a reserved service path", async () => {
        const title = "Enter Configuration Values for the New REST Service";
        await openDialog(connections, ROOT,
            ["Add REST Service..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "path", "/mrs");
            await clickButton(view, "OK");
            assert.match((await dialogErrors(view)).join(" "), /reserved/);
            await clickButton(view, "Cancel");
        });
        await waitDialogClosed(title);
    });

    it("adds a REST service", async () => {
        const title = "Enter Configuration Values for the New REST Service";
        await openDialog(connections, ROOT,
            ["Add REST Service..."], title);
        await inDialog(title, async (view) => {
            assert.equal(await (await view.findWebElement(
                By.css("[name='path']"))).getAttribute("value"), "/myService");
            await setField(view, "path", "/uitest");
            await selectTab(view, "Settings");
            // The built-in app is linked to a new service by default.
            assert.equal(await (await view.findWebElement(
                By.css("[name='authApps:MRS']"))).isSelected(), true);
            await submitDialog(view);
        });
        await appears(SERVICE);
        const row = await treeItem(connections, SERVICE);
        assert.equal(await row.getDescription(), "Unpublished");
        // Linked to MRS, so the app shows under it.
        assert.ok((await childLabels(connections, SERVICE)).includes("MRS"));
    });

    it("publishes the service with the edit dialog", async () => {
        const title = "Adjust the REST Service Configuration";
        await openDialog(connections, SERVICE,
            ["Edit REST Service..."], title);
        await inDialog(title, async (view) => {
            await setChecked(view, "published", true);
            await selectTab(view, "Settings");
            await setField(view, "comments", "The UI test's service");
            await submitDialog(view);
        });
        await waitFor(async () => {
            const row = await treeItem(connections, SERVICE);

            return await row.getDescription() === "Published";
        }, "the service to be published");
    });

    it("makes the service the current one", async () => {
        await clearNotifications();
        const entries = await contextMenuEntries(
            await treeItem(connections, SERVICE));
        if (entries.includes("Set as Current REST Service")) {
            await contextMenu(await treeItem(connections, SERVICE),
                "Set as Current REST Service");
            await waitForNotification("new default service");
        }
        await waitFor(async () => {
            return !(await contextMenuEntries(await treeItem(connections,
                SERVICE))).includes("Set as Current REST Service");
        }, "the service to be current");
    });

    it("adds a database schema to the service", async () => {
        const title = "Enter Configuration Values for the New REST Schema";
        await openDialog(connections, [...CONNECTION, "uitest"],
            ["Add Schema to REST Service..."], title);
        await inDialog(title, async (view) => {
            assert.equal(await (await view.findWebElement(
                By.css("[name='requestPath']"))).getAttribute("value"),
            "/uitest");
            await submitDialog(view);
        });
        await appears(SCHEMA);
    });

    it("adds a table as a REST view, with its reference", async () => {
        const title = "Enter Configuration Values for the New REST Object";
        await openDialog(connections, [...CONNECTION, "uitest", "Tables", "city"],
            ["Add Database Object to REST Service..."], title);
        await inDialog(title, async (view) => {
            assert.deepEqual(await tabNames(view),
                ["Data Mapping", "Settings", "Authorization", "Options"]);
            const fields = async (): Promise<string[]> => {
                const rows = await view.findWebElements(By.css(".mrs-row"));

                return Promise.all(rows.map(async (row) => {
                    return await row.getAttribute("data-field") ?? "";
                }));
            };
            assert.deepEqual(await fields(), ["id", "name", "countryId",
                "country"]);
            // Opening the reference loads the country table.
            const country = await view.findWebElement(
                By.css(".mrs-row[data-field='country']"));
            await clickLabelled(country, "Expand");
            await waitFor(async () => {
                return (await fields()).length > 4;
            }, "the referenced table's fields");
            // Sorting by name is allowed.
            const name = await view.findWebElement(
                By.css(".mrs-row[data-field='name']"));
            await clickLabelled(name,
                "Allow sorting operations using this field");
            await screenshot("02-data-mapping");
            // The preview shows what OK will run.
            await submitDialog(view);
        });
        await appears([...SCHEMA, "/city"]);
        assert.equal(await (await treeItem(connections, [...SCHEMA, "/city"]))
            .getDescription(), "city");
    });

    it("previews the view's REST SQL in the edit dialog", async () => {
        const title = "Adjust the REST Object Configuration";
        await openDialog(connections, [...SCHEMA, "/city"],
            ["Edit REST Object..."], title);
        await inDialog(title, async (view) => {
            const preview = await view.findWebElement(
                By.xpath("//label[span[normalize-space()='SQL Preview']]"
                    + "//input"));
            await preview.click();
            const sql = await (await view.findWebElement(
                By.css(".mrs-preview"))).getText();
            assert.match(sql, /ALTER REST VIEW \/city/);
            assert.match(sql, /`name`: `name` @SORTABLE/);
            assert.match(sql, /`country`: `uitest`\.`country`/);
            await preview.click();

            // A new path and the ITEM format.
            await setField(view, "requestPath", "/cities");
            await selectTab(view, "Settings");
            await selectOption(view, "format", "ITEM");
            await submitDialog(view);
        });
        await appears([...SCHEMA, "/cities"]);
    });

    it("copies the object's CREATE statement and request path", async () => {
        clearClipboard();
        await contextMenu(await treeItem(connections, [...SCHEMA, "/cities"]),
            "Copy to Clipboard", "Copy CREATE REST OBJECT Statement");
        await waitFor(async () => {
            return clipboard().includes("REST VIEW /cities");
        }, "the CREATE statement on the clipboard");
        assert.match(clipboard(), /FORMAT ITEM/);

        await contextMenu(await treeItem(connections, [...SCHEMA, "/cities"]),
            "Copy to Clipboard", "Copy REST Object Request Path");
        await waitFor(async () => {
            return clipboard() === "https://localhost:8443/uitest/uitest/cities";
        }, "the request path on the clipboard");
    });

    it("adds a procedure with a result set", async () => {
        assert.ok((await contextMenuEntries(await treeItem(connections,
            [...CONNECTION, "uitest", "Procedures", "cities"])))
            .includes("Add Database Object to REST Service..."));
        const title = "Enter Configuration Values for the New REST Object";
        await openDialog(connections, [...CONNECTION, "uitest", "Procedures", "cities"],
            ["Add Database Object to REST Service..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "requestPath", "/citiesProc");
            const modes = await view.findWebElements(By.css(".mrs-mode"));
            assert.deepEqual(await Promise.all(modes.map(async (mode) => {
                return await mode.getText();
            })), ["IN", "OUT"]);
            await clickButton(view, "Add Result");
            await clickButton(view, "Add Field");
            assert.equal((await view.findWebElements(By.css(".mrs-row")))
                .length, 1);
            await submitDialog(view);
        });
        await appears([...SCHEMA, "/citiesProc"]);
    });

    it("hides a private object until asked to show it", async () => {
        const title = "Adjust the REST Object Configuration";
        await openDialog(connections, [...SCHEMA, "/cities"],
            ["Edit REST Object..."], title);
        await inDialog(title, async (view) => {
            await selectOption(view, "enabled", "PRIVATE Access Only");
            await submitDialog(view);
        });
        await waitGone(connections, [...SCHEMA, "/cities"]);

        await contextMenu(await treeItem(connections, ROOT),
            "Show Private Items");
        await appears([...SCHEMA, "/cities"]);
        await contextMenu(await treeItem(connections, ROOT),
            "Hide Private Items");
        await waitGone(connections, [...SCHEMA, "/cities"]);
        await contextMenu(await treeItem(connections, ROOT),
            "Show Private Items");
        await appears([...SCHEMA, "/cities"]);
    });

    it("uploads a folder as a content set", async () => {
        const folder = join(FILES, "web");
        mkdirSync(folder, { recursive: true });
        writeFileSync(join(folder, "index.html"), "<html>UI test</html>");

        const title = "Enter Configuration Values for the New MRS Static Content Set";
        await openDialog(connections, SERVICE,
            ["Add New REST Content Set..."], title);
        await inDialog(title, async (view) => {
            await setField(view, "directory", folder);
            await setField(view, "requestPath", "/web");
            await setChecked(view, "requiresAuth", false);
            await submitDialog(view, "OK", DEPLOY_TIMEOUT,
                "Uploading the files...");
        });
        await waitForNotification("1 file(s) have been uploaded");
        await appears([...SERVICE, "/web", "/index.html"]);
    });

    it("changes the content set's settings", async () => {
        const title = "Adjust the MRS Static Content Set Configuration";
        await openDialog(connections, [...SERVICE, "/web"],
            ["Edit REST Content Set..."], title);
        await inDialog(title, async (view) => {
            // The folder is only asked for when the set is made.
            assert.equal((await view.findWebElements(
                By.css("[name='directory']"))).length, 0);
            await setField(view, "comments", "The UI test's files");
            await submitDialog(view);
        });
    });

    it("adds an auth app and a user", async () => {
        const appTitle = "Enter Configuration Values for the New MRS Authentication App";
        await openDialog(connections, AUTH_APPS,
            ["Add New Authentication App..."], appTitle);
        await inDialog(appTitle, async (view) => {
            await setField(view, "name", "UiApp");
            assert.deepEqual(await tabNames(view), ["Settings"]);
            await submitDialog(view);
        });
        await appears([...AUTH_APPS, "UiApp"]);

        const userTitle = "Enter new MariaDB REST User Values";
        await openDialog(connections, [...AUTH_APPS, "UiApp"],
            ["Add User..."], userTitle);
        await inDialog(userTitle, async (view) => {
            await setField(view, "name", "anna");
            await setField(view, "password", "Anna!pw123");
            await submitDialog(view);
        });
        await appears([...AUTH_APPS, "UiApp", "anna"]);
    });

    it("locks the user with the edit dialog", async () => {
        const title = "Adjust the REST User";
        await openDialog(connections, [...AUTH_APPS, "UiApp", "anna"],
            ["Edit User..."], title);
        await inDialog(title, async (view) => {
            await setChecked(view, "loginPermitted", false);
            await submitDialog(view);
        });
        await waitFor(async () => {
            return await (await treeItem(connections,
                [...AUTH_APPS, "UiApp", "anna"])).getDescription() === "Locked";
        }, "the user to be locked");
    });

    it("links the auth app to the service, and unlinks it", async () => {
        await contextMenu(await treeItem(connections, SERVICE),
            "Link REST Authentication App...");
        await pickQuick("UiApp");
        await appears([...SERVICE, "UiApp"]);

        assert.match(await menuConfirm(connections, [...SERVICE, "UiApp"],
            ["Unlink REST Authentication App..."], "Unlink"), /should be unlinked/);
        await waitGone(connections, [...SERVICE, "UiApp"]);
    });

    it("changes the configuration options", async () => {
        const title = "MariaDB REST Service Configuration";
        await openDialog(connections, ROOT,
            ["Configure MariaDB REST Service..."], title);
        await inDialog(title, async (view) => {
            assert.deepEqual(await tabNames(view), ["Authentication Throttling",
                "Caches", "Redirects & Static Content", "Options"]);
            await selectTab(view, "Caches");
            await setField(view, "responseCacheSize", "2M");
            await submitDialog(view);
        });

        // Read back by opening the dialog again.
        await contextMenu(await treeItem(connections, ROOT),
            "Configure MariaDB REST Service...");
        await inDialog(title, async (view) => {
            await selectTab(view, "Caches");
            assert.equal(await (await view.findWebElement(
                By.css("[name='responseCacheSize']"))).getAttribute("value"),
            "2M");
            await clickButton(view, "Cancel");
        });
        await waitDialogClosed(title);
    });

    it("exports the service's client SDK", async () => {
        const target = join(FILES, "uitest.mrs.sdk");
        await contextMenu(await treeItem(connections, SERVICE),
            "Dump to Disk", "Dump REST Client SDK Files...");
        await answerFileDialog(target);
        const title = "Export MRS SDK Files for /uitest";
        await inDialog(title, async (view) => {
            assert.equal(await (await view.findWebElement(
                By.css("[name='serviceUrl']"))).getAttribute("value"),
            "https://localhost:8443/uitest");
            await submitDialog(view, "Export", DEPLOY_TIMEOUT,
                "Writing the SDK files...");
        });
        await waitFor(async () => {
            return existsSync(target) && readdirSync(target).length > 0;
        }, "the SDK files");
    });

    it("dumps the service's REST SQL to a file", async () => {
        const target = join(FILES, "uitest.mrs.sql");
        await contextMenu(await treeItem(connections, SERVICE),
            "Dump to Disk", "Dump REST SERVICE SQL Script...");
        await pickQuick("Export SQL Script Including All Endpoints");
        await answerFileDialog(target);
        await waitFor(async () => { return existsSync(target); },
            "the REST SQL file");
        const sql = readFileSync(target, "utf8");
        assert.match(sql, /CREATE OR REPLACE REST SERVICE \/uitest/);
        assert.match(sql, /CREATE OR REPLACE REST CONTENT SET \/web/);
    });

    it("keeps a row when its deletion is declined", async () => {
        await menuConfirm(connections, [...AUTH_APPS, "UiApp", "anna"],
            ["Delete User..."], "Cancel");
        assert.equal(await hasTreeItem(connections,
            [...AUTH_APPS, "UiApp", "anna"]), true);
    });

    it("deletes the user, the app and the content set", async () => {
        await menuConfirm(connections, [...AUTH_APPS, "UiApp", "anna"],
            ["Delete User..."], "Delete User");
        await waitGone(connections, [...AUTH_APPS, "UiApp", "anna"]);

        await menuConfirm(connections, [...AUTH_APPS, "UiApp"],
            ["Delete Authentication App..."], "Delete Authentication App");
        await waitGone(connections, [...AUTH_APPS, "UiApp"]);

        await menuConfirm(connections, [...SERVICE, "/web"],
            ["Delete REST Content Set..."], "Delete Static Content Set");
        await waitGone(connections, [...SERVICE, "/web"]);
    });

    it("deletes the objects, the schema and the service", async () => {
        for (const object of ["/cities", "/citiesProc"]) {
            await menuConfirm(connections, [...SCHEMA, object],
                ["Delete REST Object..."], "Delete DB Object");
            await waitGone(connections, [...SCHEMA, object]);
        }

        await menuConfirm(connections, SCHEMA,
            ["Delete REST Schema..."], "Delete REST Schema");
        await waitGone(connections, SCHEMA);

        await menuConfirm(connections, SERVICE,
            ["Delete REST Service..."], "Delete REST Service");
        await waitGone(connections, SERVICE);
        assert.deepEqual(await childLabels(connections, ROOT),
            ["REST Daemons", "REST Authentication Apps"]);
        await screenshot("02-all-deleted");
    });
});
