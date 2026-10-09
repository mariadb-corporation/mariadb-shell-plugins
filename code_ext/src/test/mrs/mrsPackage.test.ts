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

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/** The extension's own folder, where `package.json` is. */
const EXTENSION_ROOT = join(
    dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

interface IMenuEntry {
    command?: string;
    submenu?: string;
    when?: string;
    group?: string;
}

interface ICommand {
    command: string;
    title: string;
    category?: string;
    icon?: string | { light: string; dark: string };
}

const manifest = JSON.parse(readFileSync(
    join(EXTENSION_ROOT, "package.json"), "utf8")) as {
    contributes: {
        commands: ICommand[];
        menus: Record<string, IMenuEntry[]>;
        submenus: Array<{ id: string; label: string }>;
        configuration: { properties: Record<string, { default?: unknown }> };
    };
};
const { commands, menus, submenus } = manifest.contributes;

const MRS = "mariadb.mrs.";

const mrsCommands = commands.filter((entry) => {
    return entry.command.startsWith(MRS);
});

/** Every menu entry, with the menu it is in. */
const menuEntries = Object.entries(menus).flatMap(([menu, entries]) => {
    return entries.map((entry) => { return { menu, ...entry }; });
});

/** The submenus the REST Service rows and the Explorer use. */
const MRS_SUBMENUS = [
    "mariadb.mrs.copyToClipboard",
    "mariadb.mrs.dumpToDisk",
    "mariadb.mrs.loadFromDisk",
    "mariadb.mrs.explorer",
];

/**
 * Every context value a REST Service row can carry (see
 * `mrsTreeItems.ts`).
 */
const CONTEXT_VALUES = [
    "mariadbMrsRoot.private", "mariadbMrsRoot.noPrivate", "mariadbMrsMessage",
    "mariadbMrsService.current", "mariadbMrsService.notCurrent",
    "mariadbMrsSchema", "mariadbMrsObject.table", "mariadbMrsObject.view",
    "mariadbMrsObject.procedure", "mariadbMrsObject.function",
    "mariadbMrsObject.script", "mariadbMrsContentSet", "mariadbMrsContentFile",
    "mariadbMrsServiceAuthApp", "mariadbMrsAuthAppGroup", "mariadbMrsAuthApp",
    "mariadbMrsUser", "mariadbMrsDaemonGroup", "mariadbMrsDaemon",
    "mariadbMrsDaemonService",
];

/**
 * @param when A `when` clause.
 *
 * @returns The context values its `viewItem` tests match, or undefined
 *          where it tests none.
 */
const matchedContextValues = (when: string): string[] | undefined => {
    const tests = [...when.matchAll(
        /viewItem\s*(==|=~)\s*(\/(?:\\.|[^/])+\/|[\w.]+)/g)];
    if (tests.length === 0) {
        return undefined;
    }

    return CONTEXT_VALUES.filter((value) => {
        return tests.some(([, operator, operand]) => {
            return operator === "=="
                ? operand === value
                : new RegExp(operand!.slice(1, -1)).test(value);
        });
    });
};

describe("the REST Service contributions", () => {
    it("contributes the REST Service commands, each once", () => {
        const ids = mrsCommands.map((entry) => { return entry.command; });

        expect(ids.length).toBeGreaterThan(40);
        expect(new Set(ids).size).toBe(ids.length);
        for (const entry of mrsCommands) {
            expect(entry.title, entry.command).not.toBe("");
            expect(entry.category, entry.command).toBe("MariaDB");
        }
    });

    it("declares every REST Service submenu it uses", () => {
        const declared = submenus.map((submenu) => { return submenu.id; });

        expect(declared).toEqual(expect.arrayContaining(MRS_SUBMENUS));
        for (const entry of menuEntries) {
            if (entry.submenu !== undefined) {
                expect(declared, entry.submenu).toContain(entry.submenu);
            }
        }
        for (const id of MRS_SUBMENUS) {
            expect(menus[id]?.length ?? 0, id).toBeGreaterThan(0);
            // Each is reachable: some menu shows it.
            expect(menuEntries.some((entry) => {
                return entry.submenu === id;
            }), id).toBe(true);
        }
    });

    it("puts only contributed commands in its menus", () => {
        const contributed = new Set(commands.map((entry) => {
            return entry.command;
        }));

        for (const entry of menuEntries) {
            if (entry.command?.startsWith(MRS)) {
                expect(contributed.has(entry.command),
                    `${entry.menu}: ${entry.command}`).toBe(true);
            }
        }
    });

    it("shows every command in a menu or hides it from the palette", () => {
        const palette = new Map((menus.commandPalette ?? []).map((entry) => {
            return [entry.command, entry.when];
        }));

        for (const { command } of mrsCommands) {
            const inMenu = menuEntries.some((entry) => {
                return entry.menu !== "commandPalette"
                    && entry.command === command;
            });
            const hidden = palette.get(command) === "false";

            expect(inMenu || hidden, command).toBe(true);
        }
    });

    it("hides the commands that need a row from the palette", () => {
        const palette = new Map((menus.commandPalette ?? []).map((entry) => {
            return [entry.command, entry.when];
        }));

        // Every one of them takes the row it was run on; from the palette
        // it would get none and do nothing. Only the documentation stands
        // on its own.
        for (const { command } of mrsCommands) {
            if (command !== "mariadb.mrs.docs") {
                expect(palette.get(command), command).toBe("false");
            }
        }
    });

    it("ships every icon a REST Service command names", () => {
        for (const { command, icon } of mrsCommands) {
            if (icon === undefined) {
                continue;
            }
            if (typeof icon === "string") {
                expect(icon, command).toMatch(/^\$\([\w-]+\)$/);
                continue;
            }
            for (const path of [icon.light, icon.dark]) {
                expect(existsSync(join(EXTENSION_ROOT, path)),
                    `${command}: ${path}`).toBe(true);
            }
        }
    });

    it("offers each row's menu entries on rows that exist", () => {
        for (const entry of menuEntries) {
            const mrsEntry = entry.command?.startsWith(MRS)
                || entry.submenu?.startsWith(MRS);
            if (!mrsEntry || entry.when === undefined
                || !entry.when.includes("mariadbMrs")) {
                continue;
            }
            const matched = matchedContextValues(entry.when);

            expect(matched?.length ?? 0,
                `${entry.menu}: ${entry.command ?? entry.submenu} `
                + `(${entry.when})`).toBeGreaterThan(0);
        }
    });

    it("offers the delete of each row kind on that row only", () => {
        const deletes: Record<string, string[]> = {
            "mariadb.mrs.deleteService": ["mariadbMrsService.current",
                "mariadbMrsService.notCurrent"],
            "mariadb.mrs.deleteSchema": ["mariadbMrsSchema"],
            "mariadb.mrs.deleteContentSet": ["mariadbMrsContentSet"],
            "mariadb.mrs.deleteContentFile": ["mariadbMrsContentFile"],
            "mariadb.mrs.deleteAuthApp": ["mariadbMrsAuthApp"],
            "mariadb.mrs.deleteUser": ["mariadbMrsUser"],
            "mariadb.mrs.deleteDaemon": ["mariadbMrsDaemon"],
        };

        for (const [command, values] of Object.entries(deletes)) {
            const entries = menus["view/item/context"]!.filter((entry) => {
                return entry.command === command;
            });

            expect(entries.length, command).toBeGreaterThan(0);
            for (const entry of entries) {
                expect(matchedContextValues(entry.when ?? "")?.sort(), command)
                    .toEqual([...values].sort());
            }
        }
    });

    it("has a setting for where the REST Daemon serves", () => {
        expect(manifest.contributes.configuration.properties[
            "mariadb.mrs.restDaemonUrl"]?.default)
            .toBe("https://localhost:8443");
    });
});
