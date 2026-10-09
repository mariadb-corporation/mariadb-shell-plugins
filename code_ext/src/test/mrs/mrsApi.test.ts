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

import type { IToolCaller } from "../../mcp/mariaDbApi.js";
import { McpToolError, type IToolResult } from "../../mcp/protocol.js";
import {
    MRS_TOOL_TIMEOUT_MS,
    MrsApi,
    MrsError,
    MrsToolsApi,
    type IMrsScope,
} from "../../mrs/mrsApi.js";
import {
    createFakeRestSql,
    mrsObject,
    mrsService,
    mrsStatus,
    type FakeRestSql,
} from "../helpers.js";

const scope: IMrsScope = { connectionId: "uuid-1" };
const named: IMrsScope = {
    connectionId: "uuid-1",
    metadataSchema: "dev_mariadb_rest_service",
};
const USE = "USE REST METADATA SCHEMA `dev_mariadb_rest_service`;";

/**
 * @param fake The server.
 *
 * @returns An API over it.
 */
const apiOver = (fake: FakeRestSql): MrsApi => {
    return new MrsApi(() => { return Promise.resolve(fake); });
};

describe("MrsApi.run", () => {
    it("sends a single statement with executeSql, adding its ;", async () => {
        const fake = createFakeRestSql();

        const results = await apiOver(fake).run(scope,
            ["DROP REST SERVICE /a"]);

        expect(fake.calls).toEqual([{
            connectionId: "uuid-1",
            statements: ["DROP REST SERVICE /a;"],
        }]);
        expect(fake.scripts).toEqual([]);
        expect(results).toHaveLength(1);
    });

    it("keeps a ; that is there, trailing blanks and all", async () => {
        const fake = createFakeRestSql();

        await apiOver(fake).run(scope, ["DROP REST SERVICE /a;  "]);

        expect(fake.sent).toEqual(["DROP REST SERVICE /a;  "]);
    });

    it("sends several statements as one script that stops on error",
        async () => {
            const fake = createFakeRestSql();

            const results = await apiOver(fake).run(scope,
                ["DROP REST SERVICE /a", "DROP REST SERVICE /b;"]);

            expect(fake.scripts).toEqual([
                "DROP REST SERVICE /a;\nDROP REST SERVICE /b;"]);
            expect(fake.stopOnError).toBe(true);
            expect(results).toHaveLength(2);
        });

    it("chooses the metadata schema first, in the same script", async () => {
        const fake = createFakeRestSql({
            answers: { "SHOW REST SERVICES FORMAT=JSON": [] },
        });

        const results = await apiOver(fake).run(named,
            ["SHOW REST SERVICES FORMAT=JSON"]);

        expect(fake.scripts).toEqual([
            `${USE}\nSHOW REST SERVICES FORMAT=JSON;`]);
        // The USE's own result is not the caller's.
        expect(results).toHaveLength(1);
        expect(results[0]!.result_sets).toBeDefined();
    });

    it("runs the USE alone where nothing else is asked for", async () => {
        const fake = createFakeRestSql();

        await expect(apiOver(fake).run(named, [])).resolves.toEqual([]);

        expect(fake.calls).toEqual([
            { connectionId: "uuid-1", statements: [USE] }]);
    });

    it("quotes a metadata schema name with a back tick", async () => {
        const fake = createFakeRestSql();

        await apiOver(fake).run({ connectionId: "c", metadataSchema: "a`b" },
            []);

        expect(fake.sent).toEqual(["USE REST METADATA SCHEMA `a``b`;"]);
    });

    it("makes no call at all for no statements and no schema", async () => {
        const fake = createFakeRestSql();
        let asked = 0;
        const api = new MrsApi(() => {
            asked += 1;

            return Promise.resolve(fake);
        });

        await expect(api.run(scope, [])).resolves.toEqual([]);

        expect(fake.calls).toEqual([]);
        // Asked for, since the API is what a call needs; nothing was sent.
        expect(asked).toBe(1);
    });

    it("raises a statement's error as an MrsError", async () => {
        const fake = createFakeRestSql({
            errors: { "DROP REST SERVICE /a": "Service not found" },
        });

        const failure = apiOver(fake).run(scope, ["DROP REST SERVICE /a"]);

        await expect(failure).rejects.toBeInstanceOf(MrsError);
        await expect(failure).rejects.toThrow("Service not found");
    });

    it("raises the first error of a script, the USE's included", async () => {
        const fake = createFakeRestSql({
            errors: {
                [USE.slice(0, -1)]: "Unknown metadata schema",
            },
        });

        await expect(apiOver(fake).run(named, ["SHOW REST SERVICES"]))
            .rejects.toThrow("Unknown metadata schema");
    });

    it("names itself MrsError", () => {
        expect(new MrsError("x").name).toBe("MrsError");
    });

    it("asks for the API on every call", async () => {
        const first = createFakeRestSql();
        const second = createFakeRestSql();
        let current = first;
        const api = new MrsApi(() => { return Promise.resolve(current); });

        await api.run(scope, ["A"]);
        current = second;
        await api.run(scope, ["B"]);

        expect(first.sent).toEqual(["A;"]);
        expect(second.sent).toEqual(["B;"]);
    });
});

describe("MrsApi.json and text", () => {
    it("decodes a cell of JSON text", async () => {
        const fake = createFakeRestSql({ answers: { "SHOW X": { a: 1 } } });

        await expect(apiOver(fake).json(scope, "SHOW X"))
            .resolves.toEqual({ a: 1 });
    });

    it("takes a cell already decoded as it is", async () => {
        const fake = createFakeRestSql();
        fake.executeSql = () => {
            return Promise.resolve({
                result_sets: [{ columns: ["r"], rows: [{ r: [{ id: "x" }] }] }],
            });
        };

        await expect(apiOver(fake).json(scope, "SHOW X"))
            .resolves.toEqual([{ id: "x" }]);
    });

    it("answers undefined where there is no row", async () => {
        const fake = createFakeRestSql();

        await expect(apiOver(fake).json(scope, "SHOW X"))
            .resolves.toBeUndefined();
    });

    it("reads a cell of text, or nothing as empty", async () => {
        const fake = createFakeRestSql({
            answers: {
                "SHOW CREATE REST SERVICE /a": "CREATE REST SERVICE /a;",
            },
        });
        const api = apiOver(fake);

        await expect(api.text(scope, "SHOW CREATE REST SERVICE /a"))
            .resolves.toBe("CREATE REST SERVICE /a;");
        await expect(api.text(scope, "SHOW CREATE REST SERVICE /b"))
            .resolves.toBe("");
    });

    it("turns a cell that is not text into text", async () => {
        const fake = createFakeRestSql();
        fake.executeSql = () => {
            return Promise.resolve({
                result_sets: [{ columns: ["r"], rows: [{ r: 42 }] }],
            });
        };

        await expect(apiOver(fake).text(scope, "SHOW X")).resolves.toBe("42");
    });
});

describe("MrsApi lists", () => {
    /**
     * @param call What to ask the API.
     * @param sql The statement it should send.
     *
     * @returns Nothing.
     */
    const sends = async (
        call: (api: MrsApi) => Promise<unknown>,
        sql: string,
    ): Promise<void> => {
        const answer = [{ marker: sql }];
        const fake = createFakeRestSql({ answers: { [sql]: answer } });

        await expect(call(apiOver(fake))).resolves.toEqual(answer);
        expect(fake.sent).toEqual([`${sql};`]);
    };

    it("reads the metadata schemas without choosing one", async () => {
        const fake = createFakeRestSql({
            answers: {
                "SHOW REST METADATA SCHEMAS FORMAT=JSON": [{
                    schema_name: "mariadb_rest_service",
                    version: "5.0.0",
                    current: true,
                }],
            },
        });

        await expect(apiOver(fake).metadataSchemas("uuid-1")).resolves
            .toEqual([{
                schema_name: "mariadb_rest_service",
                version: "5.0.0",
                current: true,
            }]);
        expect(fake.calls[0]!.connectionId).toBe("uuid-1");
    });

    it("reads the status", async () => {
        const fake = createFakeRestSql({
            answers: { "SHOW REST METADATA STATUS FORMAT=JSON": mrsStatus() },
        });

        await expect(apiOver(fake).status(scope)).resolves
            .toEqual(mrsStatus());
    });

    it("sends every list statement as the shell spells it", async () => {
        await sends((api) => { return api.services(scope); },
            "SHOW REST SERVICES FORMAT=JSON");
        await sends((api) => { return api.servicesOfDaemon(scope, "d'1"); },
            "SHOW REST SERVICES FOR DAEMON 'd''1' FORMAT=JSON");
        await sends((api) => { return api.schemas(scope, "anna@/svc"); },
            "SHOW REST SCHEMAS ON SERVICE anna@/svc FORMAT=JSON");
        await sends((api) => {
            return api.columns(scope, "TABLE", "sakila", "actor");
        }, "SHOW REST COLUMNS FROM TABLE `sakila`.`actor` FORMAT=JSON");
        await sends((api) => { return api.contentSets(scope, "/svc"); },
            "SHOW REST CONTENT SETS ON SERVICE /svc FORMAT=JSON");
        await sends((api) => {
            return api.contentFiles(scope, "/svc", "/my-app");
        }, "SHOW REST CONTENT FILES ON SERVICE /svc CONTENT SET `/my-app` "
            + "FORMAT=JSON");
        await sends((api) => { return api.authApps(scope); },
            "SHOW REST AUTH APPS ON ANY SERVICE FORMAT=JSON");
        await sends((api) => { return api.authApps(scope, "/svc"); },
            "SHOW REST AUTH APPS ON SERVICE /svc FORMAT=JSON");
        await sends((api) => { return api.authVendors(scope); },
            "SHOW REST AUTH VENDORS FORMAT=JSON");
        await sends((api) => { return api.users(scope, "MRS"); },
            "SHOW REST USERS FOR AUTH APP 'MRS' FORMAT=JSON");
        await sends((api) => { return api.user(scope, "anna", "MRS"); },
            "SHOW CREATE REST USER 'anna'@'MRS' FORMAT=JSON");
        await sends((api) => { return api.roles(scope); },
            "SHOW REST ROLES ON ANY SERVICE FORMAT=JSON");
        await sends((api) => { return api.daemons(scope); },
            "SHOW REST DAEMONS FORMAT=JSON");
    });

    it("reads one object with the statement its type needs", async () => {
        await sends((api) => {
            return api.object(scope, "TABLE", "/svc", "/sakila", "/actor");
        }, "SHOW CREATE REST VIEW /actor ON SERVICE /svc SCHEMA /sakila "
            + "FORMAT=JSON");
        await sends((api) => {
            return api.object(scope, "VIEW", "/svc", "/sakila", "/v");
        }, "SHOW CREATE REST VIEW /v ON SERVICE /svc SCHEMA /sakila "
            + "FORMAT=JSON");
        await sends((api) => {
            return api.object(scope, "PROCEDURE", "/svc", "/sakila", "/p");
        }, "SHOW CREATE REST PROCEDURE /p ON SERVICE /svc SCHEMA /sakila "
            + "FORMAT=JSON");
        await sends((api) => {
            return api.object(scope, "FUNCTION", "/svc", "/sakila", "/f");
        }, "SHOW CREATE REST FUNCTION /f ON SERVICE /svc SCHEMA /sakila "
            + "FORMAT=JSON");
    });

    it("merges the four object lists by request path", async () => {
        const on = " ON SERVICE /svc SCHEMA /sakila FORMAT=JSON";
        const fake = createFakeRestSql({
            answers: {
                [`SHOW REST VIEWS${on}`]: [
                    mrsObject({ request_path: "/film" }),
                    mrsObject({ request_path: "/actor" }),
                ],
                [`SHOW REST PROCEDURES${on}`]: [
                    mrsObject({
                        request_path: "/byName", object_type: "PROCEDURE",
                    }),
                ],
                // The shell answers null for an empty list.
                [`SHOW REST FUNCTIONS${on}`]: null,
                [`SHOW REST SCRIPTS${on}`]: [
                    mrsObject({
                        request_path: "/count", object_type: "SCRIPT",
                    }),
                ],
            },
        });

        const objects = await apiOver(fake).objects(scope, "/svc", "/sakila");

        expect(fake.scripts).toEqual([[
            `SHOW REST VIEWS${on};`,
            `SHOW REST PROCEDURES${on};`,
            `SHOW REST FUNCTIONS${on};`,
            `SHOW REST SCRIPTS${on};`,
        ].join("\n")]);
        expect(objects.map((object) => { return object.request_path; }))
            .toEqual(["/actor", "/byName", "/count", "/film"]);
    });

    it("merges the object lists behind the USE of a named schema",
        async () => {
            const fake = createFakeRestSql();

            await expect(apiOver(fake).objects(named, "/svc", "/s"))
                .resolves.toEqual([]);

            expect(fake.calls[0]!.statements).toHaveLength(5);
            expect(fake.calls[0]!.statements[0]).toBe(USE);
        });

    it("sends a service list through the chosen metadata schema",
        async () => {
            const fake = createFakeRestSql({
                answers: { "SHOW REST SERVICES FORMAT=JSON": [mrsService()] },
            });

            await expect(apiOver(fake).services(named)).resolves
                .toEqual([mrsService()]);
        });
});

/** A tool caller recording what it was asked, answering canned results. */
const createCaller = (answer: IToolResult = { content: [] }) => {
    const calls: Array<{
        name: string;
        args: Record<string, unknown>;
        timeoutMs?: number;
    }> = [];
    const caller: IToolCaller = {
        callTool: (name, args, timeoutMs) => {
            calls.push({
                name, args, ...(timeoutMs === undefined ? {} : { timeoutMs }),
            });

            return Promise.resolve(answer);
        },
    };

    return { calls, tools: new MrsToolsApi(caller) };
};

const text = (...texts: string[]): IToolResult => {
    return {
        content: texts.map((value) => {
            return { type: "text", text: value };
        }),
    };
};

describe("MrsToolsApi", () => {
    it("allows ten minutes for what writes many files", () => {
        expect(MRS_TOOL_TIMEOUT_MS).toBe(600000);
    });

    it("reads the SDK options of a folder", async () => {
        const { calls, tools } = createCaller(text(JSON.stringify({
            serviceUrl: "https://h/svc", sdkLanguage: "Python",
        })));

        await expect(tools.getSdkOptions("/sdk")).resolves.toEqual({
            serviceUrl: "https://h/svc", sdkLanguage: "Python",
        });
        expect(calls).toEqual([{
            name: "mrs.get_sdk_options", args: { directory: "/sdk" },
        }]);
    });

    it("answers no SDK options where there are none", async () => {
        const { tools } = createCaller();

        await expect(tools.getSdkOptions("/sdk")).resolves.toEqual({});
    });

    it("writes the SDK files", async () => {
        const { calls, tools } = createCaller();

        await tools.dumpSdkServiceFiles("c1", "/sdk", { a: 1 });

        expect(calls).toEqual([{
            name: "mrs.dump_sdk_service_files",
            args: { connection_id: "c1", directory: "/sdk", options: { a: 1 } },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
    });

    it("dumps a service, overwriting", async () => {
        const { calls, tools } = createCaller();

        await tools.dumpService("c1", "/svc", "/f.sql", "ALL");

        expect(calls).toEqual([{
            name: "mrs.dump_service",
            args: {
                connection_id: "c1",
                service_path: "/svc",
                file_path: "/f.sql",
                endpoints: "ALL",
                overwrite: true,
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
    });

    it("loads a service, under another path only where given", async () => {
        const { calls, tools } = createCaller();

        await tools.loadService("c1", "/f.sql");
        await tools.loadService("c1", "/f.sql", "/other");

        expect(calls.map((call) => { return call.args; })).toEqual([
            { connection_id: "c1", file_path: "/f.sql" },
            { connection_id: "c1", file_path: "/f.sql", as_path: "/other" },
        ]);
        expect(calls[0]!.name).toBe("mrs.load_service");
        expect(calls[0]!.timeoutMs).toBe(MRS_TOOL_TIMEOUT_MS);
    });

    it("dumps a service project with every endpoint", async () => {
        const { calls, tools } = createCaller();
        const options = {
            destination: "/out",
            servicePath: "/svc",
            name: "svc",
            description: "d",
            publisher: "p",
            version: "1.0.0",
            zip: false,
        };

        await tools.dumpServiceProject("c1", options);
        await tools.dumpServiceProject("c1",
            { ...options, iconPath: "/i.png", zip: true });

        expect(calls[0]).toEqual({
            name: "mrs.dump_service_project",
            args: {
                connection_id: "c1",
                destination: "/out",
                services: [{
                    name: "/svc",
                    include_database_endpoints: true,
                    include_static_endpoints: true,
                    include_dynamic_endpoints: true,
                }],
                settings: {
                    name: "svc",
                    description: "d",
                    publisher: "p",
                    version: "1.0.0",
                },
                overwrite: true,
                zip: false,
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        });
        expect(calls[1]!.args).toMatchObject({
            settings: { icon_path: "/i.png" },
            zip: true,
        });
    });

    it("loads a service project", async () => {
        const { calls, tools } = createCaller();

        await tools.loadServiceProject("c1", "/p.zip");

        expect(calls).toEqual([{
            name: "mrs.load_service_project",
            args: { connection_id: "c1", source: "/p.zip" },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
    });

    it("loads a content set, replacing, scripts only where told", async () => {
        const { calls, tools } = createCaller(text(JSON.stringify({
            files: ["/index.html"], message: "done",
        })));

        await expect(tools.loadContentSet("c1", "/dir", "/app", "/svc",
            "*.git", undefined)).resolves.toEqual({
            files: ["/index.html"], message: "done",
        });
        await tools.loadContentSet("c1", "/dir", "/app", "/svc", "", false);

        expect(calls[0]).toEqual({
            name: "mrs.load_content_set",
            args: {
                connection_id: "c1",
                directory: "/dir",
                content_set_path: "/app",
                service_path: "/svc",
                ignore_list: "*.git",
                replace: true,
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        });
        expect(calls[1]!.args).toMatchObject({ load_scripts: false });
    });

    it("reads a folder's script language, none as undefined", async () => {
        const cases: Array<[IToolResult, string | undefined]> = [
            [text("TypeScript"), "TypeScript"],
            [text("null"), undefined],
            [text(""), undefined],
            [{ content: [] }, undefined],
        ];
        for (const [answer, expected] of cases) {
            const { calls, tools } = createCaller(answer);

            await expect(tools.folderScriptLanguage("/dir", "x"))
                .resolves.toBe(expected);
            expect(calls).toEqual([{
                name: "mrs.get_folder_mrs_script_language",
                args: { path: "/dir", ignore_list: "x" },
            }]);
        }
    });

    it("reads a folder's script definitions", async () => {
        const { calls, tools } = createCaller(text(JSON.stringify({
            script_modules: [{ name: "m" }],
        })));

        await expect(tools.folderScriptDefinitions("/dir", "x"))
            .resolves.toEqual({ script_modules: [{ name: "m" }] });
        expect(calls).toEqual([{
            name: "mrs.get_folder_mrs_script_definitions",
            args: { path: "/dir", ignore_list: "x" },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
    });

    it("reads the plugin's version", async () => {
        const { calls, tools } = createCaller(text("1.19.0"));

        await expect(tools.version()).resolves.toBe("1.19.0");
        expect(calls).toEqual([{ name: "mrs.version", args: {} }]);
    });

    it("raises a tool's failure", async () => {
        const { tools } = createCaller({
            isError: true,
            content: [{ type: "text", text: "No such service" }],
        });

        const failure = tools.dumpService("c1", "/svc", "/f", "ALL");

        await expect(failure).rejects.toBeInstanceOf(McpToolError);
        await expect(failure).rejects.toThrow("No such service");
    });
});
