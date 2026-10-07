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

import * as vscode from "vscode";

import {
    hasContent,
    scanStatements,
    StatementFinishState,
} from "../sql/statementSpans.js";
import { SQL_LANGUAGE_ID } from "./sqlEditorBinding.js";

/** How long to scan before handing control back to the editor. */
const DEFAULT_SLICE_MS = 8;

/** How often, at most, to push a growing set of dots to the editor. */
const DEFAULT_APPLY_INTERVAL_MS = 150;

/** How long to wait after a keystroke before scanning again. */
const DEFAULT_DEBOUNCE_MS = 300;

export interface IStatementDecoratorOptions {
    sliceMs?: number;
    applyIntervalMs?: number;
    debounceMs?: number;
    /** How a scan hands control back between slices. */
    yieldToHost?: () => Promise<void>;
}

/**
 * Hands control back to the event loop.
 *
 * A macrotask, not a microtask: a resolved promise would be drained
 * before the editor gets a chance to paint, which is the whole point of
 * pausing.
 *
 * @returns A promise that settles on the next tick.
 */
const nextTick = (): Promise<void> => {
    return new Promise((resolve) => {
        setTimeout(resolve, 0);
    });
};

/**
 * Marks the first line of every statement in a SQL editor with a dot in
 * the gutter.
 *
 * The scan is sliced: it runs for a few milliseconds, shows what it has
 * found, hands control back to the editor and carries on. On a large file
 * the first dots are therefore on screen long before the last statement
 * has been reached, and typing stays responsive throughout.
 *
 * The ranges come from the SQL scanner for now. They are the same thing a
 * language server would report, so this is the seam to replace when one
 * exists.
 */
export class StatementDecorator implements vscode.Disposable {
    readonly #decoration: vscode.TextEditorDecorationType;
    readonly #disposables: vscode.Disposable[] = [];
    /**
     * Document URI -> the scan running for it, so it can be cancelled,
     * and which version of the text it is scanning.
     */
    readonly #running = new Map<string, {
        cancelled: boolean;
        version: number;
        /** What it has found so far, for an editor that opens meanwhile. */
        ranges: vscode.Range[];
    }>();
    /**
     * Document URI -> what the last finished scan found, and in which
     * version of the text. A document shown in two editors, or shown
     * again, is decorated from here rather than scanned again.
     */
    readonly #scanned = new Map<string, {
        version: number;
        ranges: vscode.Range[];
    }>();
    /** Document URI -> the pending rescan after an edit. */
    readonly #debounced = new Map<string, ReturnType<typeof setTimeout>>();

    readonly #sliceMs: number;
    readonly #applyIntervalMs: number;
    readonly #debounceMs: number;
    readonly #yieldToHost: () => Promise<void>;

    public constructor(
        extensionUri: vscode.Uri,
        options: IStatementDecoratorOptions = {},
    ) {
        this.#sliceMs = options.sliceMs ?? DEFAULT_SLICE_MS;
        this.#applyIntervalMs = options.applyIntervalMs
            ?? DEFAULT_APPLY_INTERVAL_MS;
        this.#debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;
        this.#yieldToHost = options.yieldToHost ?? nextTick;

        this.#decoration = vscode.window.createTextEditorDecorationType({
            gutterIconSize: "contain",
            light: {
                gutterIconPath: vscode.Uri.joinPath(
                    extensionUri, "images", "light", "statementStart.svg"),
            },
            dark: {
                gutterIconPath: vscode.Uri.joinPath(
                    extensionUri, "images", "dark", "statementStart.svg"),
            },
        });

        this.#disposables.push(
            this.#decoration,
            vscode.window.onDidChangeVisibleTextEditors(() => {
                this.refresh();
            }),
            vscode.workspace.onDidChangeTextDocument((event) => {
                this.#scheduleRescan(event.document);
            }),
            vscode.workspace.onDidCloseTextDocument((document) => {
                const key = document.uri.toString();
                this.#cancel(key);
                this.#scanned.delete(key);
            }),
        );

        this.refresh();
    }

    /**
     * Decorates every visible SQL editor: from the last scan where the
     * text has not changed since, by a new one otherwise.
     *
     * @returns Nothing.
     */
    public refresh(): void {
        for (const editor of vscode.window.visibleTextEditors) {
            if (editor.document.languageId === SQL_LANGUAGE_ID) {
                void this.decorate(editor);
            }
        }
    }

    /**
     * Scans one editor's document and marks its statement starts,
     * showing them as they are found.
     *
     * The scan is of the document, not of the editor: every editor
     * showing it gets the dots, and one whose text was scanned already -
     * or is being scanned - is not scanned again.
     *
     * @param editor The editor to decorate.
     *
     * @returns Nothing, once the whole document has been scanned.
     */
    public async decorate(editor: vscode.TextEditor): Promise<void> {
        const { document } = editor;
        const key = document.uri.toString();
        const version = document.version;

        const scanned = this.#scanned.get(key);
        if (scanned?.version === version) {
            editor.setDecorations(this.#decoration, scanned.ranges);

            return;
        }

        // A scan of this very text is under way: this editor gets what it
        // has found so far now, and the rest along with the others as the
        // scan goes on.
        const running = this.#running.get(key);
        if (running?.version === version) {
            editor.setDecorations(this.#decoration, [...running.ranges]);

            return;
        }
        this.#cancel(key);

        const ranges: vscode.Range[] = [];
        const token = { cancelled: false, version, ranges };
        this.#running.set(key, token);

        const text = document.getText();

        let sliceStart = Date.now();
        let lastApply = 0;

        /**
         * @returns True if this scan has been superseded.
         */
        const stale = (): boolean => {
            return token.cancelled || document.version !== version;
        };

        /**
         * Shows what has been found so far in every editor on the
         * document - this one, and whichever others are open on it now.
         *
         * @returns Nothing.
         */
        const apply = (): void => {
            editor.setDecorations(this.#decoration, [...ranges]);
            for (const other of vscode.window.visibleTextEditors) {
                if (other !== editor
                    && other.document.uri.toString() === key) {
                    other.setDecorations(this.#decoration, [...ranges]);
                }
            }
        };

        try {
            for (const span of scanStatements(text)) {
                if (stale()) {
                    return;
                }

                // A run of comments is not a statement and gets no dot.
                if (!hasContent(span)) {
                    continue;
                }

                // Nor is a DELIMITER command: it is a client side
                // directive the server consumes without executing, so it
                // produces no result either. Skipping it keeps the dots
                // matching the statements the result tabs come from.
                if (span.state === StatementFinishState.DelimiterChange) {
                    continue;
                }

                const line = document.positionAt(span.contentStart).line;
                ranges.push(new vscode.Range(line, 0, line, 0));

                const now = Date.now();
                if (now - sliceStart < this.#sliceMs) {
                    continue;
                }

                // The first batch always goes out, so something appears
                // at once; after that the editor is spared a full
                // re-apply on every slice.
                if (lastApply === 0
                    || now - lastApply >= this.#applyIntervalMs) {
                    apply();
                    lastApply = now;
                }

                await this.#yieldToHost();
                if (stale()) {
                    return;
                }
                sliceStart = Date.now();
            }

            this.#scanned.set(key, { version, ranges });
            apply();
        } finally {
            if (this.#running.get(key) === token) {
                this.#running.delete(key);
            }
        }
    }

    /**
     * Stops the scans and drops the decoration type.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        for (const token of this.#running.values()) {
            token.cancelled = true;
        }
        this.#running.clear();

        for (const timer of this.#debounced.values()) {
            clearTimeout(timer);
        }
        this.#debounced.clear();
        this.#scanned.clear();

        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
    }

    /**
     * Rescans a document a moment after it stops changing.
     *
     * @param document The document that changed.
     *
     * @returns Nothing.
     */
    #scheduleRescan(document: vscode.TextDocument): void {
        if (document.languageId !== SQL_LANGUAGE_ID) {
            return;
        }

        const key = document.uri.toString();
        // An event that changed no text - the dirty flag flipping on a
        // save, say - leaves the last scan good, or the one running on
        // course, and nothing to redo.
        if (this.#scanned.get(key)?.version === document.version
            || this.#running.get(key)?.version === document.version) {
            return;
        }

        // The scan in flight is for text that no longer exists.
        this.#cancel(key);

        const existing = this.#debounced.get(key);
        if (existing !== undefined) {
            clearTimeout(existing);
        }

        this.#debounced.set(key, setTimeout(() => {
            this.#debounced.delete(key);
            for (const editor of vscode.window.visibleTextEditors) {
                if (editor.document.uri.toString() === key) {
                    void this.decorate(editor);
                }
            }
        }, this.#debounceMs));
    }

    /**
     * Stops the scan running for a document, if there is one.
     *
     * @param key The document's URI, as a string.
     *
     * @returns Nothing.
     */
    #cancel(key: string): void {
        const token = this.#running.get(key);
        if (token) {
            token.cancelled = true;
            this.#running.delete(key);
        }
    }
}
