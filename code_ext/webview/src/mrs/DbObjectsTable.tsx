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
    FormatModule,
    InteractionModule,
    Tabulator,
    type CellComponent,
    type ColumnDefinition,
} from "tabulator-tables";

import {
    DB_OBJECT_TYPES,
    dbObjectKey,
    filterDbObjects,
    type DbObjectType,
    type IDbObject,
} from "../../../src/mrs/mrsDialogs.js";
import type { IFieldContext } from "./fields.js";

/**
 * A new REST schema's REST Objects tab: the database schema's tables,
 * views, procedures and functions, each with a checkbox, so REST objects
 * for any of them are created with the schema. A toolbar selects all or
 * none of those on show, filters them by type, and searches their names
 * as `LIKE '<typed>%'` does.
 *
 * The ticked objects are the dialog's `restObjects` field, by
 * {@link dbObjectKey}.
 */

// Only what this table uses: its cells' formatter and its rows' clicks.
// TabulatorFull would add every module, four times the dialogs' bundle.
Tabulator.registerModule([FormatModule, InteractionModule]);

/** One row of the table. */
interface IDbObjectRow extends IDbObject {
    key: string;
    selected: boolean;
}

/** What the type filter offers, all types first. */
const TYPE_CHOICES: ReadonlyArray<{ value: DbObjectType | ""; label: string }> = [
    { value: "", label: "All Types" },
    ...DB_OBJECT_TYPES.map((type) => {
        return {
            value: type,
            label: `${type.charAt(0)}${type.slice(1).toLowerCase()}s`,
        };
    }),
];

/**
 * Renders the Database Object cell: the checkbox, the type's icon - the
 * Connections tree's own - and the name.
 *
 * @param cell The cell.
 *
 * @returns Its content.
 */
export const formatDbObjectCell = (cell: CellComponent): HTMLElement => {
    const row = cell.getRow().getData() as IDbObjectRow;
    const host = document.createElement("span");
    host.className = "dbObjectCell";

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = row.selected;
    // A tab stop, so the keyboard can tick it: Space clicks it, and the
    // click, as the pointer's on the row, reaches the row's handler.
    box.setAttribute("aria-label", `Create a REST object for ${row.name}`);
    host.appendChild(box);

    const icon = document.createElement("span");
    icon.className = `dbObjectIcon ${row.type.toLowerCase()}`;
    host.appendChild(icon);

    const name = document.createElement("span");
    name.className = "dbObjectName";
    name.textContent = row.name;
    host.appendChild(name);

    return host;
};

/** The table's columns. */
export const DB_OBJECT_COLUMNS: ColumnDefinition[] = [
    {
        title: "Database Object", field: "name", formatter: formatDbObjectCell,
        widthGrow: 2, headerSort: false,
    },
    { title: "Type", field: "type", width: 110, headerSort: false },
    { title: "Details", field: "details", widthGrow: 3, headerSort: false },
];

/**
 * @param selected The keys ticked now.
 * @param keys The keys to tick or untick.
 * @param on Whether to tick them.
 *
 * @returns The keys ticked then, in no particular order.
 */
export const withKeys = (
    selected: readonly string[],
    keys: readonly string[],
    on: boolean,
): string[] => {
    const next = new Set(selected);
    for (const key of keys) {
        if (on) {
            next.add(key);
        } else {
            next.delete(key);
        }
    }

    return [...next];
};

export const DbObjectsTable = (props: {
    ctx: IFieldContext;
    objects: readonly IDbObject[];
}): preact.JSX.Element => {
    const [type, setType] = useState<DbObjectType | "">("");
    const [typed, setTyped] = useState("");
    const host = useRef<HTMLDivElement>(null);
    const table = useRef<Tabulator | undefined>(undefined);
    const built = useRef(false);

    const selected = (props.ctx.get("restObjects") as string[] | undefined)
        ?? [];
    const shown = filterDbObjects(props.objects, type, typed);
    const rows: IDbObjectRow[] = shown.map((object) => {
        const key = dbObjectKey(object);

        return { ...object, key, selected: selected.includes(key) };
    });

    // Tabulator's callbacks are made once; they read what is current.
    const latestRows = useRef(rows);
    latestRows.current = rows;
    const toggle = useRef<(key: string) => void>(() => { /* set below */ });
    toggle.current = (key) => {
        props.ctx.set("restObjects",
            withKeys(selected, [key], !selected.includes(key)));
    };

    useEffect(() => {
        if (!host.current) {
            return;
        }
        const instance = new Tabulator(host.current, {
            data: rows,
            index: "key",
            columns: DB_OBJECT_COLUMNS,
            layout: "fitColumns",
            height: "100%",
            placeholder: props.objects.length === 0
                ? "The database schema has no tables, views, procedures or "
                + "functions."
                : "No database object matches.",
        });
        instance.on("tableBuilt", () => {
            built.current = true;
            // What changed while it was being built.
            void instance.replaceData(latestRows.current);
        });
        instance.on("rowClick", (_event, row) => {
            toggle.current((row.getData() as IDbObjectRow).key);
        });
        table.current = instance;

        return () => {
            built.current = false;
            instance.destroy();
            table.current = undefined;
        };
    // Built once; the rows follow in the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // The rows are replaced only when other ones are on show, and only
    // their boxes are updated when one is ticked: a row rebuilt under
    // the pointer would lose the click that was aimed at it.
    const shownKeys = rows.map((row) => { return row.key; });
    const tickedKeys = rows.filter((row) => { return row.selected; })
        .map((row) => { return row.key; });
    useEffect(() => {
        if (built.current) {
            void table.current?.replaceData(latestRows.current);
        }
    }, [shownKeys.join("\n")]);
    useEffect(() => {
        if (!built.current || table.current === undefined) {
            return;
        }
        for (const data of latestRows.current) {
            const row = table.current.getRow(data.key);
            if (row && (row.getData() as IDbObjectRow).selected
                !== data.selected) {
                void row.update({ selected: data.selected });
                // `selected` is no column's field, so its cell is not
                // drawn again by the update itself. Nor is it redrawn
                // here: the box keeps the focus the keyboard gave it.
                const box = row.getElement()
                    .querySelector<HTMLInputElement>("input[type=checkbox]");
                if (box) {
                    box.checked = data.selected;
                }
            }
        }
    }, [tickedKeys.join("\n")]);

    const busy = props.ctx.busy;

    return (
        <div class="dbObjects">
            <div class="dbObjectsToolbar">
                <button type="button" disabled={busy || rows.length === 0}
                    onClick={() => {
                        props.ctx.set("restObjects",
                            withKeys(selected, shownKeys, true));
                    }}>
                    Select All
                </button>
                <button type="button" disabled={busy || rows.length === 0}
                    onClick={() => {
                        props.ctx.set("restObjects",
                            withKeys(selected, shownKeys, false));
                    }}>
                    Select None
                </button>
                <label class="dbObjectsFilter">
                    <span>Filter</span>
                    <select name="dbObjectType" value={type} disabled={busy}
                        onChange={(event) => {
                            setType((event.target as HTMLSelectElement)
                                .value as DbObjectType | "");
                        }}>
                        {TYPE_CHOICES.map((choice) => {
                            return (
                                <option key={choice.value} value={choice.value}>
                                    {choice.label}
                                </option>
                            );
                        })}
                    </select>
                </label>
                <input
                    type="text"
                    name="dbObjectName"
                    class="dbObjectsSearch"
                    placeholder="Name starts with…"
                    aria-label="Search the names"
                    spellcheck={false}
                    value={typed}
                    disabled={busy}
                    onInput={(event) => {
                        setTyped((event.target as HTMLInputElement).value);
                    }}
                />
                <span class="dbObjectsCount">
                    {`${String(selected.length)} of `
                        + `${String(props.objects.length)} selected`}
                </span>
            </div>
            <div class="dbObjectsTable" ref={host} />
        </div>
    );
};
