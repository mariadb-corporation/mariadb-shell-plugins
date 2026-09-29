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

import { describe, expect, it } from "vitest";

import {
    COLLAPSED_FOLDERS_KEY,
    CUSTOM_FOLDERS_KEY,
    FolderSet,
    isWithin,
    rebase,
    type IFolderMemento,
} from "../../tree/customFolders.js";

/**
 * @returns A memento over a plain map, and the map.
 */
const createMemento = (): { memento: IFolderMemento; values: Map<string, unknown> } => {
    const values = new Map<string, unknown>();

    return {
        values,
        memento: {
            get: <T>(key: string, defaultValue: T): T => {
                return (values.has(key) ? values.get(key) : defaultValue) as T;
            },
            update: (key: string, value: unknown) => {
                values.set(key, value);

                return Promise.resolve();
            },
        },
    };
};

describe("FolderSet", () => {
    it("keeps each folder once, normalized and sorted, in the memento",
        async () => {
            const { memento, values } = createMemento();
            const folders = new FolderSet(memento);

            await folders.add("Work/");
            await folders.add("/Archive");
            await folders.add(" /Work ");
            await folders.add("/");

            expect(folders.list()).toEqual(["/Archive", "/Work"]);
            expect(values.get(CUSTOM_FOLDERS_KEY)).toEqual(["/Archive", "/Work"]);
        });

    it("removes a folder with everything inside it, and nothing else",
        async () => {
            const folders = new FolderSet();
            for (const path of ["/A", "/A/B", "/A/B/C", "/AB", "/Z"]) {
                await folders.add(path);
            }

            await folders.remove("/A");

            expect(folders.list()).toEqual(["/AB", "/Z"]);
        });

    it("tells a folder inside another from one that only shares a prefix",
        () => {
            expect(isWithin("/A/B", "/A")).toBe(true);
            expect(isWithin("/A", "/A")).toBe(true);
            expect(isWithin("/AB", "/A")).toBe(false);
            expect(isWithin("/A", "/A/B")).toBe(false);
        });
});

describe("moving folders", () => {
    it("moves a folder with everything inside it, keeping their places",
        async () => {
            const folders = new FolderSet();
            for (const path of ["/A", "/A/B", "/A/B/C", "/AB", "/Z"]) {
                await folders.add(path);
            }

            await folders.move("/A/B", "/Z/B");

            expect(folders.list()).toEqual(["/A", "/AB", "/Z", "/Z/B", "/Z/B/C"]);
        });

    it("rebases a path from one folder into another", () => {
        expect(rebase("/A/B/C", "/A", "/X/A")).toBe("/X/A/B/C");
        expect(rebase("/A", "/A", "/A2")).toBe("/A2");
        expect(rebase("/A/B", "/A/B", "/")).toBe("/");
    });
});

describe("FolderSet membership", () => {
    it("deletes one folder, leaving those inside it", async () => {
        const folders = new FolderSet();
        await folders.add("/A");
        await folders.add("/A/B");

        expect(folders.has("A/")).toBe(true);
        await expect(folders.delete("/A")).resolves.toBe(true);
        await expect(folders.delete("/A")).resolves.toBe(false);
        expect(folders.list()).toEqual(["/A/B"]);
    });

    it("keeps each set under its own key", async () => {
        const { memento, values } = createMemento();
        const created = new FolderSet(memento);
        const collapsed = new FolderSet(memento, COLLAPSED_FOLDERS_KEY);

        await created.add("/Made");
        await collapsed.add("/Closed");

        expect(values.get(CUSTOM_FOLDERS_KEY)).toEqual(["/Made"]);
        expect(values.get(COLLAPSED_FOLDERS_KEY)).toEqual(["/Closed"]);
        expect(created.has("/Closed")).toBe(false);
    });
});
