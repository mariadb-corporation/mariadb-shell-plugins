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
 * Colours `"..."` as an identifier in a document whose connection has
 * ANSI_QUOTES.
 *
 * The grammar reads `"..."` as a string, as the default `sql_mode` does.
 * Under ANSI_QUOTES the server reads it as an identifier, and a grammar
 * cannot know the connection - so this semantic tokens provider reports
 * those spans as `sqlQuotedIdentifier`, which `package.json` maps to the
 * grammar's own scope for a quoted identifier, and the editor draws them
 * over what the grammar said. The document keeps its one language
 * throughout: changing it would close and reopen the document as far as
 * every extension is concerned.
 */

import * as vscode from "vscode";

import type { ConnectionManager } from "../connections/connectionManager.js";
import { ANSI_QUOTES_SETTING, ansiQuotes, CONFIG_SECTION } from
    "../connections/settings.js";
import type { SqlModes } from "../connections/sqlModes.js";
import { doubleQuotedSpans } from "../sql/doubleQuotedSpans.js";
import { SQL_LANGUAGE_IDS } from "./sqlLanguage.js";

/** The one token type: a `"..."` the server reads as an identifier. */
export const ANSI_QUOTES_LEGEND = new vscode.SemanticTokensLegend(
    ["sqlQuotedIdentifier"]);

/**
 * Provides the double-quoted identifiers of SQL documents whose
 * connection has ANSI_QUOTES, and nothing for the others.
 */
export class AnsiQuotesHighlighter implements
    vscode.DocumentSemanticTokensProvider, vscode.Disposable {
    readonly #changed = new vscode.EventEmitter<void>();
    readonly #disposables: vscode.Disposable[] = [];

    /** Asks the editor to request the tokens of every document again. */
    public readonly onDidChangeSemanticTokens = this.#changed.event;

    /**
     * @param connections The open connections: one opening or closing
     *                    may change what a document is read as.
     * @param sqlModes What each connection's sql_mode says.
     * @param connectionFor Which connection a document runs on.
     */
    public constructor(
        connections: ConnectionManager,
        private readonly sqlModes: SqlModes,
        private readonly connectionFor:
            (document: vscode.TextDocument) => string | undefined,
    ) {
        this.#disposables.push(
            this.#changed,
            vscode.languages.registerDocumentSemanticTokensProvider(
                SQL_LANGUAGE_IDS.map((language) => {
                    return { language };
                }),
                this,
                ANSI_QUOTES_LEGEND,
            ),
            vscode.workspace.onDidChangeConfiguration((event) => {
                if (event.affectsConfiguration(
                    `${CONFIG_SECTION}.${ANSI_QUOTES_SETTING}`)) {
                    this.refresh();
                }
            }),
            {
                dispose: connections.onDidChange(() => {
                    this.refresh();
                }),
            },
            {
                // A run that changed the session's sql_mode, say.
                dispose: sqlModes.onDidChange(() => {
                    this.refresh();
                }),
            },
        );
    }

    /**
     * Has the editor ask for every document's tokens again: after a
     * document was bound to another connection, say.
     *
     * @returns Nothing.
     */
    public refresh(): void {
        this.#changed.fire();
    }

    /**
     * Whether `"..."` quotes an identifier in a document: as the setting
     * says when it is not `auto`, else as its connection's `sql_mode`
     * says once that connection is open, else not.
     *
     * @param document The document.
     *
     * @returns True under ANSI_QUOTES.
     */
    public async isAnsiQuotes(
        document: vscode.TextDocument,
    ): Promise<boolean> {
        const setting = ansiQuotes();
        if (setting !== "auto") {
            return setting === "on";
        }
        const uri = this.connectionFor(document);

        return uri === undefined ? false : this.sqlModes.ansiQuotes(uri);
    }

    /**
     * The double-quoted identifiers of a document, one token per line
     * they cover, since a semantic token cannot span lines.
     *
     * @param document The document.
     * @param token Cancelled when the document changed again.
     *
     * @returns The tokens; none unless the document is under ANSI_QUOTES.
     */
    public async provideDocumentSemanticTokens(
        document: vscode.TextDocument,
        token: vscode.CancellationToken,
    ): Promise<vscode.SemanticTokens> {
        const builder = new vscode.SemanticTokensBuilder(ANSI_QUOTES_LEGEND);
        if (!await this.isAnsiQuotes(document)) {
            return builder.build();
        }

        for (const span of doubleQuotedSpans(document.getText())) {
            if (token.isCancellationRequested) {
                break;
            }
            const from = document.positionAt(span.start);
            const to = document.positionAt(span.start + span.length);
            for (let line = from.line; line <= to.line; line += 1) {
                const start = line === from.line ? from.character : 0;
                const end = line === to.line
                    ? to.character
                    : document.lineAt(line).text.length;
                if (end > start) {
                    builder.push(line, start, end - start, 0, 0);
                }
            }
        }

        return builder.build();
    }

    public dispose(): void {
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
    }
}
