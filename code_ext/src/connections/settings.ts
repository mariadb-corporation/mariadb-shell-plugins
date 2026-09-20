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
    };
};
