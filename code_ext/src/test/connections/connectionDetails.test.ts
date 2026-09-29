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
    captionProblem,
    connectionKeyProblem,
    isConnectionColor,
    normalizeCaption,
} from "../../connections/connectionDetails.js";

describe("connection details", () => {
    describe("the stored key's length", () => {
        /** A URI exactly `length` bytes long. */
        const uriOf = (length: number): string => {
            const base = "mariadb://dba@localhost:3306/";

            return base + "s".repeat(length - base.length);
        };

        it("leaves the URI 247 bytes, as the server does", () => {
            // 256 for the key, less the 9 of MCP:CONN:.
            expect(CONNECTION_KEY_BUDGET).toBe(247);
        });

        it("refuses a URI one byte over, saying by how much", () => {
            expect(connectionKeyProblem(uriOf(247))).toBeUndefined();
            expect(connectionKeyProblem(uriOf(248)))
                .toContain("its URI may take at most 247 bytes, and it "
                    + "takes 248");
        });

        it("counts bytes, so a non-ASCII URI costs more", () => {
            expect(connectionKeyProblem(`${uriOf(245)}ä`)).toBeUndefined();
            expect(connectionKeyProblem(`${uriOf(246)}ä`))
                .toContain("takes 248");
        });
    });

    it("keeps a caption to one line of at most 100 characters", () => {
        expect(normalizeCaption("  Notes ")).toBe("Notes");
        expect(captionProblem("")).toBeUndefined();
        expect(captionProblem(` ${"c".repeat(100)} `)).toBeUndefined();
        expect(captionProblem("c".repeat(101)))
            .toContain("at most 100 characters");
        expect(captionProblem("two\nlines")).toContain("single line");
    });

    it("knows the colors the server stores", () => {
        expect(isConnectionColor("green")).toBe(true);
        expect(isConnectionColor("Green")).toBe(false);
        expect(isConnectionColor("#00ff00")).toBe(false);
        expect(isConnectionColor(undefined)).toBe(false);
    });
});
