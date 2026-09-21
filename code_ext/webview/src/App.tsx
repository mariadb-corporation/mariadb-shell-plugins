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
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import {
    blankRow,
    collectChanges,
    initialRows,
    type IEditableRow,
} from "../../src/webview/changes.js";
import type {
    HostMessage,
    IOutputRow,
    IResultSet,
    IViewState,
} from "../../src/webview/protocol.js";
import { createQueryBuilder } from "../../src/sql/resultSetQueryBuilder.js";
import { OutputGrid } from "./OutputGrid.js";
import { ResultGrid } from "./ResultGrid.js";
import { SqlPreview } from "./SqlPreview.js";
import { post } from "./vscodeApi.js";

/** The id of the always-present output tab. */
const OUTPUT_TAB = "output";

/**
 * What to put in the error bar: the last thing that went wrong in the
 * last run.
 *
 * Only the last run is looked at. The output keeps every run, and an
 * error two runs ago is not what the bar is for - it says what just
 * happened.
 *
 * @param output The runs, oldest last.
 *
 * @returns The message, or undefined if the last run was clean.
 */
export const lastErrorOf = (output: IOutputRow[]): string | undefined => {
    const run = output.at(-1);
    if (!run || run.kind !== "error") {
        return undefined;
    }

    // The statement that failed says what the server said; the run's own
    // row only counts them.
    const failed = run.children?.findLast((child) => {
        return child.kind === "error";
    });

    return failed?.message ?? run.summary ?? run.message;
};

/** The editing state held for one result set. */
interface IEditingState {
    rows: IEditableRow[];
    /** True while the SQL preview is shown instead of the grid. */
    previewActive: boolean;
    /** Errors from the last failed apply, by statement position. */
    errors: Record<number, string>;
    /** The row to scroll to, set when a preview line is clicked. */
    selectedRowIndex?: number;
}

/**
 * The result view, docked in the bottom panel.
 *
 * It shows the output of everything run on the selected connection, and
 * a tab per result set of that connection's last execution. The
 * connection is picked from the toolbar, so the results of a connection
 * can be looked at while another one is being worked on.
 *
 * @returns The rendered view.
 */
export const App = (): JSX.Element => {
    const [state, setState] = useState<IViewState | undefined>();
    const [activeTab, setActiveTab] = useState<string>(OUTPUT_TAB);
    const [notice, setNotice] = useState<string | undefined>();
    const [error, setError] = useState<string | undefined>();
    const [editing, setEditing] = useState<Record<string, IEditingState>>({});
    const [scrollToRowId, setScrollToRowId] = useState<string | undefined>();

    useEffect(() => {
        const onMessage = (event: MessageEvent<HostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "state": {
                    setState(message.state);
                    setEditing(Object.fromEntries(
                        message.state.resultSets.map((set) => {
                            return [set.id, {
                                rows: initialRows(set),
                                previewActive: false,
                                errors: {},
                            }];
                        }),
                    ));
                    setNotice(undefined);
                    // A run that produced rows opens on them; one that
                    // did not - or one that has only just started - stays
                    // on the output, which is where its progress and its
                    // outcome are.
                    setActiveTab(message.state.resultSets[0]?.id
                        ?? OUTPUT_TAB);
                    setError(lastErrorOf(message.state.output));
                    break;
                }

                case "applied": {
                    if (message.error !== undefined) {
                        setError(message.error);
                        setNotice(undefined);
                        // The preview is where the failing statement is,
                        // so that is where the user is taken.
                        setEditing((previous) => {
                            const current = previous[message.resultId];
                            if (!current) {
                                return previous;
                            }

                            return {
                                ...previous,
                                [message.resultId]: {
                                    ...current,
                                    previewActive: true,
                                    errors: {
                                        [message.failedIndex ?? 0]:
                                            message.error as string,
                                    },
                                },
                            };
                        });
                    } else {
                        setError(undefined);
                        setNotice(
                            `Applied ${message.statements.length} statement`
                            + `${message.statements.length === 1 ? "" : "s"}.`,
                        );
                        post({ type: "refresh", resultId: message.resultId });
                    }
                    break;
                }
            }
        };

        window.addEventListener("message", onMessage);
        post({ type: "ready" });

        return () => {
            window.removeEventListener("message", onMessage);
        };
    }, []);

    const active: IResultSet | undefined = state?.resultSets.find((set) => {
        return set.id === activeTab;
    });
    const editState = active ? editing[active.id] : undefined;

    const availableResultIds = useMemo(() => {
        return new Set((state?.resultSets ?? []).map((set) => {
            return set.id;
        }));
    }, [state]);

    const statements = useMemo(() => {
        if (!active || !editState) {
            return [];
        }

        const builder = createQueryBuilder(active);
        if (!builder) {
            return [];
        }

        return builder.buildStatements(
            collectChanges(active, editState.rows));
    }, [active, editState]);

    /**
     * Updates the editing state of the active result set.
     *
     * @param update Produces the new state from the old.
     *
     * @returns Nothing.
     */
    const updateState = useCallback((
        update: (current: IEditingState) => IEditingState,
    ): void => {
        setEditing((previous) => {
            const id = activeTab;
            const current = previous[id];
            if (!current) {
                return previous;
            }

            return { ...previous, [id]: update(current) };
        });
    }, [activeTab]);

    const onCellEdited = useCallback((
        rowIndex: number,
        column: string,
        value: unknown,
    ): void => {
        updateState((current) => {
            const rows = [...current.rows];
            const row = rows[rowIndex];
            if (!row) {
                return current;
            }

            rows[rowIndex] = {
                ...row,
                current: { ...row.current, [column]: value },
            };

            return { ...current, rows };
        });
    }, [updateState]);

    const onToggleDeleted = useCallback((rowIndex: number): void => {
        updateState((current) => {
            const rows = [...current.rows];
            const row = rows[rowIndex];
            if (!row) {
                return current;
            }

            rows[rowIndex] = { ...row, deleted: !row.deleted };

            return { ...current, rows };
        });
    }, [updateState]);

    const addRow = useCallback((): void => {
        if (!active) {
            return;
        }

        updateState((current) => {
            return { ...current, rows: [...current.rows, blankRow(active)] };
        });
    }, [active, updateState]);

    const revert = useCallback((): void => {
        if (!active) {
            return;
        }

        updateState((current) => {
            return {
                ...current,
                rows: initialRows(active),
                errors: {},
                previewActive: false,
            };
        });
        setError(undefined);
        setNotice(undefined);
    }, [active, updateState]);

    const apply = useCallback((): void => {
        if (!active || !editState) {
            return;
        }

        post({
            type: "applyChanges",
            resultId: active.id,
            changes: collectChanges(active, editState.rows),
        });
    }, [active, editState]);

    const togglePreview = useCallback((): void => {
        updateState((current) => {
            return { ...current, previewActive: !current.previewActive };
        });
    }, [updateState]);

    const refresh = useCallback((): void => {
        if (!active) {
            return;
        }

        post({ type: "refresh", resultId: active.id });
    }, [active]);

    const goToRow = useCallback((rowIndex: number): void => {
        updateState((current) => {
            return {
                ...current,
                previewActive: false,
                selectedRowIndex: rowIndex,
            };
        });
    }, [updateState]);

    const jumpToResult = useCallback((resultId: string): void => {
        setActiveTab(resultId);
    }, []);

    const goToStatement = useCallback((row: IOutputRow): void => {
        if (row.source) {
            post({ type: "revealStatement", source: row.source });
        }

        // The row of a failed run also opens it and carries the output
        // to the error it is reporting.
        if (row.jumpToRowId !== undefined) {
            setScrollToRowId(row.jumpToRowId);
        }
    }, []);

    if (!state) {
        return (
            <div class="placeholder">
                <p>Run a .sql file to see its results here.</p>
            </div>
        );
    }

    const dirty = statements.length;

    return (
        <div class="panel">
            <section class="content">
                {activeTab === OUTPUT_TAB
                    ? (
                        <OutputGrid
                            rows={state.output}
                            availableResultIds={availableResultIds}
                            onJumpToResult={jumpToResult}
                            onGoToStatement={goToStatement}
                            scrollToRowId={scrollToRowId}
                        />
                    )
                    : active && editState && (editState.previewActive
                        ? (
                            <SqlPreview
                                statements={statements}
                                errors={editState.errors}
                                onStatementClick={goToRow}
                            />
                        )
                        : (
                            <ResultGrid
                                resultSet={active}
                                rows={editState.rows}
                                selectedRowIndex={editState.selectedRowIndex}
                                onCellEdited={onCellEdited}
                                onToggleDeleted={onToggleDeleted}
                                onSelectionChanged={() => {
                                    // Selection is Tabulator's own; the
                                    // app does not track it.
                                }}
                            />
                        ))}
            </section>

            <footer class="statusBar">
                <select
                    class="connectionPicker"
                    title="The connection whose output and results are shown"
                    value={state.connection}
                    onChange={(event) => {
                        post({
                            type: "selectConnection",
                            connection:
                                (event.target as HTMLSelectElement).value,
                        });
                    }}
                >
                    {state.connections.map((uri) => {
                        return <option key={uri} value={uri}>{uri}</option>;
                    })}
                </select>

                <nav class="tabs" role="tablist">
                    <button
                        type="button"
                        role="tab"
                        aria-selected={activeTab === OUTPUT_TAB}
                        class={activeTab === OUTPUT_TAB ? "tab active" : "tab"}
                        onClick={() => {
                            setActiveTab(OUTPUT_TAB);
                        }}
                    >
                        Output
                        {state.output.length > 0
                            && <span class="badge">
                                {state.output.length}
                            </span>}
                    </button>
                    {state.resultSets.map((set) => {
                        return (
                            <button
                                key={set.id}
                                type="button"
                                role="tab"
                                aria-selected={activeTab === set.id}
                                title={set.statement}
                                class={activeTab === set.id
                                    ? "tab active"
                                    : "tab"}
                                onClick={() => {
                                    setActiveTab(set.id);
                                }}
                            >
                                {set.caption}
                            </button>
                        );
                    })}
                </nav>

                <span class="status">
                    {notice ?? (activeTab === OUTPUT_TAB
                        ? ""
                        : active?.status ?? "")}
                </span>

                <div class="toolbar">
                    {active && (
                        <>
                            {active.readOnlyReason !== undefined && (
                                <span
                                    class="readOnly"
                                    title={active.readOnlyReason}
                                >
                                    read only
                                </span>
                            )}
                            {active.editable && editState && (
                                <>
                                    <button
                                        type="button"
                                        class={editState.previewActive
                                            ? "iconButton toggled"
                                            : "iconButton"}
                                        title="Preview the SQL these changes
 would run"
                                        onClick={togglePreview}
                                    >
                                        {editState.previewActive
                                            ? "▦ Grid"
                                            : "≡ Preview SQL"}
                                        {dirty > 0
                                            && !editState.previewActive
                                            && <span class="badge">
                                                {dirty}
                                            </span>}
                                    </button>
                                    <button
                                        type="button"
                                        class="iconButton"
                                        title="Add a new row"
                                        onClick={addRow}
                                    >
                                        + Row
                                    </button>
                                    <button
                                        type="button"
                                        class="iconButton"
                                        title="Discard the pending changes"
                                        disabled={dirty === 0}
                                        onClick={revert}
                                    >
                                        Revert
                                    </button>
                                    <button
                                        type="button"
                                        class="iconButton primary"
                                        title="Run the previewed statements"
                                        disabled={dirty === 0}
                                        onClick={apply}
                                    >
                                        Apply{dirty > 0 ? ` (${dirty})` : ""}
                                    </button>
                                </>
                            )}
                            <button
                                type="button"
                                class="iconButton"
                                title="Run this statement again"
                                onClick={refresh}
                            >
                                Refresh
                            </button>
                        </>
                    )}
                </div>
            </footer>

            {error !== undefined && (
                <div class="errorBar" role="alert">{error}</div>
            )}
        </div>
    );
};
