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

import type { IConnectionSettings } from "./connectionManager.js";

/** The configuration section the extension contributes. */
export const CONFIG_SECTION = "mariadb";

/** The setting holding the default connection's URI. */
export const DEFAULT_CONNECTION_SETTING = "defaultConnection";

/** The setting deciding whether a failing statement ends a script. */
export const STOP_ON_ERROR_SETTING = "execute.stopOnError";

/** The setting deciding how many rows a result set shows at a time. */
export const PAGE_SIZE_SETTING = "execute.pageSize";

/** The setting deciding whether primary key columns are frozen. */
export const FREEZE_KEYS_SETTING = "resultSet.freezePrimaryKeyColumns";

/** The page size a setting that is missing or no count falls back to. */
export const DEFAULT_PAGE_SIZE = 200;

/** The setting deciding how a connection in the tree is opened. */
export const CONNECT_MODE_SETTING = "connections.connectMode";

/** The setting that logs every MCP call, under General Actions. */
export const LOG_ALL_CALLS_SETTING = "actions.logAllCalls";

/**
 * The context key saying that connecting is implicit.
 *
 * The menus need it to drop the Connect button from a connection row,
 * which in that mode duplicates what expanding the row already does.
 */
export const CONNECT_ON_OPEN_CONTEXT_KEY = "mariadb.connectOnOpen";

/**
 * The context key holding the active SQL file's stop-on-error state.
 *
 * A toolbar button cannot change its own icon, so the two states are two
 * commands and this key decides which of them the toolbar shows.
 */
export const STOP_ON_ERROR_CONTEXT_KEY = "mariadb.stopOnError";

/**
 * Whether a failing statement should end the script it is part of.
 *
 * @returns True to stop at the first failure, the default.
 */
export const stopOnError = (): boolean => {
    return vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<boolean>(STOP_ON_ERROR_SETTING) ?? true;
};

/**
 * How many rows of a result set are fetched and shown at a time.
 *
 * @returns The page size: a whole number of rows, at least one.
 */
export const pageSize = (): number => {
    const value = vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<number>(PAGE_SIZE_SETTING);

    // settings.json can hold anything; what is no count of rows is not
    // worth a failed run over.
    return typeof value === "number" && Number.isInteger(value) && value >= 1
        ? value
        : DEFAULT_PAGE_SIZE;
};

/**
 * Whether a result set's primary key columns start out frozen.
 *
 * @returns True to freeze them, the default.
 */
export const freezePrimaryKeyColumns = (): boolean => {
    return vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<boolean>(FREEZE_KEYS_SETTING) ?? true;
};

/**
 * Whether expanding a connection in the tree should open it.
 *
 * Anything but the explicit mode means yes, so a setting written by hand
 * with a value nobody knows still lands on the documented default.
 *
 * @returns True to connect on open, the default.
 */
export const connectOnOpen = (): boolean => {
    return vscode.workspace
        .getConfiguration(CONFIG_SECTION)
        .get<string>(CONNECT_MODE_SETTING) !== "explicit";
};

/**
 * Publishes the connect mode to the context key the menus read.
 *
 * @returns Nothing.
 */
export const publishConnectMode = async (): Promise<void> => {
    await vscode.commands.executeCommand(
        "setContext",
        CONNECT_ON_OPEN_CONTEXT_KEY,
        connectOnOpen(),
    );
};

/**
 * The default connection, stored in the extension's settings.
 *
 * It is written to the workspace where there is one, so a project can
 * default to its own database, and to the user settings otherwise.
 *
 * @returns Settings backed by the VS Code configuration.
 */
export const createWorkspaceSettings = (): IConnectionSettings => {
    return {
        getDefaultConnection: (): string | undefined => {
            const value = vscode.workspace
                .getConfiguration(CONFIG_SECTION)
                .get<string>(DEFAULT_CONNECTION_SETTING);

            return value && value.length > 0 ? value : undefined;
        },

        setDefaultConnection: async (
            uri: string | undefined,
        ): Promise<void> => {
            const target = vscode.workspace.workspaceFolders?.length
                ? vscode.ConfigurationTarget.Workspace
                : vscode.ConfigurationTarget.Global;

            await vscode.workspace
                .getConfiguration(CONFIG_SECTION)
                .update(DEFAULT_CONNECTION_SETTING, uri, target);
        },

        logAllCalls: (): boolean => {
            return vscode.workspace
                .getConfiguration(CONFIG_SECTION)
                .get<boolean>(LOG_ALL_CALLS_SETTING, false);
        },
    };
};
