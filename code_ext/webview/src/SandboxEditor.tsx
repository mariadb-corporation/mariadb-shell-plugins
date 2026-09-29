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
    emptySandboxFields,
    isServerOnPath,
    sandboxConnectionUri,
    sandboxFieldProblem,
    serverVersionChoices,
    serverVersionProblem,
    type ISandboxFields,
    type SandboxField,
} from "../../src/sandboxes/sandboxFields.js";
import { ComboBox } from "./ComboBox.js";
import type {
    SandboxHostMessage,
    SandboxWebviewMessage,
} from "../../src/sandboxes/sandboxProtocol.js";
import { Field, useScrollFades } from "./dialogParts.js";
import { post } from "./vscodeApi.js";

/**
 * The New Sandbox dialog, laid out as the connection editor is: a header,
 * what the result will be called above the tabs, the tabs, and the buttons
 * at the foot. What it asks for is what `sandbox.deploy` takes, less the
 * sandbox directory - the Sandboxes view lists the default one only, so a
 * sandbox deployed anywhere else would vanish from it.
 */

/** The tabs: what nearly every sandbox needs, and the rest. */
const TABS = ["Basic", "Advanced"] as const;

type Tab = (typeof TABS)[number];

/** Which tab each field is on, so a problem can bring its tab up. */
const FIELD_TABS: Record<SandboxField, Tab> = {
    port: "Basic",
    serverVersion: "Basic",
    password: "Basic",
    passwordConfirmation: "Basic",
    allowRootFrom: "Advanced",
    serverId: "Advanced",
    ssl: "Advanced",
    mariadbdOptions: "Advanced",
    timeout: "Advanced",
    mcpAccess: "Basic",
};

export const SandboxEditor = (): preact.JSX.Element => {
    const [fields, setFields] =
        useState<ISandboxFields>(emptySandboxFields());
    const [tab, setTab] = useState<Tab>("Basic");
    const [takenPorts, setTakenPorts] = useState<number[]>([]);
    const [versions, setVersions] = useState<string[]>([]);
    const [createError, setCreateError] =
        useState<string | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    // A problem is only shown once Create was pressed, or for a field that
    // has been typed into: a new dialog is not a list of complaints.
    const [touched, setTouched] = useState<Set<SandboxField>>(new Set());
    const tabBody = useRef<HTMLDivElement>(null);
    const fades = useScrollFades(tabBody);

    useEffect(() => {
        const onMessage = (event: MessageEvent<SandboxHostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "load": {
                    setFields(message.fields);
                    setTakenPorts(message.takenPorts);
                    setVersions(message.versions);
                    setCreateError(undefined);
                    setTouched(new Set());
                    break;
                }

                case "createError": {
                    setCreateError(message.message);
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
        post<SandboxWebviewMessage>({ type: "ready" });

        return () => { window.removeEventListener("message", onMessage); };
    }, []);

    const problem = sandboxFieldProblem(fields, takenPorts);
    // Not waited on like the others: a version that is not one is shown at
    // once, and Create stays off until it is.
    const versionProblem = serverVersionProblem(fields.serverVersion);
    const shownProblem = versionProblem !== undefined
        ? { field: "serverVersion" as const, message: versionProblem }
        : problem !== undefined && touched.has(problem.field)
            ? problem
            : undefined;

    /** Changes one field, clearing what the last deploy said about them. */
    const update = <K extends SandboxField>(
        key: K,
        value: ISandboxFields[K],
    ): void => {
        setFields((current) => { return { ...current, [key]: value }; });
        setTouched((current) => { return new Set(current).add(key); });
        setCreateError(undefined);
    };

    /**
     * The hint under a field: its problem when it has the one shown,
     * otherwise what it is for.
     *
     * @param key The field.
     * @param hint What it is for.
     *
     * @returns The text.
     */
    const hintOf = (key: SandboxField, hint?: string): string | undefined => {
        return shownProblem?.field === key ? shownProblem.message : hint;
    };

    const text = (
        key: Exclude<SandboxField, "ssl" | "mcpAccess">,
        options: { placeholder?: string; type?: string; list?: string } = {},
    ): preact.JSX.Element => {
        return (
            <input
                type={options.type ?? "text"}
                class={shownProblem?.field === key ? "invalid" : undefined}
                aria-invalid={shownProblem?.field === key}
                value={fields[key]}
                placeholder={options.placeholder ?? ""}
                list={options.list}
                spellcheck={false}
                disabled={busy}
                onInput={(event) => {
                    update(key, (event.target as HTMLInputElement).value);
                }}
            />
        );
    };

    const create = (): void => {
        if (problem !== undefined) {
            // Shown, and brought into view: a problem on the other tab would
            // otherwise leave Create doing nothing that can be seen.
            setTouched((current) => {
                return new Set(current).add(problem.field);
            });
            setTab(FIELD_TABS[problem.field]);

            return;
        }

        setCreateError(undefined);
        post<SandboxWebviewMessage>({ type: "create", fields });
    };

    return (
        <div class="editor">
            <header class="editor-header">
                <h1>New Sandbox</h1>
                <p class="editor-description">
                    A local MariaDB server, deployed in the default sandbox
                    directory. Its root account is added to the Connections
                    view, in the Sandboxes folder, with the password below.
                </p>
            </header>

            {/* The fields' own two columns, so the checkbox lines up with the
                right-hand column below it: Server Version, and Confirm Root
                Password. */}
            <section class="grid sandbox-connection">
                <span class="field-caption sandbox-connection-caption">
                    Connection
                </span>
                {/* Text, not a box: the URI follows from the port and cannot
                    be typed into. */}
                <code class="sandbox-uri">
                    {sandboxConnectionUri(fields.port)}
                </code>
                <label
                    class="checkbox"
                    title={"Stores it in the shared MCP connection list, "
                        + "so any MCP client on this machine - an AI "
                        + "assistant, say - can open it. Left unticked, "
                        + "it belongs to this extension alone."}
                >
                    <input
                        type="checkbox"
                        checked={fields.mcpAccess}
                        disabled={busy}
                        onChange={(event) => {
                            update("mcpAccess", (event.target as
                                HTMLInputElement).checked);
                        }}
                    />
                    <span>Allow MCP access to this connection</span>
                </label>
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
                                caption="Port"
                                hint={hintOf("port",
                                    "Between 1024 and 65535.")}
                            >
                                {text("port", { placeholder: "3310" })}
                            </Field>
                            <Field
                                caption="Server Version"
                                hint={hintOf("serverVersion",
                                    isServerOnPath(fields.serverVersion)
                                        ? "The mariadbd found on the PATH, "
                                        + "whichever version that is."
                                        : "One this machine does not have is "
                                        + "downloaded first, which can take a "
                                        + "few minutes.")}
                            >
                                <ComboBox
                                    value={fields.serverVersion}
                                    choices={serverVersionChoices(versions)}
                                    invalid={versionProblem !== undefined}
                                    disabled={busy}
                                    placeholder="12.3.2"
                                    listLabel="Show the server versions"
                                    onInput={(value) => {
                                        update("serverVersion", value);
                                    }}
                                />
                            </Field>

                            <Field caption="Root Password">
                                {text("password", { type: "password" })}
                            </Field>
                            <Field
                                caption="Confirm Root Password"
                                hint={hintOf("passwordConfirmation")}
                            >
                                {text("passwordConfirmation",
                                    { type: "password" })}
                            </Field>
                            <p class="note password-note">
                                Stored with the connection, so it is not
                                asked for again. Left empty, root has no
                                password - which suits a server only this
                                machine can reach.
                            </p>
                        </section>
                    ) : null}

                    {tab === "Advanced" ? (
                        <section class="grid">
                            <Field
                                caption="Allow root Access From"
                                hint={hintOf("allowRootFrom",
                                    "The host a second root account may "
                                    + "connect from, as root@host. "
                                    + "127.0.0.1 is this machine only; % is "
                                    + "anywhere. Empty creates none.")}
                            >
                                {text("allowRootFrom",
                                    { placeholder: "127.0.0.1" })}
                            </Field>
                            <Field
                                caption="Server ID"
                                hint={hintOf("serverId",
                                    "The server_id, for replication.")}
                            >
                                {text("serverId")}
                            </Field>
                            <Field
                                caption="Startup Timeout"
                                hint={hintOf("timeout",
                                    "Seconds to wait for the server to "
                                    + "start. Defaults to 60.")}
                            >
                                {text("timeout", { placeholder: "60" })}
                            </Field>

                            <div class="group">
                                <label class="checkbox">
                                    <input
                                        type="checkbox"
                                        checked={fields.ssl}
                                        disabled={busy}
                                        onChange={(event) => {
                                            update("ssl", (event.target as
                                                HTMLInputElement).checked);
                                        }}
                                    />
                                    <span>Enable SSL/TLS</span>
                                </label>
                                <p class="note">
                                    Generates certificates for the server,
                                    which needs openssl. Off by default: a
                                    local sandbox does not need it.
                                </p>
                            </div>

                            <div class="group">
                                <h2>Server Options</h2>
                                <textarea
                                    rows={5}
                                    value={fields.mariadbdOptions}
                                    placeholder="innodb_buffer_pool_size=64M"
                                    spellcheck={false}
                                    disabled={busy}
                                    onInput={(event) => {
                                        const area = event.target as
                                            HTMLTextAreaElement;
                                        update("mariadbdOptions", area.value);
                                    }}
                                />
                                <p class="note">
                                    One option=value per line, written to the
                                    [mysqld] section of the sandbox's my.cnf.
                                </p>
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

            {busy ? (
                <p class="message">
                    Deploying the sandbox. A server version this machine does
                    not have is downloaded first, which can take a few
                    minutes.
                </p>
            ) : null}
            {createError === undefined ? null : (
                <p class="message error">{createError}</p>
            )}

            <footer class="editor-footer">
                <span class="spacer" />
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                        post<SandboxWebviewMessage>({ type: "cancel" });
                    }}
                >
                    Cancel
                </button>
                <button
                    type="button"
                    class="primary"
                    disabled={busy || versionProblem !== undefined}
                    title={versionProblem}
                    onClick={create}
                >
                    Create
                </button>
            </footer>
        </div>
    );
};
