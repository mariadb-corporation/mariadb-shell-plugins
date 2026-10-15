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

import type { IMariaDbApi, IStatementResult } from "../mcp/types.js";
import { formatTime } from "../sql/executionService.js";
import { counted, errorText } from "../text.js";
import type { IActionRow } from "../webview/protocol.js";
import {
    diagramNames,
    diagramRows,
    listDiagramsSql,
    readDiagramsSql,
} from "./diagramQueries.js";
import type { IDiagramRow } from "./modelFormat.js";

/**
 * Reads a schema's diagrams on a session of their own, "Diagram", and puts
 * every query in the Actions of its connection as it runs: a row with the
 * spinner while it does, replaced by what it read or why it failed, the
 * statement beside it, the session in the Conn column.
 */

/** The session the diagram queries run on, as the Conn column names it. */
export const DIAGRAM_SESSION = "Diagram";

/** What the service needs from the extension. */
export interface IDiagramServiceHost {
    /**
     * Opens a session on a connection, or finds the one open.
     *
     * @param uri The connection.
     * @param name The session's name.
     *
     * @returns Its id.
     */
    connect(uri: string, name: string): Promise<string>;
    api(): Promise<IMariaDbApi>;
    /** Puts up a row in a connection's Actions, or replaces the one with its id. */
    put(connection: string, row: IActionRow): void;
}

export class DiagramService {
    #next = 0;

    public constructor(private readonly host: IDiagramServiceHost) { }

    /**
     * Lists a schema's diagrams.
     *
     * @param uri The connection.
     * @param schema The schema.
     *
     * @returns Their names, as the view has them.
     */
    public async listDiagrams(uri: string, schema: string): Promise<string[]> {
        const result = await this.#run(uri, listDiagramsSql(schema),
            `Listing the diagrams of ${schema}`, (ran) => {
                return `Listed ${counted(diagramNames(ran).length, "diagram")}`
                    + ` of ${schema}`;
            });

        return diagramNames(result);
    }

    /**
     * Reads every document of a schema's diagrams.
     *
     * @param uri The connection.
     * @param schema The schema.
     *
     * @returns The view's rows.
     */
    public async readDiagrams(uri: string, schema: string): Promise<IDiagramRow[]> {
        const result = await this.#run(uri, readDiagramsSql(schema),
            `Reading the diagrams of ${schema}`, (ran) => {
                return `Read ${counted(diagramRows(ran).length, "document")}`
                    + ` of ${schema}'s diagrams`;
            });

        return diagramRows(result);
    }

    /**
     * Runs a query on the Diagram session, reporting it.
     *
     * @param uri The connection.
     * @param sql The query.
     * @param running What is done, while it runs.
     * @param done What was done, from its result.
     *
     * @returns The result.
     * @throws Where the query failed.
     */
    async #run(
        uri: string,
        sql: string,
        running: string,
        done: (result: IStatementResult) => string,
    ): Promise<IStatementResult> {
        const connectionId = await this.host.connect(uri, DIAGRAM_SESSION);
        this.#next += 1;
        const id = `diagram-${String(this.#next)}-${String(Date.now())}`;
        const when = new Date();
        const started = performance.now();
        const base = {
            id,
            time: formatTime(when),
            connection: uri,
            connectionLabel: DIAGRAM_SESSION,
            statement: sql,
        };
        this.host.put(uri, {
            ...base, role: "event", message: `${running}…`, kind: "pending",
        });
        const report = (message: string, failed: boolean): void => {
            this.host.put(uri, {
                ...base, role: "event", message,
                kind: failed ? "error" : "info",
                elapsedMs: performance.now() - started,
            });
        };
        let result: IStatementResult;
        try {
            const api = await this.host.api();
            result = await api.executeSql(connectionId, sql);
        } catch (error) {
            report(errorText(error), true);
            throw error;
        }
        // The server answers a failed statement with its error, not a throw.
        if (result.error !== undefined) {
            report(result.error, true);
            throw new Error(result.error);
        }
        report(done(result), false);

        return result;
    }
}
