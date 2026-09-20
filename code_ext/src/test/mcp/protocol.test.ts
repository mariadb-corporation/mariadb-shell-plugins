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
    decodeList,
    decodeObject,
    decodeScalar,
    decodeVoid,
    McpToolError,
    parseMaybeJson,
    textContents,
} from "../../mcp/protocol.js";

/**
 * The fixtures here are the shapes the running MariaDB MCP server actually
 * answers with, so the decoding is pinned to the server rather than to an
 * idea of it.
 */

describe("parseMaybeJson", () => {
    it("parses JSON", () => {
        expect(parseMaybeJson('{"a":1}')).toEqual({ a: 1 });
    });

    it("hands back a bare string unchanged", () => {
        expect(parseMaybeJson("dba@localhost:3310"))
            .toBe("dba@localhost:3310");
    });
});

describe("textContents", () => {
    it("collects the text items", () => {
        expect(textContents("t", {
            content: [
                { type: "text", text: "one" },
                { type: "image" },
                { type: "text", text: "two" },
            ],
        })).toEqual(["one", "two"]);
    });

    it("throws the reported message on an error result", () => {
        expect(() => {
            return textContents("db.execute_sql_script", {
                content: [{
                    type: "text",
                    text: "Error executing tool db.execute_sql_script: "
                        + "MySQL Error (1146): Table 'nope.nope' doesn't exist",
                }],
                isError: true,
            });
        }).toThrow(/Table 'nope.nope' doesn't exist/);
    });

    it("names the tool on an error with no message", () => {
        expect(() => {
            return textContents("db.close", { content: [], isError: true });
        }).toThrow(McpToolError);
    });
});

describe("decodeList", () => {
    it("decodes a list of bare strings", () => {
        expect(decodeList<string>("db.list_connections", {
            content: [{ type: "text", text: "dba@localhost:3310" }],
            isError: false,
        })).toEqual(["dba@localhost:3310"]);
    });

    it("decodes one item per list element", () => {
        expect(decodeList("db.list_schemas", {
            content: [
                {
                    type: "text",
                    text: '{"schema_name": "mysql", '
                        + '"schema_type": "System Schema", '
                        + '"schema_comment": ""}',
                },
                {
                    type: "text",
                    text: '{"schema_name": "sys", '
                        + '"schema_type": "User Schema", '
                        + '"schema_comment": ""}',
                },
            ],
        })).toEqual([
            {
                schema_name: "mysql",
                schema_type: "System Schema",
                schema_comment: "",
            },
            {
                schema_name: "sys",
                schema_type: "User Schema",
                schema_comment: "",
            },
        ]);
    });

    it("decodes an empty list", () => {
        expect(decodeList("db.list_objects", { content: [] })).toEqual([]);
    });
});

describe("decodeScalar", () => {
    it("prefers the structured result", () => {
        expect(decodeScalar("db.connect", {
            content: [{ type: "text", text: "ignored" }],
            structuredContent: { result: "24cc602b" },
        })).toBe("24cc602b");
    });

    it("falls back to the text item", () => {
        expect(decodeScalar("db.connect", {
            content: [{ type: "text", text: "24cc602b" }],
        })).toBe("24cc602b");
    });

    it("throws when there is nothing to read", () => {
        expect(() => {
            return decodeScalar("db.connect", { content: [] });
        }).toThrow(/returned nothing/);
    });
});

describe("decodeObject", () => {
    it("decodes the first item", () => {
        expect(decodeObject("db.get_object_details", {
            content: [{ type: "text", text: '{"basic":{"name":"city"}}' }],
        })).toEqual({ basic: { name: "city" } });
    });

    it("throws on an empty result", () => {
        expect(() => {
            return decodeObject("db.get_object_details", { content: [] });
        }).toThrow(/returned nothing/);
    });
});

describe("decodeVoid", () => {
    it("accepts an empty result", () => {
        expect(() => {
            return decodeVoid("db.close", { content: [] });
        }).not.toThrow();
    });

    it("still surfaces an error", () => {
        expect(() => {
            return decodeVoid("db.close", {
                content: [{ type: "text", text: "no such connection" }],
                isError: true,
            });
        }).toThrow(/no such connection/);
    });
});
