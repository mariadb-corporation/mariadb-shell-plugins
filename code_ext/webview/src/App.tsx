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
    IStatementSource,
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

/** One error the bar shows, and what it takes the user to. */
export interface IRunError {
    /** What the server said about it. */
    message: string;
    /** The action row it is, so the grid can be carried to it. */
    rowId?: string;
    /** Where in the file it is, when there was an editor behind it. */
    source?: IStatementSource;
}

/**
 * What to put in the error bar: everything that went wrong in the last
 * thing the connection did, in the order the script hit them.
 *
 * Only the newest thing is reported. The actions keep everything, and an
 * error two runs ago is not what the bar is for - it says what just
 * happened. A run that worked therefore clears it.
 *
 * What "newest" means is **not** simply the first row. A run's row goes
 * up before the connection it needs has been opened, and opening one is
 * itself logged - so on the first execution on a connection an event
 * sits in front of the run that failed. Everything that is neither a run
 * nor a failure of its own is stepped over to find it.
 *
 * @param actions What happened, newest first.
 *
 * @returns One entry per failure, empty if the last thing went well.
 */
export const errorsOf = (actions: IActionRow[]): IRunError[] => {
    const newest = actions.find((row) => {
        return row.role === "run" || row.kind === "error";
    });
    if (!newest || newest.kind !== "error") {
        return [];
    }

    // An event has no children and no summary: it says it itself.
    if (newest.role !== "run") {
        return [{
            message: newest.message,
            rowId: newest.id,
            source: newest.source,
        }];
    }

    // The statements that failed say what the server said; the run's own
    // row only counts them. They are already in the order they ran, which
    // is the order they are worth being walked in.
    const failed = (newest.children ?? []).filter((child) => {
        return child.kind === "error";
    });
    if (failed.length === 0) {
        return [{
            message: newest.summary ?? newest.message,
            rowId: newest.id,
            source: newest.source,
        }];
    }

    return failed.map((child) => {
        return {
            message: child.message,
            rowId: child.id,
            source: child.source,
        };
    });
};

/**
 * What tells one set of errors from another, so that state arriving
 * while the user is part way through the set does not put them back at
 * the first one. State arrives whenever anything at all happens on the
 * connection, not only when a run finishes.
 *
 * @param errors The errors now being shown.
 *
 * @returns A key that changes only when the errors do.
 */
const errorKey = (errors: IRunError[]): string => {
    return errors.map((error) => {
        return `${error.rowId ?? ""}\u0001${error.message}`;
    }).join("\u0000");
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
    /** The failures of the last thing that ran, in the order it hit them. */
    const [errors, setErrors] = useState<IRunError[]>([]);
    /** Which of them the bar is on. It opens on the first. */
    const [errorIndex, setErrorIndex] = useState(0);
    /** Set by the bar's close button, cleared by the next set of errors. */
    const [errorsHidden, setErrorsHidden] = useState(false);
    /** The set the three above were last put in place for. */
    const shownErrors = useRef<string>("");

    /**
     * Puts a set of errors in the bar, at the first of them.
     *
     * The same set arriving again leaves the bar exactly as it is. State
     * arrives whenever anything at all happens on the connection - a
     * schema listed while the user is part way through the errors - and
     * that must not put them back at the first one, nor bring back a bar
     * they have closed.
     *
     * @param found The errors to show, empty to take the bar down.
     *
     * @returns Nothing.
     */
    const showErrors = useCallback((found: IRunError[]): void => {
        const key = errorKey(found);
        if (key === shownErrors.current) {
            return;
        }

        shownErrors.current = key;
        setErrors(found);
        setErrorIndex(0);
        setErrorsHidden(false);
    }, []);
    const [editing, setEditing] = useState<Record<string, IEditingState>>({});
    const [scrollToRowId, setScrollToRowId] = useState<string | undefined>();
    /** The strip of result tabs, measured for the paging buttons. */
    const resultTabs = useRef<HTMLDivElement>(null);
    /** Every result tab now rendered, by id, so one can be scrolled to. */
    const tabElements = useRef(new Map<string, HTMLButtonElement>());
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
                    showErrors(errorsOf(message.state.actions));
                    break;
                }

                case "applied": {
                    if (message.error !== undefined) {
                        // An apply failure is one error with no row of
                        // its own: it did not come from a run.
                        showErrors([{ message: message.error }]);
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
                        showErrors([]);
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
        if (!resultTabs.current) {
            return;
        }

        // Scrolling fires this at every frame of a page, so what is
        // measured is kept when it has not changed: a fresh object
        // every frame is a render every frame, for nothing.
        const measured = pagingOf(resultTabs.current);
        setPaging((previous) => {
            return previous.overflowing === measured.overflowing
                && previous.atStart === measured.atStart
                && previous.atEnd === measured.atEnd
                ? previous
                : measured;
        });
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

    // A tab the host has just switched to may be off the end of the
    // strip, so it is brought in - but only when it changes. Doing it
    // on every render would undo a page the moment the tab it started
    // from scrolled out of sight.
    useEffect(() => {
        tabElements.current.get(activeTab)?.scrollIntoView({
            block: "nearest",
            inline: "nearest",
        });
    }, [activeTab]);

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
        showErrors([]);
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

    /**
     * Moves the error bar onto another of its errors, and takes the
     * user to it.
     *
     * Stepping through errors is for fixing them, so a step is the same
     * thing the arrow on the row does rather than only a change of text:
     * the cursor goes to the statement, and the actions open on the row
     * that reported it.
     *
     * @param index Which error to move to.
     *
     * @returns Nothing.
     */
    const goToError = useCallback((index: number): void => {
        const error = errors[index];
        if (!error) {
            return;
        }

        setErrorIndex(index);
        if (error.source) {
            post({ type: "revealStatement", source: error.source });
        }
        if (error.rowId !== undefined) {
            // The row is no use behind a result tab.
            setActiveTab(ACTIONS_TAB);
            setScrollToRowId(error.rowId);
        }
    }, [errors]);

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
            {!errorsHidden && errors[errorIndex] !== undefined && (
                <div class="errorBar" role="alert">
                    {/*
                      * The message itself is the way to the statement
                      * that caused it - the thing the reader wants the
                      * moment they have read it - so it is a button
                      * rather than text with an arrow beside it.
                      */}
                    <button
                        type="button"
                        class="errorBarText"
                        title="Go to the statement this came from"
                        onClick={() => {
                            goToError(errorIndex);
                        }}
                    >
                        {errors[errorIndex].message}
                    </button>
                    {errors.length > 1 && (
                        <div class="errorBarNav">
                            <button
                                type="button"
                                class="errorBarStep codicon
                                    codicon-chevron-left"
                                title="The error before this one"
                                aria-label="Previous error"
                                disabled={errorIndex === 0}
                                onClick={() => {
                                    goToError(errorIndex - 1);
                                }}
                            />
                            <span class="errorBarCount">
                                {errorIndex + 1} of {errors.length}
                            </span>
                            <button
                                type="button"
                                class="errorBarStep codicon
                                    codicon-chevron-right"
                                title="The error after this one"
                                aria-label="Next error"
                                disabled={errorIndex === errors.length - 1}
                                onClick={() => {
                                    goToError(errorIndex + 1);
                                }}
                            />
                        </div>
                    )}
                    <button
                        type="button"
                        class="errorBarClose codicon codicon-close"
                        title="Hide these errors"
                        aria-label="Hide these errors"
                        onClick={() => {
                            setErrorsHidden(true);
                        }}
                    />
                </div>
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
                            onCopyText={(text) => {
                                post({ type: "copyToClipboard", text });
                            }}
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
                                        if (element) {
                                            tabElements.current.set(
                                                set.id, element);
                                        } else {
                                            tabElements.current.delete(
                                                set.id);
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
