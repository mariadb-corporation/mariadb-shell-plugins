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

import { splitStatements } from "../../sql/splitStatements.js";

/**
 * @param script The script to split.
 *
 * @returns Just the statement texts, which is what most cases care about.
 */
const texts = (script: string): string[] => {
    return splitStatements(script).map((statement) => {
        return statement.text;
    });
};

/**
 * @param script The script to split.
 *
 * @returns Which of the statements the server actually runs.
 */
const executable = (script: string): boolean[] => {
    return splitStatements(script).map((statement) => {
        return statement.executable;
    });
};

describe("splitStatements", () => {
    it("splits on semicolons", () => {
        expect(texts("SELECT 1; SELECT 2;"))
            .toEqual(["SELECT 1", "SELECT 2"]);
    });

    it("keeps a trailing statement without a semicolon", () => {
        expect(texts("SELECT 1;\nSELECT 2"))
            .toEqual(["SELECT 1", "SELECT 2"]);
    });

    it("drops empty statements, as the server does", () => {
        expect(texts(";;\nSELECT 1;;\n\n;")).toEqual(["SELECT 1"]);
    });

    it("returns nothing for a blank script", () => {
        expect(texts("   \n\n  ")).toEqual([]);
    });

    it("ignores a semicolon inside a string", () => {
        expect(texts("SELECT 'a;b'; SELECT 2;"))
            .toEqual(["SELECT 'a;b'", "SELECT 2"]);
    });

    it("ignores a semicolon inside a double quoted string", () => {
        expect(texts('SELECT "a;b";')).toEqual(['SELECT "a;b"']);
    });

    it("ignores a semicolon inside a quoted identifier", () => {
        expect(texts("SELECT `we;ird` FROM t;"))
            .toEqual(["SELECT `we;ird` FROM t"]);
    });

    it("handles a backslash escaped quote", () => {
        expect(texts("SELECT 'it\\'s; here'; SELECT 2;"))
            .toEqual(["SELECT 'it\\'s; here'", "SELECT 2"]);
    });

    it("handles a doubled quote", () => {
        expect(texts("SELECT 'it''s; here';"))
            .toEqual(["SELECT 'it''s; here'"]);
    });

    it("ignores a semicolon in a line comment", () => {
        expect(texts("SELECT 1 -- ; not an end\n;SELECT 2;"))
            .toEqual(["SELECT 1 -- ; not an end", "SELECT 2"]);
    });

    it("ignores a semicolon in a hash comment", () => {
        expect(texts("SELECT 1 # ; nope\n; SELECT 2;"))
            .toEqual(["SELECT 1 # ; nope", "SELECT 2"]);
    });

    it("ignores a semicolon in a block comment", () => {
        expect(texts("SELECT /* a; b */ 1; SELECT 2;"))
            .toEqual(["SELECT /* a; b */ 1", "SELECT 2"]);
    });

    /*
     * The server makes a line comment that opens a statement a statement
     * of its own. These expectations were read off the running server
     * with db.execute_sql_script and counted: getting them wrong shifts
     * every result after the comment by one.
     */
    describe("a line comment that opens a statement", () => {
        it("stands on its own", () => {
            expect(texts("-- a note\nSELECT 1;"))
                .toEqual(["-- a note", "SELECT 1"]);
        });

        it("stands on its own across a blank line", () => {
            expect(texts("-- a note\n\nSELECT 1;"))
                .toEqual(["-- a note", "SELECT 1"]);
        });

        it("stands on its own when indented", () => {
            expect(texts("   -- a note\nSELECT 1;"))
                .toEqual(["-- a note", "SELECT 1"]);
        });

        it("counts once per comment line", () => {
            expect(texts("-- a\n-- b\nSELECT 1;"))
                .toEqual(["-- a", "-- b", "SELECT 1"]);
        });

        it("applies to a hash comment too", () => {
            expect(texts("# a note\nSELECT 1;"))
                .toEqual(["# a note", "SELECT 1"]);
        });

        it("applies after a semicolon", () => {
            expect(texts("SELECT 1; -- a note\nSELECT 2;"))
                .toEqual(["SELECT 1", "-- a note", "SELECT 2"]);
        });

        it("applies between two statements", () => {
            expect(texts("SELECT 1;\n-- mid\nSELECT 2;"))
                .toEqual(["SELECT 1", "-- mid", "SELECT 2"]);
        });

        it("is the whole script when that is all there is", () => {
            expect(texts("-- just a note")).toEqual(["-- just a note"]);
        });

        it("leaves a comment inside a statement inside it", () => {
            expect(texts("SELECT 1 -- a note\nAS a;"))
                .toEqual(["SELECT 1 -- a note\nAS a"]);
        });

        it("does not apply to a block comment", () => {
            // The server keeps a block comment with what follows it.
            expect(texts("/* a note */ SELECT 1;"))
                .toEqual(["/* a note */ SELECT 1"]);
            expect(texts("/* a note */\nSELECT 1;"))
                .toEqual(["/* a note */\nSELECT 1"]);
        });

        it("pairs the header of a generated file with its own result",
            () => {
                // What the Connections view's New SQL Editor button
                // writes, followed by a query.
                expect(texts(
                    "-- MariaDB connection: dba@localhost:3310\n\n"
                    + "SELECT ID FROM world.city;",
                )).toEqual([
                    "-- MariaDB connection: dba@localhost:3310",
                    "SELECT ID FROM world.city",
                ]);
            });
    });

    it("does not treat a bare -- as a comment", () => {
        // `a--b` is a subtraction of a negative, not a comment.
        expect(texts("SELECT 1--2;")).toEqual(["SELECT 1--2"]);
    });

    it("honours a DELIMITER change", () => {
        const script = [
            "DELIMITER //",
            "CREATE PROCEDURE p()",
            "BEGIN",
            "  SELECT 1;",
            "  SELECT 2;",
            "END//",
            "DELIMITER ;",
            "CALL p();",
        ].join("\n");

        expect(texts(script)).toEqual([
            "CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\n  SELECT 2;\nEND",
            "CALL p()",
        ]);
    });

    describe("what the server runs", () => {
        it("marks a comment as a statement it does not run", () => {
            // It is still a statement there, and still takes up an index:
            // the server numbers what it split, not what it ran, so
            // leaving the comment out here would pair the query's result
            // with the comment.
            expect(executable(
                "-- MariaDB connection: dba@localhost:3310\n\nSELECT 1;",
            )).toEqual([false, true]);
        });

        it("marks a # comment the same way", () => {
            expect(executable("# a note\nSELECT 1;")).toEqual([false, true]);
        });

        it("marks a comment between two statements", () => {
            expect(executable("SELECT 1;\n-- mid\nSELECT 2;"))
                .toEqual([true, false, true]);
        });

        it("runs nothing in a script of only comments", () => {
            // One statement per comment line, as the shell splits it -
            // checked against the real splitter, which returns
            // ["-- one", "-- two"] for this.
            expect(texts("-- one\n-- two")).toEqual(["-- one", "-- two"]);
            expect(executable("-- one\n-- two")).toEqual([false, false]);
        });

        it("runs nothing in a comment followed by a bare delimiter", () => {
            // The delimiter closes a statement that holds only a comment,
            // and the shell returns just the comment for it.
            expect(texts("-- note\n;")).toEqual(["-- note"]);
            expect(executable("-- note\n;")).toEqual([false]);
        });

        it("runs a statement that merely contains a comment", () => {
            // The comment is inside it, not in front of it, so the
            // splitter never separates the two.
            expect(executable("SELECT 1 -- a note\nAS a;")).toEqual([true]);
        });

        it("runs a block comment, which may carry SQL", () => {
            // `/*!...*/` and `/*+...*/` are not comments to the server,
            // and neither side splits any block comment out.
            expect(executable("/*!40101 SET NAMES utf8 */;")).toEqual([true]);
            expect(executable("/* a note */ SELECT 1;")).toEqual([true]);
        });
    });

    it("numbers the statements in order", () => {
        expect(splitStatements("SELECT 1; SELECT 2; SELECT 3;")
            .map((statement) => {
                return statement.index;
            })).toEqual([0, 1, 2]);
    });

    it("reports the line each statement starts on", () => {
        const script = "SELECT 1;\n\nSELECT 2;\nSELECT 3;";

        expect(splitStatements(script).map((statement) => {
            return statement.line;
        })).toEqual([1, 3, 4]);
    });

    it("survives an unterminated string", () => {
        expect(texts("SELECT 'oops")).toEqual(["SELECT 'oops"]);
    });

    it("survives an unterminated block comment", () => {
        expect(texts("SELECT 1 /* oops")).toEqual(["SELECT 1 /* oops"]);
    });
});
