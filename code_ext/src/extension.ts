/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import * as vscode from "vscode";

import { ensureShell } from "./shell/bootstrap.js";
import { MINIMUM_SHELL_VERSION } from "./shell/constants.js";
import { McpServerController } from "./shell/mcpServer.js";
import {
    createNodeMcpServerSpawner,
    createNodeProcessRunner,
    createNodeShellEnvironment,
} from "./shell/nodeRuntime.js";
import { createNotificationProgressHost } from "./shell/vscodeProgress.js";

let output: vscode.OutputChannel | undefined;
let controller: McpServerController | undefined;

/**
 * Finds or installs the MariaDB Shell and starts the MCP server with it.
 *
 * @param log Where to write progress and errors.
 *
 * @returns Nothing.
 */
const startMcpServer = async (
    log: (message: string) => void,
): Promise<void> => {
    try {
        const { location, installed } = await ensureShell({
            environment: createNodeShellEnvironment(),
            runner: createNodeProcessRunner(),
            progress: createNotificationProgressHost(),
            log,
            minimumVersion: MINIMUM_SHELL_VERSION,
        });

        if (installed) {
            void vscode.window.showInformationMessage(
                `MariaDB Shell ${MINIMUM_SHELL_VERSION} was installed.`,
            );
        }

        controller?.start(location);
    } catch (error) {
        const message = error instanceof Error
            ? error.message
            : String(error);
        log(`Failed to start the MariaDB MCP server: ${message}`);
        void vscode.window.showErrorMessage(
            `Failed to start the MariaDB MCP server: ${message}`,
        );
    }
};

/**
 * Activates the extension.
 *
 * @param context The extension context.
 *
 * @returns Nothing.
 */
export const activate = (context: vscode.ExtensionContext): void => {
    output = vscode.window.createOutputChannel("MariaDB");
    context.subscriptions.push(output);

    const log = (message: string): void => {
        output?.appendLine(message);
    };

    const serverController = new McpServerController(
        createNodeMcpServerSpawner(),
        log,
    );
    controller = serverController;
    context.subscriptions.push({
        dispose: () => {
            serverController.stop();
        },
    });

    context.subscriptions.push(
        vscode.commands.registerCommand(
            "mariadb.restartMcpServer",
            async () => {
                serverController.stop();
                await startMcpServer(log);
            },
        ),
        vscode.commands.registerCommand("mariadb.showMcpServerLog", () => {
            output?.show(true);
        }),
    );

    void startMcpServer(log);
};

/**
 * Deactivates the extension, shutting the MCP server down with it.
 *
 * @returns Nothing.
 */
export const deactivate = (): void => {
    controller?.stop();
    controller = undefined;
    output = undefined;
};
