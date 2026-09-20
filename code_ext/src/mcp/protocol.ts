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
 * Decoding of MCP tool results into the values the Python tools returned.
 *
 * The server is a FastMCP server, which renders a tool's return value into
 * the `content` array rather than into one JSON document:
 *
 * - a tool returning a list produces one text item per element,
 * - a tool returning a dict produces one text item holding its JSON,
 * - a tool returning a scalar produces one text item holding it verbatim,
 *   plus `structuredContent.result`,
 * - a tool that raised produces `isError: true` and the message as text.
 *
 * These helpers are pure, so the mapping is testable without a server.
 */

/** The slice of an MCP `CallToolResult` this module reads. */
export interface IToolResult {
    content?: Array<{ type: string; text?: string }>;
    structuredContent?: { result?: unknown };
    isError?: boolean;
}

/** Raised when a tool reported a failure. */
export class McpToolError extends Error {
    public constructor(public readonly toolName: string, message: string) {
        super(message);
        this.name = "McpToolError";
    }
}

/**
 * Parses a text item, falling back to the text itself when it is not JSON.
 *
 * A tool returning a list of strings yields items that are bare strings, not
 * quoted JSON, so a failed parse is the normal case rather than an error.
 *
 * @param text The text to parse.
 *
 * @returns The parsed value, or the text unchanged.
 */
export const parseMaybeJson = (text: string): unknown => {
    try {
        return JSON.parse(text) as unknown;
    } catch {
        return text;
    }
};

/**
 * Pulls the text items out of a tool result, failing on a reported error.
 *
 * @param toolName The tool that was called, for the error message.
 * @param result The result it answered with.
 *
 * @returns The text of every text item, in order.
 */
export const textContents = (
    toolName: string,
    result: IToolResult,
): string[] => {
    const texts = (result.content ?? [])
        .filter((item) => {
            return item.type === "text" && item.text !== undefined;
        })
        .map((item) => {
            return item.text as string;
        });

    if (result.isError) {
        throw new McpToolError(
            toolName,
            texts.join("\n") || `The tool ${toolName} failed.`,
        );
    }

    return texts;
};

/**
 * Decodes a tool that returns a list.
 *
 * @param toolName The tool that was called.
 * @param result The result it answered with.
 *
 * @returns One decoded element per text item.
 */
export const decodeList = <T>(toolName: string, result: IToolResult): T[] => {
    return textContents(toolName, result).map((text) => {
        return parseMaybeJson(text) as T;
    });
};

/**
 * Decodes a tool that returns a single object.
 *
 * @param toolName The tool that was called.
 * @param result The result it answered with.
 *
 * @returns The decoded object.
 */
export const decodeObject = <T>(toolName: string, result: IToolResult): T => {
    const texts = textContents(toolName, result);
    if (texts.length === 0) {
        throw new McpToolError(toolName, `${toolName} returned nothing.`);
    }

    return parseMaybeJson(texts[0]) as T;
};

/**
 * Decodes a tool that returns a scalar, such as the connection UUID.
 *
 * The structured result is preferred where the server sent one, because it
 * carries the value with its type rather than as text.
 *
 * @param toolName The tool that was called.
 * @param result The result it answered with.
 *
 * @returns The scalar as a string.
 */
export const decodeScalar = (
    toolName: string,
    result: IToolResult,
): string => {
    const texts = textContents(toolName, result);
    const structured = result.structuredContent?.result;
    if (typeof structured === "string") {
        return structured;
    }

    if (texts.length === 0) {
        throw new McpToolError(toolName, `${toolName} returned nothing.`);
    }

    return texts[0];
};

/**
 * Decodes a tool that returns nothing, only surfacing a reported error.
 *
 * @param toolName The tool that was called.
 * @param result The result it answered with.
 *
 * @returns Nothing.
 */
export const decodeVoid = (toolName: string, result: IToolResult): void => {
    textContents(toolName, result);
};
