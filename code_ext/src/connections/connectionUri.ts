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
 * Turning the connection editor's fields into a MariaDB Shell connection URI,
 * and back again.
 *
 * The extension has no way to ask the shell to parse or compose a URI - there
 * is no MCP tool for it - so the grammar is implemented here, against the
 * shell's own (`mysqlshdk/libs/db/uri_parser.cc` and `utils_connection.h`):
 *
 *     [scheme://]user@host[:port][/schema][?option=value&...]
 *     [scheme://]user@<percent-encoded socket path>[?option=value&...]
 *
 * What this does NOT have to do is produce the shell's canonical spelling.
 * `db.add_connection` normalizes whatever it is given and answers with the
 * URI it stored, and that is the spelling everything afterwards uses. So this
 * only has to compose a URI the shell accepts and means the same by; the
 * round trip is closed by the server, not here.
 *
 * Parsing, on the other hand, has to cope with exactly what the shell emits,
 * because that is what `db.list_connections` hands back for editing.
 */

/**
 * The protocol schemes the shell's URI parser accepts.
 *
 * `mariadb` is the shell's own name for the classic client-server protocol and
 * the one a URI without a scheme means; `mysql` is a synonym of it kept for
 * URIs written for MySQL Shell. The `+ssh` forms are the same two protocols
 * reached through an SSH tunnel, which is asked for by scheme and by nothing
 * else - there is no option that turns one on.
 */
export const CONNECTION_SCHEMES = [
    "mariadb",
    "mariadb+ssh",
    "mysql",
    "mysql+ssh",
    "mysqlx",
] as const;

export type ConnectionScheme = (typeof CONNECTION_SCHEMES)[number];

/**
 * The scheme a URI without one means, and what a new connection starts on.
 *
 * The MCP server fills this in when it normalizes a URI, so a connection is
 * stored - and listed - with it whether or not it was written down.
 */
export const DEFAULT_SCHEME = "mariadb";

/** The scheme extension that asks for an SSH tunnel. */
export const SSH_SCHEME_SUFFIX = "+ssh";

/** Matches the `scheme://` a URI starts with, if it has one. */
const SCHEME_PREFIX = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;

/**
 * The same URI with {@link DEFAULT_SCHEME} filled in where it names none.
 *
 * What it is for is comparing a URI written down by an older version of this
 * extension - the default-connection setting is one - against a URI
 * `db.list_connections` reports now, which always carries its scheme. The two
 * spell one connection and have to compare equal.
 *
 * @param uri The URI to read.
 *
 * @returns The URI with a scheme, or what was given if it already had one.
 */
export const withDefaultScheme = (uri: string): string => {
    const trimmed = uri.trim();

    return trimmed === "" || SCHEME_PREFIX.test(trimmed)
        ? trimmed
        : `${DEFAULT_SCHEME}://${trimmed}`;
};

/**
 * Whether a scheme asks for an SSH tunnel.
 *
 * @param scheme The scheme to check.
 *
 * @returns True when it carries the `+ssh` extension.
 */
export const usesSshTunnel = (scheme: string): boolean => {
    return scheme.endsWith(SSH_SCHEME_SUFFIX);
};

/**
 * The same scheme with the SSH tunnel turned on or off.
 *
 * @param scheme The scheme to change. An empty one is the default.
 * @param on Whether the result should tunnel.
 *
 * @returns The scheme to use.
 */
export const withSshTunnel = (scheme: string, on: boolean): string => {
    const base = scheme === ""
        ? DEFAULT_SCHEME
        : scheme.replace(SSH_SCHEME_SUFFIX, "");

    return on ? `${base}${SSH_SCHEME_SUFFIX}` : base;
};

/** The values `ssl-mode` takes, and how the editor captions them. */
export const SSL_MODES = [
    { value: "", caption: "Preferred (default)" },
    { value: "DISABLED", caption: "Disable" },
    { value: "PREFERRED", caption: "Preferred" },
    { value: "REQUIRED", caption: "Require" },
    { value: "VERIFY_CA", caption: "Require and Verify CA" },
    { value: "VERIFY_IDENTITY", caption: "Require and Verify Identity" },
] as const;

/** The values `compression` takes. */
export const COMPRESSION_MODES = [
    { value: "", caption: "Not set" },
    { value: "PREFERRED", caption: "Preferred" },
    { value: "REQUIRED", caption: "Required" },
    { value: "DISABLED", caption: "Disabled" },
] as const;

/** The algorithms `compression-algorithms` accepts. */
export const COMPRESSION_ALGORITHMS = [
    "zstd",
    "zlib",
    "lz4",
    "uncompressed",
] as const;

/**
 * The `ssh-*` options a URI may carry, and which field each one feeds.
 *
 * A separate set in the shell (`ssh_uri_query_attributes`), and deliberately
 * not all of the SSH options it knows: the two passwords are left out there
 * because a URI is an identity - it is what names a connection, what the
 * credential store keys on and what gets logged - so they are prompted for
 * rather than written down. They only mean anything on a `+ssh` URI; the shell
 * refuses them on any other.
 */
export const SSH_URI_OPTIONS = {
    "ssh-host": "sshHost",
    "ssh-user": "sshUser",
    "ssh-port": "sshPort",
    "ssh-identity-file": "sshIdentityFile",
    "ssh-config-file": "sshConfigFile",
} as const;

/**
 * Every option the shell allows in a URI's query string.
 *
 * Taken from `uri_connection_attributes` and `ssh_uri_query_attributes` in the
 * shell's `mysqlshdk/libs/db/utils_connection.h`. It is deliberately the whole
 * set even though the editor gives only some of them a field of their own: the
 * rest are reachable through the "Other Connection Options" table, and this is
 * what tells a typo there from a real option.
 *
 * Note what is NOT here: `sql-mode` is not a connection option in the first
 * place, and the two SSH passwords are not options a URI may carry.
 */
export const URI_OPTIONS = [
    "ssl-ca",
    "ssl-capath",
    "ssl-cert",
    "ssl-key",
    "ssl-crl",
    "ssl-crlpath",
    "ssl-cipher",
    "tls-version",
    "tls-ciphersuites",
    "ssl-mode",
    "auth-method",
    "get-server-public-key",
    "server-public-key-path",
    "connect-timeout",
    "net-read-timeout",
    "net-write-timeout",
    "compression",
    "compression-algorithms",
    "compression-level",
    "connection-attributes",
    "plugin-authentication-kerberos-client-mode",
    "oci-config-file",
    "authentication-oci-client-config-profile",
    ...Object.keys(SSH_URI_OPTIONS),
] as const;

/** The options the editor gives a field of its own, so the table skips them. */
const DEDICATED_OPTIONS = new Set<string>([
    "ssl-mode",
    "ssl-cipher",
    "ssl-ca",
    "ssl-cert",
    "ssl-key",
    "connect-timeout",
    "compression",
    "compression-level",
    "compression-algorithms",
    ...Object.keys(SSH_URI_OPTIONS),
]);

/** One row of the "Other Connection Options" table. */
export interface IExtraOption {
    name: string;
    value: string;
}

/** Everything the connection editor edits, as plain strings. */
export interface IConnectionFields {
    /**
     * One of {@link CONNECTION_SCHEMES}. "" is accepted and means
     * {@link DEFAULT_SCHEME}, which is what a URI stored before the scheme was
     * kept parses to.
     */
    scheme: string;
    host: string;
    /** Text rather than a number, so "not set" and 0 stay distinguishable. */
    port: string;
    /** A local socket or named pipe, used instead of host and port. */
    socket: string;
    user: string;
    schema: string;
    sslMode: string;
    sslCipher: string;
    sslCa: string;
    sslCert: string;
    sslKey: string;
    connectTimeout: string;
    compression: string;
    compressionLevel: string;
    compressionAlgorithms: string[];
    /** The SSH tunnel, which only a `+ssh` scheme may ask for. */
    sshHost: string;
    sshUser: string;
    sshPort: string;
    sshIdentityFile: string;
    sshConfigFile: string;
    extraOptions: IExtraOption[];
}

/** The fields a brand new connection starts from. */
export const emptyConnectionFields = (): IConnectionFields => {
    return {
        scheme: DEFAULT_SCHEME,
        host: "localhost",
        port: "3306",
        socket: "",
        user: "",
        schema: "",
        sslMode: "",
        sslCipher: "",
        sslCa: "",
        sslCert: "",
        sslKey: "",
        connectTimeout: "",
        compression: "",
        compressionLevel: "",
        compressionAlgorithms: [],
        sshHost: "",
        sshUser: "",
        sshPort: "",
        sshIdentityFile: "",
        sshConfigFile: "",
        extraOptions: [],
    };
};

/** What `buildConnectionUri` answers with. */
export interface IBuildResult {
    uri?: string;
    /** Set instead of `uri` when the fields do not name a connection. */
    error?: string;
}

/**
 * Percent-encodes one component the way the shell's encoder does.
 *
 * `encodeURIComponent` leaves `!'()*` alone where the shell escapes them, so
 * they are finished off by hand. Over-encoding is safe - the parser decodes
 * either spelling - while under-encoding is not, since an unescaped `/`, `@`
 * or `?` would end the component early.
 *
 * @param value The raw value.
 *
 * @returns The value, safe to place in a URI.
 */
const encodeComponent = (value: string): string => {
    return encodeURIComponent(value)
        .replace(/[!'()*]/g, (character) => {
            return `%${character.charCodeAt(0).toString(16).toUpperCase()}`;
        });
};

/**
 * Whether a host has to be bracketed, which a bare IPv6 address does: its
 * colons would otherwise be read as the start of the port.
 *
 * @param host The host to check.
 *
 * @returns True when it needs brackets.
 */
const needsBrackets = (host: string): boolean => {
    return host.includes(":") && !host.startsWith("[");
};

/**
 * Collects the options the fields carry, dedicated ones and table rows alike.
 *
 * @param fields The editor's fields.
 *
 * @returns The options, by name.
 */
const optionsOf = (fields: IConnectionFields): Map<string, string> => {
    const options = new Map<string, string>();
    const set = (name: string, value: string): void => {
        if (value !== "") {
            options.set(name, value);
        }
    };

    set("ssl-mode", fields.sslMode);
    set("ssl-cipher", fields.sslCipher);
    set("ssl-ca", fields.sslCa);
    set("ssl-cert", fields.sslCert);
    set("ssl-key", fields.sslKey);
    set("connect-timeout", fields.connectTimeout);
    set("compression", fields.compression);
    set("compression-level", fields.compressionLevel);
    set("compression-algorithms", fields.compressionAlgorithms.join(","));

    // Only on a tunnelling URI: the shell refuses an ssh-* option on any other
    // scheme, so carrying one over after the tunnel is switched off would make
    // the connection unsaveable rather than simply untunnelled.
    if (usesSshTunnel(fields.scheme)) {
        for (const [option, field] of Object.entries(SSH_URI_OPTIONS)) {
            set(option, fields[field]);
        }
    }

    // Last, so a row the user typed wins over a field - they can see the row.
    for (const option of fields.extraOptions) {
        const name = option.name.trim();
        if (name !== "") {
            options.set(name, option.value);
        }
    }

    return options;
};

/**
 * Checks the fields name a connection the shell could open.
 *
 * @param fields The editor's fields.
 * @param options The options collected from them.
 *
 * @returns The complaint to show, or undefined when they are fine.
 */
const validate = (
    fields: IConnectionFields,
    options: Map<string, string>,
): string | undefined => {
    if (fields.user.trim() === "") {
        return "A user name is required.";
    }

    const usesSocket = fields.socket.trim() !== "";
    if (!usesSocket && fields.host.trim() === "") {
        return "A host name, an IP address or a socket is required.";
    }

    if (!usesSocket && fields.port.trim() !== "") {
        const port = Number(fields.port);
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
            return `'${fields.port}' is not a port number between 1 and 65535.`;
        }
    }

    if (fields.scheme !== ""
        && !CONNECTION_SCHEMES.includes(fields.scheme as ConnectionScheme)) {
        return `'${fields.scheme}' is not a protocol the shell accepts.`;
    }

    for (const name of options.keys()) {
        if (!(URI_OPTIONS as readonly string[]).includes(name)) {
            return `'${name}' is not a connection option a URI can carry.`;
        }

        // Caught here rather than at the server, which would answer with the
        // shell's own wording about a scheme extension the user never typed.
        if (name in SSH_URI_OPTIONS && !usesSshTunnel(fields.scheme)) {
            return `'${name}' needs an SSH tunnel. Tick "Connect through an `
                + `SSH tunnel" on the SSH tab, or pick a '+ssh' protocol.`;
        }
    }

    if (usesSshTunnel(fields.scheme) && fields.sshPort.trim() !== "") {
        const port = Number(fields.sshPort);
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
            return `'${fields.sshPort}' is not an SSH port `
                + "between 1 and 65535.";
        }
    }

    return undefined;
};

/**
 * Writes the fields and options out as a URI, checking nothing.
 *
 * @param fields The editor's fields.
 * @param options The options collected from them.
 *
 * @returns The URI.
 */
const composeUri = (
    fields: IConnectionFields,
    options: Map<string, string>,
): string => {
    let uri = fields.scheme === "" ? "" : `${fields.scheme}://`;
    uri += encodeComponent(fields.user.trim());
    uri += "@";

    const socket = fields.socket.trim();
    if (socket !== "") {
        // The whole path is encoded, separators included, so that it reads as
        // one component rather than as a host followed by a path.
        uri += encodeComponent(socket);
    } else {
        const host = fields.host.trim();
        uri += needsBrackets(host) ? `[${host}]` : host;
        if (fields.port.trim() !== "") {
            uri += `:${fields.port.trim()}`;
        }
    }

    const schema = fields.schema.trim();
    if (schema !== "") {
        uri += `/${encodeComponent(schema)}`;
    }

    if (options.size > 0) {
        // Sorted, which is the order the shell's own encoder emits, so a URI
        // built here and one read back compare equal without re-normalizing.
        const query = [...options.entries()]
            .sort(([left], [right]) => {
                return left < right ? -1 : left > right ? 1 : 0;
            })
            .map(([name, value]) => {
                return `${name}=${encodeComponent(value)}`;
            })
            .join("&");
        uri += `?${query}`;
    }

    return uri;
};

/**
 * Builds the connection URI the editor's fields describe.
 *
 * @param fields The editor's fields.
 *
 * @returns The URI, or the reason the fields do not name a connection. A
 *          password is never part of it: the MCP server refuses a URI that
 *          carries one, and it is stored separately.
 */
export const buildConnectionUri = (
    fields: IConnectionFields,
): IBuildResult => {
    const options = optionsOf(fields);
    const error = validate(fields, options);
    if (error !== undefined) {
        return { error };
    }

    return { uri: composeUri(fields, options) };
};

/**
 * The URI the fields spell, whether or not it names a connection.
 *
 * What the editor shows while the user is still filling the fields in: a
 * new connection has no user yet, and a URI reading `mariadb://@localhost`
 * says so more plainly than an empty box would.
 *
 * @param fields The editor's fields.
 *
 * @returns The URI, unvalidated.
 */
export const previewConnectionUri = (fields: IConnectionFields): string => {
    return composeUri(fields, optionsOf(fields));
};

/**
 * Takes a connection URI apart into the editor's fields.
 *
 * Anything it cannot make sense of is left at its default rather than
 * throwing: the editor has to be able to open on whatever is configured, and
 * a field the user can see and correct beats a dialog that refuses to appear.
 *
 * @param uri The URI to parse, as `db.list_connections` reports it.
 *
 * @returns The fields it describes.
 */
export const parseConnectionUri = (uri: string): IConnectionFields => {
    const fields = emptyConnectionFields();
    fields.host = "";
    fields.port = "";

    let rest = uri.trim();

    // Kept as written, lowercased - schemes are case-insensitive. A URI with
    // none is one the MCP server stored before it kept them, and it means the
    // default, which is what the field already holds.
    const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):\/\//.exec(rest);
    if (scheme) {
        fields.scheme = scheme[1]!.toLowerCase();
        rest = rest.slice(scheme[0].length);
    }

    const query = rest.indexOf("?");
    let queryString = "";
    if (query >= 0) {
        queryString = rest.slice(query + 1);
        rest = rest.slice(0, query);
    }

    // The LAST `@`, since a password or user may contain an encoded one.
    const at = rest.lastIndexOf("@");
    if (at >= 0) {
        const credentials = rest.slice(0, at);
        rest = rest.slice(at + 1);
        // A password in the URI is dropped: the server refuses to store one
        // there, and the editor keeps passwords out of the URI entirely.
        const colon = credentials.indexOf(":");
        fields.user = decodeURIComponent(
            colon >= 0 ? credentials.slice(0, colon) : credentials,
        );
    }

    const slash = rest.indexOf("/");
    if (slash === 0) {
        // Everything after the `@` is a path: a socket, spelled unencoded.
        fields.socket = decodeURIComponent(rest);
        rest = "";
    } else if (slash > 0) {
        fields.schema = decodeURIComponent(rest.slice(slash + 1));
        rest = rest.slice(0, slash);
    }

    if (rest !== "") {
        if (rest.includes("%2F") || rest.includes("%2f")) {
            // A percent-encoded separator means the whole component is a
            // socket path rather than a host.
            fields.socket = decodeURIComponent(rest);
        } else if (rest.startsWith("[")) {
            const close = rest.indexOf("]");
            fields.host = rest.slice(1, close < 0 ? rest.length : close);
            const tail = close < 0 ? "" : rest.slice(close + 1);
            if (tail.startsWith(":")) {
                fields.port = tail.slice(1);
            }
        } else {
            const colon = rest.lastIndexOf(":");
            if (colon >= 0) {
                fields.host = decodeURIComponent(rest.slice(0, colon));
                fields.port = rest.slice(colon + 1);
            } else {
                fields.host = decodeURIComponent(rest);
            }
        }
    }

    for (const pair of queryString.split("&")) {
        if (pair === "") {
            continue;
        }

        const equals = pair.indexOf("=");
        const name = decodeURIComponent(
            equals < 0 ? pair : pair.slice(0, equals),
        ).toLowerCase();
        const value = equals < 0
            ? ""
            : decodeURIComponent(pair.slice(equals + 1));

        switch (name) {
            case "ssl-mode": { fields.sslMode = value.toUpperCase(); break; }
            case "ssl-cipher": { fields.sslCipher = value; break; }
            case "ssl-ca": { fields.sslCa = value; break; }
            case "ssl-cert": { fields.sslCert = value; break; }
            case "ssl-key": { fields.sslKey = value; break; }
            case "connect-timeout": { fields.connectTimeout = value; break; }
            case "compression": {
                fields.compression = value.toUpperCase();
                break;
            }
            case "compression-level": {
                fields.compressionLevel = value;
                break;
            }
            case "compression-algorithms": {
                fields.compressionAlgorithms = value
                    .split(",")
                    .map((entry) => { return entry.trim(); })
                    .filter((entry) => { return entry !== ""; });
                break;
            }

            default: {
                const sshField =
                    (SSH_URI_OPTIONS as Record<string, string>)[name];
                if (sshField === undefined) {
                    fields.extraOptions.push({ name, value });
                } else {
                    fields[sshField as keyof IConnectionFields] =
                        value as never;
                }
            }
        }
    }

    return fields;
};

/**
 * Whether an option belongs in the "Other Connection Options" table rather
 * than in a field of its own.
 *
 * @param name The option name.
 *
 * @returns True when the table owns it.
 */
export const isExtraOption = (name: string): boolean => {
    return !DEDICATED_OPTIONS.has(name);
};

/**
 * The scheme a URI names, lowercased, or {@link DEFAULT_SCHEME} where it
 * names none.
 *
 * @param uri The URI to read.
 *
 * @returns The scheme.
 */
export const schemeOf = (uri: string): string => {
    const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):\/\//.exec(uri.trim());

    return scheme ? scheme[1]!.toLowerCase() : DEFAULT_SCHEME;
};

/**
 * What a connection is called where there is little room for it: the URI
 * without its scheme and without its options, so `user@host:port/schema`.
 *
 * The scheme is left to an icon and the options to a tooltip. Two
 * connections differing only in either come out the same here, which is
 * why this is a caption and never a key.
 *
 * @param uri The connection's URI.
 *
 * @returns The caption.
 */
export const connectionLabel = (uri: string): string => {
    let label = uri.trim().replace(SCHEME_PREFIX, "");
    const query = label.indexOf("?");
    if (query >= 0) {
        label = label.slice(0, query);
    }

    // A socket path is stored percent-encoded, which nobody wants to read.
    try {
        return decodeURIComponent(label);
    } catch {
        return label;
    }
};

/** Where in a URI the text is wrong, and what is wrong with it. */
export interface IUriProblem {
    message: string;
    /** The offset of the first offending character in the text checked. */
    start: number;
    /** The offset just past the last one; never before `start`. */
    end: number;
}

/** What `checkConnectionUri` answers with: one of the two is set. */
export interface IUriCheck {
    fields?: IConnectionFields;
    /**
     * The password the URI carried, if it had one. It is taken out rather
     * than refused - a URI copied from elsewhere often has one - and is NOT
     * in `fields`, which never hold a password.
     */
    password?: string;
    problem?: IUriProblem;
}

/**
 * Whether a port is a number the shell accepts.
 *
 * @param port The port as written.
 *
 * @returns True for 1 to 65535.
 */
const isPort = (port: string): boolean => {
    if (!/^\d+$/.test(port)) {
        return false;
    }
    const value = Number(port);

    return value >= 1 && value <= 65535;
};

/**
 * Whether a component's percent-encoding decodes.
 *
 * @param component The component as written.
 *
 * @returns True when `decodeURIComponent` accepts it.
 */
const decodes = (component: string): boolean => {
    try {
        decodeURIComponent(component);

        return true;
    } catch {
        return false;
    }
};

/**
 * Checks a URI the user typed or pasted, and takes it apart if it is sound.
 *
 * `parseConnectionUri` is lenient on purpose - it has to open the editor on
 * whatever is stored. This is its strict counterpart for text coming the
 * other way: it refuses what the shell would refuse, and says WHERE, so the
 * editor can select the offending part rather than only describe it.
 *
 * @param text The URI as typed. Surrounding blanks are ignored.
 *
 * @returns The fields it describes and any password it carried, or the
 *          problem with it.
 */
export const checkConnectionUri = (text: string): IUriCheck => {
    const base = text.length - text.trimStart().length;
    const uri = text.trim();
    const fail = (message: string, start: number, end: number): IUriCheck => {
        return {
            problem: {
                message,
                start: base + start,
                end: base + Math.max(start, end),
            },
        };
    };

    if (uri === "") {
        return fail("Type or paste a connection URI, as "
            + "mariadb://user@host:port/schema.", 0, 0);
    }

    let position = 0;
    const scheme = /^([^:/?@]*):\/\//.exec(uri);
    if (scheme) {
        const name = scheme[1]!.toLowerCase();
        if (!CONNECTION_SCHEMES.includes(name as ConnectionScheme)) {
            return fail(`'${scheme[1]!}' is not a protocol the shell accepts. `
                + `Use one of ${CONNECTION_SCHEMES.join(", ")}.`,
            0, scheme[1]!.length);
        }
        position = scheme[0].length;
    }

    const query = uri.indexOf("?", position);
    const authorityEnd = query < 0 ? uri.length : query;

    const at = uri.lastIndexOf("@", authorityEnd - 1);
    if (at < position) {
        return fail("A user name is required: the URI has to read "
            + "user@host.", position, authorityEnd);
    }

    const credentials = uri.slice(position, at);
    if (credentials === "") {
        return fail("The user name before the '@' is empty.", position,
            at + 1);
    }

    // `user:password`, split on the first colon: a user name cannot hold
    // one unencoded, a password can.
    const colon = credentials.indexOf(":");
    const user = colon < 0 ? credentials : credentials.slice(0, colon);
    if (user === "") {
        return fail("The user name before the password is empty.", position,
            position + 1);
    }
    if (!decodes(user)) {
        return fail(`'${user}' is not validly percent-encoded.`,
            position, position + user.length);
    }

    let password: string | undefined;
    if (colon >= 0) {
        const raw = credentials.slice(colon + 1);
        if (!decodes(raw)) {
            return fail("The password is not validly percent-encoded.",
                position + colon + 1, at);
        }
        password = decodeURIComponent(raw);
    }

    const hostStart = at + 1;
    const address = uri.slice(hostStart, authorityEnd);
    if (address === "") {
        return fail("A host name, an IP address or a socket is required "
            + "after the '@'.", at, hostStart);
    }

    // A path straight after the `@` is a socket, as is a component holding
    // an encoded separator. Neither has a port or a schema to check.
    const isSocket = address.startsWith("/")
        || /%2f/i.test(address.split("/")[0]!);
    if (isSocket) {
        if (!decodes(address)) {
            return fail(`'${address}' is not validly percent-encoded.`,
                hostStart, authorityEnd);
        }
    } else {
        const slash = address.indexOf("/");
        const hostPort = slash < 0 ? address : address.slice(0, slash);

        let portAt = -1;
        if (hostPort.startsWith("[")) {
            const close = hostPort.indexOf("]");
            if (close < 0) {
                return fail("The IPv6 address is missing its closing ']'.",
                    hostStart, hostStart + hostPort.length);
            }
            const tail = hostPort.slice(close + 1);
            if (tail !== "" && !tail.startsWith(":")) {
                return fail(`'${tail}' cannot follow the IPv6 address; a `
                    + "port is written ':port'.", hostStart + close + 1,
                hostStart + hostPort.length);
            }
            portAt = tail === "" ? -1 : close + 1;
        } else {
            portAt = hostPort.lastIndexOf(":");
            if (portAt === 0) {
                return fail("The host name before the port is empty.",
                    hostStart, hostStart + 1);
            }
            if (!decodes(portAt < 0 ? hostPort : hostPort.slice(0, portAt))) {
                return fail("The host name is not validly percent-encoded.",
                    hostStart, hostStart + (portAt < 0
                        ? hostPort.length
                        : portAt));
            }
        }

        if (portAt >= 0) {
            const port = hostPort.slice(portAt + 1);
            if (!isPort(port)) {
                return fail(`'${port}' is not a port number between 1 and `
                    + "65535.", hostStart + portAt + 1,
                hostStart + hostPort.length);
            }
        }

        if (slash >= 0) {
            const schema = address.slice(slash + 1);
            if (!decodes(schema)) {
                return fail(`'${schema}' is not validly percent-encoded.`,
                    hostStart + slash + 1, authorityEnd);
            }
        }
    }

    const tunnels = usesSshTunnel(scheme ? scheme[1]!.toLowerCase() : "");
    const seen = new Set<string>();
    let pairStart = authorityEnd + 1;
    for (const pair of query < 0 ? [] : uri.slice(query + 1).split("&")) {
        const pairEnd = pairStart + pair.length;
        const equals = pair.indexOf("=");
        const rawName = equals < 0 ? pair : pair.slice(0, equals);
        const nameEnd = pairStart + rawName.length;

        if (pair === "") {
            pairStart = pairEnd + 1;
            continue;
        }

        if (!decodes(pair)) {
            return fail(`'${pair}' is not validly percent-encoded.`,
                pairStart, pairEnd);
        }

        const name = decodeURIComponent(rawName).toLowerCase();
        if (!(URI_OPTIONS as readonly string[]).includes(name)) {
            return fail(`'${rawName}' is not a connection option a URI can `
                + "carry.", pairStart, nameEnd);
        }
        if (seen.has(name)) {
            return fail(`'${rawName}' is given more than once.`, pairStart,
                nameEnd);
        }
        seen.add(name);

        if (name in SSH_URI_OPTIONS && !tunnels) {
            return fail(`'${rawName}' needs an SSH tunnel, which only a `
                + "'+ssh' protocol asks for.", pairStart, nameEnd);
        }

        if (name === "ssh-port"
            && !isPort(decodeURIComponent(pair.slice(equals + 1)))) {
            return fail("The SSH port is not a number between 1 and 65535.",
                nameEnd + 1, pairEnd);
        }

        pairStart = pairEnd + 1;
    }

    const fields = parseConnectionUri(uri);

    // Whatever the checks above let through and the builder still refuses
    // has no single place to point at, so the whole URI is marked.
    const built = buildConnectionUri(fields);
    if (built.error !== undefined) {
        return fail(built.error, 0, uri.length);
    }

    return password === undefined ? { fields } : { fields, password };
};
