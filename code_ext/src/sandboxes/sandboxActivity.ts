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

import {
    callArguments,
    createGeneralWatcher,
    GENERAL_ACTIONS,
    type ActivityReporter,
} from "../connections/connectionActivity.js";
import { formatTime } from "../sql/executionService.js";
import { counted, errorText } from "../text.js";
import type { ISandboxApi, ISandboxDeployOptions } from "../mcp/sandboxApi.js";
import type { IActionRow } from "../webview/protocol.js";

/** Puts up a row of the Actions tab, or replaces the one with its id. */
export type ActionPutter = (connection: string, row: IActionRow) => void;

/** Where a deploy is, for its row. */
export interface ISandboxDeployState {
    id: string;
    port: number;
    /** The call, its password masked: the row's Information. */
    call: string;
    when: Date;
    /** Still running. */
    pending?: boolean;
    /** What the server said it did. */
    message?: string;
    /** Why it failed. */
    error?: string;
    elapsedMs?: number;
}

/**
 * The Actions row of a deploy, under General Actions - the sandbox has no
 * connection open yet: "Creating Sandbox on port 3310…" with the spinner
 * while it runs, replaced by "Created Sandbox on port 3310" with what the
 * server said, or by the failure with the full error as the row under it,
 * as a failed SQL script shows it.
 *
 * @param state Where the deploy is.
 *
 * @returns The row.
 */
export const sandboxDeployRow = (state: ISandboxDeployState): IActionRow => {
    const time = formatTime(state.when);
    const base = {
        id: state.id, time, connection: GENERAL_ACTIONS, connectionLabel: "",
    };
    const doing = `Creating Sandbox on port ${String(state.port)}`;
    if (state.pending === true) {
        return { ...base, role: "event", statement: state.call,
            message: `${doing}\u2026`, kind: "pending" };
    }
    if (state.error !== undefined) {
        const errorRow: IActionRow = {
            ...base, id: `${state.id}-error`, role: "statement",
            statement: state.call, message: state.error, kind: "error",
        };

        return {
            ...base, role: "run", statement: "", message: `${doing} failed`,
            summary: "Finished with 1 error", kind: "error",
            ...(state.elapsedMs === undefined
                ? {} : { elapsedMs: state.elapsedMs }),
            jumpToRowId: errorRow.id, children: [errorRow],
        };
    }

    return {
        ...base, role: "event", kind: "info",
        message: `Created Sandbox on port ${String(state.port)}`,
        // The Information column: what the server said it did.
        statement: state.message ?? state.call,
        ...(state.elapsedMs === undefined ? {} : { elapsedMs: state.elapsedMs }),
    };
};

/**
 * @param options A deploy's options.
 *
 * @returns The call as the row shows it, the password masked.
 */
const deployCall = (options: ISandboxDeployOptions): string => {
    return `sandbox.deploy(${callArguments({
        port: options.port,
        // Whether one was given, never what it is - and the shell refuses a
        // deploy without one, so it always is.
        password: "***",
        server_version: options.serverVersion,
        allow_root_from: options.allowRootFrom,
        server_id: options.serverId,
        ssl: options.ssl,
        mariadbd_options: options.mariadbdOptions?.join(" "),
        timeout: options.timeout,
        mcp_access: options.mcpAccess,
    })})`;
};

/** Makes deploy rows' ids unique. */
let deploys = 0;

/**
 * Wraps the sandbox API so that every call is reported under General
 * Actions, where `logAllCalls` says so - except a deploy, which is always
 * an action row of its own ({@link sandboxDeployRow}).
 *
 * A sandbox call is made on no open connection - it works on a server's
 * files and process, not on a session - so General Actions is the only
 * place it can go, alongside the connection list's own calls.
 *
 * @param api The API to wrap.
 * @param report Where to send what happened.
 * @param logAllCalls Whether the calls are reported. Read on every call,
 *                    since the setting behind it can change.
 * @param put Where a deploy's row goes up, and is replaced as it ends.
 *
 * @returns The same API, reporting as it goes.
 */
export const createLoggingSandboxApi = (
    api: ISandboxApi,
    report: ActivityReporter,
    logAllCalls: () => boolean,
    put: ActionPutter,
): ISandboxApi => {
    const watch = createGeneralWatcher(report, logAllCalls);

    return {
        listInstances: (port) => {
            return watch(
                `sandbox.list_instances(${callArguments({ port })})`,
                (instances) => {
                    // Not `counted`: sandbox does not pluralize with an s.
                    return `Listed ${instances.length} `
                        + (instances.length === 1 ? "sandbox" : "sandboxes");
                },
                () => { return api.listInstances(port); },
            );
        },
        listAvailableVersions: (series) => {
            return watch(
                `sandbox.list_available_versions(${callArguments({ series })})`,
                (versions) => {
                    return `Listed ${counted(versions.length, "version")}`;
                },
                () => { return api.listAvailableVersions(series); },
            );
        },
        // Always a row of its own, whatever the setting: it can take
        // minutes, and goes on when its dialog is closed.
        deploy: async (options) => {
            deploys += 1;
            const state = {
                id: `sandbox-deploy-${String(deploys)}`, port: options.port,
                call: deployCall(options), when: new Date(),
            };
            const elapsed = (): number => {
                return Date.now() - state.when.getTime();
            };
            put(GENERAL_ACTIONS, sandboxDeployRow({ ...state, pending: true }));
            try {
                const message = await api.deploy(options);
                put(GENERAL_ACTIONS, sandboxDeployRow({
                    ...state, message, elapsedMs: elapsed() }));

                return message;
            } catch (error) {
                put(GENERAL_ACTIONS, sandboxDeployRow({
                    ...state, error: errorText(error), elapsedMs: elapsed() }));
                throw error;
            }
        },
        start: (port) => {
            return watch(`sandbox.start(port=${port})`,
                (message) => { return message; },
                () => { return api.start(port); });
        },
        stop: (port) => {
            return watch(`sandbox.stop(port=${port})`,
                (message) => { return message; },
                () => { return api.stop(port); });
        },
        delete: (port) => {
            return watch(`sandbox.delete(port=${port})`,
                (message) => { return message; },
                () => { return api.delete(port); });
        },
    };
};
