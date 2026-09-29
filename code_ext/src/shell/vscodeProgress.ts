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

import type { ProgressHost } from "./installer.js";

/**
 * Reports installer progress as a VS Code notification.
 *
 * @returns A progress host backed by `vscode.window.withProgress`.
 */
export const createNotificationProgressHost = (): ProgressHost => {
    return {
        withProgress: <T>(
            title: string,
            task: (
                report: (message: string) => void,
                signal: AbortSignal,
            ) => Promise<T>,
        ): Promise<T> => {
            return Promise.resolve(vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title,
                    // A download can stall for as long as the network lets
                    // it, and a notification that cannot be dismissed
                    // would then sit there with no way out.
                    cancellable: true,
                },
                async (progress, token) => {
                    const controller = new AbortController();
                    const subscription = token.onCancellationRequested(
                        () => { controller.abort(); });
                    try {
                        return await task((message) => {
                            progress.report({ message });
                        }, controller.signal);
                    } finally {
                        subscription.dispose();
                    }
                },
            ));
        },
    };
};
