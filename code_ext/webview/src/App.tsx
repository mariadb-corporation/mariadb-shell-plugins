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
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "preact/hooks";

import {
    blankRow,
    collectChanges,
    initialRows,
    type IEditableRow,
} from "../../src/webview/changes.js";
import type {
    HostMessage,
    IActionRow,
    IResultSet,
    IViewState,
} from "../../src/webview/protocol.js";
import { createQueryBuilder } from "../../src/sql/resultSetQueryBuilder.js";
import { ActionsGrid } from "./ActionsGrid.js";
import { ResultGrid } from "./ResultGrid.js";
import { ResultStatusBar } from "./ResultStatusBar.js";
import { SqlPreview } from "./SqlPreview.js";
import { post } from "./vscodeApi.js";

/** The id of the always-present Actions tab. */
const ACTIONS_TAB = "actions";

/**
 * What to put in the error bar: the last thing that went wrong on the
 * connection.
 *
 * Only the first row is looked at, that being the newest. The actions
 * keep everything, and an error two runs ago is not what the bar is
 * for - it says what just happened.
 *
 * @param actions What happened, newest first.
 *
 * @returns The message, or undefined if the last thing went well.
 */
export const lastErrorOf = (actions: IActionRow[]): string | undefined => {
    const run = actions[0];
    if (!run || run.kind !== "error") {
        return undefined;
    }

    // The statement that failed says what the server said; the run's own
    // row only counts them. An event has no children and says it itself.
    const failed = run.children?.findLast((child) => {
        return child.kind === "error";
    });

    return failed?.message ?? run.summary ?? run.message;
};

/** The value the session picker offers every connection together under. */
const ALL_SESSIONS = "";

/** How much of the strip a page of result tabs moves by. */
const PAGE_FRACTION = 0.8;

/** What the strip of result tabs can be paged to. */
export interface ITabPaging {
    /** True when they do not all fit, which is what the buttons are for. */
    overflowing: boolean;
    atStart: boolean;
    atEnd: boolean;
}

/**
 * Works out which way the strip of result tabs can still be paged.
 *
 * @param strip What the strip measures, as the DOM reports it.
 *
 * @returns Whether it overflows, and whether it is at either end.
 *
 * A pixel of slack throughout: a fractional layout leaves the scrolled
 * width a hair over the visible one, and the end a hair short of it.
 */
export const pagingOf = (strip: {
    scrollLeft: number;
    scrollWidth: number;
    clientWidth: number;
}): ITabPaging => {
    return {
        overflowing: strip.scrollWidth - strip.clientWidth > 1,
        atStart: strip.scrollLeft <= 1,
        atEnd: strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 1,
    };
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
 * It shows everything that has happened on the selected connection, and
 * a tab per result set of its last execution. Two pickers choose what
 * that is: the connection URI, and which of the connections open on it -
 * or all of them together, which is what it opens on and the only case
 * in which the actions name a connection per row.
 *
 * @returns The rendered view.
 */
export const App = (): JSX.Element => {
    const [state, setState] = useState<IViewState | undefined>();
    const [activeTab, setActiveTab] = useState<string>(ACTIONS_TAB);
    const [notice, setNotice] = useState<string | undefined>();
    const [error, setError] = useState<string | undefined>();
    const [editing, setEditing] = useState<Record<string, IEditingState>>({});
    const [scrollToRowId, setScrollToRowId] = useState<string | undefined>();
    /** The strip of result tabs, measured for the paging buttons. */
    const resultTabs = useRef<HTMLDivElement>(null);
    const [paging, setPaging] = useState<ITabPaging>({
        overflowing: false,
        atStart: true,
        atEnd: true,
    });
    /**
     * The tabs the editing state below was built for. State arrives
     * whenever anything at all happens on the connection - a schema
     * listed while the user is part way through editing a grid, say - and
     * only a change of tabs may throw that editing away.
     */
    const shownResults = useRef<string>("");

    useEffect(() => {
        const onMessage = (event: MessageEvent<HostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "state": {
                    setState(message.state);
                    const ids = message.state.resultSets.map((set) => {
                        return set.id;
                    }).join("\u0000");
                    if (ids !== shownResults.current) {
                        shownResults.current = ids;
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
                        // A run that produced rows opens on them; one
                        // that did not - or one that has only just
                        // started - stays on the actions, which is where
                        // its progress and its outcome are.
                        setActiveTab(message.state.resultSets[0]?.id
                            ?? ACTIONS_TAB);
                    }
                    setError(lastErrorOf(message.state.actions));
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

    const measureTabs = useCallback((): void => {
        if (resultTabs.current) {
            setPaging(pagingOf(resultTabs.current));
        }
    }, []);

    // The strip overflows when the tabs outgrow it or the panel is made
    // narrower, so both are watched.
    useEffect(() => {
        const strip = resultTabs.current;
        if (!strip) {
            return;
        }

        measureTabs();
        const observer = new ResizeObserver(measureTabs);
        observer.observe(strip);

        return () => {
            observer.disconnect();
        };
    }, [measureTabs, state?.resultSets]);

    /**
     * Moves the strip of result tabs by most of its width.
     *
     * @param direction -1 for back, 1 for on.
     *
     * @returns Nothing.
     */
    const pageTabs = useCallback((direction: number): void => {
        const strip = resultTabs.current;
        if (!strip) {
            return;
        }

        strip.scrollBy({
            left: direction * strip.clientWidth * PAGE_FRACTION,
            behavior: "smooth",
        });
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

    const goToStatement = useCallback((row: IActionRow): void => {
        if (row.source) {
            post({ type: "revealStatement", source: row.source });
        }

        // The row of a failed run also opens it and carries the actions
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
            {error !== undefined && (
                <div class="errorBar" role="alert">{error}</div>
            )}

            <section class="content">
                {activeTab === ACTIONS_TAB
                    ? (
                        <ActionsGrid
                            rows={state.actions}
                            showConnection={state.session === undefined
                                && state.sessions.length > 0}
                            availableResultIds={availableResultIds}
                            onJumpToResult={jumpToResult}
                            onGoToStatement={goToStatement}
                            scrollToRowId={scrollToRowId}
                        />
                    )
                    : active && editState && (
                        <>
                            {editState.previewActive
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
                                        selectedRowIndex={
                                            editState.selectedRowIndex}
                                        onCellEdited={onCellEdited}
                                        onToggleDeleted={onToggleDeleted}
                                        onSelectionChanged={() => {
                                            // Selection is Tabulator's
                                            // own; the app does not
                                            // track it.
                                        }}
                                    />
                                )}

                            <ResultStatusBar
                                resultSet={active}
                                notice={notice}
                                dirty={dirty}
                                previewActive={editState.previewActive}
                                onTogglePreview={togglePreview}
                                onAddRow={addRow}
                                onRevert={revert}
                                onApply={apply}
                                onRefresh={refresh}
                            />
                        </>
                    )}
            </section>

            <footer class="contentSelectionBar">
                <nav class="tabs" role="tablist">
                    <button
                        type="button"
                        role="tab"
                        aria-selected={activeTab === ACTIONS_TAB}
                        class={activeTab === ACTIONS_TAB
                            ? "tab active"
                            : "tab"}
                        onClick={() => {
                            setActiveTab(ACTIONS_TAB);
                        }}
                    >
                        Actions
                        {state.actions.length > 0
                            && <span class="badge">
                                {state.actions.length}
                            </span>}
                    </button>
                    {paging.overflowing && (
                        <button
                            type="button"
                            class="tabPager codicon codicon-chevron-left"
                            title="Show the result sets before these"
                            aria-label="Previous result sets"
                            disabled={paging.atStart}
                            onClick={() => {
                                pageTabs(-1);
                            }}
                        />
                    )}

                    <div
                        class="resultTabs"
                        ref={resultTabs}
                        onScroll={measureTabs}
                    >
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
                                    ref={(element) => {
                                        // A tab the host just switched
                                        // to may be off the end of the
                                        // strip; this brings it in.
                                        if (element
                                            && activeTab === set.id) {
                                            element.scrollIntoView({
                                                block: "nearest",
                                                inline: "nearest",
                                            });
                                        }
                                    }}
                                    onClick={() => {
                                        setActiveTab(set.id);
                                    }}
                                >
                                    {set.caption}
                                </button>
                            );
                        })}
                    </div>

                    {paging.overflowing && (
                        <button
                            type="button"
                            class="tabPager codicon codicon-chevron-right"
                            title="Show the result sets after these"
                            aria-label="Next result sets"
                            disabled={paging.atEnd}
                            onClick={() => {
                                pageTabs(1);
                            }}
                        />
                    )}
                </nav>

                <select
                    class="connectionPicker"
                    title="The connection whose actions and results are shown"
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

                {state.sessions.length > 0 && (
                    <select
                        class="sessionPicker"
                        title="Which of its open connections to show"
                        value={state.session ?? ALL_SESSIONS}
                        onChange={(event) => {
                            const picked =
                                (event.target as HTMLSelectElement).value;
                            post({
                                type: "selectSession",
                                session: picked === ALL_SESSIONS
                                    ? undefined
                                    : picked,
                            });
                        }}
                    >
                        <option value={ALL_SESSIONS}>All Sessions</option>
                        {state.sessions.map((session) => {
                            return (
                                <option
                                    key={session.label}
                                    value={session.label}
                                >
                                    {session.open
                                        ? session.label
                                        : `${session.label} (closed)`}
                                </option>
                            );
                        })}
                    </select>
                )}
            </footer>
        </div>
    );
};
