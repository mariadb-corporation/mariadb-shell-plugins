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
    mkdirSync,
    mkdtempSync,
    readdirSync,
    rmSync,
    symlinkSync,
    writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { MariaDbApi } from "../../mcp/mariaDbApi.js";
import { SandboxApi } from "../../mcp/sandboxApi.js";
import { createSdkConnector } from "../../mcp/sdkConnector.js";
import type { IMcpConnection } from "../../mcp/session.js";
import {
    buildDocument,
    loadReference,
    updateField,
} from "../../mrs/dataMapping.js";
import { MrsApi, MrsToolsApi, type IMrsScope } from "../../mrs/mrsApi.js";
import {
    authAppDefaults,
    authAppStatements,
    configureDefaults,
    configureContextOf,
    configureStatements,
    contentSetDefaults,
    contentSetStatements,
    objectDefaults,
    objectStatements,
    schemaDefaults,
    schemaStatements,
    serviceDefaults,
    serviceStatements,
    userDefaults,
    userStatements,
} from "../../mrs/mrsDialogs.js";
import { MRS_VENDOR_ID } from "../../mrs/mrsTypes.js";
import { MCP_SERVER_ARGS } from "../../shell/constants.js";
import { MrsModel, type MrsNode } from "../../tree/mrsModel.js";

/**
 * The extension's REST SQL against a real MariaDB Shell: what the dialogs
 * build is run by the shell's `mrs` module, and what the tree reads back
 * is checked. Opt-in, like mcp_plugin's end-to-end tests: it deploys a
 * sandbox. Run with
 *
 *     MARIADB_SHELL_E2E=/path/to/mariadb-shell npx vitest run mrsShell
 *
 * with `mariadbd` on the PATH. The shell runs with a configuration home of
 * its own whose plugins are this repository's, so the user's connections
 * and the installed plugins are left alone. It is set through `env`: the
 * MCP SDK passes a server only a few variables of its own environment.
 */

const shell = process.env.MARIADB_SHELL_E2E;
const PASSWORD = "Mrs_e2e_root_1";

const freePort = async (): Promise<number> => {
    return await new Promise((done) => {
        const server = createServer();
        server.listen(0, () => {
            const address = server.address();
            const port = typeof address === "object" && address !== null
                ? address.port : 0;
            server.close(() => { done(port); });
        });
    });
};

describe.runIf(shell !== undefined)("REST SQL against a real shell", () => {
    let home: string;
    let connection: IMcpConnection;
    let db: MariaDbApi;
    let sandbox: SandboxApi;
    let tools: MrsToolsApi;
    let api: MrsApi;
    let port: number;
    let uri: string;
    let connectionId: string;
    const log: string[] = [];

    beforeAll(async () => {
        home = mkdtempSync(join(tmpdir(), "mrs_e2e_"));
        const plugins = join(home, "plugins");
        mkdirSync(plugins, { recursive: true });
        const repo = resolve(__dirname, "../../../..");
        for (const plugin of ["mcp_plugin", "mrs_plugin", "msm_plugin"]) {
            symlinkSync(join(repo, plugin), join(plugins, plugin));
        }
        connection = await createSdkConnector().open({
            command: "/usr/bin/env",
            args: [`MARIADB_SHELL_USER_CONFIG_HOME=${home}`, shell!,
                ...MCP_SERVER_ARGS],
        }, (line) => { log.push(line); });
        db = new MariaDbApi(connection);
        sandbox = new SandboxApi(connection);
        tools = new MrsToolsApi(connection);
        api = new MrsApi(async () => { return db; });

        port = await freePort();
        await sandbox.deploy({ port, password: PASSWORD, mcpAccess: false });
        uri = `mariadb://root@127.0.0.1:${port}`;
        connectionId = await db.connect(uri);
    }, 600_000);

    afterAll(async () => {
        try {
            await sandbox?.stop(port);
            await sandbox?.delete(port);
        } finally {
            await connection?.close();
            rmSync(home, { recursive: true, force: true });
        }
    }, 300_000);

    const sql = async (statement: string): Promise<void> => {
        const result = await db.executeSql(connectionId, statement);
        expect(result.error).toBeUndefined();
    };

    it("deploys the metadata with an auth app and a user", async () => {
        expect(await api.metadataSchemas(connectionId)).toEqual([]);
        const status = await api.status({ connectionId });
        expect(status.service_configured).toBe(false);

        const values = {
            ...configureDefaults(status),
            authAppUser: "admin",
            authAppPassword: "Admin_pw_1",
        };
        await api.run({ connectionId },
            configureStatements(values, configureContextOf(status)));

        const configured = await api.status({ connectionId });
        expect(configured.service_configured).toBe(true);
        const apps = await api.authApps({ connectionId });
        expect(apps.map((app) => { return app.name; })).toContain("MRS");
        const users = await api.users({ connectionId }, "MRS");
        expect(users.map((user) => { return user.name; })).toEqual(["admin"]);
        // The app's default role is given at the first sign-in.
        const [app] = apps.filter((candidate) => {
            return candidate.name === "MRS";
        });
        expect(app.default_role_id)
            .toBe("31000000-0000-0000-0000-000000000000");
    }, 300_000);

    it("changes the configuration options", async () => {
        const scope: IMrsScope = { connectionId };
        const status = await api.status(scope);
        const values = {
            ...configureDefaults(status),
            responseCacheSize: "2M",
            gtidCache: true,
            gtidRefreshRate: "5",
            directoryIndex: "index.html, index.htm",
        };
        await api.run(scope, configureStatements(values,
            configureContextOf(status)));
        const after = configureDefaults(await api.status(scope));
        expect(after.responseCacheSize).toBe("2M");
        expect(after.gtidCache).toBe(true);
        expect(after.gtidRefreshRate).toBe("5");
        expect(after.directoryIndex).toBe("index.html, index.htm");
    });

    it("creates and changes a service, a schema and objects", async () => {
        const scope: IMrsScope = { connectionId };
        await sql("CREATE DATABASE e2e");
        await sql("CREATE TABLE e2e.country (id INT PRIMARY KEY, "
            + "name VARCHAR(40))");
        await sql("CREATE TABLE e2e.city (id INT PRIMARY KEY, name "
            + "VARCHAR(40), country_id INT, FOREIGN KEY (country_id) "
            + "REFERENCES e2e.country(id))");
        await sql("CREATE PROCEDURE e2e.cities(IN prefix VARCHAR(10), OUT n INT) "
            + "BEGIN SELECT id, name FROM e2e.city WHERE name LIKE prefix; "
            + "SET n = 1; END");
        await sql("CREATE FUNCTION e2e.twice(x INT) RETURNS INT "
            + "DETERMINISTIC RETURN x * 2");

        // The service, linked to the MRS app, made current.
        const allApps = (await api.authApps(scope)).map((app) => {
            return app.name;
        });
        const service = { ...serviceDefaults(undefined, [], allApps), path: "/e2e" };
        await api.run(scope, serviceStatements(service, {
            allAuthApps: allApps, linkedAuthApps: [],
        }));
        let [created] = await api.services(scope);
        expect(created.full_service_path).toBe("/e2e");
        expect(created.is_current).toBe(true);
        expect(created.auth_apps).toEqual(["MRS"]);

        // Edited: published, a comment, unlinked.
        const edited = {
            ...serviceDefaults(created, ["MRS"], allApps),
            published: true,
            comments: "it's a test",
            authApps: [],
        };
        await api.run(scope, serviceStatements(edited, {
            allAuthApps: allApps,
            linkedAuthApps: ["MRS"],
            existingPath: "/e2e",
        }));
        [created] = await api.services(scope);
        expect(created.published).toBe(true);
        expect(created.comments).toBe("it's a test");
        expect(created.auth_apps ?? []).toEqual([]);

        // A REST schema.
        const schemaValues = schemaDefaults([created], undefined, "e2e");
        await api.run(scope, schemaStatements(schemaValues, {
            services: ["/e2e"],
        }));
        const [schema] = await api.schemas(scope, "/e2e");
        expect(schema.request_path).toBe("/e2e");

        // A view of city, with its country reference unnested.
        const columns = await api.columns(scope, "TABLE", "e2e", "city");
        let document = buildDocument("TABLE", "e2e", "city", columns, [],
            "E2eCity");
        const country = document.fields.find((field) => {
            return field.reference !== undefined;
        })!;
        const countryColumns = await api.columns(scope, "TABLE", "e2e",
            "country");
        document = {
            ...document,
            crud: { insert: true, update: true, delete: false, noCheck: false },
            fields: updateField(document.fields, country.key, (field) => {
                const loaded = loadReference(field, countryColumns.columns ?? [],
                    [], ["e2e.city"]);

                return {
                    ...loaded,
                    enabled: true,
                    reference: { ...loaded.reference!, unnest: true },
                };
            }),
        };
        const objectValues = objectDefaults(
            { name: "city", object_type: "TABLE" }, document, "/e2e", "/e2e");
        const objectContext = { services: ["/e2e"], schemas: { "/e2e": ["/e2e"] } };
        await api.run(scope, objectStatements(objectValues, objectContext));

        const stored = await api.object(scope, "TABLE", "/e2e", "/e2e", "/city");
        expect(stored.crud_operations).toEqual(["CREATE", "READ", "UPDATE"]);
        const mapping = stored.data_mappings![0];
        expect(mapping.name).toBe("E2eCity");
        const countryField = mapping.fields.find((field) => {
            return field.data_mapping_reference !== null && field.enabled;
        })!;
        expect(countryField.data_mapping_reference!.unnest).toBe(true);

        // Read back as the dialog does, then changed: a new path, private.
        const reread = buildDocument("TABLE", "e2e", "city", columns,
            stored.data_mappings!, "Unused");
        expect(reread.className).toBe("E2eCity");
        const changed = {
            ...objectDefaults(stored, reread, "/e2e", "/e2e"),
            requestPath: "/cities",
            enabled: 2 as const,
        };
        await api.run(scope, objectStatements(changed,
            { ...objectContext, existingPath: "/city" }));
        const objects = await api.objects(scope, "/e2e", "/e2e");
        expect(objects.map((object) => {
            return [object.request_path, object.enabled];
        })).toEqual([["/cities", 2]]);

        // A procedure with a result set, and a function.
        const procedureColumns = await api.columns(scope, "PROCEDURE", "e2e",
            "cities");
        const procedure = buildDocument("PROCEDURE", "e2e", "cities",
            procedureColumns, [], "E2eCities");
        procedure.results = [{
            key: "r1",
            name: "E2eCitiesResult",
            fields: [{
                key: "f1", name: "id", column: { name: "id", datatype: "INT" },
                enabled: true, allowFiltering: true, allowSorting: false,
                noCheck: false, noUpdate: false, isKey: false, rowOwnership: false,
            }],
        }];
        await api.run(scope, objectStatements({
            ...objectDefaults({ name: "cities", object_type: "PROCEDURE" },
                procedure, "/e2e", "/e2e"),
            requestPath: "/citiesProc",
        }, objectContext));
        const functionColumns = await api.columns(scope, "FUNCTION", "e2e",
            "twice");
        const fn = buildDocument("FUNCTION", "e2e", "twice", functionColumns,
            [], "E2eTwice");
        await api.run(scope, objectStatements(objectDefaults(
            { name: "twice", object_type: "FUNCTION" }, fn, "/e2e", "/e2e"),
        objectContext));
        const routines = await api.objects(scope, "/e2e", "/e2e");
        expect(routines.map((object) => {
            return [object.request_path, object.object_type];
        })).toEqual([["/cities", "TABLE"], ["/citiesProc", "PROCEDURE"],
            ["/twice", "FUNCTION"]]);
        const storedProcedure = await api.object(scope, "PROCEDURE", "/e2e",
            "/e2e", "/citiesProc");
        expect(storedProcedure.data_mappings!.map((item) => {
            return [item.name, item.kind];
        })).toEqual([["E2eCitiesParams", "PARAMETERS"],
            ["E2eCitiesResult", "RESULT"]]);
    }, 300_000);

    it("uploads a content set and changes its settings", async () => {
        const scope: IMrsScope = { connectionId };
        const folder = mkdtempSync(join(tmpdir(), "mrs_e2e_web_"));
        writeFileSync(join(folder, "index.html"), "<html>e2e</html>");
        try {
            const [service] = await api.services(scope);
            const values = {
                ...contentSetDefaults([service], undefined, "/e2e", folder),
                requestPath: "/web",
                requiresAuth: false,
                comments: "uploaded",
            };
            const upload = await tools.loadContentSet(connectionId, folder,
                "/web", "/e2e", values.ignoreList, false);
            expect(upload.files).toHaveLength(1);
            const [created] = await api.contentSets(scope, "/e2e");
            await api.run(scope, contentSetStatements(values,
                { services: ["/e2e"], existingPath: "/web" }, created));
            const [after] = await api.contentSets(scope, "/e2e");
            expect(after.comments).toBe("uploaded");
            expect(after.requires_auth).toBe(false);
            const files = await api.contentFiles(scope, "/e2e", "/web");
            expect(files.map((file) => { return file.request_path; }))
                .toEqual(["/index.html"]);
        } finally {
            rmSync(folder, { recursive: true, force: true });
        }
    }, 300_000);

    it("creates an auth app linked to a service and a user", async () => {
        const scope: IMrsScope = { connectionId };
        const vendors = await api.authVendors(scope);
        const roles = await api.roles(scope);
        const roleServices = Object.fromEntries(roles.map((role) => {
            return [role.caption, role.specific_to_service ?? null];
        }));
        const context = {
            vendors: vendors.map((vendor) => {
                return { id: vendor.id, name: vendor.name };
            }),
            roles: roles.map((role) => { return role.caption; }),
            hasSecret: false,
            linkToService: "/e2e",
        };
        await api.run(scope, authAppStatements({
            ...authAppDefaults(undefined, roles), name: "E2E App",
        }, context));
        const linked = await api.authApps(scope, "/e2e");
        expect(linked.map((app) => { return app.name; })).toEqual(["E2E App"]);

        const userContext = {
            authApp: "E2E App",
            authAppVendorId: MRS_VENDOR_ID,
            allRoles: context.roles,
            roleServices,
            existingRoles: [],
            hasPassword: false,
        };
        await api.run(scope, userStatements({
            ...userDefaults(undefined, "Full Access"),
            name: "anna",
            password: "Anna_pw_12",
            email: "anna@example.com",
        }, userContext));
        let [anna] = await api.users(scope, "E2E App");
        expect([anna.name, anna.email, anna.has_password])
            .toEqual(["anna", "anna@example.com", true]);

        await api.run(scope, userStatements({
            ...userDefaults(anna, undefined),
            loginPermitted: false,
            roles: [],
        }, {
            ...userContext,
            existingName: "anna",
            existingRoles: ["Full Access"],
            hasPassword: true,
        }));
        [anna] = await api.users(scope, "E2E App");
        expect(anna.login_permitted).toBe(false);
        expect(anna.roles).toEqual([]);
    }, 300_000);

    it("shows all of it in the tree", async () => {
        const model = new MrsModel(api, () => { return connectionId; });
        const [root] = await model.rootsOf(uri);
        expect(root.kind).toBe("mrsRoot");
        const walk = async (node: MrsNode, depth: number): Promise<string[]> => {
            const children = await model.getChildren(node);
            const lines: string[] = [];
            for (const child of children) {
                lines.push(`${" ".repeat(depth)}${child.kind}`);
                lines.push(...await walk(child, depth + 1));
            }

            return lines;
        };
        const tree = await walk(root, 0);
        expect(tree).toContain("mrsService");
        expect(tree).toContain(" mrsSchema");
        expect(tree).toContain("  mrsObject");
        expect(tree).toContain(" mrsContentSet");
        expect(tree).toContain("  mrsContentFile");
        expect(tree).toContain(" mrsServiceAuthApp");
        expect(tree).toContain("mrsDaemonGroup");
        expect(tree).toContain(" mrsAuthApp");
        expect(tree).toContain("  mrsUser");

        // The private view is hidden unless asked for.
        const objects = async (): Promise<number> => {
            const services = await model.getChildren(root);
            const service = services.find((node) => {
                return node.kind === "mrsService";
            })!;
            const schema = (await model.getChildren(service)).find((node) => {
                return node.kind === "mrsSchema";
            })!;

            return (await model.getChildren(schema)).length;
        };
        const hidden = await objects();
        model.setShowPrivate(root, true);
        const [shown] = await model.rootsOf(uri);
        expect(await (async () => {
            const services = await model.getChildren(shown);
            const service = services.find((node) => {
                return node.kind === "mrsService";
            })!;
            const schema = (await model.getChildren(service)).find((node) => {
                return node.kind === "mrsSchema";
            })!;

            return (await model.getChildren(schema)).length;
        })()).toBe(hidden + 1);
    }, 300_000);

    it("dumps a service and writes its client SDK", async () => {
        const scope: IMrsScope = { connectionId };
        const text = await api.text(scope,
            "SHOW CREATE REST SERVICE /e2e INCLUDING ALL ENDPOINTS");
        expect(text).toContain("CREATE OR REPLACE REST SERVICE /e2e");
        const folder = mkdtempSync(join(tmpdir(), "mrs_e2e_sdk_"));
        try {
            await tools.dumpSdkServiceFiles(connectionId, folder, {
                url_context_root: "/e2e",
                service_url: "https://localhost:8443/e2e",
                sdk_language: "TypeScript",
            });
            expect(readdirSync(folder).length).toBeGreaterThan(0);
            const options = await tools.getSdkOptions(folder);
            expect(options.serviceUrl).toBe("https://localhost:8443/e2e");
        } finally {
            rmSync(folder, { recursive: true, force: true });
        }
    }, 300_000);

    it("serves several metadata schemas", async () => {
        const status = await api.status({ connectionId });
        await api.run({ connectionId }, configureStatements({
            ...configureDefaults(status),
            metadataSchema: "acme_mariadb_rest_service",
            createAuthApp: false,
        }, { ...configureContextOf(status), init: true }));

        const model = new MrsModel(api, () => { return connectionId; });
        const roots = await model.rootsOf(uri);
        expect(roots.map((root) => { return root.metadataSchema; }).sort())
            .toEqual(["acme_mariadb_rest_service", "mariadb_rest_service"]);
        const acme = roots.find((root) => {
            return root.metadataSchema === "acme_mariadb_rest_service";
        })!;
        const main = roots.find((root) => {
            return root.metadataSchema === "mariadb_rest_service";
        })!;
        const servicesOf = async (root: MrsNode): Promise<string[]> => {
            return (await model.getChildren(root)).flatMap((node) => {
                return node.kind === "mrsService"
                    ? [node.service.full_service_path] : [];
            });
        };
        expect(await servicesOf(acme)).toEqual([]);
        expect(await servicesOf(main)).toEqual(["/e2e"]);
        // Choosing the same schema again keeps the current service.
        const [again] = await api.services({
            connectionId, metadataSchema: "mariadb_rest_service",
        });
        await api.run({ connectionId, metadataSchema: "mariadb_rest_service" },
            ["USE REST SERVICE /e2e"]);
        const [current] = await api.services({
            connectionId, metadataSchema: "mariadb_rest_service",
        });
        expect(again.full_service_path).toBe("/e2e");
        expect(current.is_current).toBe(true);
    }, 300_000);
});
