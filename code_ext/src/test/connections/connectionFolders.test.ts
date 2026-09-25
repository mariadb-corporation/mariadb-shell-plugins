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
    allFolders,
    commonFolder,
    folderProblem,
    normalizeFolder,
} from "../../connections/connectionFolders.js";

describe("connection folders", () => {
    it("spells a folder one way", () => {
        expect(normalizeFolder("")).toBe("/");
        expect(normalizeFolder(" Sandboxes// note app /")).toBe(
            "/Sandboxes/note app");
    });

    it("refuses a colon in a folder name", () => {
        expect(folderProblem("/a/b:c")).toContain("'b:c' contains a ':'");
        expect(folderProblem("/a/b")).toBeUndefined();
    });

    it("lists every folder the paths imply, parents included", () => {
        expect(allFolders(["/A/B", "/", "/C"])).toEqual(["/A", "/A/B", "/C"]);
    });

    it("finds the deepest folder a set of folders shares", () => {
        expect(commonFolder(["/A/B", "/A/B"])).toBe("/A/B");
        expect(commonFolder(["/A/B/C", "/A/B", "/A/B/D"])).toBe("/A/B");
        expect(commonFolder(["/A/B", "/A/BC"])).toBe("/A");
        expect(commonFolder(["/A", "/B"])).toBe("/");
        expect(commonFolder(["/A", "/"])).toBe("/");
        expect(commonFolder([])).toBe("/");
    });
});
