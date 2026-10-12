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

import { doubleQuotedSpans } from "../../sql/doubleQuotedSpans.js";

/** The text of each span found. */
const found = (sql: string): string[] => {
    return [...doubleQuotedSpans(sql)].map((span) => {
        return sql.slice(span.start, span.start + span.length);
    });
};

describe("doubleQuotedSpans", () => {
    it("finds each double-quoted identifier, quotes included", () => {
        expect(found("SELECT \"a\", \"b c\" FROM \"t\";"))
            .toEqual(["\"a\"", "\"b c\"", "\"t\""]);
    });

    it("takes \"\" as an escaped quote and a backslash as a character", () => {
        // As the server reads an identifier: "a\" ends at the second quote.
        expect(found("\"a\"\"b\" \"c\\\" \"d\""))
            .toEqual(["\"a\"\"b\"", "\"c\\\"", "\"d\""]);
    });

    it("spans lines, and runs an open one to the end", () => {
        expect(found("SELECT \"a\nb\" , \"c")).toEqual(["\"a\nb\"", "\"c"]);
    });

    it("skips what is inside single-quoted strings, escapes included",
        () => {
            expect(found("'\"no\"' 'it''s \"no\"' 'a\\'\"no\"' \"yes\""))
                .toEqual(["\"yes\""]);
        });

    it("skips what is inside backticks", () => {
        expect(found("`a\"b` `c``\"d` \"yes\"")).toEqual(["\"yes\""]);
    });

    it("skips comments", () => {
        expect(found([
            "# \"no\"",
            "-- \"no\"",
            "/* \"no\" */ \"yes\"",
            "a --\"yes\"",
        ].join("\n"))).toEqual(["\"yes\"", "\"yes\""]);
    });

    it("reads the body of a versioned comment as code", () => {
        expect(found("/*!50700 \"a\" */ /*M!100500 \"b\" */ /*!! \"c\" */"))
            .toEqual(["\"a\"", "\"b\"", "\"c\""]);
    });

    it("leaves a variable's name and an account's host to the grammar",
        () => {
            expect(found("SET @\"v\" = 1; CREATE USER 'u'@\"h\"; \"yes\""))
                .toEqual(["\"yes\""]);
        });
});
