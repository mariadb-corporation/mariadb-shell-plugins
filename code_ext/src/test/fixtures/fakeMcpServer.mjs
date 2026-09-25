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

/**
 * A stand-in for `mariadb-shell -- mcp start-server --transport=stdio`.
 *
 * It speaks real MCP over stdio, so the connector is exercised against the
 * protocol rather than against a mock of it, without needing a shell or a
 * database. It answers `db.list_connections` the way the real server does
 * - one text item per list element - and writes a line to stderr on start,
 * which is what the connector's log plumbing has to pick up.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
    CallToolRequestSchema,
    ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const server = new Server(
    { name: "fake-mariadb-mcp", version: "1.0.0" },
    { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, () => {
    return {
        tools: [{
            name: "db.list_connections",
            description: "Lists the configured connection URIs.",
            inputSchema: { type: "object", properties: {} },
        }],
    };
});

server.setRequestHandler(CallToolRequestSchema, (request) => {
    // Dies mid-session, the way a crashing shell would.
    if (request.params.name === "test.exit") {
        process.exit(1);
    }

    // Answers only after a while, for a caller's own timeout to cut short.
    if (request.params.name === "test.slow") {
        return new Promise((resolve) => {
            setTimeout(() => {
                resolve({ content: [{ type: "text", text: "done" }] });
            }, 2000);
        });
    }

    if (request.params.name === "db.list_connections") {
        return {
            content: [
                { type: "text", text: "dba@localhost:3310" },
                { type: "text", text: "app@localhost:3311" },
            ],
        };
    }

    return {
        content: [{
            type: "text",
            text: `Error executing tool ${request.params.name}: no such tool`,
        }],
        isError: true,
    };
});

process.stderr.write("fake MCP server listening on stdio\n");

await server.connect(new StdioServerTransport());
