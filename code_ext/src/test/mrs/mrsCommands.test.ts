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

import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
    ConnectionManager,
    UI_BACKEND_SESSION,
} from "../../connections/connectionManager.js";
import type { IToolCaller } from "../../mcp/mariaDbApi.js";
import type { IToolResult } from "../../mcp/protocol.js";
import {
    MRS_TOOL_TIMEOUT_MS,
    MrsApi,
    MrsToolsApi,
} from "../../mrs/mrsApi.js";
import {
    MRS_DOCS_URL,
    MrsCommands,
    restDaemonUrl,
} from "../../mrs/mrsCommands.js";
import {
    MrsDialogPanel,
    type IMrsDialogSpec,
} from "../../mrs/mrsDialogPanel.js";
import type {
    IAuthAppContext,
    IAuthAppDialogValues,
    IConfigureValues,
    IContentSetDialogValues,
    IObjectContext,
    IObjectDialogValues,
    ISchemaDialogValues,
    ISdkExportValues,
    IServiceDialogValues,
    IUserContext,
    IUserDialogValues,
} from "../../mrs/mrsDialogs.js";
import type { IMrsColumns } from "../../mrs/mrsTypes.js";
import type {
    IConnectionNode,
    IObjectNode,
    ISchemaNode,
} from "../../tree/connectionsModel.js";
import {
    MrsModel,
    type IMrsAuthAppNode,
    type IMrsContentFileNode,
    type IMrsContentSetNode,
    type IMrsDaemonNode,
    type IMrsObjectNode,
    type IMrsRootNode,
    type IMrsSchemaNode,
    type IMrsServiceAuthAppNode,
    type IMrsServiceNode,
    type IMrsUserNode,
} from "../../tree/mrsModel.js";
import {
    createFakeRestSql,
    createFakeSettings,
    createRecordingLog,
    mrsAuthApp,
    mrsContentFile,
    mrsContentSet,
    mrsDaemon,
    mrsObject,
    mrsSchema,
    mrsService,
    mrsStatus,
    mrsUser,
} from "../helpers.js";
import {
    commands,
    configuration,
    env,
    errorMessages,
    fileDialogs,
    files,
    informationMessageCalls,
    informationMessages,
    inputBoxAnswers,
    openedExternally,
    quickPickAnswers,
    quickPickCalls,
    registeredCommands,
    resetVscodeMock,
    setInformationMessageAnswer,
    setWarningMessageAnswer,
    Uri,
    warningMessageCalls,
    webviewPanels,
} from "../mocks/vscode.js";

const URI = "dba@localhost:3310";
const DEV = "dev_mariadb_rest_service";
const USE_DEV = `USE REST METADATA SCHEMA \`${DEV}\`;`;
const METADATA_SCHEMAS = "SHOW REST METADATA SCHEMAS FORMAT=JSON";
const STATUS = "SHOW REST METADATA STATUS FORMAT=JSON";
const SERVICES = "SHOW REST SERVICES FORMAT=JSON";
const ANY_APPS = "SHOW REST AUTH APPS ON ANY SERVICE FORMAT=JSON";
const ROLES = "SHOW REST ROLES ON ANY SERVICE FORMAT=JSON";
const VENDORS = "SHOW REST AUTH VENDORS FORMAT=JSON";
const CONTENT_SETS =
    "SHOW REST CONTENT SETS ON SERVICE /myService FORMAT=JSON";

const metadataSchema = (name: string) => {
    return { schema_name: name, version: "5.0.0", current: true };
};

/** One configured metadata schema: what most servers have. */
const CONFIGURED = {
    [METADATA_SCHEMAS]: [metadataSchema("mariadb_rest_service")],
    [STATUS]: mrsStatus(),
};

/**
 * @param answers What the server answers REST SQL with.
 * @param options Whether the tree's connection is open, and what the
 *        `mrs.*` tools answer.
 *
 * @returns The registered commands over a fake server, and what they
 *          touched.
 */
const setup = async (
    answers: Record<string, unknown> = {},
    options: {
        open?: boolean;
        toolAnswers?: Record<string, IToolResult>;
        uris?: string[];
    } = {},
) => {
    const uris = options.uris ?? [URI];
    const api = createFakeRestSql({
        answers,
        connections: uris,
        connectionIds: Object.fromEntries(uris.map((uri, index) => {
            return [uri, `uuid-${index + 1}`];
        })),
    });
    const connections = new ConnectionManager(
        () => { return Promise.resolve(api); },
        createFakeSettings(),
    );
    const mrsApi = new MrsApi(async () => { return await connections.api(); });
    const model = new MrsModel(mrsApi, (uri) => {
        return connections.connectionIdFor(uri, UI_BACKEND_SESSION);
    });
    const toolCalls: Array<{
        name: string;
        args: Record<string, unknown>;
        timeoutMs?: number;
    }> = [];
    const caller: IToolCaller = {
        callTool: (name, args, timeoutMs) => {
            toolCalls.push({
                name, args, ...(timeoutMs === undefined ? {} : { timeoutMs }),
            });

            return Promise.resolve(options.toolAnswers?.[name]
                ?? { content: [] });
        },
    };
    const tools = new MrsToolsApi(caller);
    const log = createRecordingLog();
    const state = { refreshed: 0 };
    const mrsCommands = new MrsCommands({
        extensionUri: Uri.file("/ext") as never,
        api: mrsApi,
        tools: () => { return Promise.resolve(tools); },
        model,
        connections,
        refresh: () => { state.refreshed += 1; },
        log,
    });
    const disposables = mrsCommands.register();
    if (options.open !== false) {
        await connections.connect(uris[0]!, UI_BACKEND_SESSION);
    }

    return { api, connections, disposables, log, model, state, toolCalls };
};

/** Runs a registered command, as a menu would. */
const run = async (id: string, ...args: unknown[]): Promise<void> => {
    await commands.executeCommand(id, ...args);
};

/** The dialogs opened since the last reset. */
let shown: IMrsDialogSpec[] = [];

/** The one dialog a command opened. */
const dialog = (): IMrsDialogSpec => {
    expect(shown).toHaveLength(1);

    return shown[0]!;
};

const service = mrsService();
const schema = mrsSchema();

const root = (metadataSchema?: string): IMrsRootNode => {
    return {
        kind: "mrsRoot",
        uri: URI,
        ...(metadataSchema === undefined ? {} : { metadataSchema }),
        status: mrsStatus(),
        showPrivate: false,
    };
};

const serviceNode = (metadataSchema?: string): IMrsServiceNode => {
    return {
        kind: "mrsService",
        uri: URI,
        ...(metadataSchema === undefined ? {} : { metadataSchema }),
        service,
        showPrivate: false,
    };
};

const schemaNode: IMrsSchemaNode = {
    kind: "mrsSchema", uri: URI, service, schema, showPrivate: false,
};
const objectNode: IMrsObjectNode = {
    kind: "mrsObject", uri: URI, service, schema, object: mrsObject(),
};
const contentSetNode: IMrsContentSetNode = {
    kind: "mrsContentSet", uri: URI, service, contentSet: mrsContentSet(),
    showPrivate: false,
};
const contentFileNode: IMrsContentFileNode = {
    kind: "mrsContentFile", uri: URI, service, contentSet: mrsContentSet(),
    file: mrsContentFile(),
};
const authAppNode: IMrsAuthAppNode = {
    kind: "mrsAuthApp", uri: URI, authApp: mrsAuthApp(),
};
const serviceAuthAppNode: IMrsServiceAuthAppNode = {
    kind: "mrsServiceAuthApp", uri: URI, service, authApp: mrsAuthApp(),
};
const userNode: IMrsUserNode = {
    kind: "mrsUser", uri: URI, authApp: mrsAuthApp(), user: mrsUser(),
};
const daemonNode: IMrsDaemonNode = {
    kind: "mrsDaemon", uri: URI, daemon: mrsDaemon(), requiresUpgrade: false,
};

const connectionNode: IConnectionNode = {
    kind: "connection",
    uri: URI,
    connected: true,
    isDefault: false,
    connectionKind: "mcp",
    expandable: true,
};

const dbSchemaNode: ISchemaNode = {
    kind: "schema", uri: URI, schema: "sakila", schemaType: "User Schema",
    comment: "",
};

const tableNode: IObjectNode = {
    kind: "object", uri: URI, schema: "sakila", objectType: "table",
    name: "actor",
};

const ACTOR_COLUMNS: IMrsColumns = {
    schema: "sakila",
    name: "actor",
    type: "TABLE",
    columns: [{
        position: 1,
        name: "actor_id",
        db_column: { name: "actor_id", datatype: "int", is_primary: true },
        reference_mapping: null,
    }],
};

/** The text written to a file through `workspace.fs`. */
const written = (path: string): string => {
    return new TextDecoder().decode(files.get(path));
};

beforeEach(() => {
    MrsDialogPanel.disposeAll();
    resetVscodeMock();
    shown = [];
    const show = MrsDialogPanel.show.bind(MrsDialogPanel);
    vi.spyOn(MrsDialogPanel, "show").mockImplementation(
        (extensionUri, spec, host) => {
            shown.push(spec);
            show(extensionUri, spec, host);
        });
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("MrsCommands.register", () => {
    it("registers every REST Service command the package contributes",
        async () => {
            const packageJson = JSON.parse(readFileSync(new URL(
                "../../../package.json", import.meta.url), "utf8")) as {
                contributes: { commands: Array<{ command: string }> };
            };
            const contributed = packageJson.contributes.commands.map(
                (entry) => { return entry.command; }).filter((id) => {
                return id.startsWith("mariadb.mrs.");
            }).sort();

            const { disposables } = await setup();

            expect([...registeredCommands.keys()].sort()).toEqual(contributed);
            expect(disposables).toHaveLength(contributed.length);

            for (const disposable of disposables) {
                disposable.dispose();
            }
            expect(registeredCommands.size).toBe(0);
        });

    it("does nothing for a command run without its row", async () => {
        const { api, toolCalls } = await setup();

        for (const id of registeredCommands.keys()) {
            await run(id);
        }

        expect(api.sent).toEqual([]);
        expect(toolCalls).toEqual([]);
        expect(shown).toEqual([]);
        expect(errorMessages).toEqual([]);
    });

    it("does nothing for a command run on the wrong row", async () => {
        const { api } = await setup();
        const wrong = { kind: "mrsAuthAppGroup", uri: URI };

        for (const id of ["deleteService", "setCurrentService",
            "linkAuthApp", "unlinkAuthApp", "deleteSchema", "deleteDbObject",
            "deleteContentSet", "deleteContentFile", "deleteAuthApp",
            "deleteUser", "deleteDaemon", "editService", "editSchema",
            "editDbObject", "editContentSet", "editAuthApp", "editUser",
            "addUser", "copyCreateServiceSql", "copyCreateSchemaSql",
            "copyCreateDbObjectSql", "copyCreateContentSetSql",
            "copyCreateContentFileSql", "copyCreateAuthAppSql",
            "copyCreateUserSql", "addSchema", "addDbObject",
            "showPrivateItems", "hidePrivateItems", "exportServiceSdk",
            "dumpServiceAsProject", "addContentSet", "addAndLinkAuthApp",
            "copyDbObjectRequestPath", "openDbObjectRequestPath",
            "openContentSetRequestPath", "openContentFileRequestPath",
            "dumpCreateServiceSql", "dumpCreateSchemaSql",
            "dumpCreateDbObjectSql", "dumpCreateContentSetSql",
            "dumpCreateAuthAppSql", "dumpCreateUserSql"]) {
            await run(`mariadb.mrs.${id}`, wrong);
        }

        expect(api.sent).toEqual([]);
        expect(shown).toEqual([]);
        expect(warningMessageCalls).toEqual([]);
    });

    it("reports a failure with the log", async () => {
        const { api, log } = await setup();
        api.errors["USE REST SERVICE /myService"] = "No such service";

        await run("mariadb.mrs.setCurrentService", serviceNode());

        expect(errorMessages).toEqual(["MariaDB: No such service"]);
        expect(log.lines).toEqual(["No such service"]);
        expect(informationMessages).toEqual([]);
    });

    it("says a connection is not open rather than running anything",
        async () => {
            const { api } = await setup({}, { open: false });

            await run("mariadb.mrs.setCurrentService", serviceNode());

            expect(errorMessages).toEqual(
                [`MariaDB: The connection ${URI} is not open.`]);
            expect(api.sent).toEqual([]);
        });
});

describe("deleting", () => {
    const cases: Array<{
        id: string;
        node: unknown;
        question: string;
        action: string;
        sql: string;
        done: string;
    }> = [
        {
            id: "deleteService",
            node: serviceNode(),
            question: "Are you sure the MRS service /myService should be "
                + "deleted?",
            action: "Delete REST Service",
            sql: "DROP REST SERVICE /myService;",
            done: "The MRS service has been deleted successfully.",
        },
        {
            id: "deleteSchema",
            node: schemaNode,
            question: "Are you sure the MRS schema /sakila should be deleted?",
            action: "Delete REST Schema",
            sql: "DROP REST SCHEMA /sakila FROM SERVICE /myService;",
            done: "The MRS schema has been deleted successfully.",
        },
        {
            id: "deleteDbObject",
            node: objectNode,
            question: "Are you sure you want to delete the REST DB Object "
                + "/actor?",
            action: "Delete DB Object",
            sql: "DROP REST VIEW /actor FROM SERVICE /myService SCHEMA "
                + "/sakila;",
            done: "The REST DB Object /actor has been deleted.",
        },
        {
            id: "deleteContentSet",
            node: contentSetNode,
            question: "Are you sure you want to drop the static content set "
                + "/app?",
            action: "Delete Static Content Set",
            sql: "DROP REST CONTENT SET /app FROM SERVICE /myService;",
            done: "The MRS static content set has been deleted successfully.",
        },
        {
            id: "deleteContentFile",
            node: contentFileNode,
            question: "Are you sure you want to delete the content file "
                + "/index.html?",
            action: "Delete Content File",
            // A path with a dot is quoted.
            sql: "DROP REST CONTENT FILE `/index.html` FROM SERVICE "
                + "/myService CONTENT SET /app;",
            done: "The MRS content file has been deleted successfully.",
        },
        {
            id: "deleteAuthApp",
            node: authAppNode,
            question: "Are you sure the MRS authentication app MRS should be "
                + "deleted?",
            action: "Delete Authentication App",
            sql: "DROP REST AUTH APP 'MRS';",
            done: "The MRS Authentication App MRS has been deleted.",
        },
        {
            id: "deleteUser",
            node: userNode,
            question: "Are you sure the MRS user anna should be deleted?",
            action: "Delete User",
            sql: "DROP REST USER 'anna'@'MRS';",
            done: "The MRS User anna has been deleted.",
        },
        {
            id: "deleteDaemon",
            node: daemonNode,
            question: "Are you sure the MariaDB REST Daemon host1:8443 should "
                + "be deleted?",
            action: "Delete REST Daemon",
            sql: "DROP REST DAEMON '88888888-0000-0000-0000-000000000001';",
            done: "The MariaDB REST Daemon has been deleted successfully.",
        },
    ];

    for (const entry of cases) {
        it(`${entry.id} asks first, then drops and redraws`, async () => {
            const { api, state } = await setup();
            setWarningMessageAnswer(entry.action);

            await run(`mariadb.mrs.${entry.id}`, entry.node);

            expect(warningMessageCalls).toEqual([{
                message: entry.question,
                items: [
                    {
                        modal: true,
                        detail: "This operation cannot be reverted!",
                    },
                    entry.action,
                ],
            }]);
            expect(api.sent).toEqual([entry.sql]);
            expect(state.refreshed).toBe(1);
            expect(informationMessages).toEqual([entry.done]);
        });

        it(`${entry.id} drops nothing when declined`, async () => {
            const { api, state } = await setup();

            await run(`mariadb.mrs.${entry.id}`, entry.node);

            expect(warningMessageCalls).toHaveLength(1);
            expect(api.sent).toEqual([]);
            expect(state.refreshed).toBe(0);
            expect(informationMessages).toEqual([]);
        });
    }

    it("drops in the row's metadata schema", async () => {
        const { api } = await setup();
        setWarningMessageAnswer("Delete REST Service");

        await run("mariadb.mrs.deleteService", serviceNode(DEV));

        expect(api.scripts).toEqual([
            `${USE_DEV}\nDROP REST SERVICE /myService;`]);
    });

    it("names a daemon without an address by its name", async () => {
        await setup();

        await run("mariadb.mrs.deleteDaemon", {
            ...daemonNode, daemon: mrsDaemon({ address: "" }),
        });

        expect(warningMessageCalls[0]!.message).toBe(
            "Are you sure the MariaDB REST Daemon daemon1 should be deleted?");
    });

    it("cannot delete a user without a name", async () => {
        const { api } = await setup();

        await run("mariadb.mrs.deleteUser", {
            ...userNode, user: mrsUser({ name: null }),
        });

        expect(warningMessageCalls).toEqual([]);
        expect(api.sent).toEqual([]);
    });
});

describe("services", () => {
    it("makes a service the current one", async () => {
        const { api, state } = await setup();

        await run("mariadb.mrs.setCurrentService", serviceNode());

        expect(api.sent).toEqual(["USE REST SERVICE /myService;"]);
        expect(state.refreshed).toBe(1);
        expect(informationMessages).toEqual([
            "The MRS service has been set as the new default service."]);
    });

    it("links an auth app picked from those not linked yet", async () => {
        const { api, state } = await setup({
            "SHOW REST AUTH APPS ON SERVICE /myService FORMAT=JSON":
                [mrsAuthApp()],
            [ANY_APPS]: [
                mrsAuthApp(),
                mrsAuthApp({ name: "Google", auth_vendor: "Google" }),
            ],
        });
        quickPickAnswers.push("Google");

        await run("mariadb.mrs.linkAuthApp", serviceNode());

        expect(quickPickCalls).toEqual([
            [{ label: "Google", description: "Google" }]]);
        expect(api.sent.at(-1)).toBe(
            "ALTER REST SERVICE /myService ADD AUTH APP 'Google';");
        expect(state.refreshed).toBe(1);
        expect(informationMessages).toEqual([
            "The MRS Authentication App has been linked to service "
            + "/myService"]);
    });

    it("links nothing when the pick is cancelled", async () => {
        const { api } = await setup({
            "SHOW REST AUTH APPS ON SERVICE /myService FORMAT=JSON": [],
            [ANY_APPS]: [mrsAuthApp()],
        });

        await run("mariadb.mrs.linkAuthApp", serviceNode());

        expect(quickPickCalls).toHaveLength(1);
        expect(api.sent.filter((sql) => {
            return sql.startsWith("ALTER");
        })).toEqual([]);
    });

    it("says every app is linked already, asking nothing", async () => {
        await setup({
            "SHOW REST AUTH APPS ON SERVICE /myService FORMAT=JSON":
                [mrsAuthApp()],
            [ANY_APPS]: [mrsAuthApp()],
        });

        await run("mariadb.mrs.linkAuthApp", serviceNode());

        expect(quickPickCalls).toEqual([]);
        expect(informationMessages).toEqual(["Every REST authentication app "
            + "is linked to this service already."]);
    });

    it("unlinks an auth app once confirmed", async () => {
        const { api } = await setup();
        setWarningMessageAnswer("Unlink");

        await run("mariadb.mrs.unlinkAuthApp", serviceAuthAppNode);

        expect(warningMessageCalls[0]!.message).toBe("Are you sure the MRS "
            + "authentication app \"MRS\" should be unlinked from the "
            + "service \"/myService\"?");
        expect(api.sent).toEqual([
            "ALTER REST SERVICE /myService REMOVE AUTH APP 'MRS';"]);
        expect(informationMessages).toEqual([
            "The MRS Authentication App \"MRS\" has been unlinked."]);
    });

    it("unlinks nothing when declined", async () => {
        const { api } = await setup();

        await run("mariadb.mrs.unlinkAuthApp", serviceAuthAppNode);

        expect(api.sent).toEqual([]);
    });

    it("opens the new service dialog, and its save creates the service",
        async () => {
            const { api } = await setup({
                [ANY_APPS]: [mrsAuthApp(), mrsAuthApp({ name: "Google" })],
            });

            await run("mariadb.mrs.addService", root());

            const spec = dialog();
            expect(spec.dialog).toBe("service");
            expect(spec.title).toBe(
                "Enter Configuration Values for the New REST Service");
            expect(spec.context).toEqual({
                allAuthApps: ["MRS", "Google"], linkedAuthApps: [],
            });
            const values = spec.values as IServiceDialogValues;
            expect(values.path).toBe("/myService");
            expect(values.authApps).toEqual(["MRS"]);
            expect(webviewPanels).toHaveLength(1);

            api.sent.length = 0;
            await expect(spec.save({ ...values, path: "/shop" }))
                .resolves.toBe("The MRS service has been created.");
            expect(api.scripts.at(-1)).toMatch(/^CREATE REST SERVICE \/shop\n/);
            expect(api.sent.at(-1)).toBe("USE REST SERVICE /shop;");
        });

    it("opens the service dialog on a service, and its save alters it",
        async () => {
            const { api } = await setup({
                [ANY_APPS]: [mrsAuthApp(), mrsAuthApp({ name: "Google" })],
                "SHOW REST AUTH APPS ON SERVICE /myService FORMAT=JSON":
                    [mrsAuthApp()],
            });

            await run("mariadb.mrs.editService", serviceNode());

            const spec = dialog();
            expect(spec.title).toBe("Adjust the REST Service Configuration");
            expect(spec.context).toEqual({
                allAuthApps: ["MRS", "Google"],
                linkedAuthApps: ["MRS"],
                existingPath: "/myService",
            });
            const values = spec.values as IServiceDialogValues;
            expect(values.authApps).toEqual(["MRS"]);
            expect(values.makeCurrent).toBe(false);

            await expect(spec.save({ ...values, authApps: ["Google"] }))
                .resolves.toBe(
                    "The MRS service has been successfully updated.");
            const sql = api.sent.at(-1)!;
            expect(sql).toMatch(/^ALTER REST SERVICE \/myService\n/);
            expect(sql).toContain("'Google'");
        });

    it("redraws and says what a save said, through the panel", async () => {
        const { state } = await setup({ [ANY_APPS]: [] });
        await run("mariadb.mrs.addService", root());
        const panel = webviewPanels[0]!;

        panel.webview.receive({
            type: "save", values: dialog().values,
        });
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(state.refreshed).toBe(1);
        expect(informationMessages).toEqual([
            "The MRS service has been created."]);
        expect(panel.disposed).toBe(true);
    });
});

describe("copying and dumping REST SQL", () => {
    const CREATE = "CREATE REST SERVICE /myService;";

    const copies: Array<[string, unknown, string]> = [
        ["copyCreateServiceSql", serviceNode(),
            "SHOW CREATE REST SERVICE /myService"],
        ["copyCreateServiceSqlIncludeDatabaseEndpoints", serviceNode(),
            "SHOW CREATE REST SERVICE /myService INCLUDING DATABASE ENDPOINTS"],
        ["copyCreateSchemaSql", schemaNode,
            "SHOW CREATE REST SCHEMA /sakila ON SERVICE /myService"],
        ["copyCreateDbObjectSql", objectNode,
            "SHOW CREATE REST VIEW /actor ON SERVICE /myService SCHEMA "
            + "/sakila"],
        ["copyCreateContentSetSql", contentSetNode,
            "SHOW CREATE REST CONTENT SET /app ON SERVICE /myService"],
        ["copyCreateContentFileSql", contentFileNode,
            "SHOW CREATE REST CONTENT FILE `/index.html` ON SERVICE "
            + "/myService CONTENT SET /app"],
        ["copyCreateAuthAppSql", authAppNode,
            "SHOW CREATE REST AUTH APP 'MRS'"],
        ["copyCreateAuthAppSql", serviceAuthAppNode,
            "SHOW CREATE REST AUTH APP 'MRS'"],
        ["copyCreateUserSql", userNode, "SHOW CREATE REST USER 'anna'@'MRS'"],
    ];

    for (const [id, node, sql] of copies) {
        it(`${id} copies what ${sql} answers`, async () => {
            const { api } = await setup({ [sql]: CREATE });

            await run(`mariadb.mrs.${id}`, node);

            expect(api.sent).toEqual([`${sql};`]);
            expect(env.clipboard.text).toBe(CREATE);
            expect(informationMessages).toEqual([
                "The CREATE statement was copied to the system clipboard"]);
        });
    }

    it("copies a procedure with its own keyword", async () => {
        const { api } = await setup();

        await run("mariadb.mrs.copyCreateDbObjectSql", {
            ...objectNode,
            object: mrsObject({ object_type: "PROCEDURE", request_path: "/p" }),
        });

        expect(api.sent).toEqual([
            "SHOW CREATE REST PROCEDURE /p ON SERVICE /myService SCHEMA "
            + "/sakila;"]);
    });

    it("dumps a service with the endpoints picked to a file", async () => {
        const sql = "SHOW CREATE REST SERVICE /myService INCLUDING ALL "
            + "ENDPOINTS";
        const { api } = await setup({ [sql]: CREATE });
        quickPickAnswers.push("Export SQL Script Including All Endpoints");
        fileDialogs.saveAnswer = Uri.file("/out/svc.mrs.sql");

        await run("mariadb.mrs.dumpCreateServiceSql", serviceNode());

        expect(quickPickCalls[0]!.map((item) => { return item.label; }))
            .toEqual([
                "Export SQL Script Including All Endpoints",
                "Export SQL Script Including Database Endpoints Only",
                "Export SQL Script Including Database and Static Endpoints "
                + "Only",
            ]);
        expect(api.sent).toEqual([`${sql};`]);
        expect(fileDialogs.saveCalls[0]).toMatchObject({
            title: "Export REST Service SQL to file...",
            saveLabel: "Export SQL File",
            filters: { "REST SQL": ["sql"] },
        });
        expect((fileDialogs.saveCalls[0] as { defaultUri: Uri }).defaultUri
            .path).toMatch(/\/myService\.mrs\.sql$/);
        // A file ends its last line.
        expect(written("/out/svc.mrs.sql")).toBe(`${CREATE}\n`);
        expect(informationMessages).toEqual([
            "The REST Service SQL was exported"]);
    });

    it("dumps the endpoints of each choice", async () => {
        for (const [label, endpoints] of [
            ["Export SQL Script Including Database Endpoints Only",
                "DATABASE"],
            ["Export SQL Script Including Database and Static Endpoints Only",
                "DATABASE AND STATIC"],
        ]) {
            resetVscodeMock();
            const { api } = await setup();
            quickPickAnswers.push(label!);

            await run("mariadb.mrs.dumpCreateServiceSql", serviceNode());

            expect(api.sent).toEqual([`SHOW CREATE REST SERVICE /myService `
                + `INCLUDING ${endpoints} ENDPOINTS;`]);
        }
    });

    it("dumps nothing when the endpoints are not picked", async () => {
        const { api } = await setup();

        await run("mariadb.mrs.dumpCreateServiceSql", serviceNode());

        expect(api.sent).toEqual([]);
        expect(fileDialogs.saveCalls).toEqual([]);
    });

    it("writes nothing when the save dialog is cancelled", async () => {
        await setup({
            "SHOW CREATE REST SCHEMA /sakila ON SERVICE /myService":
                "CREATE REST SCHEMA /sakila;\n",
        });

        await run("mariadb.mrs.dumpCreateSchemaSql", schemaNode);

        expect(fileDialogs.saveCalls).toHaveLength(1);
        expect(files.size).toBe(0);
        expect(informationMessages).toEqual([]);
    });

    const dumps: Array<[string, unknown, string, string, string]> = [
        ["dumpCreateSchemaSql", schemaNode,
            "SHOW CREATE REST SCHEMA /sakila ON SERVICE /myService",
            "Schema", "sakila.myService.mrs.sql"],
        ["dumpCreateDbObjectSql", objectNode,
            "SHOW CREATE REST VIEW /actor ON SERVICE /myService SCHEMA /sakila",
            "Object", "actor.sakila.mrs.sql"],
        ["dumpCreateContentSetSql", contentSetNode,
            "SHOW CREATE REST CONTENT SET /app ON SERVICE /myService",
            "Content Set", "app.myService.mrs.sql"],
        ["dumpCreateAuthAppSql", serviceAuthAppNode,
            "SHOW CREATE REST AUTH APP 'MRS'", "Auth App", "mRS.mrs.sql"],
        ["dumpCreateUserSql", userNode, "SHOW CREATE REST USER 'anna'@'MRS'",
            "User", "anna.mRS.mrs.sql"],
    ];

    for (const [id, node, sql, kind, name] of dumps) {
        it(`${id} writes what ${sql} answers`, async () => {
            await setup({ [sql]: "CREATE;\n" });
            fileDialogs.saveAnswer = Uri.file("/out/x.sql");

            await run(`mariadb.mrs.${id}`, node);

            const options = fileDialogs.saveCalls[0] as {
                title: string;
                defaultUri: Uri;
            };
            expect(options.title).toBe(`Export REST ${kind} SQL to file...`);
            expect(options.defaultUri.path.endsWith(`/${name}`)).toBe(true);
            expect(written("/out/x.sql")).toBe("CREATE;\n");
            expect(informationMessages).toEqual([
                `The REST ${kind} SQL was exported`]);
        });
    }
});

describe("request paths", () => {
    it("copies and opens an object's URL on the REST Daemon", async () => {
        await setup();

        await run("mariadb.mrs.copyDbObjectRequestPath", objectNode);
        await run("mariadb.mrs.openDbObjectRequestPath", objectNode);

        expect(env.clipboard.text).toBe(
            "https://localhost:8443/myService/sakila/actor");
        expect(informationMessages).toEqual([
            "The DB Object Path was copied to the system clipboard"]);
        expect(openedExternally.map(String)).toEqual([
            "https://localhost:8443/myService/sakila/actor"]);
    });

    it("opens a content set and a content file", async () => {
        await setup();

        await run("mariadb.mrs.openContentSetRequestPath", contentSetNode);
        await run("mariadb.mrs.openContentFileRequestPath", contentFileNode);

        expect(openedExternally.map(String)).toEqual([
            "https://localhost:8443/myService/app/",
            "https://localhost:8443/myService/app/index.html",
        ]);
    });

    it("goes by the daemon URL setting, trailing slashes left off",
        async () => {
            expect(restDaemonUrl()).toBe("https://localhost:8443");
            configuration.set("mariadb.mrs.restDaemonUrl",
                "http://rest.example.com:8080//");
            expect(restDaemonUrl()).toBe("http://rest.example.com:8080");

            await setup();
            await run("mariadb.mrs.openDbObjectRequestPath", objectNode);

            expect(openedExternally.map(String)).toEqual([
                "http://rest.example.com:8080/myService/sakila/actor"]);
        });

    it("opens the documentation", async () => {
        await setup();

        await run("mariadb.mrs.docs");

        expect(openedExternally.map(String)).toEqual([MRS_DOCS_URL]);
        expect(MRS_DOCS_URL).toMatch(/^https:\/\/mariadb\.com\//);
    });
});

describe("private items", () => {
    it("shows and hides a root's private items, redrawing", async () => {
        const { model, state } = await setup(CONFIGURED);

        await run("mariadb.mrs.showPrivateItems", root());
        expect((await model.rootsOf(URI))[0]).toMatchObject({
            showPrivate: true,
        });

        await run("mariadb.mrs.hidePrivateItems", root());
        expect((await model.rootsOf(URI))[0]).toMatchObject({
            showPrivate: false,
        });
        expect(state.refreshed).toBe(2);
    });
});

describe("configure", () => {
    it("deploys the metadata on a connection that has none", async () => {
        const { api } = await setup({
            [METADATA_SCHEMAS]: [],
            [STATUS]: mrsStatus({
                service_configured: false, current_metadata_version: null,
            }),
        });

        await run("mariadb.mrs.configure", connectionNode);

        const spec = dialog();
        expect(spec.dialog).toBe("configure");
        expect(spec.title).toBe(
            "Configure Instance for MariaDB REST Service Support");
        expect(spec.context).toMatchObject({ init: true });
        const values = spec.values as IConfigureValues;
        expect(values.metadataSchema).toBe("mariadb_rest_service");

        api.sent.length = 0;
        api.scripts.length = 0;
        await expect(spec.save({
            ...values, authAppUser: "admin", authAppPassword: "Secret1!x",
        })).resolves.toBe("MariaDB REST Service configured successfully.");
        // The new schema may not be chosen before it exists.
        expect(api.sent.some((sql) => { return sql.startsWith("USE"); }))
            .toBe(false);
        expect(api.sent[0]).toMatch(/^CONFIGURE REST METADATA\n/);
        expect(api.sent.some((sql) => {
            return sql.startsWith("CREATE REST AUTH APP IF NOT EXISTS 'MRS'");
        })).toBe(true);
    });

    it("configures the one metadata schema there is", async () => {
        const { api } = await setup(CONFIGURED);

        await run("mariadb.mrs.configure", connectionNode);

        const spec = dialog();
        expect(spec.title).toBe("MariaDB REST Service Configuration");
        expect(spec.context).toMatchObject({ init: false });
        expect(api.sent).toEqual([`${METADATA_SCHEMAS};`, `${STATUS};`]);

        api.sent.length = 0;
        await spec.save(spec.values);
        expect(api.sent).toHaveLength(1);
        expect(api.sent[0]).toMatch(/^CONFIGURE REST METADATA\n    ENABLED/);
    });

    it("asks which of several metadata schemas, and configures it",
        async () => {
            const { api } = await setup({
                [METADATA_SCHEMAS]: [
                    metadataSchema("mariadb_rest_service"),
                    metadataSchema(DEV),
                ],
                [STATUS]: mrsStatus({ metadata_schema: DEV }),
            });
            quickPickAnswers.push(DEV);

            await run("mariadb.mrs.configure", connectionNode);

            expect(quickPickCalls[0]!.map((item) => { return item.label; }))
                .toEqual(["mariadb_rest_service", DEV,
                    "New REST metadata schema..."]);
            expect(api.scripts).toEqual([`${USE_DEV}\n${STATUS};`]);
            const spec = dialog();
            expect(spec.title).toBe("MariaDB REST Service Configuration");

            api.scripts.length = 0;
            await spec.save(spec.values);
            expect(api.scripts).toHaveLength(1);
            expect(api.scripts[0]!.startsWith(`${USE_DEV}\nCONFIGURE`))
                .toBe(true);
        });

    it("deploys a new metadata schema beside the others", async () => {
        const { api } = await setup({
            [METADATA_SCHEMAS]: [
                metadataSchema("mariadb_rest_service"),
                metadataSchema(DEV),
            ],
        });
        quickPickAnswers.push("New REST metadata schema...");

        await run("mariadb.mrs.configure", connectionNode);

        const spec = dialog();
        expect(spec.title).toBe(
            "Configure Instance for MariaDB REST Service Support");
        expect((spec.values as IConfigureValues).metadataSchema).toBe("");
        // Nothing read but the list: there is no status for a new one.
        expect(api.sent).toEqual([`${METADATA_SCHEMAS};`]);

        await spec.save({
            ...spec.values as IConfigureValues,
            metadataSchema: "test_mariadb_rest_service",
            createAuthApp: false,
        });
        expect(api.sent.at(-1)).toMatch(
            /^CONFIGURE REST METADATA\n {4}SCHEMA `test_mariadb_rest_service`/);
    });

    it("opens nothing when the metadata schema is not picked", async () => {
        await setup({
            [METADATA_SCHEMAS]: [metadataSchema("a"), metadataSchema("b")],
        });

        await run("mariadb.mrs.configure", connectionNode);

        expect(shown).toEqual([]);
    });

    it("configures a root's own metadata schema", async () => {
        const { api } = await setup({ [STATUS]: mrsStatus() });

        await run("mariadb.mrs.configure", root(DEV));

        expect(api.scripts).toEqual([`${USE_DEV}\n${STATUS};`]);
        expect(dialog().title).toBe("MariaDB REST Service Configuration");
    });

    it("opens the connection it is asked on", async () => {
        const { connections } = await setup(CONFIGURED, { open: false });

        await run("mariadb.mrs.configure", connectionNode);

        expect(connections.connectionIdFor(URI, UI_BACKEND_SESSION))
            .toBe("uuid-1");
        expect(shown).toHaveLength(1);
    });
});

describe("REST schemas", () => {
    it("adds a database schema to the REST service", async () => {
        const { api } = await setup({
            ...CONFIGURED,
            [SERVICES]: [service],
        });

        await run("mariadb.mrs.addSchema", dbSchemaNode);

        const spec = dialog();
        expect(spec.dialog).toBe("schema");
        expect(spec.title).toBe(
            "Enter Configuration Values for the New REST Schema");
        expect(spec.context).toEqual({ services: ["/myService"] });
        const values = spec.values as ISchemaDialogValues;
        expect(values).toMatchObject({
            dbSchema: "sakila", servicePath: "/myService",
        });

        await expect(spec.save(values)).resolves.toBe(
            "The MRS schema has been added successfully.");
        expect(api.sent.at(-1)!.startsWith("CREATE REST SCHEMA /sakila "
            + "ON SERVICE /myService FROM `sakila`")).toBe(true);
    });

    it("says the REST Service is not configured where it is not",
        async () => {
            await setup({ [METADATA_SCHEMAS]: [] });

            await run("mariadb.mrs.addSchema", dbSchemaNode);

            expect(errorMessages).toEqual(["The MariaDB REST Service is not "
                + "configured on this connection. Use Configure MariaDB REST "
                + "Service first."]);
            expect(shown).toEqual([]);
        });

    it("says a service is needed first", async () => {
        await setup({ ...CONFIGURED, [SERVICES]: [] });

        await run("mariadb.mrs.addSchema", dbSchemaNode);

        expect(errorMessages).toEqual([
            "Please create a REST Service before adding a DB Schema."]);
        expect(shown).toEqual([]);
    });

    it("asks which metadata schema where there are several", async () => {
        const { api } = await setup({
            [METADATA_SCHEMAS]: [
                metadataSchema("mariadb_rest_service"),
                metadataSchema(DEV),
            ],
            [STATUS]: mrsStatus(),
            [SERVICES]: [service],
        });
        quickPickAnswers.push(DEV);

        await run("mariadb.mrs.addSchema", dbSchemaNode);

        expect(quickPickCalls.at(-1)!.map((item) => { return item.label; }))
            .toEqual(["mariadb_rest_service", DEV]);
        expect(api.scripts.at(-1)).toBe(`${USE_DEV}\n${SERVICES};`);
        expect(shown).toHaveLength(1);
    });

    it("adds nothing when no metadata schema is picked", async () => {
        await setup({
            [METADATA_SCHEMAS]: [metadataSchema("a"), metadataSchema("b")],
            [STATUS]: mrsStatus(),
        });

        await run("mariadb.mrs.addSchema", dbSchemaNode);

        expect(shown).toEqual([]);
        expect(errorMessages).toEqual([]);
    });

    it("edits a REST schema", async () => {
        const { api } = await setup({ [SERVICES]: [service] });

        await run("mariadb.mrs.editSchema", schemaNode);

        const spec = dialog();
        expect(spec.title).toBe("Adjust the REST Schema Configuration");
        expect(spec.context).toEqual({
            services: ["/myService"], existingPath: "/sakila",
        });

        await expect(spec.save(spec.values)).resolves.toBe(
            "The MRS schema has been updated successfully.");
        expect(api.sent.at(-1)).toMatch(/^ALTER REST SCHEMA \/sakila/);
    });
});

describe("REST objects", () => {
    const COLUMNS = "SHOW REST COLUMNS FROM TABLE `sakila`.`actor` FORMAT=JSON";
    const SCHEMAS = "SHOW REST SCHEMAS ON SERVICE /myService FORMAT=JSON";
    const OTHER_SCHEMAS = "SHOW REST SCHEMAS ON SERVICE /other FORMAT=JSON";

    it("adds a table to the schema's REST schema", async () => {
        const { api } = await setup({
            ...CONFIGURED,
            [SERVICES]: [service],
            [SCHEMAS]: [schema],
            [COLUMNS]: ACTOR_COLUMNS,
        });

        await run("mariadb.mrs.addDbObject", tableNode);

        const spec = dialog();
        expect(spec.dialog).toBe("object");
        expect(spec.title).toBe(
            "Enter Configuration Values for the New REST Object");
        expect(spec.context as IObjectContext).toEqual({
            services: ["/myService"],
            schemas: { "/myService": ["/sakila"] },
        });
        const values = spec.values as IObjectDialogValues;
        expect(values).toMatchObject({
            servicePath: "/myService",
            schemaPath: "/sakila",
            requestPath: "/actor",
        });
        expect(values.document.fields.length).toBeGreaterThan(0);

        api.sent.length = 0;
        await expect(spec.save(values)).resolves.toBe(
            "The MRS Database Object actor was successfully created.");
        expect(api.sent[0]).toMatch(/^CREATE REST VIEW \/actor/);

        await expect(spec.loadColumns!("sakila", "film")).resolves
            .toBeUndefined();
        expect(api.sent.at(-1)).toBe(
            "SHOW REST COLUMNS FROM TABLE `sakila`.`film` FORMAT=JSON;");
    });

    it("refuses an object type REST does not serve", async () => {
        const { api } = await setup(CONFIGURED);

        await run("mariadb.mrs.addDbObject", {
            ...tableNode, objectType: "trigger",
        });

        expect(errorMessages).toEqual([
            "The database object type 'trigger' is not supported at this "
            + "time"]);
        expect(api.sent).toEqual([]);
    });

    it("says the REST Service is not configured where it is not",
        async () => {
            await setup({ [METADATA_SCHEMAS]: [] });

            await run("mariadb.mrs.addDbObject", tableNode);

            expect(errorMessages).toHaveLength(1);
            expect(errorMessages[0]).toMatch(/^The MariaDB REST Service is /);
        });

    it("says a service is needed first", async () => {
        await setup({ ...CONFIGURED, [SERVICES]: [] });

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(errorMessages).toEqual([
            "Please create a REST Service before adding DB Objects."]);
    });

    it("adds to the current service of several, asking nothing",
        async () => {
            await setup({
                ...CONFIGURED,
                [SERVICES]: [
                    mrsService({ full_service_path: "/other" }),
                    { ...service, is_current: true },
                ],
                [SCHEMAS]: [schema],
                [OTHER_SCHEMAS]: [],
                [COLUMNS]: ACTOR_COLUMNS,
            });

            await run("mariadb.mrs.addDbObject", tableNode);

            expect(quickPickCalls).toEqual([]);
            expect((dialog().values as IObjectDialogValues).servicePath)
                .toBe("/myService");
        });

    it("asks which service where none is current", async () => {
        await setup({
            ...CONFIGURED,
            [SERVICES]: [mrsService({ full_service_path: "/other" }), service],
            [SCHEMAS]: [schema],
            [OTHER_SCHEMAS]: [],
            [COLUMNS]: ACTOR_COLUMNS,
        });
        quickPickAnswers.push("/myService");

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(quickPickCalls[0]!.map((item) => { return item.label; }))
            .toEqual(["/other", "/myService"]);
        expect((dialog().values as IObjectDialogValues).servicePath)
            .toBe("/myService");
    });

    it("says no service was picked when none is", async () => {
        await setup({
            ...CONFIGURED,
            [SERVICES]: [mrsService({ full_service_path: "/other" }), service],
        });

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(errorMessages).toEqual(["No REST Service selected."]);
        expect(shown).toEqual([]);
    });

    it("offers to add the schema first, and adds it", async () => {
        const { api, state } = await setup({
            ...CONFIGURED,
            [SERVICES]: [service],
            [SCHEMAS]: [],
            [COLUMNS]: ACTOR_COLUMNS,
        });
        // The schema exists once it was created.
        const execute = api.executeSql;
        api.executeSql = (connectionId, sql, page) => {
            if (sql.startsWith("CREATE REST SCHEMA")) {
                api.answers[SCHEMAS] = [schema];
            }

            return execute(connectionId, sql, page);
        };
        setInformationMessageAnswer("Yes");

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(informationMessageCalls[0]).toEqual({
            message: "The database schema sakila has not been added to the "
                + "REST Service. Do you want to add the schema now?",
            items: [{ modal: true }, "Yes"],
        });
        expect(api.sent).toContain(
            "CREATE REST SCHEMA /sakila ON SERVICE /myService FROM `sakila`;");
        expect(state.refreshed).toBe(1);
        expect(dialog().dialog).toBe("object");
    });

    it("adds neither schema nor object when that is declined", async () => {
        const { api } = await setup({
            ...CONFIGURED,
            [SERVICES]: [service],
            [SCHEMAS]: [],
        });

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(informationMessageCalls).toHaveLength(1);
        expect(api.sent.some((sql) => { return sql.startsWith("CREATE"); }))
            .toBe(false);
        expect(shown).toEqual([]);
    });

    it("stops where the schema added cannot be found", async () => {
        await setup({
            ...CONFIGURED,
            [SERVICES]: [service],
            [SCHEMAS]: [],
        });
        setInformationMessageAnswer("Yes");

        await run("mariadb.mrs.addDbObject", tableNode);

        expect(shown).toEqual([]);
    });

    it("edits an object with its stored data mapping", async () => {
        const stored = { ...mrsObject(), data_mappings: [] };
        const { api } = await setup({
            [SERVICES]: [service],
            [SCHEMAS]: [schema],
            [COLUMNS]: ACTOR_COLUMNS,
            ["SHOW CREATE REST VIEW /actor ON SERVICE /myService SCHEMA "
                + "/sakila FORMAT=JSON"]: stored,
        });

        await run("mariadb.mrs.editDbObject", objectNode);

        const spec = dialog();
        expect(spec.title).toBe("Adjust the REST Object Configuration");
        expect((spec.context as IObjectContext).existingPath).toBe("/actor");

        api.sent.length = 0;
        await expect(spec.save(spec.values)).resolves.toBe(
            "The MRS Database Object actor was successfully updated.");
        expect(api.sent[0]).toMatch(/^ALTER REST VIEW \/actor/);
    });

    it("reads a procedure's parameters", async () => {
        const { api } = await setup({
            [SERVICES]: [service],
            [SCHEMAS]: [schema],
            ["SHOW REST COLUMNS FROM PROCEDURE `sakila`.`film_in_stock` "
                + "FORMAT=JSON"]: {
                schema: "sakila",
                name: "film_in_stock",
                type: "PROCEDURE",
                parameters: [],
            },
        });

        await run("mariadb.mrs.editDbObject", {
            ...objectNode,
            object: mrsObject({
                name: "film_in_stock", object_type: "PROCEDURE",
                request_path: "/filmInStock",
            }),
        });

        expect(api.sent).toContain("SHOW CREATE REST PROCEDURE /filmInStock "
            + "ON SERVICE /myService SCHEMA /sakila FORMAT=JSON;");
        expect(shown).toHaveLength(1);
    });
});

describe("content sets", () => {
    const LANGUAGE = "mrs.get_folder_mrs_script_language";
    const DEFINITIONS = "mrs.get_folder_mrs_script_definitions";

    it("uploads a folder as a new content set, then applies the dialog",
        async () => {
            const { api, toolCalls } = await setup({
                [SERVICES]: [service],
                "SHOW REST CONTENT SETS ON SERVICE /myService FORMAT=JSON":
                    [mrsContentSet({ request_path: "/web" })],
            }, {
                toolAnswers: {
                    "mrs.load_content_set": {
                        content: [{
                            type: "text",
                            text: JSON.stringify({ files: ["/a", "/b"] }),
                        }],
                    },
                },
            });

            await run("mariadb.mrs.addContentSet", serviceNode());

            const spec = dialog();
            expect(spec.dialog).toBe("contentSet");
            expect(spec.title).toBe("Enter Configuration Values for the New "
                + "MRS Static Content Set");
            expect(spec.context).toEqual({ services: ["/myService"] });
            const values = spec.values as IContentSetDialogValues;
            expect(values.servicePath).toBe("/myService");

            api.sent.length = 0;
            await expect(spec.save({
                ...values, directory: "/src/web", requestPath: " /web ",
            })).resolves.toBe("The MRS static content set has been added "
                + "successfully. 2 file(s) have been uploaded");
            expect(toolCalls).toEqual([{
                name: "mrs.load_content_set",
                args: {
                    connection_id: "uuid-1",
                    directory: "/src/web",
                    content_set_path: "/web",
                    service_path: "/myService",
                    ignore_list: values.ignoreList,
                    load_scripts: false,
                    replace: true,
                },
                timeoutMs: MRS_TOOL_TIMEOUT_MS,
            }]);
            expect(api.sent.at(-1)).toMatch(/^ALTER REST CONTENT SET \/web/);
        });

    it("leaves the scripts to the plugin where they are to be loaded",
        async () => {
            const { toolCalls } = await setup({
                [SERVICES]: [service], [CONTENT_SETS]: [],
            }, {
                toolAnswers: {
                    "mrs.load_content_set": {
                        content: [{ type: "text", text: "{\"files\":[]}" }],
                    },
                },
            });
            await run("mariadb.mrs.addContentSet", serviceNode());

            await dialog().save({
                ...dialog().values as IContentSetDialogValues,
                directory: "/d", loadScripts: true,
            });

            expect(toolCalls[0]!.args).not.toHaveProperty("load_scripts");
        });

    it("chooses the metadata schema before the upload", async () => {
        const { api, toolCalls } = await setup({
            [SERVICES]: [service], [CONTENT_SETS]: [],
        }, {
            toolAnswers: {
                "mrs.load_content_set": {
                    content: [{ type: "text", text: "{\"files\":[]}" }],
                },
            },
        });
        await run("mariadb.mrs.addContentSet", serviceNode(DEV));
        api.calls.length = 0;

        await dialog().save({
            ...dialog().values as IContentSetDialogValues, directory: "/d",
        });

        expect(api.calls[0]!.statements).toEqual([USE_DEV]);
        expect(toolCalls).toHaveLength(1);
    });

    it("analyzes a folder's MRS scripts for the dialog", async () => {
        const { toolCalls } = await setup({ [SERVICES]: [service] }, {
            toolAnswers: {
                [LANGUAGE]: { content: [{ type: "text", text: "TypeScript" }] },
                [DEFINITIONS]: {
                    content: [{
                        type: "text", text: "{\"script_modules\":[]}",
                    }],
                },
            },
        });
        await run("mariadb.mrs.addContentSet", serviceNode());

        await expect(dialog().analyzeFolder!("/d", "x")).resolves.toEqual({
            language: "TypeScript",
            definitions: { script_modules: [] },
        });
        expect(toolCalls.map((call) => { return call.name; }))
            .toEqual([LANGUAGE, DEFINITIONS]);
    });

    it("reports no scripts for a folder without any", async () => {
        const { toolCalls } = await setup({ [SERVICES]: [service] }, {
            toolAnswers: {
                [LANGUAGE]: { content: [{ type: "text", text: "null" }] },
            },
        });
        await run("mariadb.mrs.addContentSet", serviceNode());

        await expect(dialog().analyzeFolder!("/d", "")).resolves.toEqual({});
        expect(toolCalls).toHaveLength(1);
    });

    it("says a service is needed first", async () => {
        await setup({ [SERVICES]: [] });

        await run("mariadb.mrs.addContentSet", serviceNode());

        expect(errorMessages).toEqual([
            "Please create a REST Service before adding a content set."]);
    });

    it("edits a content set with ALTER only", async () => {
        const { api, toolCalls } = await setup({ [SERVICES]: [service] });

        await run("mariadb.mrs.editContentSet", contentSetNode);

        const spec = dialog();
        expect(spec.title).toBe(
            "Adjust the MRS Static Content Set Configuration");
        expect(spec.context).toEqual({
            services: ["/myService"], existingPath: "/app",
        });
        await expect(spec.save(spec.values)).resolves.toBe(
            "The MRS static content set has been updated.");
        expect(api.sent.at(-1)).toMatch(/^ALTER REST CONTENT SET \/app/);
        expect(toolCalls).toEqual([]);
    });

    it("uploads an Explorer folder to the one open connection", async () => {
        await setup({ ...CONFIGURED, [SERVICES]: [service] });

        await run("mariadb.mrs.addFolderAsContentSet",
            Uri.file("/home/me/shop/dist"));

        const values = dialog().values as IContentSetDialogValues;
        expect(values.directory).toBe("/home/me/shop/dist");
        expect(values.requestPath).toBe("/shopContent");
    });

    it("asks which connection where several are open", async () => {
        const other = "app@localhost:3311";
        const { connections } = await setup({
            ...CONFIGURED, [SERVICES]: [service],
        }, { uris: [URI, other] });
        await connections.connect(other, UI_BACKEND_SESSION);
        quickPickAnswers.push(other);

        await run("mariadb.mrs.addFolderAsContentSet", Uri.file("/d"));

        expect(quickPickCalls[0]!.map((item) => { return item.label; }))
            .toEqual([URI, other]);
        expect(shown).toHaveLength(1);
    });

    it("needs an open connection for an Explorer folder", async () => {
        await setup({}, { open: false });

        await run("mariadb.mrs.addFolderAsContentSet", Uri.file("/d"));

        expect(errorMessages).toEqual(["Please open a connection with the "
            + "MariaDB REST Service configured in the Connections view "
            + "first."]);
        expect(quickPickCalls).toEqual([]);
    });
});

describe("auth apps", () => {
    const vendors = [
        { id: "30000000-0000-0000-0000-000000000000", name: "MRS",
            comments: null, enabled: true },
        { id: "11000000-0000-0000-0000-000000000000", name: "Google",
            comments: null, enabled: true },
    ];
    const roles = [{
        id: "r1", caption: "Full Access", derived_from_role_id: null,
        specific_to_service_id: null, description: null, options: null,
    }];

    it("adds an app", async () => {
        const { api } = await setup({ [VENDORS]: vendors, [ROLES]: roles });

        await run("mariadb.mrs.addAuthApp", {
            kind: "mrsAuthAppGroup", uri: URI,
        });

        const spec = dialog();
        expect(spec.dialog).toBe("authApp");
        expect(spec.title).toBe("Enter Configuration Values for the New MRS "
            + "Authentication App");
        expect(spec.context as IAuthAppContext).toEqual({
            vendors: [
                { id: vendors[0]!.id, name: "MRS" },
                { id: vendors[1]!.id, name: "Google" },
            ],
            roles: ["Full Access"],
            hasSecret: false,
        });
        expect((spec.values as IAuthAppDialogValues).defaultRole)
            .toBe("Full Access");

        await expect(spec.save({
            ...spec.values as IAuthAppDialogValues, name: "Mine",
        })).resolves.toBe("The MRS Authentication App has been added.");
        expect(api.sent.at(-1))
            .toMatch(/^CREATE REST AUTH APP 'Mine' VENDOR MRS/);
    });

    it("adds an app and links it to the service", async () => {
        const { api } = await setup({ [VENDORS]: vendors, [ROLES]: roles });

        await run("mariadb.mrs.addAndLinkAuthApp", serviceNode());

        const spec = dialog();
        expect((spec.context as IAuthAppContext).linkToService)
            .toBe("/myService");
        await spec.save({ ...spec.values as IAuthAppDialogValues, name: "A" });
        expect(api.sent.at(-1)).toBe(
            "ALTER REST SERVICE /myService ADD AUTH APP 'A';");
    });

    it("edits an app from either of its rows", async () => {
        for (const node of [authAppNode, serviceAuthAppNode]) {
            shown = [];
            const { api } = await setup({ [VENDORS]: vendors, [ROLES]: roles });

            await run("mariadb.mrs.editAuthApp", {
                ...node, authApp: mrsAuthApp({ has_app_secret: true }),
            });

            const spec = dialog();
            expect(spec.title).toBe(
                "Adjust the MRS Authentication App Configuration");
            expect(spec.context).toMatchObject({
                existingName: "MRS", hasSecret: true,
            });
            await expect(spec.save(spec.values)).resolves.toBe(
                "The MRS Authentication App has been updated.");
            expect(api.sent.at(-1)).toMatch(/^ALTER REST AUTH APP 'MRS'/);
        }
    });
});

describe("users", () => {
    const roles = [
        {
            id: "r1", caption: "Full Access", derived_from_role_id: null,
            specific_to_service_id: null, description: null, options: null,
        },
        {
            id: "r2", caption: "Shop Reader", derived_from_role_id: null,
            specific_to_service_id: "s1", specific_to_service: "/myService",
            description: null, options: null,
        },
    ];

    it("adds a user with the app's default role", async () => {
        const { api } = await setup({ [ROLES]: roles });

        await run("mariadb.mrs.addUser", {
            ...authAppNode, authApp: mrsAuthApp({ default_role_id: "r1" }),
        });

        const spec = dialog();
        expect(spec.dialog).toBe("user");
        expect(spec.title).toBe("Enter new MariaDB REST User Values");
        expect(spec.context as IUserContext).toEqual({
            authApp: "MRS",
            authAppVendorId: "30000000-0000-0000-0000-000000000000",
            allRoles: ["Full Access", "Shop Reader"],
            roleServices: {
                "Full Access": null, "Shop Reader": "/myService",
            },
            existingRoles: [],
            hasPassword: false,
        });
        const values = spec.values as IUserDialogValues;
        expect(values.roles).toEqual(["Full Access"]);

        await expect(spec.save({
            ...values, name: "bob", password: "Secret1!x",
        })).resolves.toBe("The MRS User \"bob\" has been added.");
        expect(api.sent.at(-2)).toMatch(
            /^CREATE REST USER 'bob'@'MRS' IDENTIFIED BY 'Secret1!x'/);
        expect(api.sent.at(-1)).toBe(
            "GRANT REST ROLE 'Full Access' ON ANY SERVICE TO 'bob'@'MRS';");
    });

    it("edits a user as SHOW CREATE has it", async () => {
        const full = mrsUser({
            roles: [{ role_id: "r2", caption: "Shop Reader" }],
        });
        const { api } = await setup({
            [ROLES]: roles,
            "SHOW CREATE REST USER 'anna'@'MRS' FORMAT=JSON": full,
        });

        await run("mariadb.mrs.editUser", userNode);

        const spec = dialog();
        expect(spec.title).toBe("Adjust the REST User");
        expect(spec.context).toMatchObject({
            existingName: "anna",
            existingRoles: ["Shop Reader"],
            hasPassword: true,
        });

        await expect(spec.save({
            ...spec.values as IUserDialogValues, roles: ["Full Access"],
        })).resolves.toBe("The MRS User \"anna\" has been updated.");
        expect(api.sent.slice(-3)).toEqual([
            expect.stringMatching(/^ALTER REST USER 'anna'@'MRS'/),
            "REVOKE REST ROLE 'Shop Reader' ON SERVICE /myService FROM "
            + "'anna'@'MRS';",
            "GRANT REST ROLE 'Full Access' ON ANY SERVICE TO 'anna'@'MRS';",
        ]);
    });

    it("edits a user without a name from its list entry", async () => {
        const { api } = await setup({ [ROLES]: roles });

        await run("mariadb.mrs.editUser", {
            ...userNode, user: mrsUser({ name: null, has_password: false }),
        });

        expect(api.sent.some((sql) => {
            return sql.startsWith("SHOW CREATE REST USER");
        })).toBe(false);
        expect(dialog().context).not.toHaveProperty("existingName");
    });
});

describe("the mrs tools", () => {
    it("exports the SDK with the options the folder keeps", async () => {
        const { toolCalls } = await setup({}, {
            toolAnswers: {
                "mrs.get_sdk_options": {
                    content: [{
                        type: "text",
                        text: JSON.stringify({ sdkLanguage: "Python" }),
                    }],
                },
            },
        });
        fileDialogs.saveAnswer = Uri.file("/out/my.mrs.sdk");

        await run("mariadb.mrs.exportServiceSdk", serviceNode());

        expect(fileDialogs.saveCalls[0]).toMatchObject({
            title: "Export REST Service SDK Files...",
            saveLabel: "Export SDK Files",
        });
        expect(toolCalls).toEqual([{
            name: "mrs.get_sdk_options", args: { directory: "/out/my.mrs.sdk" },
        }]);
        const spec = dialog();
        expect(spec.dialog).toBe("sdkExport");
        expect(spec.title).toBe("Export MRS SDK Files for /myService");
        expect(spec.values).toEqual({
            directory: "/out/my.mrs.sdk",
            serviceUrl: "https://localhost:8443/myService",
            sdkLanguage: "Python",
            addAppBaseClass: "",
            header: "",
        });

        await expect(spec.save({
            ...spec.values as ISdkExportValues,
            addAppBaseClass: "MrsBaseAppPreact.ts",
            header: "// hi",
        })).resolves.toBe("MRS SDK Files exported successfully.");
        expect(toolCalls[1]).toEqual({
            name: "mrs.dump_sdk_service_files",
            args: {
                connection_id: "uuid-1",
                directory: "/out/my.mrs.sdk",
                options: {
                    url_context_root: "/myService",
                    service_url: "https://localhost:8443/myService",
                    sdk_language: "Python",
                    add_app_base_class: "MrsBaseAppPreact.ts",
                    header: "// hi",
                },
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        });
    });

    it("starts the SDK export from the defaults where none are kept",
        async () => {
            const { api, toolCalls } = await setup({}, {
                toolAnswers: {
                    "mrs.get_sdk_options": {
                        isError: true,
                        content: [{ type: "text", text: "no options" }],
                    },
                },
            });
            fileDialogs.saveAnswer = Uri.file("/out/x");

            await run("mariadb.mrs.exportServiceSdk", serviceNode(DEV));

            const spec = dialog();
            expect(spec.values).toMatchObject({ sdkLanguage: "TypeScript" });
            await spec.save(spec.values);
            expect(api.sent).toEqual([USE_DEV]);
            expect(toolCalls[1]!.args.options).toEqual({
                url_context_root: "/myService",
                service_url: "https://localhost:8443/myService",
                sdk_language: "TypeScript",
            });
        });

    it("exports nothing when the folder is not picked", async () => {
        const { toolCalls } = await setup();

        await run("mariadb.mrs.exportServiceSdk", serviceNode());

        expect(toolCalls).toEqual([]);
        expect(shown).toEqual([]);
    });

    it("dumps a service as a project", async () => {
        const { toolCalls } = await setup();
        fileDialogs.saveAnswer = Uri.file("/out/Shop.ZIP");
        inputBoxAnswers.push("shop", "The shop", "MariaDB", "2.0.0");
        fileDialogs.openAnswer = [Uri.file("/icon.png")];

        await run("mariadb.mrs.dumpServiceAsProject", serviceNode());

        expect(toolCalls).toEqual([{
            name: "mrs.dump_service_project",
            args: {
                connection_id: "uuid-1",
                destination: "/out/Shop.ZIP",
                services: [{
                    name: "/myService",
                    include_database_endpoints: true,
                    include_static_endpoints: true,
                    include_dynamic_endpoints: true,
                }],
                settings: {
                    name: "shop",
                    description: "The shop",
                    publisher: "MariaDB",
                    version: "2.0.0",
                    icon_path: "/icon.png",
                },
                overwrite: true,
                zip: true,
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
        expect(informationMessages).toEqual([
            "The REST Project has been dumped successfully."]);
    });

    it("dumps a project folder without an icon", async () => {
        const { api, toolCalls } = await setup();
        fileDialogs.saveAnswer = Uri.file("/out/shop");
        inputBoxAnswers.push("shop", "", "", "1.0.0");

        await run("mariadb.mrs.dumpServiceAsProject", serviceNode(DEV));

        expect(api.sent).toEqual([USE_DEV]);
        expect(toolCalls[0]!.args).toMatchObject({ zip: false });
        expect((toolCalls[0]!.args.settings as object))
            .not.toHaveProperty("icon_path");
    });

    it("dumps no project when a question is cancelled", async () => {
        for (const answers of [[undefined], ["shop", undefined]]) {
            resetVscodeMock();
            const { toolCalls } = await setup();
            fileDialogs.saveAnswer = Uri.file("/out/shop");
            inputBoxAnswers.push(...answers);

            await run("mariadb.mrs.dumpServiceAsProject", serviceNode());

            expect(toolCalls).toEqual([]);
        }
    });

    it("dumps no project when the target is not picked", async () => {
        const { toolCalls } = await setup();

        await run("mariadb.mrs.dumpServiceAsProject", serviceNode());

        expect(inputBoxAnswers).toEqual([]);
        expect(toolCalls).toEqual([]);
    });

    it("loads a project from disk", async () => {
        const { api, state, toolCalls } = await setup();
        fileDialogs.openAnswer = [Uri.file("/p.zip")];

        await run("mariadb.mrs.loadProjectFromDisk", serviceNode(DEV));

        expect(api.sent).toEqual([USE_DEV]);
        expect(toolCalls).toEqual([{
            name: "mrs.load_service_project",
            args: { connection_id: "uuid-1", source: "/p.zip" },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
        expect(state.refreshed).toBe(1);
        expect(informationMessages).toEqual([
            "The REST Project has been loaded successfully."]);
    });

    it("loads no project when none is picked", async () => {
        const { toolCalls } = await setup();

        await run("mariadb.mrs.loadProjectFromDisk", root());

        expect(toolCalls).toEqual([]);
    });

    it("loads a service from disk, under another path if given",
        async () => {
            const { state, toolCalls } = await setup();
            fileDialogs.openAnswer = [Uri.file("/svc.mrs.sql")];
            inputBoxAnswers.push("  ", " /copy ");

            await run("mariadb.mrs.loadServiceFromDisk", root());
            await run("mariadb.mrs.loadServiceFromDisk", root());

            expect(toolCalls.map((call) => { return call.args; })).toEqual([
                { connection_id: "uuid-1", file_path: "/svc.mrs.sql" },
                {
                    connection_id: "uuid-1",
                    file_path: "/svc.mrs.sql",
                    as_path: "/copy",
                },
            ]);
            expect(toolCalls[0]!.name).toBe("mrs.load_service");
            expect(state.refreshed).toBe(2);
        });

    it("loads no service when cancelled", async () => {
        const { toolCalls } = await setup();

        await run("mariadb.mrs.loadServiceFromDisk", root());
        fileDialogs.openAnswer = [Uri.file("/svc.mrs.sql")];
        await run("mariadb.mrs.loadServiceFromDisk", root());

        expect(toolCalls).toEqual([]);
    });

    it("rebuilds an SDK folder of the Explorer", async () => {
        const { toolCalls } = await setup(CONFIGURED);

        await run("mariadb.mrs.rebuildMrsSdk", Uri.file("/app/x.mrs.sdk"));

        expect(toolCalls).toEqual([{
            name: "mrs.dump_sdk_service_files",
            args: {
                connection_id: "uuid-1",
                directory: "/app/x.mrs.sdk",
                options: {},
            },
            timeoutMs: MRS_TOOL_TIMEOUT_MS,
        }]);
        expect(informationMessages).toEqual([
            "MRS SDK Files exported successfully."]);
    });

    it("rebuilds in the metadata schema picked", async () => {
        const { api, toolCalls } = await setup({
            [METADATA_SCHEMAS]: [
                metadataSchema("mariadb_rest_service"), metadataSchema(DEV),
            ],
            [STATUS]: mrsStatus(),
        });
        quickPickAnswers.push(DEV);

        await run("mariadb.mrs.rebuildMrsSdk", Uri.file("/x.mrs.sdk"));

        expect(api.sent.at(-1)).toBe(USE_DEV);
        expect(toolCalls).toHaveLength(1);
    });

    it("rebuilds nothing without the REST Service", async () => {
        const { toolCalls } = await setup({ [METADATA_SCHEMAS]: [] });

        await run("mariadb.mrs.rebuildMrsSdk", Uri.file("/x.mrs.sdk"));

        expect(toolCalls).toEqual([]);
        expect(errorMessages).toHaveLength(1);
    });

    it("needs an open connection to rebuild, offering no empty pick",
        async () => {
            const { toolCalls } = await setup({}, { open: false });

            await run("mariadb.mrs.rebuildMrsSdk", Uri.file("/x.mrs.sdk"));

            expect(errorMessages).toEqual(["Please open a connection with "
                + "the MariaDB REST Service configured in the Connections "
                + "view first."]);
            expect(toolCalls).toEqual([]);
            // As adding a folder does: nothing to pick from is not asked.
            expect(quickPickCalls).toEqual([]);
        });
});
