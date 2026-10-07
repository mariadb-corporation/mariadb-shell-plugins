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

/**
 * The options every webview of the extension is created with: scripts on,
 * and nothing loadable from outside the extension's own `dist` folder.
 *
 * @param extensionUri The root of the installed extension.
 *
 * @returns The options, for a panel to extend with its own.
 */
export const webviewOptions = (
    extensionUri: vscode.Uri,
): vscode.WebviewOptions => {
    return {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, "dist")],
    };
};

/**
 * Builds a nonce for one load of a webview.
 *
 * @returns 32 random alphanumeric characters.
 */
const createNonce = (): string => {
    const alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let nonce = "";
    for (let index = 0; index < 32; index += 1) {
        nonce += alphabet[Math.floor(Math.random() * alphabet.length)];
    }

    return nonce;
};

/**
 * Builds the HTML shell a webview of the extension loads - the result
 * view's, the connection editor's and the New Sandbox dialog's, each its
 * own build.
 *
 * Everything is served from the extension's own folder under a strict
 * content security policy, with a per-load nonce on the one script tag, so
 * a view that handles credentials cannot reach the network.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 * @param bundle The view's build: `<bundle>.js` and `<bundle>.css` under
 *               `dist/webview`.
 * @param title The document title.
 *
 * @returns The HTML document.
 */
export const buildWebviewHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
    bundle: string,
    title: string,
): string => {
    const asset = (...parts: string[]): string => {
        return webview.asWebviewUri(
            vscode.Uri.joinPath(extensionUri, ...parts),
        ).toString();
    };

    const script = asset("dist", "webview", `${bundle}.js`);
    const style = asset("dist", "webview", `${bundle}.css`);
    const nonce = createNonce();

    return `<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; ${""
        }style-src ${webview.cspSource} 'unsafe-inline'; ${""
        }img-src ${webview.cspSource} data:; ${""
        }font-src ${webview.cspSource}; ${""
        }script-src 'nonce-${nonce}';" />
    <link rel="stylesheet" href="${style}" />
    <title>${title}</title>
</head>

<body>
    <div id="root"></div>
    <script type="module" nonce="${nonce}" src="${script}"></script>
</body>

</html>`;
};
