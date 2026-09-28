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

import * as vscode from "vscode";

import { isConnectionColor } from "../connections/connectionDetails.js";
import type { ConnectionColor, ConnectionKind } from "../mcp/types.js";

/**
 * How a connection's color is shown in the Connections view.
 *
 * A tree item cannot tint an icon from a file, and the connection icons are
 * files - the MariaDB and MySQL logos. What VS Code does color is a file
 * decoration: an item with a `resourceUri` gets its label drawn in the
 * decoration's color, and its badge beside it. So a colored connection's
 * row carries a URI of this scheme naming its color, and the provider below
 * answers for it. The colors are the theme's own chart colors, which every
 * theme defines to be told apart on its background.
 */

/** The scheme of the URIs that carry a connection's color. */
export const CONNECTION_COLOR_SCHEME = "mariadb-connection";

/**
 * The theme color a connection color is drawn in.
 *
 * @param color The connection color.
 *
 * @returns The theme's chart color of that name.
 */
export const themeColorOf = (color: ConnectionColor): vscode.ThemeColor => {
    return new vscode.ThemeColor(`charts.${color}`);
};

/**
 * The URI a colored connection's row carries.
 *
 * @param color Its color.
 * @param kind The list it is in.
 * @param uri The connection URI - with the kind, what keeps two rows' URIs
 *            apart, though only the color is ever read back.
 *
 * @returns The URI.
 */
export const colorUriOf = (
    color: ConnectionColor,
    kind: ConnectionKind,
    uri: string,
): vscode.Uri => {
    return vscode.Uri.from({
        scheme: CONNECTION_COLOR_SCHEME,
        path: `/${color}/${kind}`,
        query: uri,
    });
};

/** Colors the rows that carry a `colorUriOf` URI. */
export class ConnectionColorDecorations
    implements vscode.FileDecorationProvider {

    /**
     * @param uri A row's resource URI.
     *
     * @returns The decoration for a colored connection, otherwise nothing.
     */
    public provideFileDecoration(
        uri: vscode.Uri,
    ): vscode.FileDecoration | undefined {
        if (uri.scheme !== CONNECTION_COLOR_SCHEME) {
            return undefined;
        }

        const color = uri.path.split("/")[1];
        if (!isConnectionColor(color)) {
            return undefined;
        }

        return new vscode.FileDecoration("●", undefined, themeColorOf(color));
    }
}
