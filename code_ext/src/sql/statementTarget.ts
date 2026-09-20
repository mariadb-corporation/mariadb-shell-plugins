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

/** The single table a result set can be written back to. */
export interface IStatementTarget {
    schema?: string;
    table: string;
}

/**
 * Anything that makes a row of the result stop being one row of one table.
 * A result set built by any of these cannot be edited, because there is no
 * single stored row to write an UPDATE back to.
 */
const NOT_UPDATABLE = [
    /\bjoin\b/i,
    /\bunion\b/i,
    /\bintersect\b/i,
    /\bexcept\b/i,
    /\bgroup\s+by\b/i,
    /\bhaving\b/i,
    /\bdistinct\b/i,
    /\bselect\b[\s\S]*\bselect\b/i,
];

/**
 * An aggregate in the select list collapses rows into one, so the result
 * no longer has a row per stored row even without a GROUP BY.
 */
const AGGREGATE = new RegExp(
    "\\b(?:count|sum|avg|min|max|group_concat|json_arrayagg|"
    + "json_objectagg|std|stddev|stddev_pop|stddev_samp|variance|var_pop|"
    + "var_samp|bit_and|bit_or|bit_xor)\\s*\\(",
    "i",
);

/**
 * Pulls the select list out of a statement: what stands between SELECT and
 * the FROM that follows it.
 *
 * @param cleaned The statement, with literals and comments already
 *                stripped.
 *
 * @returns The select list, or the whole statement if it has no FROM.
 */
const selectList = (cleaned: string): string => {
    const from = /\bfrom\b/i.exec(cleaned);

    return cleaned.slice("select".length, from?.index ?? cleaned.length);
};

/**
 * Strips comments and blanks out string literals, so the shape of the
 * statement can be matched without a keyword inside a literal or a comment
 * being mistaken for part of it.
 *
 * @param sql The statement to clean.
 *
 * @returns The statement with comments removed and literals emptied.
 */
export const stripLiteralsAndComments = (sql: string): string => {
    let out = "";
    let position = 0;

    while (position < sql.length) {
        const rest = sql.slice(position);
        const char = sql[position];

        if (rest.startsWith("/*")) {
            const end = sql.indexOf("*/", position + 2);
            position = end === -1 ? sql.length : end + 2;
            out += " ";
            continue;
        }

        if (/^--[\s]/.test(rest) || char === "#") {
            const end = sql.indexOf("\n", position);
            position = end === -1 ? sql.length : end;
            out += " ";
            continue;
        }

        if (char === "'" || char === '"') {
            position += 1;
            while (position < sql.length) {
                if (sql[position] === "\\") {
                    position += 2;
                    continue;
                }
                if (sql[position] === char) {
                    if (sql[position + 1] === char) {
                        position += 2;
                        continue;
                    }
                    position += 1;
                    break;
                }
                position += 1;
            }
            out += "''";
            continue;
        }

        out += char;
        position += 1;
    }

    return out;
};

/** An identifier: back-quoted, or a bare name. */
const IDENTIFIER = "(?:`[^`]+`|[A-Za-z_$][\\w$]*)";

/** What may legally follow the table name in an updatable SELECT. */
const TAIL = "(?:$|\\s+(?:where|order\\s+by|limit|for|lock)\\b|\\s*;)";

/**
 * Matches the FROM clause of a single table SELECT, qualified or not.
 * A comma means a second table and a parenthesis a derived one; neither can
 * match here, so both fall through to "not updatable".
 */
const SINGLE_TABLE_FROM = new RegExp(
    `\\bfrom\\s+(?:(${IDENTIFIER})\\s*\\.\\s*)?(${IDENTIFIER})${TAIL}`,
    "is",
);

/**
 * Removes the back quotes from an identifier.
 *
 * @param identifier The identifier as it was written.
 *
 * @returns Its bare name.
 */
const unquote = (identifier: string): string => {
    return identifier.startsWith("`")
        ? identifier.slice(1, -1)
        : identifier;
};

/**
 * Works out whether a statement's result set can be written back, and to
 * which table.
 *
 * Only a plain `SELECT ... FROM <one table>` qualifies. Everything that
 * combines, groups or derives rows is rejected, because the result then no
 * longer maps row for row onto a stored table - which is what an UPDATE
 * generated from an edited cell relies on.
 *
 * @param sql The statement to inspect.
 *
 * @returns The table the result can be written back to, or undefined when
 *          the result set has to stay read only.
 */
export const findUpdatableTarget = (
    sql: string,
): IStatementTarget | undefined => {
    const cleaned = stripLiteralsAndComments(sql).trim();
    if (!/^select\b/i.test(cleaned)) {
        return undefined;
    }

    if (NOT_UPDATABLE.some((pattern) => {
        return pattern.test(cleaned);
    })) {
        return undefined;
    }

    if (AGGREGATE.test(selectList(cleaned))) {
        return undefined;
    }

    const match = SINGLE_TABLE_FROM.exec(cleaned);
    if (!match) {
        return undefined;
    }

    const [, schema, table] = match;

    return schema === undefined
        ? { table: unquote(table) }
        : { schema: unquote(schema), table: unquote(table) };
};
