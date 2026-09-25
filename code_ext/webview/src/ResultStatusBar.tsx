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

import type { IEditableRow } from "../../src/webview/changes.js";
import type { IResultSet } from "../../src/webview/protocol.js";
import { ToolbarMenu } from "./ToolbarMenu.js";

/** The two ways a result set can be looked at. */
export type ResultViewStyle = "grid" | "preview";

/** What the action menu can be asked to do. */
export type ResultAction = "close" | "freezeKeys";

interface IResultStatusBarProperties {
    /** The result set the bar belongs to. */
    resultSet: IResultSet;
    /** Its rows as the grid holds them, pending edits and all. */
    rows: IEditableRow[];
    /** What just happened, shown in place of the result's own status. */
    notice?: string;
    /** How many statements the pending changes would run. */
    dirty: number;
    /** True while the SQL preview is shown instead of the grid. */
    previewActive: boolean;
    /** True when the result set is in an editor tab of its own. */
    maximized: boolean;
    /** Whether its primary key columns are frozen. */
    freezeKeys: boolean;
    onSelectView(style: ResultViewStyle): void;
    /** Fetches another page of the rows, from 0. */
    onPage(index: number): void;
    onTogglePreview(): void;
    onEdit(): void;
    onAddRow(): void;
    onRevert(): void;
    onApply(): void;
    onRefresh(): void;
    /** Moves the result set to an editor tab, or back to the panel. */
    onToggleMaximized(): void;
    onAction(action: ResultAction): void;
}

/**
 * @param count How many.
 * @param noun What, in the singular.
 *
 * @returns The two together, the noun made plural where it has to be.
 */
const counted = (count: number, noun: string): string => {
    return `${count} ${noun}${count === 1 ? "" : "s"}`;
};

/**
 * Says what the pending edits of a result set come to, the way the MySQL
 * Shell's result view does.
 *
 * @param rows The grid's rows.
 *
 * @returns The status line, or undefined when nothing is pending.
 */
export const editingStatusOf = (rows: IEditableRow[]): string | undefined => {
    let affected = 0;
    let fields = 0;
    let deleted = 0;
    let added = 0;
    for (const row of rows) {
        const changed = row.added
            ? 0
            : Object.keys(row.current).filter((name) => {
                return !Object.is(row.current[name], row.original[name]);
            }).length;
        if (row.added) {
            added += 1;
        } else if (row.deleted) {
            deleted += 1;
        }
        fields += changed;
        if (row.added || row.deleted || changed > 0) {
            affected += 1;
        }
    }

    if (affected === 0) {
        return undefined;
    }

    let text = `Editing, ${counted(affected, "row")} affected `
        + `(${counted(fields, "field")} changed`;
    if (deleted > 0) {
        text += `, ${counted(deleted, "row")} deleted`;
    }
    if (added > 0) {
        text += `, ${counted(added, "row")} added`;
    }

    return `${text})`;
};

/**
 * The bar along the bottom of one result set, laid out as the MySQL
 * Shell's: what it came to on the left, then the View, Edit and window
 * sections, and the action menu last.
 *
 * It belongs to the result set rather than to the view, which is why it
 * lives inside the tab's own content: the bar the tabs are in has only
 * what picks what is on show, and every button here acts on this result
 * set alone.
 *
 * The Pages section pages through the rows where the server paged them -
 * a SELECT without a LIMIT of its own - and is off for a result set that
 * holds them all. It is off while edits are pending too, since another
 * page would throw them away.
 *
 * @param props The result set and what its buttons do.
 *
 * @returns The rendered bar.
 */
export const ResultStatusBar = (
    props: IResultStatusBarProperties,
): JSX.Element => {
    const { resultSet, rows, notice, dirty, previewActive, maximized } = props;
    const editable = resultSet.editable;
    const pending = dirty > 0;
    const status = notice ?? editingStatusOf(rows) ?? resultSet.status;
    const page = resultSet.page;

    return (
        <footer class="statusBar">
            <span class="status" title={status}>{status}</span>

            <div class="toolbar">
                <span class="toolbarLabel">View:</span>
                <ToolbarMenu
                    title="Select a View Section for the Result Set"
                    label="View"
                    buttonClass={previewActive ? "previewIcon" : "gridIcon"}
                    dropdown
                    selected={previewActive ? "preview" : "grid"}
                    items={[
                        { id: "grid", caption: "Data Grid", icon: "gridIcon" },
                        {
                            id: "preview",
                            caption: "Preview Changes",
                            icon: "previewIcon",
                            disabled: !pending,
                        },
                    ]}
                    onSelect={(id) => {
                        props.onSelectView(id as ResultViewStyle);
                    }}
                />

                <span class="toolbarDivider" />
                <span class="toolbarLabel">Pages:</span>
                <button
                    type="button"
                    class="toolbarIcon pagePreviousIcon"
                    title="Previous Page"
                    aria-label="Previous Page"
                    disabled={!page || page.index === 0 || pending}
                    onClick={() => {
                        if (page) {
                            props.onPage(page.index - 1);
                        }
                    }}
                />
                <button
                    type="button"
                    class="toolbarIcon pageNextIcon"
                    title="Next Page"
                    aria-label="Next Page"
                    disabled={!page?.hasMore || pending}
                    onClick={() => {
                        if (page) {
                            props.onPage(page.index + 1);
                        }
                    }}
                />

                <span class="toolbarDivider" />
                <span class="toolbarLabel">Edit:</span>
                <button
                    type="button"
                    class="toolbarIcon editIcon"
                    title={editable
                        ? "Start Editing"
                        : resultSet.readOnlyReason ?? "Data not editable"}
                    aria-label="Start Editing"
                    disabled={!editable}
                    onClick={props.onEdit}
                />
                <button
                    type="button"
                    class="toolbarIcon addRowIcon"
                    title={editable
                        ? "Add New Row"
                        : resultSet.readOnlyReason ?? "Data not editable"}
                    aria-label="Add New Row"
                    disabled={!editable}
                    onClick={props.onAddRow}
                />
                <button
                    type="button"
                    class={`toolbarIcon previewIcon${previewActive
                        ? " toggled"
                        : ""}`}
                    title="Preview Changes"
                    aria-label="Preview Changes"
                    aria-pressed={previewActive}
                    disabled={!pending && !previewActive}
                    onClick={props.onTogglePreview}
                />
                <button
                    type="button"
                    class="toolbarIcon commitIcon"
                    title="Apply Changes"
                    aria-label="Apply Changes"
                    disabled={!pending}
                    onClick={props.onApply}
                />
                <button
                    type="button"
                    class="toolbarIcon rollbackIcon"
                    title="Rollback Changes"
                    aria-label="Rollback Changes"
                    disabled={!pending}
                    onClick={props.onRevert}
                />
                {/* A reload would throw the pending edits away. */}
                <button
                    type="button"
                    class="toolbarIcon refreshIcon"
                    title="Refresh"
                    aria-label="Refresh"
                    disabled={pending}
                    onClick={props.onRefresh}
                />

                <span class="toolbarDivider" />
                <button
                    type="button"
                    class={`toolbarIcon ${maximized
                        ? "minimizeIcon"
                        : "maximizeIcon"}`}
                    title={maximized
                        ? "Put this result set back in the MariaDB panel"
                        : "Open this result set in an editor tab"}
                    aria-label={maximized ? "Minimize" : "Maximize"}
                    onClick={props.onToggleMaximized}
                />

                <span class="toolbarDivider" />
                <ToolbarMenu
                    title="Show Action Menu"
                    label="Show Action Menu"
                    buttonClass="menuIcon"
                    items={[
                        {
                            id: "freezeKeys",
                            caption: "Freeze Primary Key Columns",
                            checked: props.freezeKeys,
                            // Known only where the table was looked up.
                            disabled: !resultSet.columns.some((column) => {
                                return column.isPrimary;
                            }),
                        },
                        { id: "separator" },
                        { id: "close", caption: "Close Result Set" },
                    ]}
                    onSelect={(id) => {
                        props.onAction(id as ResultAction);
                    }}
                />
            </div>
        </footer>
    );
};
