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

import { useEffect, useState } from "preact/hooks";

import {
    COMPRESSION_ALGORITHMS,
    COMPRESSION_MODES,
    CONNECTION_SCHEMES,
    SSL_MODES,
    emptyConnectionFields,
    type IConnectionFields,
} from "../../src/connections/connectionUri.js";
import type {
    EditorHostMessage,
    EditorWebviewMessage,
} from "../../src/connections/editorProtocol.js";
import { post } from "./vscodeApi.js";

/**
 * The connection editor, modelled on the MySQL Shell extension's
 * `ConnectionEditor`: the same three tabs in the same order, with the same
 * captions where the setting is the same one.
 *
 * What is missing from it is missing on purpose. A connection here is stored
 * as a URI and nothing else, so a setting that cannot be written into one
 * cannot be offered: that rules out the SSH tunnel tab (the shell keeps the
 * `ssh-*` options in a separate set that never reaches a URI), the OCI and
 * MDS/Bastion tabs, `sql-mode`, and the HeatWave check. What is left is the
 * whole of what a MariaDB Shell connection URI can carry.
 */

/** The tabs, in the order the original shows them. */
const TABS = ["Basic", "SSL", "Advanced"] as const;

type Tab = (typeof TABS)[number];

/** How the Test Connection result is showing. */
interface ITestState {
    ok: boolean;
    message: string;
}

/** A labelled row, which is how every field is laid out. */
const Field = (props: {
    caption: string;
    hint?: string;
    children: preact.ComponentChildren;
}): preact.JSX.Element => {
    return (
        <label class="field">
            <span class="field-caption">{props.caption}</span>
            {props.children}
            {props.hint === undefined
                ? null
                : <span class="field-hint">{props.hint}</span>}
        </label>
    );
};

export const ConnectionEditor = (): preact.JSX.Element => {
    const [fields, setFields] =
        useState<IConnectionFields>(emptyConnectionFields());
    const [tab, setTab] = useState<Tab>("Basic");
    const [mcpAccess, setMcpAccess] = useState(false);
    const [uri, setUri] = useState<string | undefined>(undefined);
    const [hasStoredPassword, setHasStoredPassword] = useState(false);
    // undefined means "keep the stored password", which is not the same as
    // the empty string - an account with no password has an empty one.
    const [password, setPassword] = useState<string | undefined>(undefined);
    const [test, setTest] = useState<ITestState | undefined>(undefined);
    const [saveError, setSaveError] = useState<string | undefined>(undefined);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        const onMessage = (event: MessageEvent<EditorHostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "load": {
                    setFields(message.fields);
                    setMcpAccess(message.mcpAccess);
                    setUri(message.uri);
                    setHasStoredPassword(message.hasStoredPassword);
                    setPassword(undefined);
                    setTest(undefined);
                    setSaveError(undefined);
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

                default: {
                    setBusy(message.busy);
                }
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
        setTest(undefined);
        setSaveError(undefined);
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

    return (
        <div class="editor">
            <header class="editor-header">
                <h1>Database Connection Configuration</h1>
                <p class="editor-subtitle">
                    {isNew
                        ? "A new MariaDB connection."
                        : `Editing ${uri}`}
                </p>
            </header>

            <nav class="tabs" role="tablist">
                {TABS.map((name) => {
                    return (
                        <button
                            key={name}
                            type="button"
                            role="tab"
                            aria-selected={tab === name}
                            class={tab === name ? "tab selected" : "tab"}
                            onClick={() => { setTab(name); }}
                        >
                            {name}
                        </button>
                    );
                })}
            </nav>

            <div class="tab-body">
                {tab === "Basic" ? (
                    <section class="grid">
                        <Field caption="Host Name or IP Address">
                            {text("host", "localhost")}
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
                                <option value="">Default</option>
                                {CONNECTION_SCHEMES.map((scheme) => {
                                    return (
                                        <option key={scheme} value={scheme}>
                                            {scheme}
                                        </option>
                                    );
                                })}
                            </select>
                        </Field>
                        <Field caption="Port">{text("port", "3306")}</Field>
                        <Field caption="User Name">{text("user")}</Field>
                        <Field caption="Default Schema">
                            {text("schema")}
                        </Field>
                        <Field
                            caption="Socket or Named Pipe"
                            hint="Used instead of the host and port."
                        >
                            {text("socket")}
                        </Field>

                        <div class="group">
                            <h2>Password</h2>
                            {password === undefined ? (
                                <div class="row">
                                    <span class="note">
                                        {hasStoredPassword
                                            ? "A password is stored for this "
                                            + "connection and will be kept."
                                            : "No password has been set."}
                                    </span>
                                    <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() => { setPassword(""); }}
                                    >
                                        {hasStoredPassword
                                            ? "Set New Password"
                                            : "Set Password"}
                                    </button>
                                </div>
                            ) : (
                                <div class="row">
                                    <input
                                        type="password"
                                        value={password}
                                        placeholder="New password"
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
                                            onClick={() => {
                                                setPassword(undefined);
                                            }}
                                        >
                                            Keep Stored Password
                                        </button>
                                    ) : null}
                                </div>
                            )}
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
                                credentials kept here. Left unticked it belongs
                                to this extension alone.
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
                                                        title="Remove"
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
                    onClick={() => {
                        post<EditorWebviewMessage>({
                            type: "test", fields, password,
                        });
                    }}
                >
                    Test Connection
                </button>
                <span class="spacer" />
                <button
                    type="button"
                    disabled={busy}
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
                    onClick={() => {
                        post<EditorWebviewMessage>({
                            type: "save", fields, password, mcpAccess,
                        });
                    }}
                >
                    {isNew ? "Create" : "Save"}
                </button>
            </footer>
        </div>
    );
};
