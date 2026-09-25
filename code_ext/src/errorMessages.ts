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

/** The button that opens the MariaDB output channel. */
export const SHOW_LOG_ACTION = "Show Log";

/** The command behind that button. */
export const SHOW_LOG_COMMAND = "mariadb.showMcpServerLog";

/**
 * Shows an error notification that can open the log.
 *
 * A notification has room for one sentence; the log has what led up to
 * it - which shells were looked at, what the installer printed, what the
 * server wrote before it stopped - so every error offers the way there.
 *
 * @param message The error, without the `MariaDB: ` prefix.
 *
 * @returns A promise that settles once the notification is dismissed.
 */
export const showErrorWithLog = async (message: string): Promise<void> => {
    const answer = await vscode.window.showErrorMessage(
        `MariaDB: ${message}`,
        SHOW_LOG_ACTION,
    );
    if (answer === SHOW_LOG_ACTION) {
        await vscode.commands.executeCommand(SHOW_LOG_COMMAND);
    }
};
