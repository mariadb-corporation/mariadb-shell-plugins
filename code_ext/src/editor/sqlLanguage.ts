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

/** Which documents are SQL to the extension. */

import type * as vscode from "vscode";

/** The language of a `.sql` file, and of every SQL editor opened here. */
export const MARIADB_SQL_LANGUAGE_ID = "mariadb-sql";

/**
 * Every language the run commands, the statement markers and the
 * highlighting serve: the extension's own, and VS Code's, which a user
 * may have set a file to by hand.
 */
export const SQL_LANGUAGE_IDS: readonly string[] = [
    MARIADB_SQL_LANGUAGE_ID,
    "sql",
];

/**
 * Whether a document is SQL to the extension.
 *
 * @param document The document.
 *
 * @returns True for both SQL languages.
 */
export const isSqlDocument = (document: vscode.TextDocument): boolean => {
    return SQL_LANGUAGE_IDS.includes(document.languageId);
};
