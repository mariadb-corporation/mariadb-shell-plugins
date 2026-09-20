/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
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
            task: (report: (message: string) => void) => Promise<T>,
        ): Promise<T> => {
            return Promise.resolve(vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title,
                    cancellable: false,
                },
                async (progress) => {
                    return await task((message) => {
                        progress.report({ message });
                    });
                },
            ));
        },
    };
};
