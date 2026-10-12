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

/**
 * The token classes, one group per state of the server's lexer
 * (`sql_lex.cc`, `lex_one_token`) and per token the REST lexer adds
 * (`mrs_lexer.h`). Each case is a line and the scope of every token in
 * it, whitespace left out, so a case reads like the line.
 */

import { describe, expect, it } from "vitest";

import { scopesOf, tokenize } from "./tokenize.js";

const K = "keyword.other.reserved.sql";
const U = "keyword.other.unreserved.sql";
const F = "support.function.sql";
const ID = "entity.name.identifier.sql";
const INT = "constant.numeric.integer.sql";
const COMMA = "punctuation.separator.comma.sql";
const SEMI = "punctuation.terminator.statement.sql";
const DOT = "punctuation.accessor.sql";
const Q1 = "punctuation.definition.string.begin.sql";
const Q2 = "punctuation.definition.string.end.sql";
const STR = "string.quoted.single.sql";
const ESC = "constant.character.escape.sql";
const OPEN = "punctuation.section.parens.begin.sql";
const CLOSE = "punctuation.section.parens.end.sql";

/** Asserts the tokens of one line, whitespace left out. */
const expectLine = async (
    line: string,
    expected: Array<[string, string]>,
): Promise<void> => {
    expect(await scopesOf(line)).toEqual(expected);
};

describe("comments", () => {
    it("# to the end of the line", async () => {
        await expectLine("SELECT 1; # x -- y", [
            ["SELECT", K], ["1", INT], [";", SEMI],
            ["# x -- y", "comment.line.number-sign.sql"],
        ]);
    });

    it("-- only before whitespace or the line's end", async () => {
        await expectLine("SELECT 1 -- x", [
            ["SELECT", K], ["1", INT],
            ["-- x", "comment.line.double-dash.sql"],
        ]);
        await expectLine("SELECT 1 --", [
            ["SELECT", K], ["1", INT],
            ["--", "comment.line.double-dash.sql"],
        ]);
        const minus = "keyword.operator.arithmetic.sql";
        await expectLine("SELECT 1--1", [
            ["SELECT", K], ["1", INT], ["-", minus], ["-", minus],
            ["1", INT],
        ]);
    });

    it("/* */ without nesting, across lines", async () => {
        const lines = await tokenize("a /* b /* c\nd */ e */ f");
        expect(lines[0].map((t) => { return t.scopes.at(-1); }))
            .toEqual([ID, undefined, "punctuation.definition.comment.sql",
                "comment.block.sql"]);
        expect(lines[1].filter((t) => { return t.text.trim(); })
            .map((t) => { return [t.text, t.scopes.at(-1)]; }))
            .toEqual([
                ["d ", "comment.block.sql"],
                ["*/", "punctuation.definition.comment.sql"],
                ["e", ID], ["*", "keyword.operator.arithmetic.sql"],
                ["/", "keyword.operator.arithmetic.sql"], ["f", ID],
            ]);
    });

    it("/*+ */ is an optimizer hint, read as a comment", async () => {
        await expectLine("SELECT /*+ NO_ICP(t) */ 1", [
            ["SELECT", K],
            ["/*+", "punctuation.definition.comment.sql"],
            [" NO_ICP(t) ", "comment.block.hint.sql"],
            ["*/", "punctuation.definition.comment.sql"],
            ["1", INT],
        ]);
    });

    it("/*! */, /*M! */ and /*!! */ hold SQL, versioned or not",
        async () => {
            const begin = "punctuation.definition.comment.begin.sql";
            const end = "punctuation.definition.comment.end.sql";
            const version = "constant.numeric.version.sql";
            for (const marker of ["/*!", "/*M!", "/*!!"]) {
                await expectLine(`${marker}100500 SELECT 1 */`, [
                    [marker, begin], ["100500", version], ["SELECT", K],
                    ["1", INT], ["*/", end],
                ]);
                await expectLine(`${marker} SELECT 1 */`, [
                    [marker, begin], ["SELECT", K], ["1", INT],
                    ["*/", end],
                ]);
            }
            // Inside one, another is a plain comment, as in the server,
            // and the outer one goes on to the next */.
            await expectLine("/*! SELECT /*!50000 1 */ 2 */", [
                ["/*!", begin], ["SELECT", K],
                ["/*", "punctuation.definition.comment.sql"],
                ["!50000 1 ", "comment.block.sql"],
                ["*/", "punctuation.definition.comment.sql"],
                ["2", INT], ["*/", end],
            ]);
        });
});

describe("the DELIMITER command", () => {
    it("is recognised on a line of its own", async () => {
        await expectLine("DELIMITER //", [
            ["DELIMITER", "keyword.other.delimiter.sql"],
            ["//", "punctuation.terminator.statement.custom.sql"],
        ]);
        await expectLine("  delimiter $$  ", [
            ["delimiter", "keyword.other.delimiter.sql"],
            ["$$", "punctuation.terminator.statement.custom.sql"],
        ]);
    });
});

describe("strings", () => {
    it("single quoted, with '' and backslash escapes", async () => {
        await expectLine("'it''s \\'ok\\' \\n'", [
            ["'", Q1], ["it", STR], ["''", ESC], ["s ", STR], ["\\'", ESC],
            ["ok", STR], ["\\'", ESC], ["\\n", ESC], ["'", Q2],
        ]);
    });

    it("continue across lines, a backslash before the break included",
        async () => {
            const lines = await tokenize("'a\\\nb\nc' d");
            expect(lines[0].map((t) => { return [t.text, t.scopes.at(-1)]; }))
                .toEqual([["'", Q1], ["a", STR], ["\\", ESC]]);
            expect(lines[1].map((t) => { return [t.text, t.scopes.at(-1)]; }))
                .toEqual([["b", STR]]);
            expect(lines[2].map((t) => { return [t.text, t.scopes.at(-1)]; }))
                .toEqual([["c", STR], ["'", Q2], [" ", undefined],
                    ["d", ID]]);
        });

    it("carry a character set introducer or N", async () => {
        await expectLine("_utf8mb4'a' _latin1\"b\" N'c' n'd' Nx", [
            ["_utf8mb4", "storage.modifier.charset.sql"],
            ["'", Q1], ["a", STR], ["'", Q2],
            ["_latin1", "storage.modifier.charset.sql"],
            ["\"", Q1], ["b", "string.quoted.double.sql"], ["\"", Q2],
            ["N", "storage.modifier.national.sql"],
            ["'", Q1], ["c", STR], ["'", Q2],
            ["n", "storage.modifier.national.sql"],
            ["'", Q1], ["d", STR], ["'", Q2],
            ["Nx", ID],
        ]);
    });

    it("double quoted is a string, as in the default sql_mode", async () => {
        await expectLine("\"a\"\"b\\\"c\"", [
            ["\"", Q1], ["a", "string.quoted.double.sql"], ["\"\"", ESC],
            ["b", "string.quoted.double.sql"], ["\\\"", ESC],
            ["c", "string.quoted.double.sql"], ["\"", Q2],
        ]);
    });
});

describe("identifiers", () => {
    it("quoted with backticks, `` an escape", async () => {
        await expectLine("`my``tbl`.`c`", [
            ["`", "punctuation.definition.identifier.begin.sql"],
            ["my", "entity.name.identifier.quoted.sql"], ["``", ESC],
            ["tbl", "entity.name.identifier.quoted.sql"],
            ["`", "punctuation.definition.identifier.end.sql"],
            [".", DOT],
            ["`", "punctuation.definition.identifier.begin.sql"],
            ["c", "entity.name.identifier.quoted.sql"],
            ["`", "punctuation.definition.identifier.end.sql"],
        ]);
    });

    it("may hold $ and digits, start with a digit, or be a number after .",
        async () => {
            await expectLine("a$b 1e 123abc t.1 _x ü", [
                ["a$b", ID], ["1e", ID], ["123abc", ID], ["t", ID],
                [".", DOT], ["1", ID], ["_x", ID], ["ü", ID],
            ]);
        });

    it("are a function only directly before (", async () => {
        await expectLine("date, DATE(x), date (y), LEFT JOIN, left(z)", [
            ["date", K], [",", COMMA], ["DATE", F], ["(", OPEN],
            ["x", ID], [")", CLOSE], [",", COMMA], ["date", F],
            ["(", OPEN], ["y", ID], [")", CLOSE], [",", COMMA],
            ["LEFT", K], ["JOIN", K], [",", COMMA], ["left", F],
            ["(", OPEN], ["z", ID], [")", CLOSE],
        ]);
    });

    it("a keyword in a qualified name stays a keyword, as in the lexer",
        async () => {
            // The lexer looks keywords up after a qualifier too, with
            // `lookup_keywords_after_qualifier`; the grammar cannot do
            // better than that without the parser.
            await expectLine("t.status", [
                ["t", ID], [".", DOT], ["status", U],
            ]);
        });
});

describe("numbers", () => {
    it("integers, decimals and floats", async () => {
        const dec = "constant.numeric.decimal.sql";
        const flt = "constant.numeric.float.sql";
        await expectLine("1 1.5 .5 1. 1e5 1E+5 1.5e-3 .5e1", [
            ["1", INT], ["1.5", dec], [".5", dec], ["1.", dec], ["1e5", flt],
            ["1E+5", flt], ["1.5e-3", flt], [".5e1", flt],
        ]);
    });

    it("hex and binary, in both notations", async () => {
        const hex = "constant.numeric.hex.sql";
        const bin = "constant.numeric.binary.sql";
        await expectLine("0x1F x'1f' X'' 0b10 b'01' B'' 0xg 0b2", [
            ["0x1F", hex], ["x'1f'", hex], ["X''", hex], ["0b10", bin],
            ["b'01'", bin], ["B''", bin], ["0xg", ID], ["0b2", ID],
        ]);
    });

    it("1..10 is a range of two integers", async () => {
        await expectLine("FOR i IN 1..10 DO", [
            ["FOR", K], ["i", ID], ["IN", K], ["1", INT],
            ["..", "keyword.operator.range.sql"], ["10", INT], ["DO", K],
        ]);
    });
});

describe("constants", () => {
    it("NULL, TRUE, FALSE, UNKNOWN and \\N", async () => {
        const C = "constant.language.sql";
        await expectLine("null TRUE false UNKNOWN \\N \\Nx", [
            ["null", C], ["TRUE", C], ["false", C], ["UNKNOWN", C],
            ["\\N", "constant.language.null.sql"],
            ["\\", "invalid.illegal.character.sql"], ["Nx", ID],
        ]);
    });
});

describe("variables", () => {
    it("@user, quoted too", async () => {
        const V = "variable.language.user.sql";
        const VQ = "variable.language.user.quoted.sql";
        await expectLine("SET @a = 1, @a.b := 2, @'x y' = 3, @`z` = 4", [
            ["SET", K], ["@a", V], ["=", "keyword.operator.comparison.sql"],
            ["1", INT], [",", COMMA], ["@a.b", V],
            [":=", "keyword.operator.assignment.sql"], ["2", INT],
            [",", COMMA], ["@'", VQ], ["x y", VQ], ["'", VQ],
            ["=", "keyword.operator.comparison.sql"], ["3", INT],
            [",", COMMA], ["@`", VQ], ["z", VQ], ["`", VQ],
            ["=", "keyword.operator.comparison.sql"], ["4", INT],
        ]);
    });

    it("@@system, with its scope", async () => {
        const S = "variable.language.system.sql";
        await expectLine("@@x @@global.y @@SESSION.z @@local.w @@`q`", [
            ["@@x", S], ["@@global.y", S], ["@@SESSION.z", S],
            ["@@local.w", S], ["@@`q`", S],
        ]);
    });

    it("user@host is an account, not a variable", async () => {
        const at = "punctuation.separator.host.sql";
        const host = "string.other.host.sql";
        await expectLine("'u'@'h' u@h `u`@`h` u@'%' u@192.168.0.1", [
            ["'", Q1], ["u", STR], ["'", Q2], ["@", at], ["'h'", host],
            ["u", ID], ["@", at], ["h", host],
            ["`", "punctuation.definition.identifier.begin.sql"],
            ["u", "entity.name.identifier.quoted.sql"],
            ["`", "punctuation.definition.identifier.end.sql"],
            ["@", at], ["`h`", host],
            ["u", ID], ["@", at], ["'%'", host],
            ["u", ID], ["@", at], ["192.168.0.1", host],
        ]);
    });

    it("? is a placeholder", async () => {
        await expectLine("WHERE a = ?", [
            ["WHERE", K], ["a", ID], ["=", "keyword.operator.comparison.sql"],
            ["?", "variable.parameter.placeholder.sql"],
        ]);
    });
});

describe("operators and punctuation", () => {
    it("each gets its class", async () => {
        const cmp = "keyword.operator.comparison.sql";
        await expectLine("<=> <> != <= >= = < >", [
            ["<=>", cmp], ["<>", cmp], ["!=", cmp], ["<=", cmp], [">=", cmp],
            ["=", cmp], ["<", cmp], [">", cmp],
        ]);
        const bit = "keyword.operator.bitwise.sql";
        await expectLine("<< >> & | ^ ~", [
            ["<<", bit], [">>", bit], ["&", bit], ["|", bit], ["^", bit],
            ["~", bit],
        ]);
        const logic = "keyword.operator.logical.sql";
        await expectLine("&& || !", [
            ["&&", logic], ["||", logic], ["!", logic],
        ]);
        const arith = "keyword.operator.arithmetic.sql";
        await expectLine("+ - * / %", [
            ["+", arith], ["-", arith], ["*", arith], ["/", arith],
            ["%", arith],
        ]);
        await expectLine("j->'$' j->>'$' f(a=>1) x:=1", [
            ["j", ID], ["->", "keyword.operator.json.sql"],
            ["'", Q1], ["$", STR], ["'", Q2],
            ["j", ID], ["->>", "keyword.operator.json.sql"],
            ["'", Q1], ["$", STR], ["'", Q2],
            ["f", ID], ["(", OPEN], ["a", ID],
            ["=>", "keyword.operator.arrow.sql"], ["1", INT], [")", CLOSE],
            ["x", ID], [":=", "keyword.operator.assignment.sql"],
            ["1", INT],
        ]);
        await expectLine("(a, b); l: {}", [
            ["(", OPEN], ["a", ID], [",", COMMA], ["b", ID], [")", CLOSE],
            [";", SEMI], ["l", ID], [":", "punctuation.separator.colon.sql"],
            ["{", "punctuation.section.braces.sql"],
            ["}", "punctuation.section.braces.sql"],
        ]);
    });
});

describe("REST SQL", () => {
    it("keywords of its own, the server's where shared", async () => {
        const R = "keyword.other.rest.sql";
        await expectLine("CREATE REST SERVICE /a AUTHENTICATION PATH /b", [
            ["CREATE", K], ["REST", R], ["SERVICE", R],
            ["/a", "string.unquoted.path.rest.sql"], ["AUTHENTICATION", R],
            ["PATH", U], ["/b", "string.unquoted.path.rest.sql"],
        ]);
    });

    it("request paths, after whitespace only", async () => {
        const path = "string.unquoted.path.rest.sql";
        const div = "keyword.operator.arithmetic.sql";
        await expectLine("/svc/v1/items a/b a / b /x$1 //", [
            ["/svc/v1/items", path], ["a", ID], ["/", div], ["b", ID],
            ["a", ID], ["/", div], ["b", ID], ["/x$1", path],
            ["/", div], ["/", div],
        ]);
    });

    it("data mapping annotations", async () => {
        const A = "storage.modifier.annotation.rest.sql";
        await expectLine("t @INSERT @update { id: id @KEY @SORTABLE }", [
            ["t", ID], ["@INSERT", A], ["@update", A],
            ["{", "punctuation.section.braces.sql"], ["id", U],
            [":", "punctuation.separator.colon.sql"], ["id", U],
            ["@KEY", A], ["@SORTABLE", A],
            ["}", "punctuation.section.braces.sql"],
        ]);
    });
});
