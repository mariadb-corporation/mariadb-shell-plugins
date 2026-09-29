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

import type { RefObject } from "preact";
import {
    useEffect, useLayoutEffect, useRef, useState,
} from "preact/hooks";

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
import type {
    EditorHostMessage,
    EditorWebviewMessage,
} from "../../src/connections/editorProtocol.js";
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

/** Which edges of a scrolling area have content hidden past them. */
interface IScrollFades {
    above: boolean;
    below: boolean;
    /**
     * How wide the area's scrollbar is, which the fades stop short of. Zero
     * where scrollbars overlay the content, as macOS draws them by default:
     * leaving room there would leave an unfaded strip down the edge.
     */
    scrollbar: number;
}

/**
 * Tracks whether an area can be scrolled further up or down, so a fade can
 * say so at the edge where fields are cut off. A tab's lower fields are
 * otherwise easy to miss: nothing about a half-empty looking panel says it
 * scrolls.
 *
 * Measured after every render - and once more on the frame after, since a
 * webview is still being sized, styled and given its fonts while the first
 * renders happen - and on every other thing that moves the answer: a scroll,
 * the area or its content resizing, the window resizing, the fonts arriving,
 * the content being swapped by a tab switch. The pointer entering the area
 * is the last resort: whatever was missed, the user is about to look there.
 *
 * @param area The scrolling element.
 *
 * @returns The edges with more beyond them.
 */
const useScrollFades = (
    area: RefObject<HTMLElement>,
): IScrollFades => {
    const [fades, setFades] = useState<IScrollFades>({
        above: false, below: false, scrollbar: 0,
    });

    const measure = (): void => {
        const element = area.current;
        if (element === null) {
            return;
        }
        // A pixel of slack: zoomed layouts report fractional heights, and
        // a fade over nothing is worse than none.
        const above = element.scrollTop > 1;
        const below = element.scrollTop + element.clientHeight
            < element.scrollHeight - 1;
        // The borders are the only other thing between the two widths.
        const style = getComputedStyle(element);
        const scrollbar = Math.max(0, element.offsetWidth
            - element.clientWidth
            - (parseFloat(style.borderLeftWidth) || 0)
            - (parseFloat(style.borderRightWidth) || 0));
        setFades((current) => {
            return current.above === above && current.below === below
                && current.scrollbar === scrollbar
                ? current
                : { above, below, scrollbar };
        });
    };

    useLayoutEffect(() => {
        measure();
        const frame = requestAnimationFrame(measure);

        return () => { cancelAnimationFrame(frame); };
    });

    useEffect(() => {
        const element = area.current;
        if (element === null) {
            return undefined;
        }

        element.addEventListener("scroll", measure, { passive: true });
        element.addEventListener("pointerenter", measure);
        window.addEventListener("resize", measure);
        document.fonts?.addEventListener("loadingdone", measure);
        void document.fonts?.ready.then(measure);

        // Watching the content as well as the area: a group that appears
        // grows the content without resizing the area around it. The content
        // is re-watched whenever it is swapped, which a tab switch does.
        const resizes = typeof ResizeObserver === "undefined"
            ? undefined
            : new ResizeObserver(measure);
        const watchContent = (): void => {
            resizes?.disconnect();
            resizes?.observe(element);
            for (const child of element.children) {
                resizes?.observe(child);
            }
        };
        watchContent();
        const swaps = new MutationObserver(() => {
            watchContent();
            measure();
        });
        swaps.observe(element, { childList: true });

        return () => {
            element.removeEventListener("scroll", measure);
            element.removeEventListener("pointerenter", measure);
            window.removeEventListener("resize", measure);
            document.fonts?.removeEventListener("loadingdone", measure);
            resizes?.disconnect();
            swaps.disconnect();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return fades;
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
    // The URI as the user typed or pasted it, kept while it is being edited
    // so that the box is not rewritten under the cursor. Undefined shows what
    // the fields spell instead.
    const [uriDraft, setUriDraft] = useState<string | undefined>(undefined);
    // Bumped to have the offending part of the URI selected once the box
    // shows it, which it only does after the next render.
    const [pointAt, setPointAt] = useState(0);
    const uriInput = useRef<HTMLInputElement>(null);
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
                    setMcpAccess(message.mcpAccess);
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

    const built = buildConnectionUri(fields);
    const draftProblem = uriDraft === undefined
        ? undefined
        : checkConnectionUri(uriDraft).problem;

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
                        value={uriDraft ?? previewConnectionUri(fields)}
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
                        aria-label="Paste URI"
                        title={"Paste URI: replace the fields with the URI "
                            + "on the clipboard"}
                        onClick={() => {
                            post<EditorWebviewMessage>({ type: "paste" });
                        }}
                    >
                        <span class="codicon codicon-copy" aria-hidden="true" />
                    </button>
                </div>
                {draftProblem === undefined ? (
                    built.error === undefined ? null : (
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
                                        disabled={busy}
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
                    onClick={() => {
                        if (uriIsSound()) {
                            post<EditorWebviewMessage>({
                                type: "test", fields, password,
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
                        if (uriIsSound()) {
                            post<EditorWebviewMessage>({
                                type: "save", fields, password, mcpAccess,
                            });
                        }
                    }}
                >
                    {isNew ? "Create" : "Save"}
                </button>
            </footer>
        </div>
    );
};
