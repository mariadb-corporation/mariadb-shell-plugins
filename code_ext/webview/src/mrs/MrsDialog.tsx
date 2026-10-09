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
    validateDialog,
    type MrsDialogKind,
} from "../../../src/mrs/mrsDialogs.js";
import type {
    MrsHostMessage,
    MrsWebviewMessage,
} from "../../../src/mrs/mrsDialogProtocol.js";
import { TabBody, TabStrip, Tooltips } from "../dialogParts.js";
import { post } from "../vscodeApi.js";
import { resolveColumns } from "./DataMappingEditor.js";
import { DIALOGS, type IDialogExtras } from "./dialogs.js";
import type { IFieldContext } from "./fields.js";

/**
 * The frame of every MRS dialog: the title, the main fields, the tabs and
 * the buttons, laid out as the connection editor is. What is in it comes
 * from {@link DIALOGS}, by the dialog the host's `load` message names.
 */

interface ILoaded {
    dialog: MrsDialogKind;
    title: string;
    context: unknown;
}

export const MrsDialog = (): preact.JSX.Element => {
    const [loaded, setLoaded] = useState<ILoaded | undefined>(undefined);
    const [values, setValues] = useState<Record<string, unknown>>({});
    const [tab, setTab] = useState<string | undefined>(undefined);
    const [touched, setTouched] = useState<Set<string>>(new Set());
    // Once OK was pressed, every problem shows, not only typed-in ones.
    const [attempted, setAttempted] = useState(false);
    const [saveError, setSaveError] = useState<string | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    const [scripts, setScripts] = useState<IDialogExtras["scripts"]>(undefined);

    useEffect(() => {
        const onMessage = (event: MessageEvent<MrsHostMessage>): void => {
            const message = event.data;
            switch (message.type) {
                case "load": {
                    setLoaded({
                        dialog: message.dialog,
                        title: message.title,
                        context: message.context,
                    });
                    setValues(message.values as Record<string, unknown>);
                    setTab(undefined);
                    setTouched(new Set());
                    setAttempted(false);
                    setSaveError(undefined);
                    setScripts(undefined);
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

                case "browsed": {
                    setValues((current) => {
                        return { ...current, [message.field]: message.path };
                    });
                    break;
                }

                case "columns": {
                    resolveColumns(message);
                    break;
                }

                case "scripts": {
                    setScripts(message);
                    break;
                }

                default:
            }
        };

        window.addEventListener("message", onMessage);
        post<MrsWebviewMessage>({ type: "ready" });

        return () => { window.removeEventListener("message", onMessage); };
    }, []);

    // A folder to upload is looked into for MRS scripts, which decides
    // what the content set dialog offers.
    const directory = loaded?.dialog === "contentSet"
        ? String(values.directory ?? "") : "";
    const ignoreList = String(values.ignoreList ?? "");
    useEffect(() => {
        if (directory !== "") {
            post<MrsWebviewMessage>({
                type: "analyzeFolder", directory, ignoreList,
            });
        }
    }, [directory, ignoreList]);

    if (loaded === undefined) {
        return <div class="editor" />;
    }

    const definition = DIALOGS[loaded.dialog];
    const extras: IDialogExtras = {
        context: loaded.context,
        values,
        ...(scripts === undefined || scripts.directory !== directory
            ? {} : { scripts }),
    };
    const tabs = definition.tabs(extras);
    const currentTab = tab !== undefined && tabs.includes(tab) ? tab : tabs[0];
    const problems = validateDialog(loaded.dialog, values, loaded.context);

    const ctx: IFieldContext = {
        get: (field) => { return values[field]; },
        set: (field, value) => {
            setValues((current) => {
                return {
                    ...current,
                    [field]: value,
                    ...definition.onChange?.(field, value, current,
                        loaded.context),
                };
            });
            setTouched((current) => { return new Set(current).add(field); });
            setSaveError(undefined);
        },
        update: (field, change) => {
            setValues((current) => {
                return { ...current, [field]: change(current[field]) };
            });
        },
        touch: (field) => {
            setTouched((current) => { return new Set(current).add(field); });
        },
        problem: (field) => {
            if (!attempted && !touched.has(field)) {
                return undefined;
            }

            return problems.find((problem) => {
                return problem.field === field;
            })?.message;
        },
        busy,
    };

    const save = (): void => {
        setAttempted(true);
        const first = problems[0];
        if (first !== undefined) {
            // Brought into view: a problem on another tab would otherwise
            // leave OK doing nothing that can be seen.
            const onTab = definition.fieldTabs[first.field];
            if (onTab !== undefined) {
                setTab(onTab);
            }

            return;
        }
        setSaveError(undefined);
        post<MrsWebviewMessage>({ type: "save", values });
    };

    return (
        <div class={`editor mrs-dialog mrs-${loaded.dialog}`}>
            <Tooltips />
            <header class="editor-header">
                <h1>{loaded.title}</h1>
            </header>

            {definition.main?.(ctx, extras)}

            {tabs.length === 0 ? null : (
                <>
                    <TabStrip
                        tabs={tabs}
                        current={currentTab}
                        onSelect={(name) => { setTab(name); }}
                    />
                    <TabBody>
                        {definition.render(currentTab, ctx, extras)}
                    </TabBody>
                </>
            )}

            {busy ? <p class="message">Saving...</p> : null}
            {saveError === undefined
                ? null : <p class="message error">{saveError}</p>}
            {attempted && problems.length > 0 && saveError === undefined ? (
                <p class="message error">{problems[0].message}</p>
            ) : null}

            <footer class="editor-footer">
                <span class="spacer" />
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                        post<MrsWebviewMessage>({ type: "cancel" });
                    }}
                >
                    Cancel
                </button>
                <button
                    type="button"
                    class="primary"
                    disabled={busy}
                    onClick={save}
                >
                    {definition.okLabel ?? "OK"}
                </button>
            </footer>
        </div>
    );
};
