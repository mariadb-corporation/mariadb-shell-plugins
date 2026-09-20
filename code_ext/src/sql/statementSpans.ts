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
 * Splits SQL into statement ranges.
 *
 * Ported from the MySQL Shell's `MySQLParsingServices.determineStatementRanges`.
 * It scans the text character by character and records offsets, never
 * building substrings as it goes, which is what lets it run over a very
 * large file without the quadratic cost of accumulating each statement's
 * text while scanning.
 *
 * Two deliberate differences from the original:
 *
 * - The dollar-quoted string branch is not ported. It guards a MySQL 8.1
 *   feature for JavaScript stored programs; MariaDB has no dollar-quoted
 *   strings, so the branch could only ever mis-scan a `$` here.
 * - It is a generator. Spans are yielded as they are found, so the caller
 *   can show the first ones before the rest of a large file has been
 *   scanned.
 * - One off-by-one in the original is corrected: a word starting with `d`
 *   that is not DELIMITER used to leave `contentStart` on its second
 *   character. See the comment at that branch.
 */

/** A range of the input, by offset. */
export interface ITextSpan {
    start: number;
    length: number;
}

/** How a statement ended. */
export enum StatementFinishState {
    /** Ends with a delimiter. */
    Complete,

    /** Ends with an open comment (multi line, or single line at EOF). */
    OpenComment,

    /** A string (single, double or backtick quoted) was not closed. */
    OpenString,

    /** The delimiter is missing. */
    NoDelimiter,

    /** The statement changes the delimiter. */
    DelimiterChange,
}

export interface IStatementSpan {
    /** The delimiter this statement ended with, if it found one. */
    delimiter?: string;

    /** The whole statement, including leading whitespace and comments. */
    span: ITextSpan;

    /**
     * The first character that is neither whitespace nor comment. It is
     * less than `span.start` when the statement has no content at all,
     * which is how a run of comments is told from a statement.
     *
     * Every span that can be content-free says so the same way, including
     * the ones a comment left open at the end of the input produces. A
     * trailing `-- note` with no newline after it used to report content,
     * so it read as a statement while the same line with a newline did
     * not.
     */
    contentStart: number;

    state: StatementFinishState;
}

/** Matches the DELIMITER keyword and its mandatory trailing space. */
const DELIMITER_KEYWORD = /delimiter /i;

/**
 * @param span The span to test.
 *
 * @returns True if the span holds anything but whitespace and comments.
 */
export const hasContent = (span: IStatementSpan): boolean => {
    return span.contentStart >= span.span.start;
};

/**
 * Splits SQL into statement ranges, yielding each as it is found.
 *
 * The length of a span includes everything up to and including its
 * delimiter. Line breaks are assumed to be `\n`.
 *
 * @param sql The SQL to split.
 * @param initialDelimiter The delimiter in force at the start.
 *
 * @returns Each statement range, in order.
 */
export function* scanStatements(
    sql: string,
    initialDelimiter = ";",
): Generator<IStatementSpan> {
    if (sql.length === 0) {
        return;
    }

    let delimiter = initialDelimiter;

    let start = 0;    // Start of the current statement.
    let head = start; // The current content position in the current token.
    let tail = head;
    const end = sql.length;

    // Set once anything other than comments is found for this statement.
    let haveContent = false;

    // The inner helpers cannot yield, so what they find is parked here and
    // drained by the loop below. This keeps the ported control flow
    // identical to the original.
    const pending: IStatementSpan[] = [];

    /**
     * Ends the current statement if the tail sits on a delimiter.
     *
     * @returns True if a delimiter was found.
     */
    const checkDelimiter = (): boolean => {
        if (sql[tail] !== delimiter[0]) {
            return false;
        }

        // Found a possible start of the delimiter. Check if it really is.
        if (delimiter.length === 1) {
            // The most common case.
            ++tail;
            pending.push({
                delimiter,
                span: { start, length: tail - start },
                contentStart: haveContent ? head : start - 1,
                state: StatementFinishState.Complete,
            });

            head = tail;
            start = head;
            haveContent = false;

            return true;
        }

        const candidate = sql.substring(tail, tail + delimiter.length);
        if (candidate === delimiter) {
            // A multi character delimiter, complete. The tail still points
            // at the start of it.
            tail += delimiter.length;
            pending.push({
                delimiter,
                span: { start, length: tail - start },
                contentStart: haveContent ? head : start - 1,
                state: StatementFinishState.Complete,
            });

            head = tail;
            start = head;
            haveContent = false;

            return true;
        }

        return false;
    };

    /**
     * Steps over whatever the tail sits on: a comment, a quoted string, a
     * DELIMITER command, or an ordinary character.
     *
     * @returns Nothing.
     */
    const handleStringsCommentsAndDelimiter = (): void => {
        switch (sql[tail]) {
            case "/": { // A multi line comment, or a hidden command.
                if (sql[tail + 1] === "*") {
                    if (sql[tail + 2] === "!") { // A hidden command.
                        if (!haveContent) {
                            haveContent = true;
                            head = tail;
                        }
                        ++tail;
                    }
                    tail += 2;

                    for (;;) {
                        while (tail < end && sql[tail] !== "*") {
                            ++tail;
                        }

                        if (tail === end) { // Unfinished multi line comment.
                            pending.push({
                                delimiter,
                                span: { start, length: tail - start },
                                contentStart: haveContent ? head : start - 1,
                                state: StatementFinishState.OpenComment,
                            });
                            start = tail;
                            head = tail;

                            break;
                        }

                        if (sql[++tail] === "/") {
                            ++tail; // Skip the slash too.
                            break;
                        }
                    }

                    if (!haveContent) {
                        head = tail; // Skip over the comment.
                    }
                } else {
                    ++tail;
                    haveContent = true;
                }

                break;
            }

            case "-": { // A possible single line comment.
                const temp = tail + 2;
                if (sql[tail + 1] === "-"
                    && (sql[temp] === " " || sql[temp] === "\t"
                        || sql[temp] === "\n")) {
                    // Skip everything up to the end of the line.
                    tail += 2;
                    while (tail < end && sql[tail] !== "\n") {
                        ++tail;
                    }

                    if (tail === end) { // Unfinished single line comment.
                        pending.push({
                            delimiter,
                            span: { start, length: tail - start },
                            contentStart: haveContent ? head : start - 1,
                            state: StatementFinishState.OpenComment,
                        });
                        start = tail;
                        head = tail;

                        break;
                    }

                    if (!haveContent) {
                        head = tail;
                    }
                } else {
                    ++tail;
                    haveContent = true;
                }

                break;
            }

            case "#": { // A single line comment.
                while (tail < end && sql[tail] !== "\n") {
                    ++tail;
                }

                if (tail === end) { // Unfinished single line comment.
                    pending.push({
                        delimiter,
                        span: { start, length: tail - start },
                        contentStart: haveContent ? head : start - 1,
                        state: StatementFinishState.OpenComment,
                    });
                    start = tail;
                    head = tail;

                    break;
                }

                if (!haveContent) {
                    head = tail;
                }

                break;
            }

            case '"':
            case "'":
            case "`": { // A quoted string or identifier.
                haveContent = true;
                const quote = sql[tail++];
                while (tail < end && sql[tail] !== quote) {
                    // Skip any escaped character too.
                    if (sql[tail] === "\\") {
                        ++tail;
                    }
                    ++tail;
                }

                if (sql[tail] === quote) {
                    ++tail; // Skip the trailing quote if there was one.
                } else { // Unfinished string.
                    pending.push({
                        delimiter,
                        span: { start, length: tail - start },
                        contentStart: head,
                        state: StatementFinishState.OpenString,
                    });
                    start = tail;
                    head = tail;
                }

                break;
            }

            case "d":
            case "D": {
                // A possible DELIMITER keyword, counting its trailing
                // space - hence 10 characters.
                if (tail + 10 >= end) {
                    if (!haveContent) {
                        haveContent = true;
                        head = tail;
                    }
                    ++tail;

                    break; // Not enough input for it.
                }

                const candidate = sql.substring(tail, tail + 10);
                if (DELIMITER_KEYWORD.test(candidate)) {
                    // The DELIMITER keyword. Anything found so far and not
                    // yet emitted goes out first.
                    if (haveContent && tail > start) {
                        pending.push({
                            delimiter,
                            span: { start, length: tail - start },
                            contentStart: head,
                            state: StatementFinishState.NoDelimiter,
                        });
                        start = tail;
                    }

                    head = tail;
                    tail += 10;
                    let run = tail;

                    // Skip leading spaces and tabs.
                    while (run < end && (sql[run] === " "
                        || sql[run] === "\t")) {
                        ++run;
                    }
                    tail = run;

                    // Forward to the next whitespace on this line.
                    while (run < end && sql[run] !== "\n" && sql[run] !== " "
                        && sql[run] !== "\t") {
                        ++run;
                    }

                    delimiter = sql.substring(tail, run);
                    if (delimiter.length > 0) {
                        pending.push({
                            delimiter,
                            span: { start, length: run - start },
                            contentStart: head,
                            state: StatementFinishState.DelimiterChange,
                        });

                        tail = run;
                        head = tail;
                        start = head;
                        haveContent = false;
                    } else {
                        haveContent = true;
                        head = tail;
                    }
                } else {
                    // Corrected against the original, which advanced the
                    // tail first and so pointed contentStart at the
                    // second character of any word starting with d that
                    // is not DELIMITER - `delete` reported `elete`.
                    // Every other branch marks content before advancing.
                    if (!haveContent) {
                        haveContent = true;
                        head = tail;
                    }
                    ++tail;
                }

                break;
            }

            default: {
                if (!haveContent && sql[tail] > " ") {
                    haveContent = true;
                    head = tail;
                }
                ++tail;

                break;
            }
        }
    };

    while (tail < end) {
        if (!checkDelimiter()) {
            handleStringsCommentsAndDelimiter();
        }

        while (pending.length > 0) {
            yield pending.shift() as IStatementSpan;
        }
    }

    // Whatever is left over after the last delimiter.
    if (head < end) {
        yield {
            span: { start, length: end - start },
            // Below start, to say there is no content.
            contentStart: haveContent ? head : start - 1,
            state: StatementFinishState.NoDelimiter,
        };
    } else if (head > start) {
        // The last statement is nothing but whitespace and comments,
        // which also means haveContent is false.
        yield {
            span: { start, length: end - start },
            contentStart: start - 1,
            state: StatementFinishState.NoDelimiter,
        };
    }
}

/**
 * Splits SQL into statement ranges, all at once.
 *
 * @param sql The SQL to split.
 * @param delimiter The delimiter in force at the start.
 *
 * @returns Every statement range.
 */
export const determineStatementRanges = (
    sql: string,
    delimiter = ";",
): IStatementSpan[] => {
    return [...scanStatements(sql, delimiter)];
};
