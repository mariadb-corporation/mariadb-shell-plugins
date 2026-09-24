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
 * A stand-in for the `vscode` module. The extension host provides the real
 * one, which cannot be imported from a plain Node process, so the vitest
 * config aliases `vscode` onto this file.
 *
 * Only what the extension actually touches is modelled here, and every call
 * is recorded so tests can assert on it.
 */

/** The editor version `vscode.version` reports. */
export const version = "1.139.0";

export enum ProgressLocation {
    SourceControl = 1,
    Window = 10,
    Notification = 15,
}

export enum TreeItemCollapsibleState {
    None = 0,
    Collapsed = 1,
    Expanded = 2,
}

export enum StatusBarAlignment {
    Left = 1,
    Right = 2,
}

export enum ViewColumn {
    Active = -1,
    Beside = -2,
    One = 1,
    Two = 2,
}

export enum ConfigurationTarget {
    Global = 1,
    Workspace = 2,
    WorkspaceFolder = 3,
}

export interface Progress<T> {
    report(value: T): void;
}

export interface ProgressOptions {
    location: ProgressLocation | { viewId: string };
    title?: string;
    cancellable?: boolean;
}

export interface CancellationToken {
    isCancellationRequested: boolean;
    onCancellationRequested(listener: () => void): Disposable;
}

export interface Disposable {
    dispose(): unknown;
}

/** A stand-in for `vscode.Uri`, keeping only the path. */
export class Uri {
    private constructor(
        public readonly scheme: string,
        public readonly path: string,
    ) { }

    public static file(path: string): Uri {
        return new Uri("file", path);
    }

    public static parse(value: string): Uri {
        const match = /^(\w+):\/\/(.*)$/.exec(value);

        return match
            ? new Uri(match[1], match[2])
            : new Uri("file", value);
    }

    public static joinPath(base: Uri, ...parts: string[]): Uri {
        const path = [base.path.replace(/\/$/, ""), ...parts].join("/");

        return new Uri(base.scheme, path);
    }

    public get fsPath(): string {
        return this.path;
    }

    public toString(): string {
        return `${this.scheme}://${this.path}`;
    }
}

export class ThemeIcon {
    public constructor(public readonly id: string) { }
}

export class TreeItem {
    public iconPath?: unknown;
    public contextValue?: string;
    public description?: string | boolean;
    public tooltip?: string;
    public command?: unknown;

    public constructor(
        public label: string,
        public collapsibleState: TreeItemCollapsibleState =
        TreeItemCollapsibleState.None,
    ) { }
}

export class EventEmitter<T> {
    readonly #listeners = new Set<(value: T) => void>();

    public readonly event = (listener: (value: T) => void): Disposable => {
        this.#listeners.add(listener);

        return {
            dispose: () => {
                this.#listeners.delete(listener);
            },
        };
    };

    public fire(value: T): void {
        for (const listener of this.#listeners) {
            listener(value);
        }
    }

    public dispose(): void {
        this.#listeners.clear();
    }
}

export interface WithProgressCall {
    options: ProgressOptions;
    reported: string[];
    /** Presses the notification's Cancel button. */
    cancel: () => void;
}

/** Every `withProgress` call made since the last `resetVscodeMock()`. */
export const withProgressCalls: WithProgressCall[] = [];
export const informationMessages: string[] = [];
export const warningMessages: string[] = [];

/** What the next `showWarningMessage` answers with; see the mock below. */
let warningMessageAnswer: string | undefined;

/**
 * Makes the next warning dialog answer with the given button.
 *
 * @param answer The button to press, or undefined to dismiss it.
 *
 * @returns Nothing.
 */
export const setWarningMessageAnswer = (answer: string | undefined): void => {
    warningMessageAnswer = answer;
};
export const errorMessages: string[] = [];
export const outputChannels: MockOutputChannel[] = [];
export const statusBarItems: MockStatusBarItem[] = [];
export const treeViews: Array<{
    id: string;
    options: unknown;
    /** Fires the view's `onDidExpandElement`, as a user expanding a row. */
    expand: (element: unknown) => void;
}> = [];
export const webviewPanels: MockWebviewPanel[] = [];
export const webviewViewProviders = new Map<string, {
    provider: { resolveWebviewView(view: MockWebviewView): void };
    options: unknown;
}>();
export const webviewViews: MockWebviewView[] = [];
/** The documents `workspace.openTextDocument` created. */
export const openedDocuments: MockTextDocument[] = [];
/** The documents `window.showTextDocument` was asked to show. */
export const shownDocuments: MockTextDocument[] = [];
/** The editors `window.showTextDocument` handed back. */
export const shownEditors: Array<{
    document: MockTextDocument;
    selection: Range;
}> = [];
/** The decoration types that were created. */
export const decorationTypes: MockDecorationType[] = [];
/** What `window.visibleTextEditors` reports. */
export let visibleTextEditors: MockTextEditor[] = [];

/**
 * Sets the editors the mock reports as visible.
 *
 * @param editors The visible editors.
 *
 * @returns Nothing.
 */
export const setVisibleTextEditors = (
    editors: MockTextEditor[],
): void => {
    visibleTextEditors = editors;
    window.visibleTextEditors = editors;
};
export const registeredCommands = new Map<
    string,
    (...args: unknown[]) => unknown
>();
/** The context keys set through the built-in `setContext` command. */
export const contextKeys = new Map<string, unknown>();
/** Everything shown with `window.setStatusBarMessage`. */
export const statusBarMessages: string[] = [];

/** The next answer `showQuickPick` should give, as a label. */
export const quickPickAnswers: string[] = [];
/** Every set of items `showQuickPick` was offered. */
export const quickPickCalls: Array<Array<{ label: string }>> = [];

/** The configuration `workspace.getConfiguration` serves. */
export const configuration = new Map<string, unknown>();
export const configurationUpdates: Array<{
    key: string;
    value: unknown;
    target: ConfigurationTarget;
}> = [];

/** Set to a non-empty array to make the mock look like a workspace. */
export let workspaceFolders: Array<{ name: string }> | undefined;

/**
 * Sets what `workspace.workspaceFolders` reports.
 *
 * @param folders The folders to report, or undefined for none.
 *
 * @returns Nothing.
 */
export const setWorkspaceFolders = (
    folders: Array<{ name: string }> | undefined,
): void => {
    workspaceFolders = folders;
    workspace.workspaceFolders = folders;
};

export class MockOutputChannel {
    public readonly lines: string[] = [];
    public shown = false;
    public disposed = false;

    public constructor(public readonly name: string) { }

    public appendLine(line: string): void {
        this.lines.push(line);
    }

    public show(_preserveFocus?: boolean): void {
        this.shown = true;
    }

    public dispose(): void {
        this.disposed = true;
    }
}

export class MockStatusBarItem {
    public text = "";
    public tooltip?: string;
    public command?: string;
    public visible = false;
    public disposed = false;

    public constructor(
        public readonly alignment: StatusBarAlignment,
        public readonly priority?: number,
    ) { }

    public show(): void {
        this.visible = true;
    }

    public hide(): void {
        this.visible = false;
    }

    public dispose(): void {
        this.disposed = true;
    }
}

export class MockWebview {
    public html = "";
    public readonly posted: unknown[] = [];
    public readonly cspSource = "vscode-webview://mock";
    #handler?: (message: unknown) => void;

    public asWebviewUri(uri: Uri): Uri {
        return uri;
    }

    public postMessage(message: unknown): Promise<boolean> {
        this.posted.push(message);

        return Promise.resolve(true);
    }

    public onDidReceiveMessage(
        handler: (message: unknown) => void,
    ): Disposable {
        this.#handler = handler;

        return { dispose: () => { this.#handler = undefined; } };
    }

    /** Drives a message in, as the frontend would. */
    public receive(message: unknown): void {
        this.#handler?.(message);
    }
}

/** A stand-in for a Position. */
export class Position {
    public constructor(
        public readonly line: number,
        public readonly character: number,
    ) { }
}

/** A stand-in for a Range. */
export class Range {
    public readonly start: Position;
    public readonly end: Position;

    // Both of the real Range's overloads, since callers use either.
    public constructor(start: Position, end: Position);
    public constructor(
        startLine: number,
        startCharacter: number,
        endLine: number,
        endCharacter: number,
    );
    public constructor(
        startOrLine: Position | number,
        endOrCharacter: Position | number,
        endLine?: number,
        endCharacter?: number,
    ) {
        if (typeof startOrLine === "number") {
            this.start = new Position(startOrLine,
                endOrCharacter as number);
            this.end = new Position(endLine as number,
                endCharacter as number);
        } else {
            this.start = startOrLine;
            this.end = endOrCharacter as Position;
        }
    }

    public get isEmpty(): boolean {
        return this.start.line === this.end.line
            && this.start.character === this.end.character;
    }
}

/** A stand-in for a Selection: a Range that knows which end moves. */
export class Selection extends Range {
    public readonly anchor: Position;
    public readonly active: Position;

    public constructor(anchor: Position, active: Position) {
        super(anchor, active);
        this.anchor = anchor;
        this.active = active;
    }
}

/** A stand-in for a TextDocument. */
export class MockTextDocument {
    public version = 1;

    public constructor(
        public readonly uri: Uri,
        public readonly languageId: string,
        private text: string,
    ) { }

    public getText(range?: unknown): string {
        return range === undefined ? this.text : this.text;
    }

    public setText(value: string): void {
        this.text = value;
        this.version += 1;
    }

    public positionAt(offset: number): Position {
        const before = this.text.slice(0, offset);
        const line = before.split("\n").length - 1;
        const lineStart = before.lastIndexOf("\n") + 1;

        return new Position(line, offset - lineStart);
    }

    public offsetAt(position: Position): number {
        const lines = this.text.split("\n");
        let offset = 0;
        for (let index = 0; index < position.line; index += 1) {
            offset += (lines[index]?.length ?? 0) + 1;
        }

        return offset + position.character;
    }
}

/** A stand-in for a TextEditor, recording the decorations applied. */
export class MockTextEditor {
    /** Every setDecorations call, as the ranges it was given. */
    public readonly decorationCalls: Array<{
        type: MockDecorationType;
        ranges: Range[];
    }> = [];

    public selection: { isEmpty: boolean } = { isEmpty: true };

    public constructor(public readonly document: MockTextDocument) { }

    public setDecorations(
        type: MockDecorationType,
        ranges: Range[],
    ): void {
        this.decorationCalls.push({ type, ranges: [...ranges] });
    }

    /**
     * @returns The lines of the most recent decoration call.
     */
    public get decoratedLines(): number[] {
        const last = this.decorationCalls.at(-1);

        return (last?.ranges ?? []).map((range) => {
            return range.start.line;
        });
    }
}

export class MockDecorationType {
    public disposed = false;

    public constructor(public readonly options: unknown) { }

    public dispose(): void {
        this.disposed = true;
    }
}

/** A stand-in for a WebviewView, as the bottom panel hosts one. */
export class MockWebviewView {
    public readonly webview = new MockWebview();
    /** Shown beside the view's title in the panel header. */
    public description?: string;
    public visible = false;
    public shown = 0;
    public preserveFocus = false;
    #onDispose?: () => void;

    public constructor(public readonly viewType: string) { }

    public show(preserveFocus?: boolean): void {
        this.shown += 1;
        this.visible = true;
        this.preserveFocus = Boolean(preserveFocus);
    }

    public onDidDispose(handler: () => void): Disposable {
        this.#onDispose = handler;

        return { dispose: () => { this.#onDispose = undefined; } };
    }

    public dispose(): void {
        this.visible = false;
        this.#onDispose?.();
    }
}

export class MockWebviewPanel {
    public readonly webview = new MockWebview();
    public iconPath?: unknown;
    public disposed = false;
    public revealed = 0;
    #onDispose?: () => void;

    public constructor(
        public readonly viewType: string,
        // Not readonly: a panel reused for another subject retitles itself.
        public title: string,
        public viewColumn: ViewColumn | undefined,
        public readonly options: unknown,
    ) { }

    public reveal(column?: ViewColumn): void {
        this.revealed += 1;
        if (column !== undefined) {
            this.viewColumn = column;
        }
    }

    public onDidDispose(handler: () => void): Disposable {
        this.#onDispose = handler;

        return { dispose: () => { this.#onDispose = undefined; } };
    }

    public dispose(): void {
        this.disposed = true;
        this.#onDispose?.();
    }
}

/** The editor `window.activeTextEditor` reports. */
export let activeTextEditor: unknown;

/**
 * Sets the active editor the mock reports.
 *
 * @param editor The editor, or undefined for none.
 *
 * @returns Nothing.
 */
export const setActiveTextEditor = (editor: unknown): void => {
    activeTextEditor = editor;
    window.activeTextEditor = editor;
};

const activeEditorListeners = new Set<(editor: unknown) => void>();
const visibleEditorListeners =
    new Set<(editors: MockTextEditor[]) => void>();
const changeDocumentListeners = new Set<(event: unknown) => void>();
const closeDocumentListeners = new Set<(document: unknown) => void>();
const configurationListeners = new Set<(event: unknown) => void>();

/**
 * Fires the configuration change event.
 *
 * @param section The section that changed.
 *
 * @returns Nothing.
 */
export const fireConfigurationChange = (section: string): void => {
    for (const listener of configurationListeners) {
        listener({
            affectsConfiguration: (candidate: string) => {
                return section.startsWith(candidate);
            },
        });
    }
};

/**
 * Fires the visible editors change event.
 *
 * @param editors The editors that are now visible.
 *
 * @returns Nothing.
 */
export const fireVisibleEditorsChange = (
    editors: MockTextEditor[],
): void => {
    setVisibleTextEditors(editors);
    for (const listener of visibleEditorListeners) {
        listener(editors);
    }
};

/**
 * Fires the document change event.
 *
 * @param document The document that changed.
 *
 * @returns Nothing.
 */
export const fireDocumentChange = (document: MockTextDocument): void => {
    for (const listener of changeDocumentListeners) {
        listener({ document });
    }
};

/**
 * Fires the document close event.
 *
 * @param document The document that was closed.
 *
 * @returns Nothing.
 */
export const fireDocumentClose = (document: MockTextDocument): void => {
    for (const listener of closeDocumentListeners) {
        listener(document);
    }
};

/**
 * Fires the active editor change event.
 *
 * @param editor The editor that became active.
 *
 * @returns Nothing.
 */
export const fireActiveEditorChange = (editor: unknown): void => {
    setActiveTextEditor(editor);
    for (const listener of activeEditorListeners) {
        listener(editor);
    }
};

export const window = {
    activeTextEditor: undefined as unknown,
    visibleTextEditors: [] as MockTextEditor[],

    createTextEditorDecorationType: (
        options: unknown,
    ): MockDecorationType => {
        const type = new MockDecorationType(options);
        decorationTypes.push(type);

        return type;
    },

    onDidChangeVisibleTextEditors: (
        listener: (editors: MockTextEditor[]) => void,
    ): Disposable => {
        visibleEditorListeners.add(listener);

        return {
            dispose: () => {
                visibleEditorListeners.delete(listener);
            },
        };
    },

    createOutputChannel: (name: string): MockOutputChannel => {
        const channel = new MockOutputChannel(name);
        outputChannels.push(channel);

        return channel;
    },

    createStatusBarItem: (
        alignment: StatusBarAlignment,
        priority?: number,
    ): MockStatusBarItem => {
        const item = new MockStatusBarItem(alignment, priority);
        statusBarItems.push(item);

        return item;
    },

    createTreeView: (id: string, options: unknown) => {
        const expanded = new EventEmitter<{ element: unknown }>();
        treeViews.push({
            id,
            options,
            expand: (element: unknown) => {
                expanded.fire({ element });
            },
        });

        return {
            onDidExpandElement: expanded.event,
            dispose: () => {
                expanded.dispose();
            },
        };
    },

    registerWebviewViewProvider: (
        viewType: string,
        provider: { resolveWebviewView(view: MockWebviewView): void },
        options?: unknown,
    ): Disposable => {
        webviewViewProviders.set(viewType, { provider, options });

        return {
            dispose: () => {
                webviewViewProviders.delete(viewType);
            },
        };
    },

    createWebviewPanel: (
        viewType: string,
        title: string,
        showOptions: { viewColumn: ViewColumn } | ViewColumn,
        options: unknown,
    ): MockWebviewPanel => {
        const column = typeof showOptions === "object"
            ? showOptions.viewColumn
            : showOptions;
        const panel = new MockWebviewPanel(viewType, title, column, options);
        webviewPanels.push(panel);

        return panel;
    },

    showInformationMessage: (message: string): Promise<undefined> => {
        informationMessages.push(message);

        return Promise.resolve(undefined);
    },

    showWarningMessage: (
        message: string,
        ...rest: unknown[]
    ): Promise<string | undefined> => {
        warningMessages.push(message);

        // A modal warning is a question with buttons, and the caller branches
        // on which was pressed. Tests set the answer with
        // `setWarningMessageAnswer`; the default is a dismissal, which keeps
        // a destructive action from running in a test that never opted in.
        const answer = warningMessageAnswer;
        warningMessageAnswer = undefined;
        void rest;

        return Promise.resolve(answer);
    },

    showErrorMessage: (message: string): Promise<undefined> => {
        errorMessages.push(message);

        return Promise.resolve(undefined);
    },

    setStatusBarMessage: (
        message: string,
        _hideAfterTimeout?: number,
    ): Disposable => {
        statusBarMessages.push(message);

        return { dispose: () => { /* nothing to undo */ } };
    },

    showQuickPick: <T extends { label: string }>(
        items: T[],
    ): Promise<T | undefined> => {
        quickPickCalls.push(items);
        const wanted = quickPickAnswers.shift();

        return Promise.resolve(items.find((item) => {
            return item.label === wanted;
        }));
    },

    showTextDocument: (
        document: MockTextDocument,
        options?: { selection?: Range },
    ): Promise<{ document: MockTextDocument; selection: Range }> => {
        shownDocuments.push(document);
        const selection = options?.selection
            ?? new Range(0, 0, 0, 0);
        const editor = { document, selection };
        shownEditors.push(editor);
        setActiveTextEditor(editor);

        return Promise.resolve(editor);
    },

    onDidChangeActiveTextEditor: (
        listener: (editor: unknown) => void,
    ): Disposable => {
        activeEditorListeners.add(listener);

        return {
            dispose: () => {
                activeEditorListeners.delete(listener);
            },
        };
    },

    withProgress: async <T>(
        options: ProgressOptions,
        task: (
            progress: Progress<{ message?: string; increment?: number }>,
            token: CancellationToken,
        ) => Thenable<T>,
    ): Promise<T> => {
        const listeners = new Set<() => void>();
        const token: CancellationToken = {
            isCancellationRequested: false,
            onCancellationRequested: (listener) => {
                listeners.add(listener);

                return { dispose: () => { listeners.delete(listener); } };
            },
        };
        const call: WithProgressCall = {
            options,
            reported: [],
            cancel: () => {
                token.isCancellationRequested = true;
                for (const listener of [...listeners]) {
                    listener();
                }
            },
        };
        withProgressCalls.push(call);

        return await task(
            {
                report: (value) => {
                    if (value.message !== undefined) {
                        call.reported.push(value.message);
                    }
                },
            },
            token,
        );
    },
};

export const workspace = {
    workspaceFolders: undefined as Array<{ name: string }> | undefined,

    openTextDocument: (
        target: Uri | { language?: string; content?: string },
    ): Promise<MockTextDocument> => {
        const document = target instanceof Uri
            ? new MockTextDocument(target, "sql", "")
            : new MockTextDocument(
                Uri.parse(
                    `untitled://Untitled-${openedDocuments.length + 1}`),
                target.language ?? "plaintext",
                target.content ?? "",
            );
        openedDocuments.push(document);

        return Promise.resolve(document);
    },

    getConfiguration: (section: string) => {
        return {
            get: <T>(key: string): T | undefined => {
                return configuration.get(`${section}.${key}`) as T | undefined;
            },

            update: (
                key: string,
                value: unknown,
                target: ConfigurationTarget,
            ): Promise<void> => {
                const full = `${section}.${key}`;
                configurationUpdates.push({ key: full, value, target });
                if (value === undefined) {
                    configuration.delete(full);
                } else {
                    configuration.set(full, value);
                }

                return Promise.resolve();
            },
        };
    },

    onDidCloseTextDocument: (
        listener: (document: unknown) => void,
    ): Disposable => {
        closeDocumentListeners.add(listener);

        return {
            dispose: () => {
                closeDocumentListeners.delete(listener);
            },
        };
    },

    onDidChangeTextDocument: (
        listener: (event: unknown) => void,
    ): Disposable => {
        changeDocumentListeners.add(listener);

        return {
            dispose: () => {
                changeDocumentListeners.delete(listener);
            },
        };
    },

    onDidChangeConfiguration: (
        listener: (event: unknown) => void,
    ): Disposable => {
        configurationListeners.add(listener);

        return {
            dispose: () => {
                configurationListeners.delete(listener);
            },
        };
    },
};

export const commands = {
    registerCommand: (
        command: string,
        callback: (...args: unknown[]) => unknown,
    ): Disposable => {
        registeredCommands.set(command, callback);

        return {
            dispose: () => {
                registeredCommands.delete(command);
            },
        };
    },

    executeCommand: async (
        command: string,
        ...args: unknown[]
    ): Promise<unknown> => {
        // A built-in, not something an extension registers.
        if (command === "setContext") {
            contextKeys.set(args[0] as string, args[1]);

            return undefined;
        }

        // VS Code registers a `<viewId>.focus` command for every view it
        // knows about, which is how a webview view gets resolved.
        const focus = /^(.+)\.focus$/.exec(command);
        if (focus && webviewViewProviders.has(focus[1])) {
            resolveWebviewView(focus[1]);

            return undefined;
        }

        const callback = registeredCommands.get(command);
        if (!callback) {
            throw new Error(`Command not registered: ${command}`);
        }

        return await callback(...args);
    },
};

export const env = {
    clipboard: {
        text: "",
        writeText: (value: string): Promise<void> => {
            env.clipboard.text = value;

            return Promise.resolve();
        },
        readText: (): Promise<string> => {
            return Promise.resolve(env.clipboard.text);
        },
    },
};

/**
 * Hands a view to a registered provider, the way VS Code does when the
 * view first becomes visible.
 *
 * @param viewType The view to resolve.
 *
 * @returns The view that was created.
 */
export const resolveWebviewView = (viewType: string): MockWebviewView => {
    const entry = webviewViewProviders.get(viewType);
    if (!entry) {
        throw new Error(`No webview view provider for ${viewType}`);
    }

    const view = new MockWebviewView(viewType);
    webviewViews.push(view);
    entry.provider.resolveWebviewView(view);

    return view;
};

/**
 * Clears everything the mock recorded. Call it from `beforeEach`.
 *
 * @returns Nothing.
 */
export const resetVscodeMock = (): void => {
    withProgressCalls.length = 0;
    informationMessages.length = 0;
    warningMessages.length = 0;
    warningMessageAnswer = undefined;
    errorMessages.length = 0;
    outputChannels.length = 0;
    statusBarItems.length = 0;
    treeViews.length = 0;
    // Disposed, not just forgotten: a panel is owned by whatever created it,
    // and code that keeps one - the connection editor holds a single panel
    // and reuses it - only lets go when told it closed. Dropping the array
    // alone would leave the next test reusing a panel it cannot see.
    for (const panel of webviewPanels) {
        panel.dispose();
    }
    webviewPanels.length = 0;
    webviewViewProviders.clear();
    webviewViews.length = 0;
    openedDocuments.length = 0;
    shownDocuments.length = 0;
    shownEditors.length = 0;
    decorationTypes.length = 0;
    visibleEditorListeners.clear();
    changeDocumentListeners.clear();
    setVisibleTextEditors([]);
    quickPickAnswers.length = 0;
    quickPickCalls.length = 0;
    configurationUpdates.length = 0;
    registeredCommands.clear();
    contextKeys.clear();
    statusBarMessages.length = 0;
    configuration.clear();
    activeEditorListeners.clear();
    closeDocumentListeners.clear();
    configurationListeners.clear();
    setActiveTextEditor(undefined);
    setWorkspaceFolders(undefined);
    env.clipboard.text = "";
};
