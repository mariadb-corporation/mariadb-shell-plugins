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
    buildUtilOptions,
    type IUtilOperationSpec,
    type IUtilOptionSpec,
    type IUtilProblem,
    type UtilValues,
} from "../../src/util/utilFields.js";
import type {
    UtilHostMessage,
    UtilWebviewMessage,
} from "../../src/util/utilProtocol.js";
import { Field, TabBody, TabStrip } from "./dialogParts.js";
import { post } from "./vscodeApi.js";

/**
 * The dialog that sets up a dump, load, copy, export or import. What it
 * asks for comes from the extension, as the operation's spec; it draws the
 * path or the target on top, and the utility's options on two tabs.
 */

const TABS = ["Basic", "Advanced"] as const;

type Tab = (typeof TABS)[number];

export const UtilDialog = (): preact.JSX.Element => {
    const [spec, setSpec] = useState<IUtilOperationSpec | undefined>();
    const [values, setValues] = useState<UtilValues>({});
    const [targets, setTargets] = useState<string[]>([]);
    const [tab, setTab] = useState<Tab>("Basic");
    const [problem, setProblem] = useState<IUtilProblem | undefined>();
    const [startError, setStartError] = useState<string | undefined>();
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        const onMessage = (event: MessageEvent<UtilHostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "load": {
                    setSpec(message.spec);
                    setValues(message.values);
                    setTargets(message.targets);
                    setProblem(undefined);
                    setStartError(undefined);
                    break;
                }

                case "browsed": {
                    setValues((current) => {
                        return { ...current, path: message.path };
                    });
                    setProblem(undefined);
                    break;
                }

                case "startError": {
                    if (message.field === undefined) {
                        setStartError(message.message);
                    } else {
                        setProblem({
                            field: message.field, message: message.message,
                        });
                    }
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
        post<UtilWebviewMessage>({ type: "ready" });

        return () => { window.removeEventListener("message", onMessage); };
    }, []);

    if (spec === undefined) {
        return <div class="editor" />;
    }

    const update = (name: string, value: boolean | string): void => {
        setValues((current) => { return { ...current, [name]: value }; });
        setProblem(undefined);
        setStartError(undefined);
    };

    const hintOf = (field: string, hint: string): string => {
        return problem?.field === field ? problem.message : hint;
    };

    const start = (): void => {
        const built = buildUtilOptions(spec, values);
        if ("problem" in built) {
            setProblem(built.problem);
            const option = spec.options.find((candidate) => {
                return candidate.name === built.problem.field;
            });
            // Brought into view: a problem on the other tab would otherwise
            // leave the button doing nothing that can be seen.
            if (option !== undefined) {
                setTab(option.advanced === true ? "Advanced" : "Basic");
            }

            return;
        }

        setStartError(undefined);
        post<UtilWebviewMessage>({ type: "start", values });
    };

    const input = (option: IUtilOptionSpec): preact.JSX.Element => {
        const invalid = problem?.field === option.name;

        if (option.type === "choice") {
            return (
                <select
                    name={option.name}
                    value={String(values[option.name])}
                    disabled={busy}
                    onChange={(event) => {
                        update(option.name,
                            (event.target as HTMLSelectElement).value);
                    }}
                >
                    {(option.choices ?? []).map((choice) => {
                        return <option key={choice} value={choice}>{choice}</option>;
                    })}
                </select>
            );
        }

        return (
            <input
                type="text"
                name={option.name}
                class={invalid ? "invalid" : undefined}
                aria-invalid={invalid}
                value={String(values[option.name] ?? "")}
                spellcheck={false}
                disabled={busy}
                onInput={(event) => {
                    update(option.name,
                        (event.target as HTMLInputElement).value);
                }}
            />
        );
    };

    const options = (advanced: boolean): preact.JSX.Element => {
        const shown = spec.options.filter((option) => {
            return (option.advanced === true) === advanced;
        });
        const fields = shown.filter((option) => { return option.type !== "bool"; });
        const boxes = shown.filter((option) => { return option.type === "bool"; });

        return (
            <section class="grid">
                {fields.map((option) => {
                    return (
                        <Field
                            key={option.name}
                            caption={option.label}
                            hint={hintOf(option.name, option.hint)}
                        >
                            {input(option)}
                        </Field>
                    );
                })}
                {boxes.length === 0 ? null : (
                    <div class="group util-flags">
                        {boxes.map((option) => {
                            return (
                                <label
                                    key={option.name}
                                    class="checkbox"
                                    data-tooltip={option.hint}
                                >
                                    <input
                                        type="checkbox"
                                        name={option.name}
                                        checked={values[option.name] === true}
                                        disabled={busy}
                                        onChange={(event) => {
                                            update(option.name, (event.target as
                                                HTMLInputElement).checked);
                                        }}
                                    />
                                    <span>{option.label}</span>
                                    {problem?.field === option.name ? (
                                        <span class="field-hint util-problem">
                                            {problem.message}
                                        </span>
                                    ) : null}
                                </label>
                            );
                        })}
                    </div>
                )}
            </section>
        );
    };

    const hasAdvanced = spec.options.some((option) => {
        return option.advanced === true;
    });

    return (
        <div class="editor">
            <header class="editor-header">
                <h1>{spec.title}</h1>
                <p class="editor-description">{spec.description}</p>
            </header>

            <section class="grid util-where">
                {spec.path === undefined ? null : (
                    <Field
                        caption={spec.path.label}
                        hint={hintOf("path", spec.path.hint)}
                    >
                        <div class="row">
                            <input
                                type="text"
                                name="path"
                                class={problem?.field === "path" ? "invalid" : undefined}
                                aria-invalid={problem?.field === "path"}
                                value={String(values.path ?? "")}
                                spellcheck={false}
                                disabled={busy}
                                onInput={(event) => {
                                    update("path",
                                        (event.target as HTMLInputElement).value);
                                }}
                            />
                            <button
                                type="button"
                                disabled={busy}
                                onClick={(event) => {
                                    // Not the label's own click, which would
                                    // focus the input instead.
                                    event.preventDefault();
                                    post<UtilWebviewMessage>({
                                        type: "browse",
                                        current: String(values.path ?? ""),
                                    });
                                }}
                            >
                                Browse...
                            </button>
                        </div>
                    </Field>
                )}
                {spec.target !== true ? null : (
                    <Field
                        caption="Target Connection"
                        hint={hintOf("target", targets.length === 0
                            ? "There is no other connection to copy to. "
                            + "Add one in the Connections view first."
                            : "The server to copy to.")}
                    >
                        <select
                            name="target"
                            class={problem?.field === "target" ? "invalid" : undefined}
                            value={String(values.target ?? "")}
                            disabled={busy}
                            onChange={(event) => {
                                update("target",
                                    (event.target as HTMLSelectElement).value);
                            }}
                        >
                            <option value="">Choose a connection</option>
                            {targets.map((target) => {
                                return <option key={target} value={target}>{target}</option>;
                            })}
                        </select>
                    </Field>
                )}
            </section>

            {hasAdvanced ? (
                <TabStrip
                    tabs={TABS}
                    current={tab}
                    onSelect={(name) => { setTab(name); }}
                />
            ) : null}

            <TabBody>
                {options(hasAdvanced && tab === "Advanced")}
            </TabBody>

            {busy ? (
                <div class="dialog-busy">
                    <p class="message">Starting...</p>
                    <div
                        class="progress"
                        role="progressbar"
                        aria-label="Starting..."
                    >
                        <div class="progress-bar" />
                    </div>
                </div>
            ) : null}
            {startError === undefined ? null : (
                <p class="message error">{startError}</p>
            )}

            <footer class="editor-footer">
                <span class="spacer" />
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                        post<UtilWebviewMessage>({ type: "cancel" });
                    }}
                >
                    Cancel
                </button>
                <button
                    type="button"
                    class="primary"
                    disabled={busy}
                    onClick={start}
                >
                    {spec.action}
                </button>
            </footer>
        </div>
    );
};
