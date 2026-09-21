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

import type { IResultSet } from "../../src/webview/protocol.js";

interface IResultStatusBarProperties {
    /** The result set the bar belongs to. */
    resultSet: IResultSet;
    /** What just happened, shown in place of the result's own status. */
    notice?: string;
    /** How many statements the pending changes would run. */
    dirty: number;
    /** True while the SQL preview is shown instead of the grid. */
    previewActive: boolean;
    onTogglePreview(): void;
    onAddRow(): void;
    onRevert(): void;
    onApply(): void;
    onRefresh(): void;
}

/**
 * The bar along the bottom of one result set: what it came to on the
 * left, and what can be done with it on the right.
 *
 * It belongs to the result set rather than to the view, which is why it
 * lives inside the tab's own content: the bar the tabs are in has only
 * what picks what is on show, and every button here acts on this result
 * set alone.
 *
 * @param props The result set and what its buttons do.
 *
 * @returns The rendered bar.
 */
export const ResultStatusBar = (
    props: IResultStatusBarProperties,
): JSX.Element => {
    const { resultSet, notice, dirty, previewActive } = props;
    const editable = resultSet.editable;

    return (
        <footer class="statusBar">
            <span class="status">{notice ?? resultSet.status}</span>

            <div class="toolbar">
                {resultSet.readOnlyReason !== undefined && (
                    <span class="readOnly" title={resultSet.readOnlyReason}>
                        read only
                    </span>
                )}
                {editable && (
                    <>
                        <button
                            type="button"
                            class={previewActive
                                ? "iconButton toggled"
                                : "iconButton"}
                            title="Preview the SQL these changes would run"
                            onClick={props.onTogglePreview}
                        >
                            {previewActive ? "▦ Grid" : "≡ Preview SQL"}
                            {dirty > 0 && !previewActive && (
                                <span class="badge">{dirty}</span>
                            )}
                        </button>
                        <button
                            type="button"
                            class="iconButton"
                            title="Add a new row"
                            onClick={props.onAddRow}
                        >
                            + Row
                        </button>
                        <button
                            type="button"
                            class="iconButton"
                            title="Discard the pending changes"
                            disabled={dirty === 0}
                            onClick={props.onRevert}
                        >
                            Revert
                        </button>
                        <button
                            type="button"
                            class="iconButton primary"
                            title="Run the previewed statements"
                            disabled={dirty === 0}
                            onClick={props.onApply}
                        >
                            Apply{dirty > 0 ? ` (${dirty})` : ""}
                        </button>
                    </>
                )}
                <button
                    type="button"
                    class="iconButton"
                    title="Run this statement again"
                    onClick={props.onRefresh}
                >
                    Refresh
                </button>
            </div>
        </footer>
    );
};
