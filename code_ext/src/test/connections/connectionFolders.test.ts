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
    CONNECTION_KEY_BUDGET,
    allFolders,
    commonFolder,
    connectionKeyBytes,
    connectionKeyProblem,
    filingProblem,
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

    describe("the stored key's length", () => {
        /** A URI exactly `length` bytes long. */
        const uriOf = (length: number): string => {
            const base = "mariadb://dba@localhost:3306/";

            return base + "s".repeat(length - base.length);
        };

        it("leaves the folder and URI 247 bytes, as the server does", () => {
            // 256 for the key, less the 9 of MCP:CONN:.
            expect(CONNECTION_KEY_BUDGET).toBe(247);
        });

        it("counts the folder and its ':', and nothing for the top", () => {
            expect(connectionKeyBytes(uriOf(50), "/")).toBe(50);
            expect(connectionKeyBytes(uriOf(50), "Work/")).toBe(56);
        });

        it("counts bytes, so a non-ASCII name costs more", () => {
            expect(connectionKeyBytes(uriOf(50), "/Wörk")).toBe(57);
        });

        it("refuses a key one byte over, saying where and by how much",
            () => {
                expect(connectionKeyProblem(uriOf(247), "/")).toBeUndefined();
                expect(connectionKeyProblem(uriOf(248), "/"))
                    .toContain("at the top level: its folder and URI may "
                        + "take at most 247 bytes together, and they take 248");
                expect(connectionKeyProblem(uriOf(242), "/Work"))
                    .toContain("in the folder '/Work'");
            });

        it("refuses a folder that leaves no room for any URI", () => {
            expect(folderProblem(`/${"f".repeat(244)}`)).toBeUndefined();
            expect(folderProblem(`/${"f".repeat(245)}`))
                .toContain("leaves no room for a URI");
        });

        it("names every connection a move would not fit, or the one", () => {
            const folder = `/${"f".repeat(100)}`;
            const fits = { uri: uriOf(50), path: folder };
            const over = { uri: uriOf(150), path: folder };

            expect(filingProblem([fits, fits])).toBeUndefined();
            expect(filingProblem([fits, over]))
                .toBe(connectionKeyProblem(over.uri, over.path));
            const both = filingProblem([over, fits, over]);
            expect(both).toContain("2 connections would not fit");
            expect(both).toContain("(252 bytes)");
        });
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
