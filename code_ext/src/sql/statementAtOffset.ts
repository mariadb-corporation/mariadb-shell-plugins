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

/** One statement of a script, with where it sits in the text. */
export interface IStatementRange {
    /** The statement text, without its trailing delimiter. */
    text: string;
    /** Where the statement's first real character is. */
    start: number;
    /** One past its last character, before the delimiter. */
    end: number;
}

/**
 * Finds the statement the cursor is in.
 *
 * A caret between two statements belongs to the one it is inside; when it
 * sits in the whitespace or comments after a statement, the statement
 * that *follows* is taken, because that is the one about to be typed
 * into. With the caret past the last statement, the last one is used.
 *
 * DELIMITER commands are skipped: the shell consumes them and there is
 * nothing to run.
 *
 * @param script The text to look in.
 * @param offset The cursor's offset in it.
 *
 * @returns The statement to run, or undefined when there is none.
 */
export const statementAtOffset = (
    script: string,
    offset: number,
): IStatementRange | undefined => {
    const candidates: IStatementRange[] = [];

    for (const span of scanStatements(script)) {
        if (!hasContent(span)
            || span.state === StatementFinishState.DelimiterChange) {
            continue;
        }

        const range = toRange(script, span);
        if (range.text.length === 0) {
            continue;
        }

        // Inside this statement, counting the position just after its
        // last character - that is where the caret sits once a statement
        // has been typed.
        if (offset >= range.start && offset <= range.end) {
            return range;
        }

        candidates.push(range);
    }

    // Not inside any of them: the next one down is what the caret is
    // heading for, and failing that the last one above it.
    return candidates.find((range) => {
        return range.start > offset;
    }) ?? candidates.at(-1);
};

/**
 * Narrows a span to its content, leaving the leading comments and the
 * trailing delimiter out.
 *
 * @param script The whole text.
 * @param span The span to narrow.
 *
 * @returns The statement's range.
 */
const toRange = (
    script: string,
    span: IStatementSpan,
): IStatementRange => {
    const spanEnd = span.span.start + span.span.length;
    const withoutDelimiter =
        span.state === StatementFinishState.Complete && span.delimiter
            ? spanEnd - span.delimiter.length
            : spanEnd;

    const start = span.contentStart;
    const raw = script.slice(start, withoutDelimiter);
    const trimmed = raw.trimEnd();

    return {
        text: trimmed,
        start,
        end: start + trimmed.length,
    };
};
