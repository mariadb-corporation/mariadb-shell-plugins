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

import { describe, expect, it } from "vitest";

import type { IRestApi, IRestdDeployment } from "../../mcp/restApi.js";
import type { ISandboxRestdOutcome } from "../../mcp/sandboxApi.js";
import type { IMariaDbApi } from "../../mcp/types.js";
import {
    deploySandboxWithRest,
    followSandbox,
    type ISandboxRestHost,
} from "../../rest/sandboxRest.js";
import type { IActionRow } from "../../webview/protocol.js";

const DEPLOYMENT: IRestdDeployment = {
    directory: "/s/3311/mariadb-restd", host: "127.0.0.1", server_port: 3311,
    port: 33110, url: "https://localhost:33110", status: "running", pid: 42,
    log_path: "/s/3311/mariadb-restd/log/mariadb-restd.log",
    metadata_schema: "mariadb_rest_service",
    connection_uri: "mariadb://root@127.0.0.1:3311", sandbox_port: 3311,
};

/** A REST API recording its calls, failing those it is told to. */
const restApi = (
    calls: string[],
    fail: string[] = [],
    deployments: IRestdDeployment[] = [DEPLOYMENT],
): IRestApi => {
    const answer = <T>(name: string, value: T): Promise<T> => {
        calls.push(name);
        return fail.includes(name)
            ? Promise.reject(new Error(`${name} failed`))
            : Promise.resolve(value);
    };

    return {
        getStatus: () => { return Promise.resolve(deployments); },
        configure: (options) => {
            return answer(`configure ${JSON.stringify(options)}`, {
                ...DEPLOYMENT, installed: false, setup_output: "",
            });
        },
        start: () => { return answer("start", DEPLOYMENT); },
        stop: () => { return answer("stop", DEPLOYMENT); },
        delete: () => { return answer("delete", "Deleted"); },
        certificateStatus: () => {
            return Promise.resolve({ exists: true, trusted: false, ca_path: "/c" });
        },
        trustCertificate: () => {
            return answer("trust", { trusted: true, changed: true,
                ca_path: "/certs/rootCA.crt" });
        },
    };
};

const createHost = (fail: string[] = []): {
    host: ISandboxRestHost;
    calls: string[];
    last: () => IActionRow;
} => {
    const calls: string[] = [];
    let last: IActionRow | undefined;
    const api = {
        executeSql: (_id: string, sql: string) => {
            calls.push(sql.split(" ").slice(0, 3).join(" "));
            return Promise.resolve(fail.some((failing) => {
                return sql.startsWith(failing);
            }) ? { error: "REST SQL failed" } : {});
        },
    } as unknown as IMariaDbApi;

    return {
        calls,
        last: () => { return last!; },
        host: {
            put: (_connection, row) => { last = row; },
            deploy: (options) => {
                calls.push(`deploy ${String(options.port)}`);
                return fail.includes("deploy")
                    ? Promise.reject(new Error("no server"))
                    : Promise.resolve("Sandbox instance deployed");
            },
            connect: (uri) => { calls.push(`connect ${uri}`); return Promise.resolve("c1"); },
            api: () => { return Promise.resolve(api); },
            restApi: () => { return Promise.resolve(restApi(calls, fail)); },
            writeLog: (name) => { return Promise.resolve(`/logs/${name}.log`); },
        },
    };
};

const REST = { servicePath: "/myService/v1", userName: "dba",
    password: "Secret1!", trustCertificate: false };

describe("deploySandboxWithRest", () => {
    it("deploys, sets the daemon up, then the REST objects, in one action",
        async () => {
            const { host, calls, last } = createHost();

            const deployed = await deploySandboxWithRest(host,
                { port: 3311, password: "Root1!" }, REST);

            expect(deployed.message).toBe("Sandbox instance deployed");
            expect(await deployed.rest).toBe(true);
            expect(calls).toEqual([
                "deploy 3311",
                "connect mariadb://root@127.0.0.1:3311",
                'configure {"connectionId":"c1","password":"Root1!",'
                    + '"sandboxPort":3311}',
                "CREATE REST AUTH",
                "CREATE REST USER",
                "CREATE REST SERVICE",
            ]);
            expect(last()).toMatchObject({
                kind: "info", message: "Created Sandbox on port 3311",
                summary: "https://localhost:33110/myService/v1",
            });
            expect(last().children?.map((row) => { return row.message; }))
                .toEqual([
                    "Created Sandbox on port 3311",
                    "Set up REST Daemon",
                    "Created REST Auth App 'MRS'",
                    "Created REST User 'dba'",
                    "Created REST Service '/myService/v1'",
                ]);
            // The password is never shown.
            expect(JSON.stringify(last())).not.toContain("Secret1!");
        });

    it("passes the port typed", async () => {
        const { host, calls } = createHost();

        await (await deploySandboxWithRest(host, { port: 3311, password: "p" },
            { ...REST, restdPort: 9443 })).rest;

        expect(calls).toContain('configure {"connectionId":"c1",'
            + '"password":"p","sandboxPort":3311,"port":9443}');
    });

    it("publishes the service and links it to the MRS app", async () => {
        const statements: string[] = [];
        const { host } = createHost();
        host.api = () => {
            return Promise.resolve({
                executeSql: (_id: string, sql: string) => {
                    statements.push(sql);
                    return Promise.resolve({});
                },
            } as unknown as IMariaDbApi);
        };

        await (await deploySandboxWithRest(host, { port: 3311, password: "p" },
            REST)).rest;

        const service = statements.find((sql) => {
            return sql.startsWith("CREATE REST SERVICE");
        })!;
        expect(service).toContain("/myService/v1");
        expect(service).toContain("PUBLISHED");
        expect(service).not.toContain("UNPUBLISHED");
        expect(service).toContain("ADD AUTH APP 'MRS'");
    });

    it("fails the call where the deploy fails", async () => {
        const { host, last } = createHost(["deploy"]);

        await expect(deploySandboxWithRest(host,
            { port: 3311, password: "p" }, REST)).rejects.toThrow("no server");
        expect(last().kind).toBe("error");
    });

    it("keeps the sandbox where the daemon's setup fails, with its log",
        async () => {
            const failing = 'configure {"connectionId":"c1","password":"p",'
                + '"sandboxPort":3311}';
            const { host, calls, last } = createHost([failing]);

            const deployed = await deploySandboxWithRest(host,
                { port: 3311, password: "p" }, REST);

            expect(await deployed.rest).toBe(false);
            // Nothing is tried after it.
            expect(calls.at(-1)).toBe(failing);
            const failed = last().children!.at(-1)!;
            expect(failed).toMatchObject({ kind: "error",
                logPath: "/logs/restd-setup-3311.log" });
            expect(last()).toMatchObject({ kind: "error",
                jumpToRowId: failed.id });
        });
});

describe("registering the HTTPS certificate", () => {
    it("is the last step, where it was asked for", async () => {
        const { host, calls, last } = createHost();

        await (await deploySandboxWithRest(host, { port: 3311, password: "p" },
            { ...REST, trustCertificate: true })).rest;

        expect(calls.at(-1)).toBe("trust");
        expect(last().children?.at(-1)).toMatchObject({
            kind: "info", message: "Registered the HTTPS Certificate",
            statement: "Trusted by this system: /certs/rootCA.crt",
        });
    });

    it("leaves everything else done when the dialog is cancelled", async () => {
        const { host, last } = createHost(["trust"]);

        const deployed = await deploySandboxWithRest(host,
            { port: 3311, password: "p" }, { ...REST, trustCertificate: true });

        expect(await deployed.rest).toBe(false);
        const steps = last().children!;
        expect(steps.slice(0, -1).every((row) => { return row.kind === "info"; }))
            .toBe(true);
        expect(steps.at(-1)).toMatchObject({ kind: "error" });
    });
});

describe("followSandbox", () => {
    const followHost = (deployments = [DEPLOYMENT]) => {
        let last: IActionRow | undefined;
        return {
            host: {
                put: (_connection: string, row: IActionRow) => { last = row; },
                deployments: () => { return Promise.resolve(deployments); },
            },
            last: () => { return last; },
        };
    };
    const outcome = (error: string | null = null): ISandboxRestdOutcome => {
        return {
            directory: DEPLOYMENT.directory, port: DEPLOYMENT.port,
            url: DEPLOYMENT.url, log_path: DEPLOYMENT.log_path, error,
        };
    };
    const changed = (restd: ISandboxRestdOutcome[]) => {
        return () => { return Promise.resolve({ message: "done", restd }); };
    };

    it("makes a row of each daemon the sandbox's call followed", async () => {
        const { host, last } = followHost();

        await followSandbox(host, 3311, "start", changed([outcome()]));

        expect(last()?.children?.map((row) => {
            return [row.message, row.statement];
        })).toEqual([
            ["Started Sandbox on port 3311", "sandbox.start(port=3311)"],
            ["Started REST Daemon on port 33110", `Serves ${DEPLOYMENT.url}`],
        ]);
        expect(last()?.kind).toBe("info");
    });

    it("leaves the sandbox changed where its daemon failed", async () => {
        const { host, last } = followHost();

        await followSandbox(host, 3311, "stop",
            changed([outcome("will not stop")]));

        expect(last()?.children?.at(-1)).toMatchObject({ kind: "error",
            message: "Stopping REST Daemon on port 33110 failed: will not stop",
            logPath: DEPLOYMENT.log_path });
    });

    it("reports a sandbox's failure in its row", async () => {
        const { host, last } = followHost();

        await expect(followSandbox(host, 3311, "delete", () => {
            return Promise.reject(new Error("It is running."));
        })).rejects.toThrow("It is running.");

        expect(last()).toMatchObject({ kind: "error" });
        expect(last()?.children).toHaveLength(1);
    });

    it("changes a sandbox without a daemon as before, reporting nothing",
        async () => {
            const { host, last } = followHost([]);

            await followSandbox(host, 3311, "stop", changed([]));

            expect(last()).toBeUndefined();
        });

    it("reports a daemon set up since the list was read", async () => {
        const { host, last } = followHost([]);

        await followSandbox(host, 3311, "start", changed([outcome()]));

        expect(last()?.children?.map((row) => { return row.message; }))
            .toEqual(["Started Sandbox on port 3311",
                "Started REST Daemon on port 33110"]);
    });
});
