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

import { GENERAL_ACTIONS } from "../connections/connectionActivity.js";
import type { IMariaDbApi } from "../mcp/types.js";
import type { IRestApi, IRestdDeployment, IRestdSetup } from "../mcp/restApi.js";
import type { ISandboxDeployOptions } from "../mcp/sandboxApi.js";
import {
    DEFAULT_AUTH_APP,
    defaultAuthAppStatements,
    serviceDefaults,
    serviceStatements,
} from "../mrs/mrsDialogs.js";
import { deployCall } from "../sandboxes/sandboxActivity.js";
import {
    sandboxConnectionUri,
    type ISandboxRestOptions,
} from "../sandboxes/sandboxFields.js";
import { ActionGroup, type ActionPutter } from "./actionGroup.js";

/**
 * A sandbox with the MariaDB REST Service: the deploy, then the REST
 * Daemon and the REST objects a first try needs, all one action of the
 * Actions tab with a row for each step. And a sandbox's daemon following
 * it as it is started, stopped and deleted, its steps rows of the
 * sandbox's action.
 *
 * The MCP server's sandbox tools start, stop and delete the daemon on
 * their own (so an agent's sandbox does the same); the extension makes
 * the same calls first, which do nothing where they are done already,
 * so that each is a row the user sees, with its error where it failed.
 */

/** What the REST steps need from the extension. */
export interface ISandboxRestHost {
    put: ActionPutter;
    /** Deploys the sandbox, reporting nothing of its own. */
    deploy(options: ISandboxDeployOptions): Promise<string>;
    /** Opens the extension's connection to a URI; its connection id. */
    connect(uri: string): Promise<string>;
    api(): Promise<IMariaDbApi>;
    restApi(): Promise<IRestApi>;
    /**
     * Keeps a failure's full text in a log file, for its row to open.
     *
     * @returns The file.
     */
    writeLog(name: string, text: string): Promise<string>;
}

/** A sandbox deployed with the REST Service. */
export interface ISandboxRestDeploy {
    /** What the server said it deployed. */
    message: string;
    /**
     * The REST steps, which go on after the deploy: the dialog is closed
     * once the sandbox is there. Resolves to whether all of them worked.
     */
    rest: Promise<boolean>;
}

/**
 * Runs one REST SQL statement.
 *
 * @param api The database API.
 * @param connectionId Where.
 * @param sql The statement.
 */
const runSql = async (
    api: IMariaDbApi,
    connectionId: string,
    sql: string,
): Promise<void> => {
    const result = await api.executeSql(connectionId, sql);
    if (result.error !== undefined) {
        throw new Error(result.error);
    }
};

/**
 * @param setup The REST Daemon set up.
 *
 * @returns What its row says it came to.
 */
const setupDetail = (setup: IRestdSetup): string => {
    return `Serves ${setup.url}`
        + (setup.installed ? ", mariadb-restd was installed" : "")
        + ` (${setup.directory})`;
};

/**
 * Deploys a sandbox, then sets the MariaDB REST Service up on it: a REST
 * Daemon, the MRS auth app with a REST user, and a REST service linked
 * to it and published, so it can be tried at once.
 *
 * A failure of the deploy fails the call, as a plain deploy does. A
 * failure after it leaves the sandbox as it is - the REST Daemon can be
 * set up again from the connection's menu - and is reported in its row,
 * with a log to open.
 *
 * @param host The extension.
 * @param options The deploy.
 * @param rest What the REST part of the dialog asked for.
 *
 * @returns What was deployed, and the REST steps still going on.
 */
export const deploySandboxWithRest = async (
    host: ISandboxRestHost,
    options: ISandboxDeployOptions,
    rest: ISandboxRestOptions,
): Promise<ISandboxRestDeploy> => {
    const port = String(options.port);
    const group = new ActionGroup(host.put, GENERAL_ACTIONS, {
        doing: `Creating Sandbox on port ${port}`,
        done: `Created Sandbox on port ${port}`,
    });

    let message: string;
    try {
        message = await group.step({
            doing: `Creating Sandbox on port ${port}`,
            done: `Created Sandbox on port ${port}`,
        }, deployCall(options), () => {
            return host.deploy(options);
        }, { detail: (said) => { return said; } });
    } catch (error) {
        group.finish();
        throw error;
    }

    const steps = async (): Promise<boolean> => {
        try {
            const connectionId = await host.connect(sandboxConnectionUri(port));
            const setup = await group.step({
                doing: rest.restdPort === undefined
                    ? "Setting up REST Daemon"
                    : `Setting up REST Daemon on port ${String(rest.restdPort)}`,
                done: "Set up REST Daemon",
            }, `rest.configure_restd(sandbox_port=${port}`
                + (rest.restdPort === undefined
                    ? "" : `, port=${String(rest.restdPort)}`)
                + ")", async () => {
                return (await host.restApi()).configure({
                    connectionId,
                    password: options.password,
                    sandboxPort: options.port,
                    ...(rest.restdPort === undefined
                        ? {} : { port: rest.restdPort }),
                });
            }, {
                detail: setupDetail,
                logOf: async (error) => {
                    return host.writeLog(`restd-setup-${port}`,
                        error instanceof Error ? error.message : String(error));
                },
            });

            const api = await host.api();
            const [authApp, user] = defaultAuthAppStatements(rest.userName,
                rest.password);
            await group.step({
                doing: `Creating REST Auth App '${DEFAULT_AUTH_APP}'`,
                done: `Created REST Auth App '${DEFAULT_AUTH_APP}'`,
            }, authApp!, () => { return runSql(api, connectionId, authApp!); });
            await group.step({
                doing: `Creating REST User '${rest.userName}'`,
                done: `Created REST User '${rest.userName}'`,
            }, `CREATE REST USER '${rest.userName}'@'${DEFAULT_AUTH_APP}' `
                + "IDENTIFIED BY '***'", () => {
                return runSql(api, connectionId, user!);
            });

            const service = serviceStatements({
                ...serviceDefaults(undefined, [], [DEFAULT_AUTH_APP]),
                path: rest.servicePath,
                // Published, so the REST Daemon serves it to everyone
                // rather than to its developers only.
                published: true,
                makeCurrent: false,
                authApps: [DEFAULT_AUTH_APP],
            }, { allAuthApps: [DEFAULT_AUTH_APP], linkedAuthApps: [] });
            await group.step({
                doing: `Creating REST Service '${rest.servicePath}'`,
                done: `Created REST Service '${rest.servicePath}'`,
            }, service[0]!, async () => {
                for (const sql of service) {
                    await runSql(api, connectionId, sql);
                }
            });

            // Last: a dialog the user cancels leaves everything else done.
            if (rest.trustCertificate) {
                await group.step({
                    doing: "Registering the HTTPS Certificate",
                    done: "Registered the HTTPS Certificate",
                }, "rest.trust_certificate()", async () => {
                    return (await host.restApi()).trustCertificate();
                }, {
                    detail: (trust) => {
                        return trust.changed
                            ? `Trusted by this system: ${trust.ca_path}`
                            : "Trusted already";
                    },
                });
            }

            group.finish(`${setup.url}${rest.servicePath}`);

            return true;
        } catch {
            // The step's row says what failed.
            group.finish();

            return false;
        }
    };

    return { message, rest: steps() };
};

/** What a sandbox's daemon does as the sandbox does. */
export type SandboxChange = "start" | "stop" | "delete";

/** What following a sandbox needs from the extension. */
export interface IRestdFollowHost {
    put: ActionPutter;
    restApi(): Promise<IRestApi>;
    /**
     * Keeps the Actions tab on General Actions while the steps run, where
     * the panel is there by default; returns what lets go of it.
     */
    hold?(): () => void;
}

const CHANGE_WORDS: Record<SandboxChange, {
    sandbox: [string, string];
    restd: [string, string];
}> = {
    start: {
        sandbox: ["Starting", "Started"], restd: ["Starting", "Started"],
    },
    stop: {
        sandbox: ["Stopping", "Stopped"], restd: ["Stopping", "Stopped"],
    },
    delete: {
        sandbox: ["Deleting", "Deleted"], restd: ["Deleting", "Deleted"],
    },
};

/**
 * Starts, stops or deletes a sandbox, and its REST Daemon with it where it
 * has one: the daemon after the server when starting - it reads the REST
 * metadata as it starts - and before it otherwise. One action of the
 * Actions tab, a row for each.
 *
 * A sandbox without a daemon is changed as it always was, reporting
 * nothing new.
 *
 * @param host The extension.
 * @param port The sandbox's port.
 * @param change What is done.
 * @param work Does it to the sandbox.
 */
export const followSandbox = async (
    host: IRestdFollowHost,
    port: number,
    change: SandboxChange,
    work: () => Promise<void>,
): Promise<void> => {
    let deployment: IRestdDeployment | undefined;
    try {
        deployment = (await (await host.restApi()).getStatus())
            .find((found) => { return found.sandbox_port === port; });
    } catch {
        // A server without the rest group: there is no daemon to follow.
    }
    if (deployment === undefined) {
        await work();

        return;
    }

    const release = host.hold?.();
    const words = CHANGE_WORDS[change];
    const target = deployment;
    const sandbox = `Sandbox on port ${String(port)}`;
    const daemon = `REST Daemon on port ${String(target.port)}`;
    const group = new ActionGroup(host.put, GENERAL_ACTIONS, {
        doing: `${words.sandbox[0]} ${sandbox}`,
        done: `${words.sandbox[1]} ${sandbox}`,
    });
    const sandboxStep = (): Promise<void> => {
        return group.step({
            doing: `${words.sandbox[0]} ${sandbox}`,
            done: `${words.sandbox[1]} ${sandbox}`,
        }, `sandbox.${change}(port=${String(port)})`, work);
    };
    const restdStep = async (): Promise<void> => {
        const api = await host.restApi();
        await group.step({
            doing: `${words.restd[0]} ${daemon}`,
            done: `${words.restd[1]} ${daemon}`,
        }, change === "delete"
            ? `rest.delete_restd_config(directory='${target.directory}', `
            + "drop_accounts=False)"
            : `rest.${change}_restd(directory='${target.directory}')`,
        async () => {
            if (change === "start") {
                await api.start(target.directory);
            } else if (change === "stop") {
                await api.stop(target.directory);
            } else {
                await api.delete(target.directory);
            }
        }, {
            // Why a daemon did not come up is in its own log.
            logOf: () => { return Promise.resolve(target.log_path); },
        });
    };

    try {
        if (change === "start") {
            await sandboxStep();
            // The sandbox is up whether or not its daemon follows.
            await restdStep().catch(() => { /* its row says why */ });
        } else {
            // A daemon that will not stop does not keep the sandbox
            // running: the server's own stop stops it in any case.
            await restdStep().catch(() => { /* its row says why */ });
            await sandboxStep();
        }
    } finally {
        group.finish();
        release?.();
    }
};
