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

import type { JSX } from "preact";

import type { IGeneratedStatement } from "../../src/webview/protocol.js";

interface ISqlPreviewProperties {
    /** The statements the pending changes would run. */
    statements: IGeneratedStatement[];
    /** Errors reported for a statement, by its position in the list. */
    errors?: Record<number, string>;
    /** Takes the user back to the grid row a statement came from. */
    onStatementClick(rowIndex: number): void;
}

/**
 * Shows the SQL the pending changes would run, before running it.
 *
 * The statements come from the same builder the extension executes with,
 * so what is shown here is what will be sent. Clicking one goes back to
 * the grid row it was generated from, which is how an error is traced to
 * the row that caused it.
 *
 * @param props The statements to show.
 *
 * @returns The rendered preview.
 */
export const SqlPreview = (props: ISqlPreviewProperties): JSX.Element => {
    const { statements, errors = {}, onStatementClick } = props;

    if (statements.length === 0) {
        return (
            <div class="sqlPreview">
                <p class="empty">No changes to preview.</p>
            </div>
        );
    }

    return (
        <div class="sqlPreview">
            {statements.map((statement, index) => {
                const error = errors[index];

                return (
                    <div
                        key={`${statement.rowIndex}-${index}`}
                        class={error === undefined
                            ? "previewItem"
                            : "previewItem failed"}
                    >
                        <button
                            type="button"
                            class="previewStatement"
                            title={`Go to row ${statement.rowIndex + 1}`}
                            onClick={() => {
                                onStatementClick(statement.rowIndex);
                            }}
                        >
                            <span class="previewRow">
                                {statement.rowIndex + 1}
                            </span>
                            <code>{`${statement.sql};`}</code>
                        </button>
                        {error !== undefined
                            && <p class="previewError">{error}</p>}
                    </div>
                );
            })}
        </div>
    );
};
