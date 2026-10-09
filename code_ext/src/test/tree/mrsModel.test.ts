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

import { MrsApi } from "../../mrs/mrsApi.js";
import {
    compareVersions,
    isMrsNode,
    mrsHasChildren,
    MrsModel,
    type IMrsContentSetNode,
    type IMrsDaemonGroupNode,
    type IMrsDaemonNode,
    type IMrsRootNode,
    type IMrsSchemaNode,
    type IMrsServiceNode,
    type MrsNode,
} from "../../tree/mrsModel.js";
import {
    createFakeRestSql,
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
    type FakeRestSql,
} from "../helpers.js";

const URI = "dba@localhost:3310";
const SCHEMAS = "SHOW REST METADATA SCHEMAS FORMAT=JSON";
const STATUS = "SHOW REST METADATA STATUS FORMAT=JSON";
const USE_DEV = "USE REST METADATA SCHEMA `dev_mariadb_rest_service`;";

/**
 * @param answers What the server answers REST SQL with.
 * @param open Whether the tree's connection is open.
 *
 * @returns A model over a fake server.
 */
const createModel = (
    answers: Record<string, unknown> = {},
    open = true,
) => {
    const fake = createFakeRestSql({ answers });
    const log = createRecordingLog();
    const model = new MrsModel(new MrsApi(() => {
        return Promise.resolve(fake);
    }), (uri) => {
        return open && uri === URI ? "uuid-1" : undefined;
    }, log);

    return { fake, log, model };
};

const metadataSchema = (name: string) => {
    return { schema_name: name, version: "5.0.0", current: true };
};

const root = (overrides: Partial<IMrsRootNode> = {}): IMrsRootNode => {
    return {
        kind: "mrsRoot",
        uri: URI,
        status: mrsStatus(),
        showPrivate: false,
        ...overrides,
    };
};

const serviceNode = (
    overrides: Partial<IMrsServiceNode> = {},
): IMrsServiceNode => {
    return {
        kind: "mrsService",
        uri: URI,
        service: mrsService(),
        showPrivate: false,
        ...overrides,
    };
};

/** The statements a script or call sent, the USE left out. */
const sentWithout = (fake: FakeRestSql): string[] => {
    return fake.sent.filter((sql) => { return !sql.startsWith("USE "); });
};

describe("MrsModel.rootsOf", () => {
    it("gives a connection that is not open none, asking nothing",
        async () => {
            const { fake, model } = createModel({}, false);

            await expect(model.rootsOf(URI)).resolves.toEqual([]);
            expect(fake.sent).toEqual([]);
        });

    it("gives none where there is no metadata schema", async () => {
        const { fake, model } = createModel({ [SCHEMAS]: [] });

        await expect(model.rootsOf(URI)).resolves.toEqual([]);
        expect(fake.sent).toEqual([`${SCHEMAS};`]);
    });

    it("gives one root, without naming the only schema", async () => {
        const { fake, model } = createModel({
            [SCHEMAS]: [metadataSchema("mariadb_rest_service")],
            [STATUS]: mrsStatus(),
        });

        await expect(model.rootsOf(URI)).resolves.toEqual([{
            kind: "mrsRoot",
            uri: URI,
            status: mrsStatus(),
            showPrivate: false,
        }]);
        // No USE before the status: the shell picks the only one.
        expect(fake.sent).toEqual([`${SCHEMAS};`, `${STATUS};`]);
    });

    it("gives one root per schema, each with its status", async () => {
        const { fake, model } = createModel({
            [SCHEMAS]: [
                metadataSchema("mariadb_rest_service"),
                metadataSchema("dev_mariadb_rest_service"),
            ],
            [STATUS]: mrsStatus(),
        });

        const roots = await model.rootsOf(URI);

        expect(roots).toEqual([
            {
                kind: "mrsRoot",
                uri: URI,
                metadataSchema: "mariadb_rest_service",
                status: mrsStatus(),
                showPrivate: false,
            },
            {
                kind: "mrsRoot",
                uri: URI,
                metadataSchema: "dev_mariadb_rest_service",
                status: mrsStatus(),
                showPrivate: false,
            },
        ]);
        expect(fake.scripts).toEqual([
            `USE REST METADATA SCHEMA \`mariadb_rest_service\`;\n${STATUS};`,
            `${USE_DEV}\n${STATUS};`,
        ]);
    });

    it("says the metadata is being deployed instead of a root", async () => {
        const { model } = createModel({
            [SCHEMAS]: [metadataSchema("mariadb_rest_service")],
            [STATUS]: mrsStatus({ service_being_upgraded: true }),
        });

        await expect(model.rootsOf(URI)).resolves.toEqual([{
            kind: "mrsMessage",
            uri: URI,
            message: "The REST metadata is being deployed. Refresh once it "
                + "is done.",
        }]);
    });

    it("names the schema being deployed where there are several",
        async () => {
            const { model } = createModel({
                [SCHEMAS]: [
                    metadataSchema("a_mariadb_rest_service"),
                    metadataSchema("dev_mariadb_rest_service"),
                ],
                [STATUS]: mrsStatus({ service_being_upgraded: true }),
            });

            const roots = await model.rootsOf(URI);

            expect(roots[1]).toEqual({
                kind: "mrsMessage",
                uri: URI,
                metadataSchema: "dev_mariadb_rest_service",
                message: "The REST metadata in dev_mariadb_rest_service is "
                    + "being deployed. Refresh once it is done.",
            });
        });

    it("skips a schema that is not configured", async () => {
        const { model } = createModel({
            [SCHEMAS]: [metadataSchema("mariadb_rest_service")],
            [STATUS]: mrsStatus({ service_configured: false }),
        });

        await expect(model.rootsOf(URI)).resolves.toEqual([]);
    });

    it("logs what could not be read and shows nothing", async () => {
        const { fake, log, model } = createModel();
        fake.errors[SCHEMAS] = "Access denied";

        await expect(model.rootsOf(URI)).resolves.toEqual([]);
        expect(log.lines).toEqual([
            `Could not read the REST Service of ${URI}: Access denied`]);
    });

    it("logs a failure that is not an Error too", async () => {
        const fake = createFakeRestSql();
        const log = createRecordingLog();
        const model = new MrsModel(new MrsApi(() => {
            return Promise.reject("gone");
        }), () => { return "uuid-1"; }, log);
        void fake;

        await expect(model.rootsOf(URI)).resolves.toEqual([]);
        expect(log.lines).toEqual([
            `Could not read the REST Service of ${URI}: gone`]);
    });

    it("logs nowhere when given no log", async () => {
        const fake = createFakeRestSql({ errors: { [SCHEMAS]: "x" } });
        const model = new MrsModel(new MrsApi(() => {
            return Promise.resolve(fake);
        }), () => { return "uuid-1"; });

        await expect(model.rootsOf(URI)).resolves.toEqual([]);
    });

    it("carries whether private items are shown", async () => {
        const { model } = createModel({
            [SCHEMAS]: [metadataSchema("mariadb_rest_service")],
            [STATUS]: mrsStatus(),
        });

        model.setShowPrivate({ uri: URI }, true);
        expect((await model.rootsOf(URI))[0]).toMatchObject({
            showPrivate: true,
        });

        model.setShowPrivate({ uri: URI }, false);
        expect((await model.rootsOf(URI))[0]).toMatchObject({
            showPrivate: false,
        });
    });

    it("shows private items per metadata schema", async () => {
        const { model } = createModel({
            [SCHEMAS]: [
                metadataSchema("mariadb_rest_service"),
                metadataSchema("dev_mariadb_rest_service"),
            ],
            [STATUS]: mrsStatus(),
        });

        model.setShowPrivate(
            { uri: URI, metadataSchema: "dev_mariadb_rest_service" }, true);
        const roots = await model.rootsOf(URI) as IMrsRootNode[];

        expect(roots.map((node) => { return node.showPrivate; }))
            .toEqual([false, true]);
    });
});

describe("MrsModel.scopeOf", () => {
    it("is the tree's connection, with the node's metadata schema", () => {
        const { model } = createModel();

        expect(model.scopeOf({ uri: URI })).toEqual({ connectionId: "uuid-1" });
        expect(model.scopeOf({ uri: URI, metadataSchema: "m" }))
            .toEqual({ connectionId: "uuid-1", metadataSchema: "m" });
    });

    it("is undefined while the connection is not open", () => {
        const { model } = createModel({}, false);

        expect(model.scopeOf({ uri: URI })).toBeUndefined();
    });
});

describe("MrsModel.getChildren", () => {
    it("gives nothing while the connection is not open", async () => {
        const { fake, model } = createModel({}, false);

        await expect(model.getChildren(root())).resolves.toEqual([]);
        expect(fake.sent).toEqual([]);
    });

    it("lists a root's services, then its daemons and auth apps",
        async () => {
            const service = mrsService();
            const { model } = createModel({
                "SHOW REST SERVICES FORMAT=JSON": [service],
            });

            await expect(model.getChildren(root({ showPrivate: true })))
                .resolves.toEqual([
                    {
                        kind: "mrsService", uri: URI, service,
                        showPrivate: true,
                    },
                    {
                        kind: "mrsDaemonGroup", uri: URI,
                        requiredVersion: "26.10.0",
                    },
                    { kind: "mrsAuthAppGroup", uri: URI },
                ]);
        });

    it("carries the metadata schema to every child", async () => {
        const { fake, model } = createModel({
            "SHOW REST SERVICES FORMAT=JSON": [mrsService()],
        });

        const children = await model.getChildren(root({
            metadataSchema: "dev_mariadb_rest_service",
        }));

        expect(children.map((child) => { return child.metadataSchema; }))
            .toEqual(Array(3).fill("dev_mariadb_rest_service"));
        expect(fake.sent[0]).toBe(USE_DEV);
    });

    describe("of a service", () => {
        const answers = {
            "SHOW REST SCHEMAS ON SERVICE /myService FORMAT=JSON": [
                mrsSchema({ request_path: "/a" }),
                mrsSchema({ request_path: "/hidden", enabled: 2 }),
                mrsSchema({ request_path: "/off", enabled: 0 }),
            ],
            "SHOW REST CONTENT SETS ON SERVICE /myService FORMAT=JSON": [
                mrsContentSet({ request_path: "/app" }),
                mrsContentSet({ request_path: "/scripts", enabled: 2 }),
            ],
            "SHOW REST AUTH APPS ON SERVICE /myService FORMAT=JSON": [
                mrsAuthApp(),
            ],
        };

        const describeChildren = (children: MrsNode[]): string[] => {
            return children.map((child) => {
                switch (child.kind) {
                    case "mrsSchema": {
                        return `schema ${child.schema.request_path}`;
                    }

                    case "mrsContentSet": {
                        return `contentSet ${child.contentSet.request_path}`;
                    }

                    case "mrsServiceAuthApp": {
                        return `authApp ${child.authApp.name}`;
                    }

                    default: {
                        return child.kind;
                    }
                }
            });
        };

        it("lists its schemas, content sets and linked apps, private ones "
            + "left out", async () => {
            const { fake, model } = createModel(answers);

            const children = await model.getChildren(serviceNode());

            expect(describeChildren(children)).toEqual([
                "schema /a", "schema /off", "contentSet /app", "authApp MRS"]);
            expect(sentWithout(fake)).toEqual(Object.keys(answers).map(
                (sql) => { return `${sql};`; }));
            expect(children[0]).toEqual({
                kind: "mrsSchema", uri: URI, service: mrsService(),
                schema: mrsSchema({ request_path: "/a" }), showPrivate: false,
            });
            expect(children[3]).toEqual({
                kind: "mrsServiceAuthApp", uri: URI, service: mrsService(),
                authApp: mrsAuthApp(),
            });
        });

        it("lists the private ones where they are shown", async () => {
            const { model } = createModel(answers);

            const children = await model.getChildren(
                serviceNode({ showPrivate: true }));

            expect(describeChildren(children)).toEqual([
                "schema /a", "schema /hidden", "schema /off",
                "contentSet /app", "contentSet /scripts", "authApp MRS"]);
            expect((children[4] as IMrsContentSetNode).showPrivate).toBe(true);
        });
    });

    it("lists a schema's objects, private ones only where shown",
        async () => {
            const objects = [
                mrsObject({ request_path: "/actor" }),
                mrsObject({ request_path: "/secret", enabled: 2 }),
            ];
            const { fake, model } = createModel({
                ["SHOW REST VIEWS ON SERVICE /myService SCHEMA /sakila "
                    + "FORMAT=JSON"]: objects,
            });
            const node: IMrsSchemaNode = {
                kind: "mrsSchema", uri: URI, service: mrsService(),
                schema: mrsSchema(), showPrivate: false,
            };

            await expect(model.getChildren(node)).resolves.toEqual([{
                kind: "mrsObject", uri: URI, service: mrsService(),
                schema: mrsSchema(), object: objects[0],
            }]);
            expect(fake.scripts).toHaveLength(1);

            const all = await model.getChildren({ ...node, showPrivate: true });
            expect(all).toHaveLength(2);
        });

    it("lists a content set's files, private ones only where shown",
        async () => {
            const files = [
                mrsContentFile(),
                mrsContentFile({ request_path: "/x.js", enabled: 2 }),
            ];
            const { model } = createModel({
                ["SHOW REST CONTENT FILES ON SERVICE /myService CONTENT SET "
                    + "/app FORMAT=JSON"]: files,
            });
            const node: IMrsContentSetNode = {
                kind: "mrsContentSet", uri: URI, service: mrsService(),
                contentSet: mrsContentSet(), showPrivate: false,
            };

            await expect(model.getChildren(node)).resolves.toEqual([{
                kind: "mrsContentFile", uri: URI, service: mrsService(),
                contentSet: mrsContentSet(), file: files[0],
            }]);
            await expect(model.getChildren({ ...node, showPrivate: true }))
                .resolves.toHaveLength(2);
        });

    it("lists every auth app under the group", async () => {
        const { model } = createModel({
            "SHOW REST AUTH APPS ON ANY SERVICE FORMAT=JSON": [mrsAuthApp()],
        });

        await expect(model.getChildren({ kind: "mrsAuthAppGroup", uri: URI }))
            .resolves.toEqual([{
                kind: "mrsAuthApp", uri: URI, authApp: mrsAuthApp(),
            }]);
    });

    it("lists an auth app's users", async () => {
        const { model } = createModel({
            "SHOW REST USERS FOR AUTH APP 'MRS' FORMAT=JSON": [mrsUser()],
        });

        await expect(model.getChildren({
            kind: "mrsAuthApp", uri: URI, authApp: mrsAuthApp(),
        })).resolves.toEqual([{
            kind: "mrsUser", uri: URI, authApp: mrsAuthApp(), user: mrsUser(),
        }]);
    });

    it("lists the daemons, marking those older than required", async () => {
        const daemons = [
            mrsDaemon({ version: "26.9.4" }),
            mrsDaemon({ version: "26.10.0" }),
            mrsDaemon({ version: "27.1.0" }),
        ];
        const { model } = createModel({
            "SHOW REST DAEMONS FORMAT=JSON": daemons,
        });
        const group: IMrsDaemonGroupNode = {
            kind: "mrsDaemonGroup", uri: URI, requiredVersion: "26.10.0",
        };

        const children = await model.getChildren(group) as IMrsDaemonNode[];

        expect(children.map((child) => { return child.requiresUpgrade; }))
            .toEqual([true, false, false]);
        expect(children[0]).toEqual({
            kind: "mrsDaemon", uri: URI, daemon: daemons[0],
            requiresUpgrade: true,
        });
    });

    it("lists the services a daemon serves", async () => {
        const daemon = mrsDaemon();
        const { model } = createModel({
            [`SHOW REST SERVICES FOR DAEMON '${daemon.id}' FORMAT=JSON`]:
                [mrsService()],
        });

        await expect(model.getChildren({
            kind: "mrsDaemon", uri: URI, daemon, requiresUpgrade: false,
        })).resolves.toEqual([{
            kind: "mrsDaemonService", uri: URI, daemon, service: mrsService(),
        }]);
    });

    it("gives the leaves nothing, asking nothing", async () => {
        const { fake, model } = createModel();

        for (const leaf of [
            { kind: "mrsMessage", uri: URI, message: "m" },
            {
                kind: "mrsObject", uri: URI, service: mrsService(),
                schema: mrsSchema(), object: mrsObject(),
            },
            {
                kind: "mrsUser", uri: URI, authApp: mrsAuthApp(),
                user: mrsUser(),
            },
        ] as MrsNode[]) {
            await expect(model.getChildren(leaf)).resolves.toEqual([]);
        }
        expect(fake.sent).toEqual([]);
    });

    it("lets a failure through, for the view to report", async () => {
        const { fake, model } = createModel();
        fake.errors["SHOW REST SERVICES FORMAT=JSON"] = "lost";

        await expect(model.getChildren(root())).rejects.toThrow("lost");
    });
});

describe("mrsHasChildren", () => {
    it("is true for the branches and false for the leaves", () => {
        const branches = ["mrsRoot", "mrsService", "mrsSchema",
            "mrsContentSet", "mrsAuthAppGroup", "mrsAuthApp",
            "mrsDaemonGroup", "mrsDaemon"];
        const leaves = ["mrsMessage", "mrsObject", "mrsContentFile",
            "mrsServiceAuthApp", "mrsUser", "mrsDaemonService"];

        for (const kind of branches) {
            expect(mrsHasChildren({ kind } as MrsNode), kind).toBe(true);
        }
        for (const kind of leaves) {
            expect(mrsHasChildren({ kind } as MrsNode), kind).toBe(false);
        }
    });
});

describe("isMrsNode", () => {
    it("tells the REST Service rows from the others", () => {
        for (const kind of ["mrsRoot", "mrsMessage", "mrsService",
            "mrsSchema", "mrsObject", "mrsContentSet", "mrsContentFile",
            "mrsServiceAuthApp", "mrsAuthAppGroup", "mrsAuthApp", "mrsUser",
            "mrsDaemonGroup", "mrsDaemon", "mrsDaemonService"]) {
            expect(isMrsNode({ kind }), kind).toBe(true);
        }
        for (const kind of ["connection", "schema", "objectGroup", "object",
            "folder", "connectionStatus", "mrs"]) {
            expect(isMrsNode({ kind }), kind).toBe(false);
        }
    });
});

describe("compareVersions", () => {
    it("compares part by part, as numbers", () => {
        expect(compareVersions("26.10.0", "26.9.4")).toBeGreaterThan(0);
        expect(compareVersions("26.9.4", "26.10.0")).toBeLessThan(0);
        expect(compareVersions("26.10.0", "26.10.0")).toBe(0);
    });

    it("takes a missing part as 0", () => {
        expect(compareVersions("26.10", "26.10.0")).toBe(0);
        expect(compareVersions("26.10.1", "26.10")).toBeGreaterThan(0);
    });

    it("reads a dash as a separator and text as 0", () => {
        expect(compareVersions("26.10.0-1", "26.10.0")).toBeGreaterThan(0);
        expect(compareVersions("x.1", "0.1")).toBe(0);
    });
});
