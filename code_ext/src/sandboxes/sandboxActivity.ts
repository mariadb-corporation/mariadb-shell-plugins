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
    counted,
    createGeneralWatcher,
    type ActivityReporter,
} from "../connections/connectionActivity.js";
import type { ISandboxApi } from "../mcp/sandboxApi.js";

/**
 * Wraps the sandbox API so that every call is reported under General
 * Actions, where `logAllCalls` says so.
 *
 * A sandbox call is made on no open connection - it works on a server's
 * files and process, not on a session - so General Actions is the only
 * place it can go, alongside the connection list's own calls.
 *
 * @param api The API to wrap.
 * @param report Where to send what happened.
 * @param logAllCalls Whether the calls are reported. Read on every call,
 *                    since the setting behind it can change.
 *
 * @returns The same API, reporting as it goes.
 */
export const createLoggingSandboxApi = (
    api: ISandboxApi,
    report: ActivityReporter,
    logAllCalls: () => boolean,
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
        deploy: (options) => {
            return watch(
                `sandbox.deploy(${callArguments({
                    port: options.port,
                    // Whether one was given, never what it is - and the
                    // shell refuses a deploy without one, so it always is.
                    password: "***",
                    server_version: options.serverVersion,
                    allow_root_from: options.allowRootFrom,
                    server_id: options.serverId,
                    ssl: options.ssl,
                    mariadbd_options: options.mariadbdOptions?.join(" "),
                    timeout: options.timeout,
                    mcp_access: options.mcpAccess,
                })})`,
                (message) => { return message; },
                () => { return api.deploy(options); },
            );
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
