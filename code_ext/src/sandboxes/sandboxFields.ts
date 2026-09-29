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

import { parseConnectionUri } from "../connections/connectionUri.js";
import type { ISandboxDeployOptions } from "../mcp/sandboxApi.js";

/**
 * The New Sandbox dialog's fields and what they turn into. Pure, so the
 * dialog and its host agree on the rules and both can be tested without
 * VS Code: the dialog marks a field the moment it goes wrong, and the host
 * checks again before anything is deployed.
 */

/** The fields as the dialog holds them: as typed. */
export interface ISandboxFields {
    port: string;
    password: string;
    /** The password typed again, which has to match it. */
    passwordConfirmation: string;
    /**
     * As typed: `12`, `11.8`, `11.8.9`, or {@link SERVER_ON_PATH} for
     * whatever server is on the PATH.
     */
    serverVersion: string;
    /**
     * The host a second root account may connect from: `127.0.0.1` by
     * default, `%` for anywhere, empty for no second account.
     */
    allowRootFrom: string;
    serverId: string;
    ssl: boolean;
    /** One `option=value` per line. */
    mariadbdOptions: string;
    /** Seconds; empty for the shell's own minute. */
    timeout: string;
    /** Whether any MCP client may open the connection a deploy registers. */
    mcpAccess: boolean;
}

/** The fields a problem can be about, so the dialog can mark the one. */
export type SandboxField = keyof ISandboxFields;

/** The lowest and highest port the shell deploys a sandbox on. */
export const MIN_SANDBOX_PORT = 1024;
export const MAX_SANDBOX_PORT = 65535;

/**
 * The port the suggestion starts from. The MySQL Shell's own sandboxes
 * start at 3310, clear of a server installed on the default 3306.
 */
export const FIRST_SUGGESTED_PORT = 3310;

/**
 * What the Server Version field says for the server on the PATH. It is a
 * choice in the field's list like any version, and read in any case.
 */
export const SERVER_ON_PATH = "Server on the PATH";

/** A version as `sandbox.deploy` takes it: `11`, `11.8` or `11.8.9`. */
const VERSION_PATTERN = /^v?\d+(\.\d+){0,2}$/;

/** The port a connection that names none is made on. */
const DEFAULT_SERVER_PORT = 3306;

/**
 * The names of this machine a connection can use. The same server answers
 * on all of them, so `localhost:3310` and `127.0.0.1:3310` are one port.
 */
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

/**
 * @param port The port to start on, as typed.
 * @param serverVersion The version to start on.
 *
 * @returns Fields for a new sandbox.
 */
export const emptySandboxFields = (
    port = "",
    serverVersion = SERVER_ON_PATH,
): ISandboxFields => {
    return {
        port,
        password: "",
        passwordConfirmation: "",
        serverVersion,
        // This machine only. The shell's own default is `%`, root from
        // anywhere, which a local test server has no need of.
        allowRootFrom: "127.0.0.1",
        serverId: "",
        ssl: false,
        mariadbdOptions: "",
        timeout: "",
        // As an agent's own deploy does: a local test server is what an AI
        // assistant is most likely to be asked to work on.
        mcpAccess: true,
    };
};

/**
 * The ports of this machine that configured connections point at.
 *
 * A connection to `localhost`, `127.0.0.1` or `::1` - in any case - names a
 * port here; one that names no port is on the default 3306. A tunnelled one
 * (`+ssh`) does NOT count: its `localhost` is the SSH host's own loopback,
 * which says nothing about what is free on this machine. Nor does one on a
 * socket, which has no host or port to speak of.
 *
 * @param uris The configured connections.
 *
 * @returns The ports, each once, lowest first.
 */
export const localConnectionPorts = (uris: readonly string[]): number[] => {
    const ports = new Set<number>();
    for (const uri of uris) {
        const fields = parseConnectionUri(uri);
        if (fields.scheme.endsWith("+ssh")
            || !LOCAL_HOSTS.has(fields.host.toLowerCase())) {
            continue;
        }

        const port = fields.port.trim() === ""
            ? DEFAULT_SERVER_PORT
            : wholeNumber(fields.port);
        if (port !== undefined) {
            ports.add(port);
        }
    }

    return [...ports].sort((a, b) => { return a - b; });
};

/**
 * Suggests a port for a new sandbox: the first from 3310 up that nothing
 * known is on - no sandbox, and no configured connection to this machine.
 * It says nothing about whether something else is listening there - the
 * shell finds that out, and says so.
 *
 * @param taken The ports in use: the sandboxes' and the local connections'.
 *
 * @returns The port.
 */
export const suggestSandboxPort = (taken: readonly number[]): number => {
    const used = new Set(taken);
    let port = FIRST_SUGGESTED_PORT;
    while (used.has(port) && port < MAX_SANDBOX_PORT) {
        port += 1;
    }

    return port;
};

/**
 * @param version `major.minor.patch`, as the server lists them.
 *
 * @returns Its numbers, for comparing: `11.10` is newer than `11.9`, which
 *          a comparison of the text would get backwards.
 */
const versionNumbers = (version: string): number[] => {
    return version.replace(/^v/, "").split(".").map((part) => {
        return Number(part) || 0;
    });
};

/**
 * Orders versions newest first.
 *
 * @param versions The versions, in any order.
 *
 * @returns A sorted copy.
 */
export const newestFirst = (versions: readonly string[]): string[] => {
    return [...versions].sort((a, b) => {
        const left = versionNumbers(a);
        const right = versionNumbers(b);
        for (let at = 0; at < Math.max(left.length, right.length); at += 1) {
            const difference = (right[at] ?? 0) - (left[at] ?? 0);
            if (difference !== 0) {
                return difference;
            }
        }

        return 0;
    });
};

/**
 * @param text The Server Version field, as typed.
 *
 * @returns Whether it asks for the server on the PATH.
 */
export const isServerOnPath = (text: string): boolean => {
    return text.trim().toLowerCase() === SERVER_ON_PATH.toLowerCase();
};

/**
 * @param versions The versions a deploy can download.
 *
 * @returns The highest of them, which is what a new sandbox starts on; the
 *          server on the PATH where there are none.
 */
export const defaultServerVersion = (versions: readonly string[]): string => {
    return newestFirst(versions)[0] ?? SERVER_ON_PATH;
};

/**
 * What the Server Version field's list offers: every version listed,
 * newest first, and the server on the PATH last.
 *
 * @param versions The versions a deploy can download.
 *
 * @returns The choices, each once.
 */
export const serverVersionChoices = (
    versions: readonly string[],
): string[] => {
    return [...new Set([...newestFirst(versions), SERVER_ON_PATH])];
};

/**
 * Checks the Server Version field on its own, since an unsound one is not
 * waited on the way other fields are: it disables Create at once.
 *
 * @param text The field, as typed.
 *
 * @returns What is wrong with it, or undefined.
 */
export const serverVersionProblem = (text: string): string | undefined => {
    if (isServerOnPath(text) || VERSION_PATTERN.test(text.trim())) {
        return undefined;
    }

    return text.trim() === ""
        ? `Enter a server version, such as 12 or 11.8.9, or pick `
            + `${SERVER_ON_PATH}.`
        : `'${text.trim()}' is not a server version. Enter major, `
            + `major.minor or major.minor.patch, such as 11.8, or pick `
            + `${SERVER_ON_PATH}.`;
};

/** What is wrong with the fields, and where. */
export interface ISandboxFieldProblem {
    field: SandboxField;
    message: string;
}

/**
 * Reads a whole number, refusing anything else: `Number("")` is 0 and
 * `parseInt("12abc")` is 12, and neither is what was typed.
 *
 * @param text The text.
 *
 * @returns The number, or undefined when the text is not one.
 */
const wholeNumber = (text: string): number | undefined => {
    return /^\d+$/.test(text.trim()) ? Number(text.trim()) : undefined;
};


/**
 * Checks the fields.
 *
 * @param fields The fields as typed.
 * @param taken The ports of the sandboxes deployed already.
 *
 * @returns The first problem, or undefined when they can be deployed.
 */
export const sandboxFieldProblem = (
    fields: ISandboxFields,
    taken: readonly number[] = [],
): ISandboxFieldProblem | undefined => {
    const port = wholeNumber(fields.port);
    if (port === undefined) {
        return { field: "port", message: "Enter the port the sandbox listens on." };
    }

    if (port < MIN_SANDBOX_PORT || port > MAX_SANDBOX_PORT) {
        return {
            field: "port",
            message: `The port must be between ${MIN_SANDBOX_PORT} and `
                + `${MAX_SANDBOX_PORT}.`,
        };
    }

    if (taken.includes(port)) {
        return {
            field: "port",
            message: `There is a sandbox on port ${port} already.`,
        };
    }

    const versionProblem = serverVersionProblem(fields.serverVersion);
    if (versionProblem !== undefined) {
        return { field: "serverVersion", message: versionProblem };
    }

    // Compared as typed, spaces and all: they are characters of a password.
    if (fields.passwordConfirmation !== fields.password) {
        return {
            field: "passwordConfirmation",
            message: "The passwords do not match.",
        };
    }

    if (fields.serverId.trim() !== ""
        && wholeNumber(fields.serverId) === undefined) {
        return { field: "serverId", message: "The server ID is a whole number." };
    }

    if (fields.timeout.trim() !== "") {
        const timeout = wholeNumber(fields.timeout);
        if (timeout === undefined || timeout === 0) {
            return {
                field: "timeout",
                message: "The timeout is a whole number of seconds.",
            };
        }
    }

    return undefined;
};

/**
 * The server options, one per non-empty line.
 *
 * @param text The options as typed.
 *
 * @returns The options.
 */
export const mariadbdOptionLines = (text: string): string[] => {
    return text.split(/\r?\n/)
        .map((line) => { return line.trim(); })
        .filter((line) => { return line !== ""; });
};

/**
 * Turns the fields into what `sandbox.deploy` is asked for. Anything left
 * at its default is left out, so the server's own default applies.
 *
 * @param fields The fields as typed.
 * @param taken The ports of the sandboxes deployed already.
 *
 * @returns The options, or the problem that stops them being built.
 */
export const sandboxDeployOptions = (
    fields: ISandboxFields,
    taken: readonly number[] = [],
): { options: ISandboxDeployOptions } | { problem: ISandboxFieldProblem } => {
    const problem = sandboxFieldProblem(fields, taken);
    if (problem !== undefined) {
        return { problem };
    }

    const serverVersion = isServerOnPath(fields.serverVersion)
        ? ""
        : fields.serverVersion.trim();
    const serverId = fields.serverId.trim();
    const timeout = fields.timeout.trim();
    const mariadbdOptions = mariadbdOptionLines(fields.mariadbdOptions);

    return {
        options: {
            port: Number(fields.port.trim()),
            // Sent even when empty: the shell refuses a deploy without one,
            // and an empty root password is a real choice for a local
            // sandbox.
            password: fields.password,
            ...(serverVersion === "" ? {} : { serverVersion }),
            // Always sent: the dialog's default is not the shell's (`%`).
            allowRootFrom: fields.allowRootFrom.trim(),
            ...(serverId === "" ? {} : { serverId: Number(serverId) }),
            ...(fields.ssl ? { ssl: true } : {}),
            ...(mariadbdOptions.length === 0 ? {} : { mariadbdOptions }),
            ...(timeout === "" ? {} : { timeout: Number(timeout) }),
            // Always said, either way: which list the connection lands in
            // is the user's choice, not a default to leave to the server.
            mcpAccess: fields.mcpAccess,
        },
    };
};

/**
 * The connection a deploy registers for the new sandbox, as the Connections
 * view will list it. Spelled as the MCP plugin's `_sandbox_connection_uri`
 * spells it.
 *
 * @param port The port, as typed.
 *
 * @returns The URI.
 */
export const sandboxConnectionUri = (port: string): string => {
    return `mariadb://root@127.0.0.1:${port.trim() || "<port>"}`;
};
