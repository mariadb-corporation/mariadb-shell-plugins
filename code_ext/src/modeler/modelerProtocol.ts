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
 * What the model viewer webview and the extension say to each other.
 * Shared by both sides, so free of `vscode`.
 */

import type { ISchemaModel } from "./modelFormat.js";
import type { IPageSettings } from "./pageSettings.js";

/** Messages from the viewer to the extension. */
export type ModelerWebviewMessage =
    /** The page has loaded and can take the model. */
    | { type: "ready" }
    /** The diagram shown changed, by the viewer's own list. */
    | { type: "diagramShown"; name: string }
    /** Something to put in the extension's log. */
    | { type: "log"; message: string };

/** Messages from the extension to the viewer. */
export type ModelerHostMessage =
    /** The model to show, where it came from, and the diagram to open. */
    | {
        type: "load";
        model: ISchemaModel;
        source: string;
        pageSettings: IPageSettings;
        /** The diagram to show first: the one opened in the tree. */
        diagram?: string;
    }
    /** Show another of the model's diagrams: opened again in the tree. */
    | { type: "showDiagram"; name: string }
    /** The model could not be read. */
    | { type: "error"; message: string };
