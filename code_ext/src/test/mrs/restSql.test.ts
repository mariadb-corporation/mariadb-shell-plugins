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

import {
    ENABLED_STATE,
    MARIADB_VENDOR_ID,
    MRS_VENDOR_ID,
} from "../../mrs/mrsTypes.js";
import {
    alterAuthAppSql,
    alterContentSetSql,
    alterSchemaSql,
    alterServiceSql,
    alterUserSql,
    configureMetadataSql,
    createAuthAppSql,
    createSchemaSql,
    createServiceSql,
    createUserSql,
    dropAuthAppSql,
    dropContentFileSql,
    dropContentSetSql,
    dropDaemonSql,
    dropObjectSql,
    dropSchemaSql,
    dropServiceSql,
    dropUserSql,
    enabledKeyword,
    jsonText,
    linkAuthAppSql,
    objectKeyword,
    objectOptions,
    qualifiedName,
    quoteIdentifier,
    quoteRequestPath,
    quoteServicePath,
    quoteText,
    serviceSchemaSelector,
    splitQualified,
    useServiceSql,
    userRef,
    userRoleSql,
    vendorClause,
    type IAuthAppValues,
    type IContentSetValues,
    type IObjectSettings,
    type ISchemaValues,
    type IServiceValues,
    type IUserValues,
} from "../../mrs/restSql.js";

const service = (overrides: Partial<IServiceValues> = {}): IServiceValues => {
    return {
        path: "/svc",
        enabled: true,
        published: false,
        protocol: "HTTPS",
        comments: "",
        options: null,
        metadata: null,
        authPath: "",
        authCompletedUrl: "",
        authCompletedUrlValidation: "",
        authCompletedPageContent: "",
        ...overrides,
    };
};

const schema = (overrides: Partial<ISchemaValues> = {}): ISchemaValues => {
    return {
        servicePath: "/svc",
        requestPath: "/sakila",
        dbSchema: "sakila",
        enabled: ENABLED_STATE.enabled,
        requiresAuth: false,
        itemsPerPage: null,
        comments: "",
        options: null,
        metadata: null,
        ...overrides,
    };
};

const settings = (
    overrides: Partial<IObjectSettings> = {},
): IObjectSettings => {
    return {
        enabled: ENABLED_STATE.enabled,
        requiresAuth: true,
        itemsPerPage: null,
        comments: "",
        mediaType: "",
        autoDetectMediaType: false,
        format: "FEED",
        authStoredProcedure: "",
        options: null,
        metadata: null,
        ...overrides,
    };
};

const contentSet = (
    overrides: Partial<IContentSetValues> = {},
): IContentSetValues => {
    return {
        servicePath: "/svc",
        requestPath: "/web",
        enabled: ENABLED_STATE.enabled,
        requiresAuth: false,
        comments: "",
        options: null,
        ...overrides,
    };
};

const authApp = (overrides: Partial<IAuthAppValues> = {}): IAuthAppValues => {
    return {
        name: "app",
        vendorId: MRS_VENDOR_ID,
        vendorName: "MRS",
        enabled: true,
        description: "",
        limitToRegisteredUsers: false,
        defaultRole: "",
        appId: "",
        appSecret: "",
        url: "",
        ...overrides,
    };
};

const user = (overrides: Partial<IUserValues> = {}): IUserValues => {
    return {
        name: "anna",
        authApp: "MRS",
        password: "",
        email: "",
        vendorUserId: "",
        mappedUserId: "",
        loginPermitted: true,
        options: null,
        appOptions: null,
        ...overrides,
    };
};

describe("quoting", () => {
    it("single quotes text, doubling quotes and backslashes", () => {
        expect(quoteText("abc")).toBe("'abc'");
        expect(quoteText("")).toBe("''");
        expect(quoteText("it's")).toBe("'it''s'");
        expect(quoteText("a\\b")).toBe("'a\\\\b'");
        expect(quoteText("\\'")).toBe("'\\\\'''");
    });

    it("back tick quotes identifiers, doubling back ticks", () => {
        expect(quoteIdentifier("city")).toBe("`city`");
        expect(quoteIdentifier("a`b")).toBe("`a``b`");
        expect(qualifiedName("sakila", "city")).toBe("`sakila`.`city`");
        expect(qualifiedName("a`", "b")).toBe("`a```.`b`");
    });

    it("leaves a plain request path as it is", () => {
        expect(quoteRequestPath("/sakila")).toBe("/sakila");
        expect(quoteRequestPath("/a/b_c/_d9")).toBe("/a/b_c/_d9");
    });

    it("quotes a request path the lexer would not read as one", () => {
        expect(quoteRequestPath("/a.b")).toBe("`/a.b`");
        expect(quoteRequestPath("/a-b")).toBe("`/a-b`");
        expect(quoteRequestPath("/1abc")).toBe("`/1abc`");
        expect(quoteRequestPath("/")).toBe("`/`");
        expect(quoteRequestPath("svc")).toBe("`svc`");
        expect(quoteRequestPath("")).toBe("``");
        expect(quoteRequestPath("/a`b")).toBe("`/a``b`");
    });

    it("quotes a service path's path part, keeping its developers", () => {
        expect(quoteServicePath("/svc")).toBe("/svc");
        expect(quoteServicePath("/my-svc")).toBe("`/my-svc`");
        expect(quoteServicePath("mike@/svc")).toBe("mike@/svc");
        expect(quoteServicePath("mike,anna@/my.svc"))
            .toBe("mike,anna@`/my.svc`");
        // An @ inside a quoted developer is not the separator.
        expect(quoteServicePath("mike,'a@b.com'@/svc"))
            .toBe("mike,'a@b.com'@/svc");
        expect(quoteServicePath("mike,'a@b.com'@/s-v"))
            .toBe("mike,'a@b.com'@`/s-v`");
    });

    it("writes JSON indented by four", () => {
        expect(jsonText({ a: 1 })).toBe("{\n    \"a\": 1\n}");
        expect(jsonText({})).toBe("{}");
        expect(jsonText([1])).toBe("[\n    1\n]");
        expect(jsonText(null)).toBe("null");
    });

    it("names the enabled states", () => {
        expect(enabledKeyword(ENABLED_STATE.enabled)).toBe("ENABLED");
        expect(enabledKeyword(ENABLED_STATE.disabled)).toBe("DISABLED");
        expect(enabledKeyword(ENABLED_STATE.private)).toBe("PRIVATE");
    });

    it("selects a service and a schema", () => {
        expect(serviceSchemaSelector()).toBe("");
        expect(serviceSchemaSelector("/svc")).toBe(" ON SERVICE /svc");
        expect(serviceSchemaSelector(undefined, "/sakila"))
            .toBe(" ON SCHEMA /sakila");
        expect(serviceSchemaSelector("mike@/s.v", "/a-b"))
            .toBe(" ON SERVICE mike@`/s.v` SCHEMA `/a-b`");
    });
});

describe("configureMetadataSql", () => {
    it("enables or disables, and nothing more by default", () => {
        expect(configureMetadataSql({ enabled: true }))
            .toBe("CONFIGURE REST METADATA\n    ENABLED;");
        expect(configureMetadataSql({ enabled: false }))
            .toBe("CONFIGURE REST METADATA\n    DISABLED;");
        expect(configureMetadataSql({ enabled: true, update: false }))
            .toBe("CONFIGURE REST METADATA\n    ENABLED;");
    });

    it("names the schema, sets the options and updates", () => {
        expect(configureMetadataSql({
            enabled: true,
            metadataSchema: "x_mariadb_rest_service",
            options: { a: { b: 1 } },
            update: true,
        })).toBe([
            "CONFIGURE REST METADATA",
            "    SCHEMA `x_mariadb_rest_service`",
            "    ENABLED",
            "    OPTIONS {",
            "        \"a\": {",
            "            \"b\": 1",
            "        }",
            "    }",
            "    UPDATE IF AVAILABLE;",
        ].join("\n"));
    });

    it("writes empty options", () => {
        expect(configureMetadataSql({ enabled: false, options: {} }))
            .toBe("CONFIGURE REST METADATA\n    DISABLED\n    OPTIONS {};");
    });
});

describe("services", () => {
    it("creates a service with every option", () => {
        expect(createServiceSql(service())).toBe([
            "CREATE REST SERVICE /svc",
            "    ENABLED",
            "    UNPUBLISHED",
            "    PROTOCOL HTTPS",
            "    COMMENT ''",
            "    AUTHENTICATION",
            "        PATH DEFAULT",
            "        REDIRECTION DEFAULT",
            "        VALIDATION DEFAULT",
            "        PAGE CONTENT DEFAULT",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("writes what is set and links apps", () => {
        expect(createServiceSql(service({
            path: "mike,'a@b.com'@/my-svc",
            enabled: false,
            published: true,
            protocol: "HTTP",
            comments: "it's",
            options: { x: 1 },
            metadata: { m: true },
            authPath: "/auth",
            authCompletedUrl: "https://x",
            authCompletedUrlValidation: "^x$",
            authCompletedPageContent: "<p>",
        }), ["MRS", "Mari'a"])).toBe([
            "CREATE REST SERVICE mike,'a@b.com'@`/my-svc`",
            "    DISABLED",
            "    PUBLISHED",
            "    PROTOCOL HTTP",
            "    COMMENT 'it''s'",
            "    AUTHENTICATION",
            "        PATH '/auth'",
            "        REDIRECTION 'https://x'",
            "        VALIDATION '^x$'",
            "        PAGE CONTENT '<p>'",
            "    OPTIONS {",
            "        \"x\": 1",
            "    }",
            "    METADATA {",
            "        \"m\": true",
            "    }",
            "    ADD AUTH APP 'MRS'",
            "    ADD AUTH APP 'Mari''a';",
        ].join("\n"));
    });

    it("alters a service in place", () => {
        const sql = alterServiceSql("/svc", service());
        expect(sql.split("\n")[0]).toBe("ALTER REST SERVICE /svc");
        expect(sql).not.toContain("NEW REQUEST PATH");
        expect(sql).not.toContain("AUTH APP");
    });

    it("moves a service to a new path", () => {
        const sql = alterServiceSql("mike@/svc", service({ path: "/new.svc" }));
        expect(sql.split("\n").slice(0, 3)).toEqual([
            "ALTER REST SERVICE mike@/svc",
            "    NEW REQUEST PATH `/new.svc`",
            "    ENABLED",
        ]);
    });

    it("removes apps before adding others", () => {
        const sql = alterServiceSql("/svc", service(), ["A"], ["B", "C"]);
        expect(sql.split("\n").slice(-3)).toEqual([
            "    REMOVE AUTH APP 'B' IF EXISTS",
            "    REMOVE AUTH APP 'C' IF EXISTS",
            "    ADD AUTH APP 'A';",
        ]);
    });

    it("links and unlinks one app", () => {
        expect(linkAuthAppSql("/svc", "MRS", true))
            .toBe("ALTER REST SERVICE /svc ADD AUTH APP 'MRS';");
        expect(linkAuthAppSql("a@/s-v", "it's", false))
            .toBe("ALTER REST SERVICE a@`/s-v` REMOVE AUTH APP 'it''s';");
    });

    it("makes a service the current one", () => {
        expect(useServiceSql("/svc")).toBe("USE REST SERVICE /svc;");
        expect(useServiceSql("mike@/a.b"))
            .toBe("USE REST SERVICE mike@`/a.b`;");
    });
});

describe("schemas", () => {
    it("creates a schema with the default page size", () => {
        expect(createSchemaSql(schema())).toBe([
            "CREATE REST SCHEMA /sakila ON SERVICE /svc FROM `sakila`",
            "    ENABLED",
            "    AUTHENTICATION NOT REQUIRED",
            "    ITEMS PER PAGE 25",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("writes what is set", () => {
        expect(createSchemaSql(schema({
            servicePath: "m@/s-v",
            requestPath: "/sa-kila",
            dbSchema: "sa`kila",
            enabled: ENABLED_STATE.private,
            requiresAuth: true,
            itemsPerPage: 10,
            comments: "c",
            options: { o: 1 },
            metadata: { m: 2 },
        }))).toBe([
            "CREATE REST SCHEMA `/sa-kila` ON SERVICE m@`/s-v` FROM `sa``kila`",
            "    PRIVATE",
            "    AUTHENTICATION REQUIRED",
            "    ITEMS PER PAGE 10",
            "    COMMENT 'c'",
            "    OPTIONS {",
            "        \"o\": 1",
            "    }",
            "    METADATA {",
            "        \"m\": 2",
            "    };",
        ].join("\n"));
    });

    it("alters a schema, moving it where its path changed", () => {
        expect(alterSchemaSql("/sakila", schema({
            enabled: ENABLED_STATE.disabled,
        }))).toBe([
            "ALTER REST SCHEMA /sakila ON SERVICE /svc",
            "    FROM `sakila`",
            "    DISABLED",
            "    AUTHENTICATION NOT REQUIRED",
            "    ITEMS PER PAGE 25",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
        expect(alterSchemaSql("/old", schema()).split("\n").slice(0, 3))
            .toEqual([
                "ALTER REST SCHEMA /old ON SERVICE /svc",
                "    NEW REQUEST PATH /sakila",
                "    FROM `sakila`",
            ]);
    });
});

describe("REST objects", () => {
    it("splits a qualified name at its first dot", () => {
        expect(splitQualified("sakila.auth")).toEqual(["sakila", "auth"]);
        expect(splitQualified("  auth ")).toEqual(["", "auth"]);
        expect(splitQualified("")).toEqual(["", ""]);
        expect(splitQualified("a.b.c")).toEqual(["a", "b.c"]);
    });

    it("picks the statement's keyword", () => {
        expect(objectKeyword("PROCEDURE")).toBe("PROCEDURE");
        expect(objectKeyword("FUNCTION")).toBe("FUNCTION");
        expect(objectKeyword("TABLE")).toBe("VIEW");
        expect(objectKeyword("VIEW")).toBe("VIEW");
        expect(objectKeyword("SCRIPT")).toBe("VIEW");
    });

    it("writes a view's options", () => {
        expect(objectOptions(settings(), false)).toEqual([
            "ENABLED",
            "AUTHENTICATION REQUIRED",
            "ITEMS PER PAGE 25",
            "COMMENT ''",
            "FORMAT FEED",
            "OPTIONS {}",
        ]);
    });

    it("leaves pages and format out of a routine's options", () => {
        expect(objectOptions(settings({
            requiresAuth: false,
            itemsPerPage: 5,
            format: "ITEM",
        }), true)).toEqual([
            "ENABLED",
            "AUTHENTICATION NOT REQUIRED",
            "COMMENT ''",
            "OPTIONS {}",
        ]);
    });

    it("writes the media type, autodetect winning", () => {
        expect(objectOptions(settings({ mediaType: "image/png" }), false))
            .toContain("MEDIA TYPE 'image/png'");
        const auto = objectOptions(settings({
            mediaType: "image/png",
            autoDetectMediaType: true,
        }), true);
        expect(auto).toContain("MEDIA TYPE AUTODETECT");
        expect(auto).not.toContain("MEDIA TYPE 'image/png'");
    });

    it("writes the authentication procedure, qualified or not", () => {
        expect(objectOptions(settings({
            authStoredProcedure: "sakila.check",
        }), false)).toContain("AUTHENTICATION PROCEDURE `sakila`.`check`");
        expect(objectOptions(settings({
            authStoredProcedure: " check ",
        }), false)).toContain("AUTHENTICATION PROCEDURE `check`");
        expect(objectOptions(settings({ authStoredProcedure: "  " }), false)
            .some((line) => { return line.includes("PROCEDURE"); }))
            .toBe(false);
    });

    it("writes everything in order", () => {
        expect(objectOptions(settings({
            enabled: ENABLED_STATE.private,
            itemsPerPage: 3,
            comments: "x",
            mediaType: "text/plain",
            format: "MEDIA",
            authStoredProcedure: "s.p",
            options: { a: 1 },
            metadata: { b: 2 },
        }), false)).toEqual([
            "PRIVATE",
            "AUTHENTICATION REQUIRED",
            "ITEMS PER PAGE 3",
            "COMMENT 'x'",
            "MEDIA TYPE 'text/plain'",
            "FORMAT MEDIA",
            "AUTHENTICATION PROCEDURE `s`.`p`",
            "OPTIONS {\n    \"a\": 1\n}",
            "METADATA {\n    \"b\": 2\n}",
        ]);
    });

    it("drops an object", () => {
        expect(dropObjectSql("TABLE", "/city", "/svc", "/sakila"))
            .toBe("DROP REST VIEW /city FROM SERVICE /svc SCHEMA /sakila;");
        expect(dropObjectSql("PROCEDURE", "/p-1", "a@/svc", "/s.s"))
            .toBe("DROP REST PROCEDURE `/p-1` FROM SERVICE a@/svc "
                + "SCHEMA `/s.s`;");
        expect(dropObjectSql("FUNCTION", "/f", "/svc", "/s"))
            .toBe("DROP REST FUNCTION /f FROM SERVICE /svc SCHEMA /s;");
    });
});

describe("content sets", () => {
    it("alters a content set in place", () => {
        expect(alterContentSetSql("/web", contentSet())).toBe([
            "ALTER REST CONTENT SET /web ON SERVICE /svc",
            "    ENABLED",
            "    AUTHENTICATION NOT REQUIRED",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("moves it and loads its scripts", () => {
        expect(alterContentSetSql("/old", contentSet({
            servicePath: "m@/s-v",
            requestPath: "/new-web",
            enabled: ENABLED_STATE.disabled,
            requiresAuth: true,
            comments: "c",
            options: { k: "v" },
        }), true)).toBe([
            "ALTER REST CONTENT SET /old ON SERVICE m@`/s-v`",
            "    NEW REQUEST PATH `/new-web`",
            "    DISABLED",
            "    AUTHENTICATION REQUIRED",
            "    COMMENT 'c'",
            "    OPTIONS {",
            "        \"k\": \"v\"",
            "    }",
            "    LOAD TYPESCRIPT SCRIPTS;",
        ].join("\n"));
    });
});

describe("auth apps", () => {
    it("names the vendor", () => {
        expect(vendorClause(MRS_VENDOR_ID, "MRS")).toBe("MRS");
        expect(vendorClause(MARIADB_VENDOR_ID, "MariaDB Internal"))
            .toBe("MARIADB");
        expect(vendorClause("x", "Goo'gle")).toBe("'Goo''gle'");
    });

    it("creates an app with what is set", () => {
        expect(createAuthAppSql(authApp())).toBe([
            "CREATE REST AUTH APP 'app' VENDOR MRS",
            "    ENABLED",
            "    COMMENT ''",
            "    ALLOW NEW USERS TO REGISTER;",
        ].join("\n"));
        expect(createAuthAppSql(authApp({
            name: "g",
            vendorId: "40000000-0000-0000-0000-000000000000",
            vendorName: "Google",
            enabled: false,
            description: "d",
            limitToRegisteredUsers: true,
            defaultRole: "Full Access",
            appId: "id",
            appSecret: "s'cret",
            url: "https://g",
        }))).toBe([
            "CREATE REST AUTH APP 'g' VENDOR 'Google'",
            "    DISABLED",
            "    COMMENT 'd'",
            "    DO NOT ALLOW NEW USERS TO REGISTER",
            "    DEFAULT ROLE 'Full Access'",
            "    APP ID 'id'",
            "    APP SECRET 's''cret'",
            "    URL 'https://g';",
        ].join("\n"));
    });

    it("alters an app, renaming it where its name changed", () => {
        expect(alterAuthAppSql("app", authApp({
            vendorId: MARIADB_VENDOR_ID,
        }))).toBe([
            "ALTER REST AUTH APP 'app'",
            "    ENABLED",
            "    COMMENT ''",
            "    ALLOW NEW USERS TO REGISTER;",
        ].join("\n"));
        expect(alterAuthAppSql("old", authApp()).split("\n").slice(0, 2))
            .toEqual(["ALTER REST AUTH APP 'old'", "    NEW NAME 'app'"]);
    });
});

describe("users", () => {
    it("refers to a user by name and app", () => {
        expect(userRef("anna", "MRS")).toBe("'anna'@'MRS'");
        expect(userRef("o'neil", "a\\b")).toBe("'o''neil'@'a\\\\b'");
    });

    it("creates a user without a password", () => {
        expect(createUserSql(user())).toBe([
            "CREATE REST USER 'anna'@'MRS'",
            "    ACCOUNT UNLOCK",
            "    OPTIONS {",
            "        \"email\": null,",
            "        \"vendor_user_id\": null,",
            "        \"mapped_user_id\": null",
            "    };",
        ].join("\n"));
    });

    it("creates a user with a password and folds the ids into the options",
        () => {
            expect(createUserSql(user({
                password: "Pa'ss",
                email: "a@b.c",
                vendorUserId: "v1",
                mappedUserId: "m1",
                loginPermitted: false,
                options: { keep: 1, email: "overridden" },
                appOptions: { x: 2 },
            }))).toBe([
                "CREATE REST USER 'anna'@'MRS' IDENTIFIED BY 'Pa''ss'",
                "    ACCOUNT LOCK",
                "    APP OPTIONS {",
                "        \"x\": 2",
                "    }",
                "    OPTIONS {",
                "        \"keep\": 1,",
                "        \"email\": \"a@b.c\",",
                "        \"vendor_user_id\": \"v1\",",
                "        \"mapped_user_id\": \"m1\"",
                "    };",
            ].join("\n"));
        });

    it("alters a user by its current name", () => {
        expect(alterUserSql("old", user()).split("\n")[0])
            .toBe("ALTER REST USER 'old'@'MRS'");
        expect(alterUserSql("old", user({ password: "x" })).split("\n")[0])
            .toBe("ALTER REST USER 'old'@'MRS' IDENTIFIED BY 'x'");
    });

    it("grants and revokes a role of any service", () => {
        expect(userRoleSql("Full Access", "anna", "MRS", true))
            .toBe("GRANT REST ROLE 'Full Access' ON ANY SERVICE "
                + "TO 'anna'@'MRS';");
        expect(userRoleSql("R'1", "anna", "MRS", false, null))
            .toBe("REVOKE REST ROLE 'R''1' ON ANY SERVICE "
                + "FROM 'anna'@'MRS';");
    });

    it("grants and revokes a role of one service", () => {
        expect(userRoleSql("Editor", "anna", "MRS", true, "/svc"))
            .toBe("GRANT REST ROLE 'Editor' ON SERVICE /svc "
                + "TO 'anna'@'MRS';");
        expect(userRoleSql("Editor", "anna", "MRS", false,
            "mike,'a@b.com'@/s-v"))
            .toBe("REVOKE REST ROLE 'Editor' ON SERVICE "
                + "mike,'a@b.com'@`/s-v` FROM 'anna'@'MRS';");
    });
});

describe("dropping", () => {
    it("drops each kind of object", () => {
        expect(dropServiceSql("m,'a@b'@/s-v"))
            .toBe("DROP REST SERVICE m,'a@b'@`/s-v`;");
        expect(dropSchemaSql("/sakila", "/svc"))
            .toBe("DROP REST SCHEMA /sakila FROM SERVICE /svc;");
        expect(dropContentSetSql("/web.app", "/svc"))
            .toBe("DROP REST CONTENT SET `/web.app` FROM SERVICE /svc;");
        expect(dropContentFileSql("/index.html", "/svc", "/web"))
            .toBe("DROP REST CONTENT FILE `/index.html` FROM SERVICE /svc "
                + "CONTENT SET /web;");
        expect(dropAuthAppSql("it's")).toBe("DROP REST AUTH APP 'it''s';");
        expect(dropUserSql("anna", "MRS"))
            .toBe("DROP REST USER 'anna'@'MRS';");
        expect(dropDaemonSql("d-1")).toBe("DROP REST DAEMON 'd-1';");
    });
});
