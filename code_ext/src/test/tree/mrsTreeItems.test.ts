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

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type {
    EnabledState,
    IMrsService,
    MrsObjectType,
} from "../../mrs/mrsTypes.js";
import type { MrsNode } from "../../tree/mrsModel.js";
import {
    accessSuffix,
    developersOf,
    formatBytes,
    MRS_ROOT_LABEL,
    MrsTreeItem,
} from "../../tree/mrsTreeItems.js";
import type { IconResolver } from "../../tree/treeItems.js";
import {
    mrsAuthApp,
    mrsContentFile,
    mrsContentSet,
    mrsDaemon,
    mrsObject,
    mrsSchema,
    mrsService,
    mrsStatus,
    mrsUser,
} from "../helpers.js";
import { ThemeIcon, TreeItemCollapsibleState, Uri } from "../mocks/vscode.js";

/** The extension's own folder, where `images/` is. */
const EXTENSION_ROOT = join(
    dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

const URI = "dba@localhost:3310";

/** Resolves an icon to its name, which is what the tests compare. */
const resolveIcon: IconResolver = (name: string) => {
    return {
        light: Uri.file(`/ext/images/light/${name}`),
        dark: Uri.file(`/ext/images/dark/${name}`),
    } as never;
};

const item = (node: MrsNode): MrsTreeItem => {
    return new MrsTreeItem(node, resolveIcon);
};

/** The icon file an item shows, or the codicon's id. */
const iconOf = (treeItem: MrsTreeItem): string => {
    if (treeItem.iconPath instanceof ThemeIcon) {
        return `$(${treeItem.iconPath.id})`;
    }
    const path = (treeItem.iconPath as { light: Uri }).light.path;

    return path.slice(path.lastIndexOf("/") + 1);
};

const serviceItem = (
    overrides: Partial<IMrsService> = {},
    metadataSchema?: string,
): MrsTreeItem => {
    return item({
        kind: "mrsService",
        uri: URI,
        ...(metadataSchema === undefined ? {} : { metadataSchema }),
        service: mrsService(overrides),
        showPrivate: false,
    });
};

describe("formatBytes", () => {
    it("counts bytes up to a KB, then one decimal below ten", () => {
        expect(formatBytes(0)).toBe("0 bytes");
        expect(formatBytes(1023)).toBe("1023 bytes");
        expect(formatBytes(1024)).toBe("1.0 KB");
        expect(formatBytes(1434)).toBe("1.4 KB");
        expect(formatBytes(20 * 1024)).toBe("20 KB");
        expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
        expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
    });

    it("stops at GB", () => {
        expect(formatBytes(2048 * 1024 ** 3)).toBe("2048 GB");
    });
});

describe("accessSuffix", () => {
    it("names the variant: disabled, then private, then locked", () => {
        expect(accessSuffix(0, true)).toBe("Disabled");
        expect(accessSuffix(2, true)).toBe("Private");
        expect(accessSuffix(1, true)).toBe("Locked");
        expect(accessSuffix(1, false)).toBe("");
    });
});

describe("developersOf", () => {
    it("reads the developers from in_development, else the list", () => {
        expect(developersOf({ in_development: { developers: ["a"] } }))
            .toEqual(["a"]);
        expect(developersOf({ in_development: null, developers: ["b"] }))
            .toEqual(["b"]);
        expect(developersOf({ in_development: {}, developers: ["c"] }))
            .toEqual(["c"]);
        expect(developersOf({ in_development: null })).toEqual([]);
    });
});

describe("MrsTreeItem for a root", () => {
    const rootItem = (
        overrides: Parameters<typeof mrsStatus>[0] = {},
        extra: { metadataSchema?: string; showPrivate?: boolean } = {},
    ): MrsTreeItem => {
        return item({
            kind: "mrsRoot",
            uri: URI,
            status: mrsStatus(overrides),
            showPrivate: extra.showPrivate ?? false,
            ...(extra.metadataSchema === undefined
                ? {} : { metadataSchema: extra.metadataSchema }),
        });
    };

    it("is a collapsed MariaDB REST Service row", () => {
        const root = rootItem();

        expect(MRS_ROOT_LABEL).toBe("MariaDB REST Service");
        expect(root.label).toBe("MariaDB REST Service");
        expect(root.description).toBeUndefined();
        expect(root.collapsibleState).toBe(TreeItemCollapsibleState.Collapsed);
        expect(iconOf(root)).toBe("mrs.svg");
        expect(root.contextValue).toBe("mariadbMrsRoot.noPrivate");
        expect(root.tooltip).toBe(
            "MariaDB REST Service\nMetadata version 5.0.0\nEnabled");
        expect(root.id).toBe(`mrs:${URI}::root`);
    });

    it("names its metadata schema where there are several", () => {
        const root = rootItem({}, {
            metadataSchema: "dev_mariadb_rest_service", showPrivate: true,
        });

        expect(root.description).toBe("dev_mariadb_rest_service");
        expect(root.contextValue).toBe("mariadbMrsRoot.private");
        expect(root.tooltip).toContain(
            "MariaDB REST Service (dev_mariadb_rest_service)");
        expect(root.id).toBe(`mrs:${URI}:dev_mariadb_rest_service/:root`);
    });

    it("shows a disabled service", () => {
        const root = rootItem({ service_enabled: false });

        expect(iconOf(root)).toBe("mrsDisabled.svg");
        expect(root.tooltip).toMatch(/\nDisabled$/);
    });

    it("shows an update that is available and not skipped", () => {
        const root = rootItem({
            service_upgradeable: true, available_metadata_version: "5.1.0",
        });

        expect(iconOf(root)).toBe("mrsUpdateAvailable.svg");
        expect(root.tooltip).toContain(
            "Metadata version 5.0.0, 5.1.0 available");
    });

    it("leaves out an update that was skipped", () => {
        const root = rootItem({
            service_upgradeable: true, service_upgrade_ignored: true,
        });

        expect(iconOf(root)).toBe("mrs.svg");
        expect(root.tooltip).not.toContain("available");
    });

    it("prefers disabled over an update, and says - for no version", () => {
        const root = rootItem({
            service_enabled: false,
            service_upgradeable: true,
            current_metadata_version: null,
        });

        expect(iconOf(root)).toBe("mrsDisabled.svg");
        expect(root.tooltip).toContain("Metadata version -");
    });
});

describe("MrsTreeItem for a message", () => {
    it("shows the message with the info codicon, as a leaf", () => {
        const message = item({ kind: "mrsMessage", uri: URI, message: "Wait" });

        expect(message.label).toBe("Wait");
        expect(iconOf(message)).toBe("$(info)");
        expect(message.contextValue).toBe("mariadbMrsMessage");
        expect(message.collapsibleState).toBe(TreeItemCollapsibleState.None);
    });
});

describe("MrsTreeItem for a service", () => {
    it("shows an unpublished service", () => {
        const service = serviceItem({ comments: "The shop" });

        expect(service.label).toBe("/myService");
        expect(service.description).toBe("Unpublished");
        expect(iconOf(service)).toBe("mrsService.svg");
        expect(service.contextValue).toBe("mariadbMrsService.notCurrent");
        expect(service.tooltip).toBe("/myService\nThe shop");
        expect(service.id).toBe(
            `mrs:${URI}::service:11111111-0000-0000-0000-000000000001`);
        expect(service.collapsibleState)
            .toBe(TreeItemCollapsibleState.Collapsed);
    });

    it("shows a published one, as true or 1", () => {
        for (const published of [true, 1]) {
            const service = serviceItem({ published });

            expect(service.description).toBe("Published");
            expect(iconOf(service)).toBe("mrsServicePublished.svg");
        }
    });

    it("shows a disabled one, published or not", () => {
        const service = serviceItem({ enabled: 0, published: true });

        expect(service.description).toBe("Disabled");
        expect(iconOf(service)).toBe("mrsServiceDisabled.svg");
    });

    it("shows one in development with its developers", () => {
        const service = serviceItem({
            full_service_path: "anna,mike@/myService",
            in_development: { developers: ["anna", "mike"] },
            published: true,
        });

        expect(service.label).toBe("/myService");
        expect(service.description).toBe("In Development [anna,mike]");
        expect(iconOf(service)).toBe("mrsServiceInDevelopment.svg");
        expect(service.tooltip).toBe("anna,mike@/myService");
    });

    it("says disabled for a disabled one in development", () => {
        const service = serviceItem({
            enabled: 0, in_development: { developers: ["anna"] },
        });

        expect(service.description).toBe("Disabled");
        expect(iconOf(service)).toBe("mrsServiceDisabled.svg");
    });

    it("marks the current service with its default icons", () => {
        const cases: Array<[Partial<IMrsService>, string]> = [
            [{}, "mrsServiceDefault.svg"],
            [{ published: true }, "mrsServiceDefaultPublished.svg"],
            [{ enabled: 0 }, "mrsServiceDefaultDisabled.svg"],
            [{ developers: ["anna"] }, "mrsServiceDefaultInDevelopment.svg"],
        ];
        for (const [overrides, icon] of cases) {
            const service = serviceItem({ ...overrides, is_current: true });

            expect(iconOf(service)).toBe(icon);
            expect(service.contextValue).toBe("mariadbMrsService.current");
            expect(service.tooltip).toBe(
                "/myService\nThe current REST service");
        }
    });

    it("carries the metadata schema in its id", () => {
        expect(serviceItem({}, "m").id).toBe(
            `mrs:${URI}:m/:service:11111111-0000-0000-0000-000000000001`);
    });
});

describe("MrsTreeItem for a schema", () => {
    const schemaItem = (
        overrides: Parameters<typeof mrsSchema>[0] = {},
    ): MrsTreeItem => {
        return item({
            kind: "mrsSchema", uri: URI, service: mrsService(),
            schema: mrsSchema(overrides), showPrivate: false,
        });
    };

    it("shows its path and database schema", () => {
        const schema = schemaItem({ comments: "Sample" });

        expect(schema.label).toBe("/sakila (sakila)");
        expect(iconOf(schema)).toBe("mrsSchema.svg");
        expect(schema.contextValue).toBe("mariadbMrsSchema");
        expect(schema.tooltip).toBe("Sample");
        expect(schema.id).toBe(
            `mrs:${URI}::schema:22222222-0000-0000-0000-000000000001`);
    });

    it("has no tooltip without a comment", () => {
        expect(schemaItem({ comments: "" }).tooltip).toBeUndefined();
    });

    it("picks the access variant, for a script module too", () => {
        const cases: Array<[EnabledState, boolean, string]> = [
            [0, false, "Disabled"],
            [2, false, "Private"],
            [1, true, "Locked"],
            [1, false, ""],
        ];
        for (const [enabled, requiresAuth, suffix] of cases) {
            expect(iconOf(schemaItem({ enabled, requires_auth: requiresAuth })))
                .toBe(`mrsSchema${suffix}.svg`);
            expect(iconOf(schemaItem({
                enabled, requires_auth: requiresAuth,
                schema_type: "SCRIPT_MODULE",
            }))).toBe(`mrsSchemaModule${suffix}.svg`);
        }
    });
});

describe("MrsTreeItem for an object", () => {
    const objectItem = (
        overrides: Parameters<typeof mrsObject>[0] = {},
    ): MrsTreeItem => {
        return item({
            kind: "mrsObject", uri: URI, service: mrsService(),
            schema: mrsSchema(), object: mrsObject(overrides),
        });
    };

    it("shows its path, its database object and its type", () => {
        const object = objectItem({ comments: "Actors" });

        expect(object.label).toBe("/actor");
        expect(object.description).toBe("actor");
        expect(iconOf(object)).toBe("mrsDbObjectTable.svg");
        expect(object.contextValue).toBe("mariadbMrsObject.table");
        expect(object.tooltip).toBe("Actors");
        expect(object.id).toBe(
            `mrs:${URI}::object:33333333-0000-0000-0000-000000000001`);
        expect(object.collapsibleState).toBe(TreeItemCollapsibleState.None);
    });

    it("picks the icon by type and access", () => {
        expect(iconOf(objectItem({ object_type: "PROCEDURE", enabled: 0 })))
            .toBe("mrsDbObjectProcedureDisabled.svg");
        expect(iconOf(objectItem({ object_type: "SCRIPT", enabled: 2 })))
            .toBe("mrsDbObjectScriptPrivate.svg");
        expect(iconOf(objectItem({ object_type: "VIEW", requires_auth: true })))
            .toBe("mrsDbObjectViewLocked.svg");
        expect(objectItem({ object_type: "FUNCTION" }).contextValue)
            .toBe("mariadbMrsObject.function");
        expect(objectItem({ comments: null }).tooltip).toBeUndefined();
    });
});

describe("MrsTreeItem for a content set and its files", () => {
    const setItem = (
        overrides: Parameters<typeof mrsContentSet>[0] = {},
    ): MrsTreeItem => {
        return item({
            kind: "mrsContentSet", uri: URI, service: mrsService(),
            contentSet: mrsContentSet(overrides), showPrivate: false,
        });
    };

    const fileItem = (
        overrides: Parameters<typeof mrsContentFile>[0] = {},
    ): MrsTreeItem => {
        return item({
            kind: "mrsContentFile", uri: URI, service: mrsService(),
            contentSet: mrsContentSet(), file: mrsContentFile(overrides),
        });
    };

    it("shows a content set", () => {
        const set = setItem({ comments: "The app" });

        expect(set.label).toBe("/app");
        expect(iconOf(set)).toBe("mrsContentSet.svg");
        expect(set.contextValue).toBe("mariadbMrsContentSet");
        expect(set.tooltip).toBe("The app");
        expect(set.id).toBe(
            `mrs:${URI}::contentSet:44444444-0000-0000-0000-000000000001`);
        expect(set.collapsibleState).toBe(TreeItemCollapsibleState.Collapsed);
        expect(setItem().tooltip).toBeUndefined();
    });

    it("shows one with MRS scripts with their icon", () => {
        expect(iconOf(setItem({ content_type: "SCRIPTS" })))
            .toBe("mrsContentSetScripts.svg");
        expect(iconOf(setItem({ content_type: "SCRIPTS", enabled: 2 })))
            .toBe("mrsContentSetScriptsPrivate.svg");
        expect(iconOf(setItem({ requires_auth: true })))
            .toBe("mrsContentSetLocked.svg");
    });

    it("shows a file with its size and access", () => {
        const file = fileItem({ size: 2048 });

        expect(file.label).toBe("/index.html");
        expect(file.description).toBe("2.0 KB");
        expect(iconOf(file)).toBe("mrsContentFile.svg");
        expect(file.contextValue).toBe("mariadbMrsContentFile");
        expect(file.tooltip).toBe("/index.html\nAccess: ENABLED\n"
            + "Authentication: NOT REQUIRED");
        expect(file.id).toBe(
            `mrs:${URI}::contentFile:55555555-0000-0000-0000-000000000001`);
    });

    it("shows a private or disabled file", () => {
        const hidden = fileItem({ enabled: 2, requires_auth: true });
        expect(iconOf(hidden)).toBe("mrsContentFilePrivate.svg");
        expect(hidden.tooltip).toBe("/index.html\nAccess: PRIVATE\n"
            + "Authentication: REQUIRED");

        const off = fileItem({ enabled: 0 });
        expect(iconOf(off)).toBe("mrsContentFileDisabled.svg");
        expect(off.tooltip).toContain("Access: DISABLED");
    });
});

describe("MrsTreeItem for auth apps and users", () => {
    it("shows an app linked to a service", () => {
        const linked = item({
            kind: "mrsServiceAuthApp", uri: URI, service: mrsService(),
            authApp: mrsAuthApp({ description: "Built in" }),
        });

        expect(linked.label).toBe("MRS");
        expect(linked.description).toBe("MRS");
        expect(iconOf(linked)).toBe("mrsAuthAppLink.svg");
        expect(linked.contextValue).toBe("mariadbMrsServiceAuthApp");
        expect(linked.tooltip).toBe("Built in");
        expect(linked.id).toBe(`mrs:${URI}::serviceAuthApp:`
            + "11111111-0000-0000-0000-000000000001:"
            + "66666666-0000-0000-0000-000000000001");
        expect(linked.collapsibleState).toBe(TreeItemCollapsibleState.None);
    });

    it("shows a disabled linked app, with its name as the tooltip", () => {
        const linked = item({
            kind: "mrsServiceAuthApp", uri: URI, service: mrsService(),
            authApp: mrsAuthApp({ enabled: false }),
        });

        expect(iconOf(linked)).toBe("mrsAuthAppLinkDisabled.svg");
        expect(linked.tooltip).toBe("MRS");
    });

    it("shows the group of apps", () => {
        const group = item({ kind: "mrsAuthAppGroup", uri: URI });

        expect(group.label).toBe("REST Authentication Apps");
        expect(iconOf(group)).toBe("mrsAuthApps.svg");
        expect(group.contextValue).toBe("mariadbMrsAuthAppGroup");
        expect(group.id).toBe(`mrs:${URI}::authApps`);
        expect(group.collapsibleState).toBe(TreeItemCollapsibleState.Collapsed);
    });

    it("shows an app, enabled or not", () => {
        const app = item({
            kind: "mrsAuthApp", uri: URI,
            authApp: mrsAuthApp({ name: "Google", auth_vendor: "Google" }),
        });

        expect(app.label).toBe("Google");
        expect(app.description).toBe("Google");
        expect(iconOf(app)).toBe("mrsAuthApp.svg");
        expect(app.contextValue).toBe("mariadbMrsAuthApp");
        expect(app.tooltip).toBe("Google");
        expect(app.id).toBe(
            `mrs:${URI}::authApp:66666666-0000-0000-0000-000000000001`);
        expect(iconOf(item({
            kind: "mrsAuthApp", uri: URI,
            authApp: mrsAuthApp({ enabled: false }),
        }))).toBe("mrsAuthAppDisabled.svg");
    });

    it("shows a user by name, else email, and whether it is locked", () => {
        const user = item({
            kind: "mrsUser", uri: URI, authApp: mrsAuthApp(),
            user: mrsUser({ email: "anna@example.com" }),
        });

        expect(user.label).toBe("anna");
        expect(user.description).toBeUndefined();
        expect(iconOf(user)).toBe("mrsUser.svg");
        expect(user.contextValue).toBe("mariadbMrsUser");
        expect(user.tooltip).toBe("anna\nanna@example.com");
        expect(user.id).toBe(
            `mrs:${URI}::user:77777777-0000-0000-0000-000000000001`);

        const locked = item({
            kind: "mrsUser", uri: URI, authApp: mrsAuthApp(),
            user: mrsUser({
                name: null, email: "x@example.com", login_permitted: false,
            }),
        });
        expect(locked.label).toBe("x@example.com");
        expect(locked.description).toBe("Locked");

        const nobody = item({
            kind: "mrsUser", uri: URI, authApp: mrsAuthApp(),
            user: mrsUser({ name: null, email: null }),
        });
        expect(nobody.label).toBe("<unknown>");
        expect(nobody.tooltip).toBeUndefined();
    });
});

describe("MrsTreeItem for REST Daemons", () => {
    it("shows the group", () => {
        const group = item({
            kind: "mrsDaemonGroup", uri: URI, requiredVersion: "26.10.0",
        });

        expect(group.label).toBe("REST Daemons");
        expect(iconOf(group)).toBe("restDaemons.svg");
        expect(group.contextValue).toBe("mariadbMrsDaemonGroup");
        expect(group.id).toBe(`mrs:${URI}::daemons`);
    });

    it("shows an active daemon by address with its version", () => {
        const daemon = item({
            kind: "mrsDaemon", uri: URI, daemon: mrsDaemon(),
            requiresUpgrade: false,
        });

        expect(daemon.label).toBe("host1:8443");
        expect(daemon.description).toBe("26.10.0");
        expect(iconOf(daemon)).toBe("restDaemon.svg");
        expect(daemon.contextValue).toBe("mariadbMrsDaemon");
        expect(daemon.tooltip)
            .toBe("MariaDB REST Daemon 26.10.0 - host1:8443");
        expect(daemon.id).toBe(
            `mrs:${URI}::daemon:88888888-0000-0000-0000-000000000001`);
        expect(daemon.collapsibleState)
            .toBe(TreeItemCollapsibleState.Collapsed);
    });

    it("shows an inactive developer daemon without an address", () => {
        const daemon = item({
            kind: "mrsDaemon", uri: URI,
            daemon: mrsDaemon({
                address: "", active: false, developer: "anna",
            }),
            requiresUpgrade: false,
        });

        expect(daemon.label).toBe("daemon1");
        expect(daemon.description).toBe("[anna] 26.10.0");
        expect(iconOf(daemon)).toBe("restDaemonNotActive.svg");
        expect(daemon.tooltip).toContain("(not active)");
    });

    it("shows one that needs an upgrade", () => {
        const daemon = item({
            kind: "mrsDaemon", uri: URI, daemon: mrsDaemon(),
            requiresUpgrade: true,
        });

        expect(iconOf(daemon)).toBe("restDaemonError.svg");
        expect(daemon.tooltip)
            .toBe("This MariaDB REST Daemon requires an upgrade.");
    });

    it("shows the services a daemon serves", () => {
        const daemonService = (overrides: Partial<IMrsService>) => {
            return item({
                kind: "mrsDaemonService", uri: URI, daemon: mrsDaemon(),
                service: mrsService(overrides),
            });
        };

        const plain = daemonService({});
        expect(plain.label).toBe("/myService");
        expect(plain.description).toBe("Unpublished");
        expect(iconOf(plain)).toBe("mrsServiceLink.svg");
        expect(plain.contextValue).toBe("mariadbMrsDaemonService");
        expect(plain.id).toBe(`mrs:${URI}::daemonService:`
            + "88888888-0000-0000-0000-000000000001:"
            + "11111111-0000-0000-0000-000000000001");
        expect(plain.collapsibleState).toBe(TreeItemCollapsibleState.None);

        const published = daemonService({ published: 1 });
        expect(published.description).toBe("Published");
        expect(iconOf(published)).toBe("mrsServicePublished.svg");

        const developed = daemonService({
            full_service_path: "anna@/myService", developers: ["anna"],
        });
        expect(developed.label).toBe("anna@/myService");
        expect(developed.description).toBe("In Development");
        expect(iconOf(developed)).toBe("mrsServiceInDevelopment.svg");
    });
});

describe("the REST Service icons", () => {
    /**
     * @returns An item for every variant a row can be drawn with.
     */
    const everyVariant = (): MrsTreeItem[] => {
        const items: MrsTreeItem[] = [];
        for (const status of [
            {}, { service_enabled: false }, { service_upgradeable: true },
        ]) {
            items.push(item({
                kind: "mrsRoot", uri: URI, status: mrsStatus(status),
                showPrivate: false,
            }));
        }
        const services: Array<Partial<IMrsService>> = [
            {}, { enabled: 0 }, { published: true }, { developers: ["a"] },
        ];
        for (const overrides of services) {
            for (const isCurrent of [false, true]) {
                items.push(serviceItem({
                    ...overrides, is_current: isCurrent,
                }));
            }
            items.push(item({
                kind: "mrsDaemonService", uri: URI, daemon: mrsDaemon(),
                service: mrsService(overrides),
            }));
        }
        const access: Array<[EnabledState, boolean]> = [
            [0, false], [1, false], [1, true], [2, false],
        ];
        for (const [enabled, requiresAuth] of access) {
            for (const schemaType of ["DATABASE_SCHEMA", "SCRIPT_MODULE"]) {
                items.push(item({
                    kind: "mrsSchema", uri: URI, service: mrsService(),
                    schema: mrsSchema({
                        enabled, requires_auth: requiresAuth,
                        schema_type: schemaType,
                    }),
                    showPrivate: false,
                }));
            }
            for (const objectType of ["TABLE", "VIEW", "PROCEDURE",
                "FUNCTION", "SCRIPT"] as MrsObjectType[]) {
                items.push(item({
                    kind: "mrsObject", uri: URI, service: mrsService(),
                    schema: mrsSchema(),
                    object: mrsObject({
                        enabled, requires_auth: requiresAuth,
                        object_type: objectType,
                    }),
                }));
            }
            for (const contentType of ["STATIC", "SCRIPTS"]) {
                items.push(item({
                    kind: "mrsContentSet", uri: URI, service: mrsService(),
                    contentSet: mrsContentSet({
                        enabled, requires_auth: requiresAuth,
                        content_type: contentType,
                    }),
                    showPrivate: false,
                }));
            }
            items.push(item({
                kind: "mrsContentFile", uri: URI, service: mrsService(),
                contentSet: mrsContentSet(),
                file: mrsContentFile({ enabled, requires_auth: requiresAuth }),
            }));
        }
        for (const enabled of [true, false]) {
            items.push(item({
                kind: "mrsServiceAuthApp", uri: URI, service: mrsService(),
                authApp: mrsAuthApp({ enabled }),
            }));
            items.push(item({
                kind: "mrsAuthApp", uri: URI, authApp: mrsAuthApp({ enabled }),
            }));
        }
        items.push(item({ kind: "mrsAuthAppGroup", uri: URI }));
        items.push(item({
            kind: "mrsUser", uri: URI, authApp: mrsAuthApp(), user: mrsUser(),
        }));
        items.push(item({
            kind: "mrsDaemonGroup", uri: URI, requiredVersion: "1",
        }));
        for (const [active, requiresUpgrade] of [
            [true, false], [false, false], [true, true],
        ]) {
            items.push(item({
                kind: "mrsDaemon", uri: URI, daemon: mrsDaemon({ active }),
                requiresUpgrade,
            }));
        }

        return items;
    };

    it("ships every icon a row can be drawn with, in both themes", () => {
        const icons = new Set(everyVariant().map(iconOf));

        // Every variant above, each drawn differently.
        expect(icons.size).toBeGreaterThan(60);
        for (const icon of icons) {
            for (const theme of ["light", "dark"]) {
                expect(existsSync(join(EXTENSION_ROOT, "images", theme, icon)),
                    `${theme}/${icon}`).toBe(true);
            }
        }
    });
});
