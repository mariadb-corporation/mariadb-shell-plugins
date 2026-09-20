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
    determineStatementRanges,
    hasContent,
    scanStatements,
    StatementFinishState,
    type IStatementSpan,
} from "../../sql/statementSpans.js";

/**
 * @param sql The script to scan.
 *
 * @returns The text of each span, as the offsets describe it.
 */
const spanTexts = (sql: string): string[] => {
    return determineStatementRanges(sql).map((span) => {
        return sql.substr(span.span.start, span.span.length);
    });
};

/**
 * @param sql The script to scan.
 *
 * @returns The content of each span, or undefined where it has none.
 */
const contentTexts = (sql: string): Array<string | undefined> => {
    return determineStatementRanges(sql).map((span) => {
        return hasContent(span)
            ? sql.slice(span.contentStart,
                span.span.start + span.span.length)
            : undefined;
    });
};

/**
 * @param sql The script to scan.
 *
 * @returns The finish state of each span.
 */
const states = (sql: string): StatementFinishState[] => {
    return determineStatementRanges(sql).map((span) => {
        return span.state;
    });
};

describe("scanStatements", () => {
    it("returns nothing for empty input", () => {
        expect(determineStatementRanges("")).toEqual([]);
    });

    it("splits on the delimiter, keeping it in the span", () => {
        expect(spanTexts("SELECT 1;SELECT 2;"))
            .toEqual(["SELECT 1;", "SELECT 2;"]);
    });

    it("keeps whitespace between statements with the next one", () => {
        expect(spanTexts("SELECT 1;\n  SELECT 2;"))
            .toEqual(["SELECT 1;", "\n  SELECT 2;"]);
    });

    it("points contentStart at the first real character", () => {
        expect(contentTexts("SELECT 1;\n  SELECT 2;"))
            .toEqual(["SELECT 1;", "SELECT 2;"]);
    });

    it("marks a finished statement Complete", () => {
        expect(states("SELECT 1;")).toEqual([StatementFinishState.Complete]);
    });

    it("marks a statement without a delimiter", () => {
        expect(states("SELECT 1"))
            .toEqual([StatementFinishState.NoDelimiter]);
    });

    it("says a run of comments has no content", () => {
        expect(contentTexts("-- a note\n")).toEqual([undefined]);
        expect(contentTexts("/* a note */")).toEqual([undefined]);
    });

    it("skips leading comments when placing contentStart", () => {
        expect(contentTexts("-- a note\nSELECT 1;"))
            .toEqual(["SELECT 1;"]);
        expect(contentTexts("/* a note */ SELECT 1;"))
            .toEqual(["SELECT 1;"]);
        expect(contentTexts("# a note\nSELECT 1;"))
            .toEqual(["SELECT 1;"]);
    });

    it("keeps a comment inside a statement inside it", () => {
        expect(spanTexts("SELECT 1 -- a note\nAS a;"))
            .toEqual(["SELECT 1 -- a note\nAS a;"]);
    });

    it("does not treat a bare -- as a comment", () => {
        // `1--2` is a subtraction of a negative.
        expect(contentTexts("SELECT 1--2;")).toEqual(["SELECT 1--2;"]);
    });

    it("treats a hidden command as content", () => {
        // /*! ... */ is executed by the server, so it is not a comment.
        expect(contentTexts("/*!40101 SET NAMES utf8 */;"))
            .toEqual(["/*!40101 SET NAMES utf8 */;"]);
    });

    it("ignores a delimiter inside a string", () => {
        expect(spanTexts("SELECT 'a;b';")).toEqual(["SELECT 'a;b';"]);
        expect(spanTexts('SELECT "a;b";')).toEqual(['SELECT "a;b";']);
        expect(spanTexts("SELECT `a;b`;")).toEqual(["SELECT `a;b`;"]);
    });

    it("ignores a delimiter inside an escaped string", () => {
        expect(spanTexts("SELECT 'it\\'s; here';"))
            .toEqual(["SELECT 'it\\'s; here';"]);
    });

    it("ignores a delimiter inside a block comment", () => {
        expect(spanTexts("SELECT /* a; b */ 1;"))
            .toEqual(["SELECT /* a; b */ 1;"]);
    });

    it("reports an unclosed string", () => {
        expect(states("SELECT 'oops"))
            .toEqual([StatementFinishState.OpenString]);
    });

    it("reports an unclosed block comment", () => {
        expect(states("SELECT 1 /* oops"))
            .toEqual([StatementFinishState.OpenComment]);
    });

    it("reports an unclosed line comment at the end of input", () => {
        expect(states("-- oops"))
            .toEqual([StatementFinishState.OpenComment]);
    });

    it("follows a delimiter change", () => {
        const sql = "DELIMITER //\nSELECT 1;\nSELECT 2//\nSELECT 3;";
        const ranges = determineStatementRanges(sql);

        expect(ranges.map((span) => {
            return span.state;
        })).toEqual([
            StatementFinishState.DelimiterChange,
            StatementFinishState.Complete,
            StatementFinishState.NoDelimiter,
        ]);
        // Everything up to the // is one statement now.
        expect(sql.substr(ranges[1].span.start, ranges[1].span.length))
            .toBe("\nSELECT 1;\nSELECT 2//");
    });

    it("handles a multi character delimiter", () => {
        expect(spanTexts("DELIMITER $$\nSELECT 1$$")).toEqual([
            "DELIMITER $$",
            "\nSELECT 1$$",
        ]);
    });

    it("keeps a procedure body together under a new delimiter", () => {
        const sql = [
            "DELIMITER //",
            "CREATE PROCEDURE p()",
            "BEGIN",
            "  SELECT 1;",
            "  SELECT 2;",
            "END//",
        ].join("\n");
        const ranges = determineStatementRanges(sql);

        expect(ranges).toHaveLength(2);
        expect(sql.slice(ranges[1].contentStart))
            .toBe("CREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\n"
                + "  SELECT 2;\nEND//");
    });

    it("does not mistake an identifier starting with d for DELIMITER", () => {
        expect(spanTexts("SELECT description FROM t;"))
            .toEqual(["SELECT description FROM t;"]);
        expect(spanTexts("SELECT d FROM t;"))
            .toEqual(["SELECT d FROM t;"]);
    });

    it("yields spans one at a time, before the scan has finished", () => {
        // This is what lets the gutter show its first dots at once.
        const sql = `${"SELECT 1;".repeat(1000)}`;
        const iterator = scanStatements(sql);

        const first = iterator.next();

        expect(first.done).toBe(false);
        expect((first.value as IStatementSpan).span)
            .toEqual({ start: 0, length: 9 });
    });

    /*
     * The branches a port gets wrong silently: each is a place where the
     * scanner has to look ahead and can mistake one thing for another.
     */
    describe("the awkward cases", () => {
        it("treats a slash that is not a comment as division", () => {
            expect(contentTexts("SELECT 1/2;")).toEqual(["SELECT 1/2;"]);
        });

        it("reports a hash comment left open at the end of input", () => {
            expect(states("SELECT 1;\n# a note"))
                .toEqual([
                    StatementFinishState.Complete,
                    StatementFinishState.OpenComment,
                ]);
        });

        it("handles a d that runs out of input before DELIMITER can", () => {
            // Fewer than ten characters left, so the keyword cannot fit.
            expect(contentTexts("do 1")).toEqual(["do 1"]);
        });

        it("handles a word starting with d that is not DELIMITER", () => {
            expect(contentTexts("delete from t;"))
                .toEqual(["delete from t;"]);
        });

        it("closes the statement in front of a DELIMITER command", () => {
            const sql = "SELECT 1\nDELIMITER //\nSELECT 2//";

            expect(states(sql)).toEqual([
                // The unterminated SELECT, then the delimiter change,
                // then what follows under the new delimiter.
                StatementFinishState.NoDelimiter,
                StatementFinishState.DelimiterChange,
                StatementFinishState.Complete,
            ]);
        });

        it("skips the spaces between DELIMITER and its argument", () => {
            expect(spanTexts("DELIMITER \t  //\nSELECT 1//"))
                .toEqual(["DELIMITER \t  //", "\nSELECT 1//"]);
        });

        it("ignores a DELIMITER command with no delimiter after it", () => {
            // Nothing follows on the line, so there is no new delimiter
            // and the old one stays in force.
            expect(spanTexts("DELIMITER \nSELECT 1;"))
                .toEqual(["DELIMITER \nSELECT 1;"]);
        });

        it("does not end on a partial multi character delimiter", () => {
            // The / of 1/2 is the first character of the // delimiter.
            expect(spanTexts("DELIMITER //\nSELECT 1/2//")).toEqual([
                "DELIMITER //",
                "\nSELECT 1/2//",
            ]);
        });

        it("keeps an apostrophe in a comment from opening a string", () => {
            expect(spanTexts("-- it's fine\nSELECT 1;\nSELECT 2;"))
                .toEqual(["-- it's fine\nSELECT 1;", "\nSELECT 2;"]);
        });
    });

    it("scans a large script in linear time", () => {
        // The port exists for this: the previous splitter accumulated
        // each statement's text character by character while scanning.
        const unit = "SELECT id FROM city WHERE name = 'a;b';\n";
        const small = unit.repeat(2000);
        const large = unit.repeat(20000);

        const time = (sql: string): number => {
            const start = performance.now();
            determineStatementRanges(sql);

            return performance.now() - start;
        };

        time(small); // Warm up, so the first run is not the measured one.
        const smallMs = Math.max(time(small), 0.1);
        const largeMs = time(large);

        // Ten times the input, well under a hundred times the work.
        expect(largeMs / smallMs).toBeLessThan(40);
    });
});
