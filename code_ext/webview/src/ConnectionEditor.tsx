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

import { useEffect, useRef, useState } from "preact/hooks";

import {
    COMPRESSION_ALGORITHMS,
    COMPRESSION_MODES,
    CONNECTION_SCHEMES,
    SSL_MODES,
    buildConnectionUri,
    checkConnectionUri,
    emptyConnectionFields,
    previewConnectionUri,
    usesSshTunnel,
    withSshTunnel,
    type IConnectionFields,
    type IUriProblem,
} from "../../src/connections/connectionUri.js";
import {
    MAX_CAPTION_LENGTH,
    captionProblem,
    connectionKeyProblem,
} from "../../src/connections/connectionDetails.js";
import type {
    EditorHostMessage,
    EditorWebviewMessage,
} from "../../src/connections/editorProtocol.js";
import {
    CONNECTION_COLORS,
    type ConnectionColor,
} from "../../src/mcp/types.js";
import { Field, Tooltips, useScrollFades } from "./dialogParts.js";
import { COPIED_FOR_MS } from "./overflowPopup.js";
import { post } from "./vscodeApi.js";

/**
 * The connection editor, modelled on the MySQL Shell extension's
 * `ConnectionEditor`: the same tabs in the same order, with the same captions
 * where the setting is the same one.
 *
 * What is missing from it is missing on purpose. A connection here is stored
 * as a URI and nothing else, so a setting that cannot be written into one
 * cannot be offered: that rules out the OCI and MDS/Bastion tabs, `sql-mode`,
 * the HeatWave check, and the two SSH passwords - the shell keeps those out of
 * a URI deliberately, so a tunnel needing one is reached with a key instead.
 * What is left is the whole of what a MariaDB Shell connection URI can carry.
 */

/** The tabs, in the order the original shows them. */
const TABS = ["Basic", "SSL", "SSH", "Advanced"] as const;

type Tab = (typeof TABS)[number];

/** What each tab's tooltip says it holds. */
const TAB_TOOLTIPS: Record<Tab, string> = {
    Basic: "Where the server is, who connects, and how the connection is "
        + "shown in the Connections view",
    SSL: "How the connection is encrypted",
    SSH: "Reaching the server through an SSH tunnel",
    Advanced: "Compression, and any other option a connection URI can "
        + "carry",
};

/** How the Test Connection result is showing. */
interface ITestState {
    ok: boolean;
    message: string;
}

/**
 * What is wrong with a URI, with the offending part of it marked, so the
 * user can see where to look even once the box has lost its selection.
 */
const UriProblem = (props: {
    text: string;
    problem: IUriProblem;
}): preact.JSX.Element => {
    const { text, problem } = props;

    return (
        <div class="uri-problem" role="alert">
            <p>{problem.message}</p>
            {problem.end > problem.start ? (
                <code>
                    {text.slice(0, problem.start)}
                    <mark>{text.slice(problem.start, problem.end)}</mark>
                    {text.slice(problem.end)}
                </code>
            ) : null}
        </div>
    );
};

/**
 * The fields as the chosen endpoint has them: a socket drops the host and
 * port, a host drops the socket. Both are kept in the dialog, so switching
 * back finds what was typed; only the chosen one is built, tested or saved.
 *
 * @param fields The fields as typed.
 * @param useSocket Whether the socket (or named pipe) is the one chosen.
 *
 * @returns The fields to build the URI from.
 */
export const endpointFields = (
    fields: IConnectionFields,
    useSocket: boolean,
): IConnectionFields => {
    return useSocket
        ? { ...fields, host: "", port: "" }
        : { ...fields, socket: "" };
};

export const ConnectionEditor = (): preact.JSX.Element => {
    const [fields, setFields] =
        useState<IConnectionFields>(emptyConnectionFields());
    const [tab, setTab] = useState<Tab>("Basic");
    const [mcpAccess, setMcpAccess] = useState(false);
    // The folder and caption as typed; the host normalizes them on save.
    const [folder, setFolder] = useState("/");
    const [folders, setFolders] = useState<string[]>([]);
    const [caption, setCaption] = useState("");
    const [color, setColor] = useState<ConnectionColor | "">("");
    // Which endpoint the connection goes to: a host and port, or a local
    // socket - a named pipe on Windows.
    const [useSocket, setUseSocket] = useState(false);
    const [windows, setWindows] = useState(false);
    const [uri, setUri] = useState<string | undefined>(undefined);
    const [hasStoredPassword, setHasStoredPassword] = useState(false);
    // undefined means "keep the stored password", which is not the same as
    // the empty string - an account with no password has an empty one.
    const [password, setPassword] = useState<string | undefined>(undefined);
    const [test, setTest] = useState<ITestState | undefined>(undefined);
    const [saveError, setSaveError] = useState<string | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    // The Copy button shows a check for a moment after copying.
    const [copied, setCopied] = useState(false);
    // The URI as the user typed or pasted it, kept while it is being edited
    // so that the box is not rewritten under the cursor. Undefined shows what
    // the fields spell instead.
    const [uriDraft, setUriDraft] = useState<string | undefined>(undefined);
    // Bumped to have the offending part of the URI selected once the box
    // shows it, which it only does after the next render.
    const [pointAt, setPointAt] = useState(0);
    const uriInput = useRef<HTMLInputElement>(null);
    const captionInput = useRef<HTMLInputElement>(null);
    // The host or socket box, whichever is shown.
    const endpointInput = useRef<HTMLInputElement>(null);
    // What to focus once the next render has put it on screen.
    const [focusNext, setFocusNext] =
        useState<"caption" | "endpoint" | undefined>(undefined);
    const tabBody = useRef<HTMLDivElement>(null);
    const fades = useScrollFades(tabBody);

    /**
     * Takes a URI the user typed or pasted. A sound one replaces the fields;
     * an unsound one leaves them as they were and is kept to be corrected.
     *
     * Reads no state, only sets it, so the message handler below - set up
     * once - can call it without seeing stale values.
     *
     * A password in it goes into the Password field, never into the fields.
     *
     * @param text The URI.
     * @param settle Whether a sound URI should give way to the fields'
     *   own spelling at once, as after a paste, rather than on blur.
     *
     * @returns The problem with it, if it has one.
     */
    const takeUri = (text: string, settle: boolean): IUriProblem | undefined => {
        const check = checkConnectionUri(text);
        setTest(undefined);
        setSaveError(undefined);
        if (check.fields === undefined) {
            setUriDraft(text);

            return check.problem;
        }

        setFields(check.fields);
        setUseSocket(check.fields.socket.trim() !== "");
        if (check.password === undefined) {
            setUriDraft(settle ? undefined : text);
        } else {
            // Into the Password field, and out of the box at once, so the
            // secret is not left on screen in the clear.
            setPassword(check.password);
            setUriDraft(undefined);
        }

        return undefined;
    };

    useEffect(() => {
        const onMessage = (event: MessageEvent<EditorHostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "load": {
                    setFields(message.fields);
                    setUseSocket(message.fields.socket.trim() !== "");
                    setWindows(message.windows);
                    setMcpAccess(message.mcpAccess);
                    setFolder(message.path);
                    setFolders(message.folders);
                    setCaption(message.caption);
                    if (message.caption === "") {
                        // Naming it is the first thing to do.
                        setFocusNext("caption");
                    }
                    setColor(message.color);
                    setUri(message.uri);
                    setHasStoredPassword(message.hasStoredPassword);
                    setPassword(undefined);
                    setTest(undefined);
                    setSaveError(undefined);
                    setUriDraft(undefined);
                    break;
                }

                case "clipboard": {
                    if (takeUri(message.text.trim(), true) !== undefined) {
                        setPointAt((count) => { return count + 1; });
                    }
                    break;
                }

                case "testResult": {
                    setTest({ ok: message.ok, message: message.message });
                    break;
                }

                case "saveError": {
                    setSaveError(message.message);
                    break;
                }

                case "busy": {
                    setBusy(message.busy);
                    break;
                }

                default:
            }
        };

        window.addEventListener("message", onMessage);
        post<EditorWebviewMessage>({ type: "ready" });

        return () => { window.removeEventListener("message", onMessage); };
    }, []);

    /** Changes one field, clearing whatever the last test said about them. */
    const update = <K extends keyof IConnectionFields>(
        key: K,
        value: IConnectionFields[K],
    ): void => {
        setFields((current) => { return { ...current, [key]: value }; });
        // A field changed is the fields speaking, so the box follows them
        // again - dropping a draft that did not parse along the way.
        setUriDraft(undefined);
        setTest(undefined);
        setSaveError(undefined);
    };

    /**
     * Switches between the host and the socket. A socket is on this
     * machine, so a protocol that tunnels gives up its tunnel.
     *
     * @param socket Whether the socket (or named pipe) is chosen.
     *
     * @returns Nothing.
     */
    const chooseEndpoint = (socket: boolean): void => {
        setUseSocket(socket);
        // Picking one is about to type into it.
        setFocusNext("endpoint");
        if (socket && usesSshTunnel(fields.scheme)) {
            update("scheme", withSshTunnel(fields.scheme, false));
        } else {
            setUriDraft(undefined);
            setTest(undefined);
            setSaveError(undefined);
        }
    };

    const text = (
        key: keyof IConnectionFields,
        placeholder = "",
    ): preact.JSX.Element => {
        return (
            <input
                type="text"
                value={fields[key] as string}
                placeholder={placeholder}
                disabled={busy}
                onInput={(event) => {
                    update(key, (event.target as HTMLInputElement).value as never);
                }}
            />
        );
    };

    const isNew = uri === undefined;

    const endpoint = endpointFields(fields, useSocket);
    const socketName = windows ? "Named Pipe" : "Socket";
    const built = buildConnectionUri(endpoint);
    const draftProblem = uriDraft === undefined
        ? undefined
        : checkConnectionUri(uriDraft).problem;
    const keyProblem = built.uri === undefined
        ? undefined
        : connectionKeyProblem(built.uri);
    const nameProblem = captionProblem(caption);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => { setCopied(false); }, COPIED_FOR_MS);

        return () => { clearTimeout(timer); };
    }, [copied]);

    useEffect(() => {
        if (focusNext === undefined) {
            return;
        }
        (focusNext === "caption" ? captionInput : endpointInput)
            .current?.focus();
        setFocusNext(undefined);
    }, [focusNext]);

    useEffect(() => {
        const input = uriInput.current;
        if (pointAt === 0 || input === null || draftProblem === undefined) {
            return;
        }
        input.focus();
        input.setSelectionRange(draftProblem.start, draftProblem.end);
        // Only a new request moves the selection, not every keystroke.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pointAt]);

    /**
     * Whether the URI box may be acted on. A draft that does not parse
     * would otherwise be quietly ignored in favour of the fields behind it,
     * so it is pointed at instead.
     *
     * @returns True when saving or testing may go ahead.
     */
    const uriIsSound = (): boolean => {
        if (draftProblem === undefined) {
            return true;
        }
        setPointAt((count) => { return count + 1; });

        return false;
    };

    return (
        <div class="editor">
            <Tooltips />
            <header class="editor-header">
                <h1>Database Connection Configuration</h1>
            </header>

            <section class="uri">
                <label class="field" for="connection-uri">
                    <span class="field-caption">Connection URI</span>
                </label>
                <div class="row">
                    <input
                        id="connection-uri"
                        ref={uriInput}
                        type="text"
                        class={draftProblem === undefined
                            ? "uri-input"
                            : "uri-input invalid"}
                        spellcheck={false}
                        aria-invalid={draftProblem !== undefined}
                        value={uriDraft ?? previewConnectionUri(endpoint)}
                        disabled={busy}
                        onInput={(event) => {
                            takeUri((event.target as HTMLInputElement).value,
                                false);
                        }}
                        onBlur={() => {
                            // A sound draft gives way to the canonical
                            // spelling once the user is done with it.
                            if (uriDraft !== undefined
                                && draftProblem === undefined) {
                                setUriDraft(undefined);
                            }
                        }}
                    />
                    <button
                        type="button"
                        class="icon-button"
                        disabled={busy}
                        aria-label="Copy URI"
                        // Not a title, here or on any button: the webview
                        // shows those late, and often not at all.
                        data-tooltip="Copy URI: put the URI on the clipboard"
                        onClick={() => {
                            post<EditorWebviewMessage>({
                                type: "copy",
                                text: uriDraft
                                    ?? previewConnectionUri(endpoint),
                            });
                            setCopied(true);
                        }}
                    >
                        <span
                            class={copied
                                ? "codicon codicon-check"
                                : "codicon codicon-copy"}
                            aria-hidden="true"
                        />
                    </button>
                    <button
                        type="button"
                        class="icon-button"
                        disabled={busy}
                        aria-label="Paste URI"
                        data-tooltip={"Paste URI: replace the fields with "
                            + "the URI on the clipboard"}
                        onClick={() => {
                            post<EditorWebviewMessage>({ type: "paste" });
                        }}
                    >
                        <span class="codicon codicon-clippy" aria-hidden="true" />
                    </button>
                </div>
                {draftProblem === undefined ? (
                    built.error === undefined ? (
                        keyProblem === undefined ? null : (
                            <div class="uri-problem" role="alert">
                                <p>{keyProblem}</p>
                            </div>
                        )
                    ) : (
                        <p class="uri-note">{built.error}</p>
                    )
                ) : (
                    <UriProblem text={uriDraft!} problem={draftProblem} />
                )}
            </section>

            <nav class="tabs" role="tablist">
                {TABS.map((name) => {
                    return (
                        <button
                            key={name}
                            type="button"
                            role="tab"
                            aria-selected={tab === name}
                            class={tab === name ? "tab selected" : "tab"}
                            data-tooltip={TAB_TOOLTIPS[name]}
                            onClick={() => { setTab(name); }}
                        >
                            {name}
                        </button>
                    );
                })}
            </nav>

            <div class="tab-scroller">
                <div class="tab-body" ref={tabBody}>
                    {tab === "Basic" ? (
                        <section class="grid">
                            <Field
                                caption="Caption"
                                hint={nameProblem
                                    ?? "What the Connections view shows "
                                    + "instead of the URI. Left empty, it "
                                    + "shows the URI."}
                            >
                                <input
                                    ref={captionInput}
                                    type="text"
                                    class={nameProblem === undefined
                                        ? undefined
                                        : "invalid"}
                                    value={caption}
                                    maxLength={MAX_CAPTION_LENGTH}
                                    disabled={busy}
                                    onInput={(event) => {
                                        setCaption((event.target as
                                            HTMLInputElement).value);
                                        setSaveError(undefined);
                                    }}
                                />
                            </Field>
                            <Field
                                caption="Folder"
                                hint={"Where the Connections view files it. "
                                    + "'/' is the top level; "
                                    + "/Sandboxes/note_app is a folder "
                                    + "inside another."}
                            >
                                <input
                                    type="text"
                                    list="connection-folders"
                                    value={folder}
                                    placeholder="/"
                                    spellcheck={false}
                                    disabled={busy}
                                    onInput={(event) => {
                                        setFolder((event.target as
                                            HTMLInputElement).value);
                                        setSaveError(undefined);
                                    }}
                                />
                                <datalist id="connection-folders">
                                    {folders.map((path) => {
                                        return <option key={path} value={path} />;
                                    })}
                                </datalist>
                            </Field>
                            {/* Not a Field: a <label> would hand a click anywhere in it
                                to its first radio button. */}
                            <div class="field">
                                <div class="field-caption endpoint-caption" role="radiogroup"
                                    aria-label="Connect to"
                                >
                                    <label class="endpoint-choice">
                                        <input
                                            type="radio"
                                            name="endpoint"
                                            checked={!useSocket}
                                            disabled={busy}
                                            onChange={() => { chooseEndpoint(false); }}
                                        />
                                        <span>Host Name/IP</span>
                                    </label>
                                    <label class="endpoint-choice">
                                        <input
                                            type="radio"
                                            name="endpoint"
                                            checked={useSocket}
                                            disabled={busy}
                                            onChange={() => { chooseEndpoint(true); }}
                                        />
                                        <span>{socketName}</span>
                                    </label>
                                </div>
                                {useSocket
                                    ? (
                                        <input
                                            ref={endpointInput}
                                            type="text"
                                            aria-label={socketName}
                                            value={fields.socket}
                                            disabled={busy}
                                            onInput={(event) => {
                                                update("socket",
                                                    (event.target as HTMLInputElement).value);
                                            }}
                                        />
                                    )
                                    : (
                                        <input
                                            ref={endpointInput}
                                            type="text"
                                            aria-label="Host Name/IP"
                                            value={fields.host}
                                            placeholder="localhost"
                                            disabled={busy}
                                            onInput={(event) => {
                                                update("host",
                                                    (event.target as HTMLInputElement).value);
                                            }}
                                        />
                                    )}
                            </div>
                            {/* Port and Protocol share one cell: neither needs a whole
                                column. */}
                            <div class="field-pair">
                                <Field caption="Port">
                                    <input
                                        type="text"
                                        value={useSocket ? "" : fields.port}
                                        placeholder={useSocket ? "" : "3306"}
                                        disabled={busy || useSocket}
                                        onInput={(event) => {
                                            update("port", (event.target as
                                                HTMLInputElement).value);
                                        }}
                                    />
                                </Field>
                                <Field caption="Protocol">
                                    <select
                                        value={fields.scheme}
                                        disabled={busy}
                                        onChange={(event) => {
                                            update("scheme",
                                                (event.target as HTMLSelectElement).value);
                                        }}
                                    >
                                        {CONNECTION_SCHEMES.map((scheme) => {
                                            return (
                                                <option
                                                key={scheme}
                                                value={scheme}
                                                // A tunnel reaches a host;
                                                // a socket is on this one.
                                                disabled={useSocket
                                                    && usesSshTunnel(scheme)}
                                            >
                                                    {scheme}
                                                </option>
                                            );
                                        })}
                                    </select>
                                </Field>
                            </div>
                            <Field caption="User Name">{text("user")}</Field>
                            {/* Not a Field: a <label> hands a click anywhere in it to its
                                first button. */}
                            <div class="field password-field">
                                <span class="field-caption">Password</span>
                                {password === undefined ? (
                                    // Beside the button, where there is room;
                                    // it wraps rather than pushing it out.
                                    <div class="row password-row">
                                        <button
                                            type="button"
                                            disabled={busy}
                                            data-tooltip={hasStoredPassword
                                                ? "Type a password to replace "
                                                + "the stored one"
                                                : "Type a password to store "
                                                + "with the connection"}
                                            onClick={() => { setPassword(""); }}
                                        >
                                            {hasStoredPassword
                                                ? "Set New Password"
                                                : "Set Password"}
                                        </button>
                                        <span class="field-hint password-state">
                                            {hasStoredPassword
                                                ? "A password for this connection "
                                                + "has been stored."
                                                : "Stores the password for this "
                                                + "connection."}
                                        </span>
                                    </div>
                                ) : (
                                    <>
                                        <div class="row">
                                            <input
                                                type="password"
                                                value={password}
                                                placeholder="New password"
                                                aria-label="Password"
                                                disabled={busy}
                                                onInput={(event) => {
                                                    setPassword((event.target as
                                                        HTMLInputElement).value);
                                                }}
                                            />
                                            {hasStoredPassword ? (
                                                <button
                                                    type="button"
                                                    disabled={busy}
                                                    data-tooltip={"Keep the "
                                                        + "stored password, "
                                                        + "dropping the one "
                                                        + "typed here"}
                                                    onClick={() => {
                                                        setPassword(undefined);
                                                    }}
                                                >
                                                    Keep Stored Password
                                                </button>
                                            ) : null}
                                        </div>
                                        <span class="field-hint">
                                            Saved with the connection.
                                        </span>
                                    </>
                                )}
                            </div>
                            <Field caption="Default Schema">
                                {text("schema")}
                            </Field>
                            {/* Not a Field: a <label> hands a click anywhere
                                in it to its first button. */}
                            <div class="field">
                                <span class="field-caption" id="color-caption">
                                    Color
                                </span>
                                <div
                                    class="row wrap colors"
                                    role="radiogroup"
                                    aria-labelledby="color-caption"
                                >
                                    {(["", ...CONNECTION_COLORS] as const)
                                        .map((choice) => {
                                            const name = choice === ""
                                                ? "None"
                                                : choice[0]!.toUpperCase()
                                                + choice.slice(1);

                                            return (
                                                <button
                                                    key={choice || "none"}
                                                    type="button"
                                                    role="radio"
                                                    aria-checked={color === choice}
                                                    aria-label={name}
                                                    data-tooltip={name}
                                                    class={color === choice
                                                        ? "swatch selected"
                                                        : "swatch"}
                                                    style={choice === ""
                                                        ? undefined
                                                        : {
                                                            background: "var("
                                                                + "--vscode-charts-"
                                                                + `${choice})`,
                                                        }}
                                                    disabled={busy}
                                                    onClick={() => {
                                                        setColor(choice);
                                                        setSaveError(undefined);
                                                    }}
                                                >
                                                    {choice === "" ? (
                                                        <span
                                                            class={"codicon "
                                                                + "codicon-circle-slash"}
                                                            aria-hidden="true"
                                                        />
                                                    ) : null}
                                                </button>
                                            );
                                        })}
                                </div>
                                <span class="field-hint">
                                    The color the Connections view draws it in.
                                </span>
                            </div>

                            <div class="group">
                                <h2>Model Context Protocol</h2>
                                <label class="checkbox">
                                    <input
                                        type="checkbox"
                                        checked={mcpAccess}
                                        disabled={busy}
                                        onChange={(event) => {
                                            setMcpAccess((event.target as
                                                HTMLInputElement).checked);
                                            setSaveError(undefined);
                                        }}
                                    />
                                    <span>Allow MCP access to this connection</span>
                                </label>
                                <p class="note">
                                    Stores it in the shared MCP connection list, so
                                    any MCP client on this machine - an AI
                                    assistant, say - can open it with the
                                    credentials kept here. Left unticked if it
                                    belongs to this extension alone.
                                </p>
                            </div>
                        </section>
                    ) : null}

                    {tab === "SSL" ? (
                        <section class="grid">
                            <Field caption="SSL Mode">
                                <select
                                    value={fields.sslMode}
                                    disabled={busy}
                                    onChange={(event) => {
                                        update("sslMode", (event.target as
                                            HTMLSelectElement).value);
                                    }}
                                >
                                    {SSL_MODES.map((mode) => {
                                        return (
                                            <option
                                                key={mode.value}
                                                value={mode.value}
                                            >
                                                {mode.caption}
                                            </option>
                                        );
                                    })}
                                </select>
                            </Field>
                            <Field caption="Permitted ciphers (optional, comma separated list)">
                                {text("sslCipher")}
                            </Field>
                            <Field caption="Path to Certificate Authority file for SSL">
                                {text("sslCa")}
                            </Field>
                            <Field caption="Path to Client Certificate file for SSL">
                                {text("sslCert")}
                            </Field>
                            <Field caption="Path to Client Key file for SSL">
                                {text("sslKey")}
                            </Field>
                        </section>
                    ) : null}

                    {tab === "SSH" ? (
                        <section class="grid">
                            <div class="group">
                                <label class="checkbox">
                                    <input
                                        type="checkbox"
                                        checked={usesSshTunnel(fields.scheme)}
                                        disabled={busy || useSocket}
                                        onChange={(event) => {
                                            update("scheme", withSshTunnel(
                                                fields.scheme,
                                                (event.target as HTMLInputElement)
                                                    .checked,
                                            ));
                                        }}
                                    />
                                    <span>Connect through an SSH tunnel</span>
                                </label>
                                <p class="note">
                                    A tunnel is asked for by protocol and by
                                    nothing else, so this is the Protocol field on
                                    the Basic tab: it switches between
                                    <code> mariadb://</code> and
                                    <code> mariadb+ssh://</code>. The host and port
                                    on that tab stay the DATABASE - what changes is
                                    how it is reached.
                                </p>
                            </div>

                            {usesSshTunnel(fields.scheme) ? (
                                <>
                                    <Field
                                        caption="SSH Host"
                                        hint={"The machine to tunnel through. "
                                            + "Left empty, the database host is "
                                            + "also the SSH host and the tunnel "
                                            + "forwards to loopback there."}
                                    >
                                        {text("sshHost")}
                                    </Field>
                                    <Field
                                        caption="SSH User Name"
                                        hint="Defaults to whoever runs VS Code."
                                    >
                                        {text("sshUser")}
                                    </Field>
                                    <Field caption="SSH Port">
                                        {text("sshPort", "22")}
                                    </Field>
                                    <Field caption="Path to SSH Identity File">
                                        {text("sshIdentityFile")}
                                    </Field>
                                    <Field caption="Path to SSH Config File">
                                        {text("sshConfigFile")}
                                    </Field>

                                    <p class="note">
                                        There is no field for an SSH password or a
                                        key passphrase: a URI is what names and
                                        identifies a connection, so the shell keeps
                                        secrets out of it. Use a key the agent has
                                        already unlocked.
                                    </p>
                                </>
                            ) : null}
                        </section>
                    ) : null}

                    {tab === "Advanced" ? (
                        <section class="grid">
                            <Field
                                caption="Connection Timeout"
                                hint="In milliseconds."
                            >
                                {text("connectTimeout")}
                            </Field>
                            <Field caption="Compression">
                                <select
                                    value={fields.compression}
                                    disabled={busy}
                                    onChange={(event) => {
                                        update("compression", (event.target as
                                            HTMLSelectElement).value);
                                    }}
                                >
                                    {COMPRESSION_MODES.map((mode) => {
                                        return (
                                            <option
                                                key={mode.value}
                                                value={mode.value}
                                            >
                                                {mode.caption}
                                            </option>
                                        );
                                    })}
                                </select>
                            </Field>
                            <Field caption="Compression Level">
                                {text("compressionLevel")}
                            </Field>

                            <div class="group">
                                <h2>Compression Algorithms</h2>
                                <div class="row wrap">
                                    {COMPRESSION_ALGORITHMS.map((algorithm) => {
                                        const on = fields.compressionAlgorithms
                                            .includes(algorithm);

                                        return (
                                            <label class="checkbox" key={algorithm}>
                                                <input
                                                    type="checkbox"
                                                    checked={on}
                                                    disabled={busy}
                                                    onChange={() => {
                                                        update(
                                                            "compressionAlgorithms",
                                                            on
                                                                ? fields
                                                                    .compressionAlgorithms
                                                                    .filter((entry) => {
                                                                        return entry
                                                                            !== algorithm;
                                                                    })
                                                                : [...fields
                                                                    .compressionAlgorithms,
                                                                algorithm],
                                                        );
                                                    }}
                                                />
                                                <span>{algorithm}</span>
                                            </label>
                                        );
                                    })}
                                </div>
                            </div>

                            <div class="group">
                                <h2>Other Connection Options</h2>
                                <p class="note">
                                    Anything else the connection URI may carry.
                                    An option the shell does not accept in a URI
                                    is refused when the connection is saved.
                                </p>
                                <table class="options">
                                    <tbody>
                                        {fields.extraOptions.map((option, index) => {
                                            return (
                                                // eslint-disable-next-line react/no-array-index-key
                                                <tr key={index}>
                                                    <td>
                                                        <input
                                                            type="text"
                                                            value={option.name}
                                                            placeholder="option"
                                                            disabled={busy}
                                                            onInput={(event) => {
                                                                const next =
                                                                    [...fields
                                                                        .extraOptions];
                                                                next[index] = {
                                                                    ...option,
                                                                    name: (event
                                                                        .target as
                                                                        HTMLInputElement)
                                                                        .value,
                                                                };
                                                                update("extraOptions",
                                                                    next);
                                                            }}
                                                        />
                                                    </td>
                                                    <td>
                                                        <input
                                                            type="text"
                                                            value={option.value}
                                                            placeholder="value"
                                                            disabled={busy}
                                                            onInput={(event) => {
                                                                const next =
                                                                    [...fields
                                                                        .extraOptions];
                                                                next[index] = {
                                                                    ...option,
                                                                    value: (event
                                                                        .target as
                                                                        HTMLInputElement)
                                                                        .value,
                                                                };
                                                                update("extraOptions",
                                                                    next);
                                                            }}
                                                        />
                                                    </td>
                                                    <td>
                                                        <button
                                                            type="button"
                                                            disabled={busy}
                                                            data-tooltip={"Remove "
                                                                + "this option"}
                                                            onClick={() => {
                                                                update("extraOptions",
                                                                    fields.extraOptions
                                                                        .filter((_, at) => {
                                                                            return at
                                                                                !== index;
                                                                        }));
                                                            }}
                                                        >
                                                            Remove
                                                        </button>
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                                <button
                                    type="button"
                                    disabled={busy}
                                    data-tooltip={"Add a connection option "
                                        + "the other tabs do not offer"}
                                    onClick={() => {
                                        update("extraOptions", [
                                            ...fields.extraOptions,
                                            { name: "", value: "" },
                                        ]);
                                    }}
                                >
                                    Add Option
                                </button>
                            </div>
                        </section>
                    ) : null}
                </div>
                <div
                    class={fades.above ? "scroll-fade top shown" : "scroll-fade top"}
                    style={{ right: `${fades.scrollbar}px` }}
                    aria-hidden="true"
                />
                <div
                    class={fades.below
                        ? "scroll-fade bottom shown"
                        : "scroll-fade bottom"}
                    style={{ right: `${fades.scrollbar}px` }}
                    aria-hidden="true"
                />
            </div>

            {test === undefined ? null : (
                <p class={test.ok ? "message ok" : "message error"}>
                    {test.message}
                </p>
            )}
            {saveError === undefined ? null : (
                <p class="message error">{saveError}</p>
            )}

            <footer class="editor-footer">
                <button
                    type="button"
                    disabled={busy}
                    data-tooltip={"Try connecting with these settings, "
                        + "without saving them"}
                    onClick={() => {
                        if (uriIsSound()) {
                            post<EditorWebviewMessage>({
                                type: "test", fields: endpoint, password,
                            });
                        }
                    }}
                >
                    Test Connection
                </button>
                <span class="spacer" />
                <button
                    type="button"
                    disabled={busy}
                    data-tooltip="Close without saving"
                    onClick={() => {
                        post<EditorWebviewMessage>({ type: "cancel" });
                    }}
                >
                    Cancel
                </button>
                <button
                    type="button"
                    class="primary"
                    disabled={busy}
                    data-tooltip={isNew
                        ? "Add the connection to the Connections view"
                        : "Save the changes to this connection"}
                    onClick={() => {
                        if (!uriIsSound()) {
                            return;
                        }
                        const problem = keyProblem ?? nameProblem;
                        if (problem !== undefined) {
                            // Shown already, by its field; saying it again
                            // by the button is what answers the click.
                            setSaveError(problem);

                            return;
                        }
                        post<EditorWebviewMessage>({
                            type: "save", fields: endpoint, password,
                            mcpAccess,
                            path: folder, caption, color,
                        });
                    }}
                >
                    {isNew ? "Create" : "Save"}
                </button>
            </footer>
        </div>
    );
};
