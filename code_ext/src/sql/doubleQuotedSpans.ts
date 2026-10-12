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
 * Finds the double-quoted identifiers of SQL read under ANSI_QUOTES.
 *
 * It follows the server's lexer (`sql_lex.cc`), not the statement
 * splitter: what matters here is what a `"..."` means, not where a
 * statement ends. So a backslash is an escape in a single-quoted string
 * only, `""` is the one escape inside double quotes, the body of a
 * versioned comment (`/*!...*\/`, `/*M!...*\/`) is code, and a `"..."`
 * right after `@` - a user variable's name or an account's host - is not
 * reported, as the grammar colours those as what they are.
 */

import type { ITextSpan } from "./statementSpans.js";

/**
 * Yields the span of every double-quoted identifier, its quotes included;
 * one left open runs to the end of the text.
 *
 * @param sql The SQL.
 *
 * @returns The spans, in order.
 */
export function* doubleQuotedSpans(sql: string): Generator<ITextSpan> {
    const end = sql.length;
    let i = 0;

    /** Where the quoted text starting at `i` ends, past its closing quote. */
    const skipQuoted = (quote: string, escapes: boolean): number => {
        let j = i + 1;
        while (j < end) {
            const c = sql[j];
            if (escapes && c === "\\") {
                j += 2;
            } else if (c === quote) {
                if (sql[j + 1] !== quote) {
                    return j + 1;
                }
                j += 2;
            } else {
                ++j;
            }
        }

        return end;
    };

    while (i < end) {
        const c = sql[i];
        if (c === "#"
            || (c === "-" && sql[i + 1] === "-"
                && (i + 2 >= end || /\s/.test(sql[i + 2])))) {
            const eol = sql.indexOf("\n", i);
            i = eol < 0 ? end : eol + 1;
        } else if (c === "/" && sql[i + 1] === "*") {
            const versioned = /^\/\*M?!!?\d*/.exec(sql.slice(i, i + 12));
            if (versioned) {
                // The markers are skipped and the body read as code; its
                // `*/` is two characters of no interest here.
                i += versioned[0].length;
            } else {
                const close = sql.indexOf("*/", i + 2);
                i = close < 0 ? end : close + 2;
            }
        } else if (c === "'") {
            i = skipQuoted("'", true);
        } else if (c === "`") {
            i = skipQuoted("`", false);
        } else if (c === "\"") {
            const start = i;
            i = skipQuoted("\"", false);
            if (sql[start - 1] !== "@") {
                yield { start, length: i - start };
            }
        } else {
            ++i;
        }
    }
}
