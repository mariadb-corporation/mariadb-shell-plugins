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

import { statementAtOffset } from "../../sql/statementAtOffset.js";

/**
 * @param script The text to look in.
 * @param offset The cursor's offset.
 *
 * @returns The statement's text, or undefined.
 */
const textAt = (script: string, offset: number): string | undefined => {
    return statementAtOffset(script, offset)?.text;
};

describe("statementAtOffset", () => {
    const script = "SELECT 1;\nSELECT 2;\nSELECT 3;";

    it("finds the statement the cursor is inside", () => {
        expect(textAt(script, 0)).toBe("SELECT 1");
        expect(textAt(script, 4)).toBe("SELECT 1");
        expect(textAt(script, 12)).toBe("SELECT 2");
        expect(textAt(script, 25)).toBe("SELECT 3");
    });

    it("counts the position just after a statement as inside it", () => {
        // Where the caret sits once the statement has been typed.
        expect(textAt(script, 8)).toBe("SELECT 1");
    });

    it("takes the next statement when the cursor is between two", () => {
        // On the newline after the first statement's delimiter: the
        // caret is heading for the second one.
        expect(textAt(script, 10)).toBe("SELECT 2");
    });

    it("takes the last statement when the cursor is past the end", () => {
        expect(textAt(script, script.length)).toBe("SELECT 3");
        expect(textAt(`${script}\n\n\n`, script.length + 2))
            .toBe("SELECT 3");
    });

    it("leaves the delimiter out", () => {
        expect(textAt("SELECT 1;", 2)).toBe("SELECT 1");
    });

    it("keeps a multi line statement whole", () => {
        const multi = "SELECT\n  1,\n  2;\nSELECT 3;";

        expect(textAt(multi, 9)).toBe("SELECT\n  1,\n  2");
    });

    it("skips leading comments", () => {
        expect(textAt("-- a note\nSELECT 1;", 14)).toBe("SELECT 1");
    });

    it("takes the statement below a comment the cursor is on", () => {
        expect(textAt("-- a note\nSELECT 1;", 2)).toBe("SELECT 1");
    });

    it("finds nothing in a file of only comments", () => {
        expect(statementAtOffset("-- a note\n", 2)).toBeUndefined();
    });

    it("finds nothing in an empty file", () => {
        expect(statementAtOffset("", 0)).toBeUndefined();
        expect(statementAtOffset("   \n  ", 3)).toBeUndefined();
    });

    it("skips a DELIMITER command", () => {
        const procedure = [
            "DELIMITER //",
            "CREATE PROCEDURE p()",
            "BEGIN",
            "  SELECT 1;",
            "END//",
            "DELIMITER ;",
            "CALL p();",
        ].join("\n");

        // On the DELIMITER line there is nothing to run, so the
        // procedure below it is taken.
        expect(textAt(procedure, 3))
            .toBe("CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND");
        // Inside the body, the whole procedure is the statement.
        expect(textAt(procedure, 45))
            .toBe("CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\nEND");
        expect(textAt(procedure, procedure.length - 2)).toBe("CALL p()");
    });

    it("reports where the statement is, so it can be selected", () => {
        const range = statementAtOffset(script, 12);

        expect(script.slice(range!.start, range!.end)).toBe("SELECT 2");
    });

    it("does not run a semicolon inside a string as two statements", () => {
        const tricky = "SELECT 'a;b' AS x;\nSELECT 2;";

        expect(textAt(tricky, 8)).toBe("SELECT 'a;b' AS x");
    });
});
