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

import {
    hasContent,
    scanStatements,
    StatementFinishState,
    type IStatementSpan,
} from "./statementSpans.js";

/** One statement of a script, with where it came from. */
export interface ISqlStatement {
    /** The statement text, without the trailing delimiter. */
    text: string;
    /** Its zero based index among the statements the server will see. */
    index: number;
    /** The 1-based line the statement starts on. */
    line: number;
    /** The offset the statement starts at. */
    offset: number;
}

/**
 * Counts lines while walking forward through a text.
 *
 * Offsets arrive in order, so each line number costs only the characters
 * since the previous one. Counting from the start every time would make
 * splitting a large script quadratic.
 */
class LineCounter {
    #offset = 0;
    #line = 1;

    public constructor(private readonly text: string) { }

    /**
     * @param offset An offset at or after the last one asked for.
     *
     * @returns The 1-based line that offset is on.
     */
    public lineAt(offset: number): number {
        for (; this.#offset < offset; this.#offset += 1) {
            if (this.text[this.#offset] === "\n") {
                this.#line += 1;
            }
        }

        return this.#line;
    }
}

/**
 * @param text The script.
 * @param position An offset into it.
 *
 * @returns True if a standalone line comment starts there.
 */
const startsLineComment = (text: string, position: number): boolean => {
    if (text[position] === "#") {
        return true;
    }

    if (text[position] !== "-" || text[position + 1] !== "-") {
        return false;
    }

    // `--` only opens a comment when whitespace follows it; `a--b` is a
    // subtraction of a negative.
    const next = text[position + 2];

    return next === undefined || next === " " || next === "\t"
        || next === "\n" || next === "\r";
};

/**
 * The offset one past the end of a span's SQL, with its delimiter left
 * off.
 *
 * @param span The span to measure.
 *
 * @returns The end offset of the statement text.
 */
const textEndOf = (span: IStatementSpan): number => {
    const end = span.span.start + span.span.length;

    return span.state === StatementFinishState.Complete && span.delimiter
        ? end - span.delimiter.length
        : end;
};

/**
 * Splits one span the way the server splits it.
 *
 * The scanner keeps a statement's leading comments with the statement,
 * but the server makes a **line** comment that opens a statement a
 * statement of its own - `-- a note` before a query is two statements
 * there, not one. Results are paired with statements by position, so
 * getting this wrong shifts every result after the comment. Block
 * comments are not treated this way by either side and stay with the
 * statement that follows them.
 *
 * @param text The whole script.
 * @param span The span to split.
 *
 * @returns The statements the server will see, as text and offset.
 */
const toServerStatements = (
    text: string,
    span: IStatementSpan,
): Array<{ text: string; offset: number }> => {
    const pieces: Array<{ text: string; offset: number }> = [];
    const contentBoundary = hasContent(span)
        ? span.contentStart
        : span.span.start + span.span.length;
    const textEnd = textEndOf(span);

    let position = span.span.start;
    while (position < contentBoundary) {
        // Only whitespace and comments can stand before the content.
        if (/\s/.test(text[position])) {
            position += 1;
            continue;
        }

        if (!startsLineComment(text, position)) {
            break;
        }

        const newline = text.indexOf("\n", position);
        const lineEnd = newline === -1 || newline > contentBoundary
            ? Math.min(contentBoundary, textEnd)
            : newline;
        const comment = text.slice(position, lineEnd).trim();
        if (comment.length > 0) {
            pieces.push({ text: comment, offset: position });
        }
        position = lineEnd;
    }

    const rest = text.slice(position, textEnd);
    const trimmed = rest.trim();
    if (trimmed.length > 0) {
        pieces.push({
            text: trimmed,
            offset: position + (rest.length - rest.trimStart().length),
        });
    }

    return pieces;
};

/**
 * Splits a SQL script into the statements the server will execute.
 *
 * The server splits the script again on its own side, and the results
 * come back as a flat list, so this exists to pair each result with the
 * statement that produced it - the two lists have to line up. Blank
 * statements are dropped and DELIMITER commands are left out, exactly as
 * the server leaves them out.
 *
 * @param script The script to split.
 *
 * @returns One entry per statement the server will see, in order.
 */
export const splitStatements = (script: string): ISqlStatement[] => {
    const statements: ISqlStatement[] = [];
    const lines = new LineCounter(script);

    for (const span of scanStatements(script)) {
        // A DELIMITER command is a client side directive. The server
        // consumes it without producing a result, so it is not one of the
        // statements the results line up against.
        if (span.state === StatementFinishState.DelimiterChange) {
            continue;
        }

        for (const piece of toServerStatements(script, span)) {
            statements.push({
                text: piece.text,
                index: statements.length,
                line: lines.lineAt(piece.offset),
                offset: piece.offset,
            });
        }
    }

    return statements;
};
