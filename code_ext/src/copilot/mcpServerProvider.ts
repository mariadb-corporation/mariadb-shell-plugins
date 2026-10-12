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

import { shellBinaryName } from "../shell/locator.js";
import { buildMcpServerCommand } from "../shell/mcpServer.js";
import type { IShellResolver } from "../shell/shellResolver.js";

/** The id `contributes.mcpServerDefinitionProviders` declares. */
export const COPILOT_MCP_PROVIDER_ID = "mariadb.mcpServer";

/** The setting that turns the server and the skills on and off. */
const ENABLED_SETTING = "copilot.enabled";

/**
 * Hands Copilot - and any other chat client in VS Code - the MariaDB
 * Shell's MCP server, started for an agent (no `--gui`).
 *
 * Listing the server costs nothing: it is listed on the bare executable
 * name, the same every time, so VS Code never sees it change. Only when
 * VS Code is about to start it does `resolveMcpServerDefinition` look the
 * shell up, install it if there is none, and put in the real binary - so
 * a window where nobody asks Copilot about MariaDB never downloads anything.
 */
export class CopilotMcpServerProvider
implements vscode.McpServerDefinitionProvider<vscode.McpStdioServerDefinition> {
    /**
     * @param shell Where the shell comes from.
     * @param isEnabled Whether the `mariadb.copilot.enabled` setting is on.
     * @param log Where the start is recorded.
     * @param onDidChangeMcpServerDefinitions Fired when the setting changes.
     * @param platform The platform, for the executable's name.
     */
    public constructor(
        private readonly shell: IShellResolver,
        private readonly isEnabled: () => boolean,
        private readonly log: (message: string) => void,
        public readonly onDidChangeMcpServerDefinitions?: vscode.Event<void>,
        private readonly platform: NodeJS.Platform = process.platform,
    ) { }

    /**
     * @returns The one server, or none while the setting is off.
     */
    public provideMcpServerDefinitions(): vscode.McpStdioServerDefinition[] {
        if (!this.isEnabled()) {
            return [];
        }

        return [new vscode.McpStdioServerDefinition(
            "MariaDB", shellBinaryName(this.platform))];
    }

    /**
     * Finds or installs the shell, just before VS Code starts the server.
     *
     * @param server The server as listed.
     *
     * @returns The server on the shell found, or undefined - which stops
     *          the start - while the setting is off.
     */
    public async resolveMcpServerDefinition(
        server: vscode.McpStdioServerDefinition,
    ): Promise<vscode.McpStdioServerDefinition | undefined> {
        if (!this.isEnabled()) {
            return undefined;
        }

        const { location } = await this.shell.ensure();
        const { command, args } = buildMcpServerCommand(location, "agent");
        this.log(`Starting the MCP server for Copilot: ${command} `
            + `${args.join(" ")}`);
        server.command = command;
        server.args = args;

        return server;
    }
}

/**
 * Registers the provider, and has VS Code list the servers again when the
 * setting changes.
 *
 * @param shell Where the shell comes from.
 * @param log Where the start is recorded.
 *
 * @returns What takes it all down again.
 */
export const registerCopilotMcpServer = (
    shell: IShellResolver,
    log: (message: string) => void,
): vscode.Disposable => {
    const changed = new vscode.EventEmitter<void>();
    const settingWatch = vscode.workspace.onDidChangeConfiguration(
        (event: vscode.ConfigurationChangeEvent) => {
            if (event.affectsConfiguration(`mariadb.${ENABLED_SETTING}`)) {
                changed.fire();
            }
        });
    const isEnabled = (): boolean => {
        return vscode.workspace.getConfiguration("mariadb")
            .get<boolean>(ENABLED_SETTING) ?? true;
    };
    const registration = vscode.lm.registerMcpServerDefinitionProvider(
        COPILOT_MCP_PROVIDER_ID,
        new CopilotMcpServerProvider(shell, isEnabled, log, changed.event));

    return {
        dispose: () => {
            registration.dispose();
            settingWatch.dispose();
            changed.dispose();
        },
    };
};
