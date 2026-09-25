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

import type { ConnectionManager } from "../connections/connectionManager.js";
import {
    stopOnError,
    STOP_ON_ERROR_CONTEXT_KEY,
} from "../connections/settings.js";
import { showErrorWithLog } from "../errorMessages.js";
import {
    captionFor,
    describeRun,
    ExecutionService,
    type IScriptSource,
    pendingRunRow,
} from "../sql/executionService.js";
import { statementAtOffset } from "../sql/statementAtOffset.js";
import type {
    IResultSet,
    IStatementSource,
} from "../webview/protocol.js";
import type { ResultViewProvider } from "../webview/resultViewProvider.js";

/** The language id VS Code gives .sql files. */
export const SQL_LANGUAGE_ID = "sql";

/**
 * The header a generated SQL file carries to record its connection.
 *
 * The in-memory binding is keyed by document URI, and an untitled
 * document's URI changes the moment it is saved, which would silently
 * lose the association. Writing it into the file keeps it across a save,
 * across reopening the file and across sharing it - and leaves it
 * visible, so it can be corrected by editing the line.
 */
export const CONNECTION_HEADER_PREFIX = "-- MariaDB connection:";

/** How far into a file the connection header is looked for. */
const HEADER_SEARCH_LINES = 5;

/**
 * Builds the header line recording a file's connection.
 *
 * @param uri The connection the file runs on.
 *
 * @returns The header line.
 */
export const buildConnectionHeader = (uri: string): string => {
    return `${CONNECTION_HEADER_PREFIX} ${uri}`;
};

/**
 * Reads the connection a file names in its header.
 *
 * @param text The file's contents.
 *
 * @returns The connection URI, or undefined if there is no header.
 */
export const readConnectionHeader = (
    text: string,
): string | undefined => {
    const lines = text.split("\n", HEADER_SEARCH_LINES);
    for (const line of lines) {
        const match = /^\s*--\s*MariaDB connection:\s*(\S.*?)\s*$/i
            .exec(line);
        if (match) {
            return match[1];
        }
    }

    return undefined;
};

/**
 * Binds SQL editors to a connection and runs their contents.
 *
 * Each open .sql document remembers the connection it was last run on;
 * one that has never been run uses the default connection. The choice is
 * shown in the status bar, and can be changed from there or from the
 * editor's toolbar.
 */
export class SqlEditorBinding implements vscode.Disposable {
    /** Document URI -> the connection chosen for it. */
    readonly #chosen = new Map<string, string>();
    /** Document URI -> its own stop-on-error choice, once made. */
    readonly #stopOnError = new Map<string, boolean>();
    /** Counts the runs, so each one's result sets have their own ids. */
    #runCount = 0;
    readonly #statusItem: vscode.StatusBarItem;
    readonly #disposables: vscode.Disposable[] = [];

    public constructor(
        private readonly connections: ConnectionManager,
        private readonly resultView: ResultViewProvider,
        private readonly log: (message: string) => void,
    ) {
        this.#statusItem = vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Right,
            100,
        );
        this.#statusItem.command = "mariadb.selectEditorConnection";
        this.#statusItem.tooltip =
            "The MariaDB connection this SQL file runs on";
        this.#disposables.push(this.#statusItem);

        this.#disposables.push(
            vscode.window.onDidChangeActiveTextEditor(() => {
                this.updateStatusBar();
            }),
            vscode.workspace.onDidCloseTextDocument((document) => {
                this.#chosen.delete(document.uri.toString());
                this.#stopOnError.delete(document.uri.toString());
            }),
        );

        this.#disposables.push({
            dispose: this.connections.onDidChange(() => {
                this.updateStatusBar();
            }),
        });

        this.updateStatusBar();
    }

    /**
     * The connection a document runs on: the one picked for it, else the
     * one its header names, else the default.
     *
     * @param document The document to look up.
     *
     * @returns The connection URI, or undefined if there is no default and
     *          nothing was picked.
     */
    public connectionFor(
        document: vscode.TextDocument,
    ): string | undefined {
        return this.#chosen.get(document.uri.toString())
            ?? readConnectionHeader(document.getText())
            ?? this.connections.defaultConnection;
    }

    /**
     * Whether a failing statement should end a run of this document.
     *
     * The file's own choice once one has been made, and the extension
     * setting until then - so a file starts out doing whatever the
     * setting says and can be switched for the session without changing
     * it for everything else.
     *
     * @param document The document to look up.
     *
     * @returns True to stop at the first failing statement.
     */
    public stopOnErrorFor(document: vscode.TextDocument): boolean {
        return this.#stopOnError.get(document.uri.toString())
            ?? stopOnError();
    }

    /**
     * Flips a document's stop-on-error choice.
     *
     * @param document The document to toggle.
     *
     * @returns What it is now.
     */
    public toggleStopOnError(document: vscode.TextDocument): boolean {
        const next = !this.stopOnErrorFor(document);
        this.#stopOnError.set(document.uri.toString(), next);
        this.updateStatusBar();

        return next;
    }

    /**
     * Pins a document to a connection, without asking.
     *
     * @param document The document to pin.
     * @param uri The connection it should run on.
     *
     * @returns Nothing.
     */
    public bindConnection(
        document: vscode.TextDocument,
        uri: string,
    ): void {
        this.#chosen.set(document.uri.toString(), uri);
        this.updateStatusBar();
    }

    /**
     * Opens a new, unsaved SQL file bound to a connection.
     *
     * @param uri The connection the file should run on.
     *
     * @returns The document that was opened.
     */
    public async openSqlEditor(
        uri: string,
    ): Promise<vscode.TextDocument> {
        const document = await vscode.workspace.openTextDocument({
            language: SQL_LANGUAGE_ID,
            content: `${buildConnectionHeader(uri)}\n\n`,
        });

        // The caret goes to the end of the header, on the blank line
        // below it, so the user can start typing straight away. Set as
        // part of showing the document rather than afterwards, so it is
        // never briefly at the top.
        const caret = document.positionAt(document.getText().length);
        await vscode.window.showTextDocument(document, {
            selection: new vscode.Range(caret, caret),
        });
        this.bindConnection(document, uri);

        return document;
    }

    /**
     * Shows the status bar entry for the active editor, and hides it for
     * anything that is not SQL.
     *
     * @returns Nothing.
     */
    public updateStatusBar(): void {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.languageId !== SQL_LANGUAGE_ID) {
            this.#statusItem.hide();

            return;
        }

        const uri = this.connectionFor(editor.document);
        this.#statusItem.text = uri === undefined
            ? "$(database) MariaDB: no connection"
            : `$(database) ${uri}`;
        this.#statusItem.show();

        // A toolbar button cannot change its own icon, so the two states
        // are two commands and this context key picks which is shown.
        void vscode.commands.executeCommand(
            "setContext",
            STOP_ON_ERROR_CONTEXT_KEY,
            this.stopOnErrorFor(editor.document),
        );
    }

    /**
     * Asks the user which connection a document should run on.
     *
     * @param document The document to choose for.
     *
     * @returns The chosen connection, or undefined if the pick was
     *          cancelled.
     */
    public async selectConnection(
        document: vscode.TextDocument,
    ): Promise<string | undefined> {
        const uris = await this.connections.listConnections();
        if (uris.length === 0) {
            // A notification draws no codicon, so the button is named
            // rather than shown; the view it sits in is what is being
            // pointed at.
            void vscode.window.showWarningMessage(
                "No MariaDB connections are configured. Add one with the "
                + "+ button in the MariaDB Connections view.",
            );

            return undefined;
        }

        const current = this.connectionFor(document);
        const defaultUri = this.connections.defaultConnection;

        const picked = await vscode.window.showQuickPick(
            uris.map((uri) => {
                const tags = [
                    uri === defaultUri ? "default" : "",
                    this.connections.isConnected(uri) ? "connected" : "",
                ].filter(Boolean);

                return {
                    label: uri,
                    description: tags.join(", ") || undefined,
                    picked: uri === current,
                };
            }),
            { title: "Select the MariaDB connection for this SQL file" },
        );

        if (!picked) {
            return undefined;
        }

        this.#chosen.set(document.uri.toString(), picked.label);
        this.updateStatusBar();

        return picked.label;
    }

    /**
     * Runs the active SQL editor's contents, or its selection when there
     * is one, and shows the results in the MariaDB panel.
     *
     * @param editor The editor to run.
     *
     * @returns Nothing.
     */
    public async run(editor: vscode.TextEditor): Promise<void> {
        const wholeFile = editor.selection.isEmpty;
        const script = wholeFile
            ? editor.document.getText()
            : editor.document.getText(editor.selection);
        const baseOffset = wholeFile
            ? 0
            : editor.document.offsetAt(editor.selection.start);

        await this.#runFrom(editor, script, baseOffset,
            wholeFile ? undefined : "the selection");
    }

    /**
     * Runs just the statement the cursor is in.
     *
     * @param editor The editor to run in.
     *
     * @returns Nothing.
     */
    public async runStatementAtCursor(
        editor: vscode.TextEditor,
    ): Promise<void> {
        const offset = editor.document.offsetAt(editor.selection.active);
        const statement = statementAtOffset(
            editor.document.getText(), offset);

        if (!statement) {
            void vscode.window.showInformationMessage(
                "MariaDB: the cursor is not in a statement.",
            );

            return;
        }

        // Selected as well as run, so it is plain which statement went.
        editor.selection = new vscode.Selection(
            editor.document.positionAt(statement.start),
            editor.document.positionAt(statement.end),
        );

        await this.#runFrom(
            editor, statement.text, statement.start, "1 statement");
    }

    /**
     * Runs a piece of a document, remembering where it came from.
     *
     * @param editor The editor it came from.
     * @param script The SQL to run.
     * @param baseOffset Where the script starts in the document.
     * @param label What to call the run in the actions.
     *
     * @returns Nothing.
     */
    async #runFrom(
        editor: vscode.TextEditor,
        script: string,
        baseOffset: number,
        label?: string,
    ): Promise<void> {
        if (script.trim().length === 0) {
            void vscode.window.showInformationMessage(
                "MariaDB: there is nothing to run.",
            );

            return;
        }

        const uri = this.connectionFor(editor.document)
            ?? await this.selectConnection(editor.document);
        if (uri === undefined) {
            return;
        }

        const { document } = editor;
        await this.runScript(uri, script, label, {
            uri: document.uri.toString(),
            positionAt: (offsetInScript: number) => {
                const position = document.positionAt(
                    baseOffset + offsetInScript);

                return {
                    line: position.line,
                    character: position.character,
                };
            },
        }, this.stopOnErrorFor(document));
    }

    /**
     * Puts the cursor on a statement an action row came from.
     *
     * @param source Where the statement is.
     *
     * @returns Nothing.
     */
    public async revealStatement(
        source: IStatementSource,
    ): Promise<void> {
        const document = await vscode.workspace.openTextDocument(
            vscode.Uri.parse(source.uri));
        const position = new vscode.Position(source.line, source.character);

        await vscode.window.showTextDocument(document, {
            selection: new vscode.Range(position, position),
            preserveFocus: false,
        });
    }

    /**
     * Runs a script on a connection and shows what it produced.
     *
     * @param uri The connection to run on.
     * @param script The SQL to run.
     * @param label What to call the run in the actions.
     * @param source Where the script came from, for the jump links.
     *
     * @returns Nothing.
     */
    public async runScript(
        uri: string,
        script: string,
        label?: string,
        source?: IScriptSource,
        stopAtFirstError: boolean = stopOnError(),
    ): Promise<void> {
        // The run is put up before the connection is even opened, which
        // is what the shell may have to be started for, so the actions
        // shows it is under way rather than nothing at all.
        const runId = this.#nextRunId();
        const what = describeRun(script, label);
        // Which connection this will run on has to be known before it is
        // opened, because the row goes up first: the manager answers it
        // from what is open now, and opens that same one below.
        const connectionLabel = this.connections.labelFor(uri);
        const run = pendingRunRow({
            runId,
            connectionUri: uri,
            connectionLabel,
            what,
        });
        await this.resultView.startRun(uri, run);

        try {
            const connectionId = await this.connections.connect(uri);
            const api = await this.connections.api();
            const service = new ExecutionService(api);
            const report = await service.execute({
                connectionUri: uri,
                connectionId,
                connectionLabel,
                script,
                runId,
                source,
                label,
                stopOnError: stopAtFirstError,
            });
            await this.resultView.showResults(report, {
                connectionUri: uri,
                connectionId,
                service,
            });
        } catch (error) {
            const message = error instanceof Error
                ? error.message
                : String(error);
            this.log(`Failed to run the script on ${uri}: ${message}`);
            void showErrorWithLog(message);
            // Shown without an apply context: the failure happened
            // before there was anything editable to write back. It
            // closes off the run that was put up above - same id - so
            // the actions do not keep a run that never ends.
            await this.resultView.showResults({
                connection: uri,
                startedAt: run.time,
                elapsedMs: 0,
                actions: [{
                    ...run,
                    message: `Ran ${what} on ${uri}`,
                    summary: `Execution failed: ${message}`,
                    kind: "error",
                    elapsedMs: 0,
                    children: [{
                        id: `${runId}-error`,
                        time: run.time,
                        connection: uri,
                        connectionLabel,
                        role: "statement",
                        statement: captionFor(script),
                        message,
                        kind: "error",
                    }],
                    jumpToRowId: `${runId}-error`,
                }],
                resultSets: [],
            });
        }
    }

    /**
     * Re-runs the statement behind a result set, on the connection it was
     * produced by.
     *
     * @param resultSet The result set to reload.
     *
     * @returns Nothing.
     */
    public async refreshResultSet(resultSet: IResultSet): Promise<void> {
        const uri = this.resultView.applyContext?.connectionUri;
        if (uri === undefined || resultSet.statement.trim().length === 0) {
            return;
        }

        await this.runScript(
            uri, `${resultSet.statement};`, "1 statement");
    }

    /**
     * @returns An id no earlier run has used.
     */
    #nextRunId(): string {
        this.#runCount += 1;

        return `run${this.#runCount}`;
    }

    /**
     * Drops the status bar entry and the listeners.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
    }
}
