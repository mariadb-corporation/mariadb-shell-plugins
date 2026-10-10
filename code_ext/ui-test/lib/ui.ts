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

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
    ActivityBar,
    By,
    CustomTreeItem,
    CustomTreeSection,
    EditorView,
    InputBox,
    Key,
    ModalDialog,
    NotificationType,
    VSBrowser,
    WebDriver,
    WebElement,
    WebView,
    Workbench,
} from "vscode-extension-tester";

/**
 * What the UI tests share: finding rows in the MariaDB views, their
 * context menus, the dialogs' webviews, VS Code's own prompts, and the
 * waiting that every step of a UI needs.
 */

/**
 * How long a step may take that does real work on a server: deploying a
 * sandbox or the REST metadata. Everything else gets {@link TIMEOUT}, so a
 * step that went wrong is noticed at once rather than after a minute.
 */
export const DEPLOY_TIMEOUT = 90_000;

/** How long a step that talks to the server may take. */
export const SERVER_TIMEOUT = 15_000;

/** How long anything else may take to show up. */
export const TIMEOUT = 10_000;

/** The port the run reserved for the sandbox the tests deploy. */
export const SANDBOX_PORT = Number(process.env.UI_TEST_PORT ?? "3399");

/** A directory the tests may write files into. */
export const FILES = process.env.UI_TEST_FILES ?? "/tmp";

/** The sandbox's root password. */
export const SANDBOX_PASSWORD = "UiTest_root_1";

/** The sandbox's row in the Sandboxes view. */
export const SANDBOX_ROW = `localhost:${SANDBOX_PORT}`;

/** The labels on the way to the sandbox's connection. */
export const CONNECTION = ["Sandboxes", `root@127.0.0.1:${SANDBOX_PORT}`];

/**
 * @param lines How many of its last lines.
 *
 * @returns The end of the extension's own log (its "MariaDB" output
 *          channel), from the test instance's newest log folder; what a
 *          failure message needs to say why.
 */
export const extensionLog = (lines = 15): string => {
    const logs = join(process.env.UI_TEST_STORAGE ?? "", "settings", "logs");
    if (!existsSync(logs)) {
        return "";
    }
    const newest = readdirSync(logs).sort().at(-1);
    const host = newest === undefined ? "" : join(logs, newest, "window1",
        "exthost");
    if (!existsSync(host)) {
        return "";
    }
    for (const dir of readdirSync(host).filter((name) => {
        return name.startsWith("output_logging_");
    }).sort().reverse()) {
        const file = readdirSync(join(host, dir)).find((name) => {
            return name.endsWith("MariaDB.log");
        });
        if (file !== undefined) {
            return readFileSync(join(host, dir, file), "utf8").trimEnd()
                .split("\n").slice(-lines).join("\n");
        }
    }

    return "";
};

export const driver = (): WebDriver => {
    return VSBrowser.instance.driver;
};

/**
 * Polls until a condition holds.
 *
 * @param condition Returns a truthy value once it holds; exceptions count
 *        as not yet - the UI may be redrawing under the probe.
 * @param message What was waited for, for the failure.
 * @param timeout How long to wait, in milliseconds.
 *
 * @returns The condition's value.
 */
export const waitFor = async <T>(
    condition: () => Promise<T | undefined | false | null>,
    message: string,
    timeout = TIMEOUT,
): Promise<T> => {
    const end = Date.now() + timeout;
    let last: unknown;
    while (Date.now() < end) {
        try {
            const value = await condition();
            if (value !== undefined && value !== false && value !== null) {
                return value;
            }
        } catch (error) {
            last = error;
        }
        await sleep(250);
    }

    throw new Error(`Timed out waiting for ${message}`
        + (last instanceof Error ? ` (last error: ${last.message})` : ""));
};

export const sleep = async (ms: number): Promise<void> => {
    await new Promise((done) => { setTimeout(done, ms); });
};

/**
 * Saves a screenshot, for looking at a step afterwards.
 *
 * @param name The file name, without extension.
 *
 * @returns Nothing.
 */
export const screenshot = async (name: string): Promise<void> => {
    const dir = process.env.UI_TEST_SCREENSHOTS;
    if (dir === undefined) {
        return;
    }
    mkdirSync(dir, { recursive: true });
    const png = await driver().takeScreenshot();
    const { writeFileSync } = await import("node:fs");
    writeFileSync(join(dir, `${name}.png`), png, "base64");
};

/** The MariaDB activity bar container's views. */
export const VIEW_CONTAINER = "MariaDB";

/**
 * Opens one of the MariaDB views.
 *
 * @param title `Connections` or `Sandboxes`.
 *
 * @returns Its tree.
 */
export const openSection = async (title: string): Promise<CustomTreeSection> => {
    const control = await waitFor(async () => {
        return await new ActivityBar().getViewControl(VIEW_CONTAINER);
    }, "the MariaDB activity bar entry");
    const view = await control.openView();

    return await waitFor(async () => {
        return await view.getContent().getSection(title) as CustomTreeSection;
    }, `the ${title} view`);
};

/** A visible row, read once: its label and how deep it is. */
interface IVisibleRow {
    item: CustomTreeItem;
    label: string;
    level: number;
}

/**
 * Reads the rows the tree shows now. Read afresh every time: VS Code
 * reuses a row's element for another row as the tree redraws, so an
 * element kept across a refresh can stand for something else.
 */
const visibleRows = async (section: CustomTreeSection): Promise<IVisibleRow[]> => {
    const rows: IVisibleRow[] = [];
    for (const item of await section.getVisibleItems()) {
        try {
            rows.push({
                item,
                label: await item.getLabel(),
                level: Number(await item.getAttribute("aria-level")),
            });
        } catch {
            // Redrawn while being read; the next poll sees it.
        }
    }

    return rows;
};

/**
 * Locates a path among the visible rows: each label below the one before,
 * one level deeper.
 *
 * @returns How far it got: the row of each label found, in order.
 */
const locate = (rows: IVisibleRow[], path: string[]): IVisibleRow[] => {
    const found: IVisibleRow[] = [];
    let from = 0;
    let level = 1;
    for (const label of path) {
        let match: IVisibleRow | undefined;
        for (let i = from; i < rows.length; i += 1) {
            if (rows[i].level < level) {
                break;
            }
            if (rows[i].level === level && rows[i].label === label) {
                match = rows[i];
                from = i + 1;
                break;
            }
        }
        if (match === undefined) {
            break;
        }
        found.push(match);
        level += 1;
    }

    return found;
};

/**
 * Finds a row by the labels on the way to it, expanding each one on the
 * way: the tree loads children from the server, so each level is waited
 * for.
 *
 * @param section The tree.
 * @param path The labels, the top-level row's first.
 * @param timeout How long the whole lookup may take.
 *
 * @returns The row.
 */
export const treeItem = async (
    section: CustomTreeSection,
    path: string[],
    timeout = SERVER_TIMEOUT,
): Promise<CustomTreeItem> => {
    let emptyPolls = 0;

    return await waitFor(async () => {
        const found = locate(await visibleRows(section), path);
        if (found.length === path.length) {
            return found.at(-1)!.item;
        }
        // The deepest row found is collapsed, or still loading: open it.
        const last = found.at(-1);
        if (last === undefined) {
            return undefined;
        }
        if (!await last.item.isExpanded()) {
            await last.item.expand();
        } else if ((await last.item.getChildren()).length === 0) {
            // A connection closed while its row was open stays open and
            // empty; opening it again is what connects it.
            emptyPolls += 1;
            if (emptyPolls % 5 === 0) {
                await last.item.collapse();
                await last.item.expand();
            }
        }

        return undefined;
    }, `the row ${path.join(" > ")}`, timeout);
};

/**
 * @param section The tree.
 * @param path The labels on the way to a row.
 *
 * @returns Whether it is there now, without waiting for it.
 */
export const hasTreeItem = async (
    section: CustomTreeSection,
    path: string[],
): Promise<boolean> => {
    try {
        await treeItem(section, path, 2000);

        return true;
    } catch {
        return false;
    }
};

/**
 * Waits for a row to go, after whatever removed it.
 *
 * @param section The tree.
 * @param path The labels on the way to it.
 *
 * @returns Nothing.
 */
export const waitGone = async (
    section: CustomTreeSection,
    path: string[],
): Promise<void> => {
    await waitFor(async () => {
        return !await hasTreeItem(section, path);
    }, `the row ${path.join(" > ")} to go`, SERVER_TIMEOUT);
};

/**
 * @param section The tree.
 * @param path The labels on the way to a row.
 *
 * @returns The labels of its children, once it is expanded and they are
 *          shown.
 */
export const childLabels = async (
    section: CustomTreeSection,
    path: string[],
): Promise<string[]> => {
    const row = await treeItem(section, path);
    if (!await row.isExpanded()) {
        await row.expand();
    }
    await sleep(500);
    const rows = await visibleRows(section);
    const found = locate(rows, path);
    const parent = found.at(-1);
    if (found.length !== path.length || parent === undefined) {
        return [];
    }
    const labels: string[] = [];
    for (const row of rows.slice(rows.indexOf(parent) + 1)) {
        if (row.level <= parent.level) {
            break;
        }
        if (row.level === parent.level + 1) {
            labels.push(row.label);
        }
    }

    return labels;
};

/**
 * Picks an entry of a row's context menu, through its submenus.
 *
 * @param item The row.
 * @param path The menu entries, a submenu's name first.
 *
 * @returns Nothing.
 */
export const contextMenu = async (
    item: CustomTreeItem,
    ...path: string[]
): Promise<void> => {
    const menu = await item.openContextMenu();
    // VS Code ignores a pick that comes right after the menu opened, as a
    // guard against the mouse-up of the right click picking an entry.
    await sleep(200);
    await menu.select(...path);
};

/**
 * @param item A row.
 *
 * @returns The entries of its context menu, the top level only.
 */
export const contextMenuEntries = async (
    item: CustomTreeItem,
): Promise<string[]> => {
    const menu = await item.openContextMenu();
    const entries = await Promise.all((await menu.getItems()).map(
        async (entry) => { return await entry.getLabel(); }));
    await menu.close();

    return entries;
};

/**
 * Presses a row's inline button.
 *
 * @param item The row.
 * @param label The button's tooltip.
 *
 * @returns Nothing.
 */
export const inlineAction = async (
    item: CustomTreeItem,
    label: string,
): Promise<void> => {
    const button = await waitFor(async () => {
        return await item.getActionButton(label);
    }, `the ${label} button`);
    await button.click();
};

// --- dialogs --------------------------------------------------------

/**
 * Waits for a dialog's tab and works in its webview.
 *
 * @param title The dialog's tab title.
 * @param work What to do inside it.
 *
 * @returns What `work` returns.
 */
/** The dialog {@link inDialog} is working in, for {@link submitDialog}. */
let currentDialog: string | undefined;

export const inDialog = async <T>(
    title: string,
    work: (view: WebView) => Promise<T>,
): Promise<T> => {
    currentDialog = title;
    await waitFor(async () => {
        const titles = await new EditorView().getOpenEditorTitles();
        if (titles.includes(title)) {
            return true;
        }
        // The command failed instead of opening it: say why, now.
        await failOnErrorNotification();

        return false;
    }, `the dialog "${title}"`, SERVER_TIMEOUT);
    await new EditorView().openEditor(title);
    const view = new WebView();
    await view.switchToFrame(TIMEOUT);
    try {
        // The frontend renders once the host's load message has arrived.
        await waitFor(async () => {
            return (await view.findWebElements(By.css("h1"))).length > 0;
        }, `the dialog "${title}" to load`);

        return await work(view);
    } finally {
        await view.switchBack();
    }
};

/**
 * Opens a dialog from a row's context menu.
 *
 * A context menu entry is now and then not picked up - the click lands
 * while the tree redraws - so a dialog that does not show up within a few
 * seconds is asked for once more before the test gives up.
 *
 * @param section The tree.
 * @param path The labels on the way to the row.
 * @param menu The menu entries, a submenu's name first.
 * @param title The dialog's tab title.
 *
 * @returns Nothing.
 */
export const openDialog = async (
    section: CustomTreeSection,
    path: string[],
    menu: string[],
    title: string,
): Promise<void> => {
    await menuAction(section, path, menu, async () => {
        return await isDialogOpen(title);
    }, `the dialog "${title}"`);
};

/**
 * Picks a row's context menu entry until what it does shows up.
 *
 * Every save redraws the tree, and VS Code closes a context menu whose
 * tree redraws: a menu opened right after a save can be gone before its
 * entry is clicked. So an action whose effect does not show within a few
 * seconds is asked for again, twice, before the test gives up - with the
 * extension's log in the message.
 *
 * @param section The tree.
 * @param path The labels on the way to the row.
 * @param menu The menu entries, a submenu's name first.
 * @param done Whether the action's effect is there.
 * @param what The effect, for the failure message.
 *
 * @returns Nothing.
 */
export const menuAction = async (
    section: CustomTreeSection,
    path: string[],
    menu: string[],
    done: () => Promise<boolean>,
    what: string,
): Promise<void> => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
        await contextMenu(await treeItem(section, path), ...menu);
        try {
            await waitFor(async () => {
                if (await done()) {
                    return true;
                }
                await failOnErrorNotification();

                return false;
            }, what, 5000);

            return;
        } catch (error) {
            if (String(error).includes("Error notification")) {
                throw error;
            }
            await screenshot(`menu-miss-${menu.at(-1)}-${attempt}`
                .replace(/[^A-Za-z0-9-]+/g, "_"));
        }
    }

    throw new Error(`${what} did not come from ${path.join(" > ")} > `
        + `${menu.join(" > ")}. The extension's log:\n${extensionLog()}`);
};

/**
 * Picks a context menu entry that asks for confirmation, and answers it.
 *
 * @param section The tree.
 * @param path The labels on the way to the row.
 * @param menu The menu entries.
 * @param button The answer.
 *
 * @returns The question asked.
 */
export const menuConfirm = async (
    section: CustomTreeSection,
    path: string[],
    menu: string[],
    button: string,
): Promise<string> => {
    await menuAction(section, path, menu, async () => {
        return await new ModalDialog().isDisplayed().catch(() => {
            return false;
        });
    }, "a confirmation");

    return await answerModal(button);
};

/**
 * @param title A dialog's tab title.
 *
 * @returns Whether it is open.
 */
export const isDialogOpen = async (title: string): Promise<boolean> => {
    return (await new EditorView().getOpenEditorTitles()).includes(title);
};

/**
 * Presses a dialog's OK (or whatever its button says) and waits for the
 * dialog to close, failing at once with the dialog's own message when it
 * shows an error instead - a failed save is reported in the dialog, and
 * waiting out the timeout would only hide it for longer.
 *
 * Called inside {@link inDialog}.
 *
 * @param view The dialog.
 * @param button The button that saves.
 * @param timeout How long the save may take.
 * @param expectProgress What the progress bar is to say while the host
 *        saves; checked, with the buttons disabled, when given.
 *
 * @returns Nothing.
 */
export const submitDialog = async (
    view: WebView,
    button = "OK",
    timeout = SERVER_TIMEOUT,
    expectProgress?: string,
): Promise<void> => {
    const title = currentDialog;
    await clickButton(view, button);
    if (expectProgress !== undefined) {
        // While the host works: a progress bar saying what it does, and
        // nothing that could start it a second time.
        // Work that is done quickly can close the dialog before the bar is
        // seen; a dialog still open has to show it.
        const bar = await waitFor(async () => {
            const bars = await view.findWebElements(
                By.css("[role='progressbar']"));
            if (bars.length > 0) {
                return bars[0];
            }
            await view.switchBack();
            const closed = title !== undefined && !await isDialogOpen(title);
            if (!closed) {
                await view.switchToFrame(2000);
            }

            return closed ? "closed" : undefined;
        }, "the progress bar", 5000);
        if (bar === "closed") {
            return;
        }
        const label = await bar.getAttribute("aria-label");
        if (label !== expectProgress) {
            throw new Error(`The progress bar says "${label}"`);
        }
        for (const text of [button, "Cancel"]) {
            const element = await view.findWebElement(
                By.xpath(`//button[normalize-space()='${text}']`));
            if (await element.isEnabled()) {
                throw new Error(`${text} is enabled while the dialog saves`);
            }
        }
    }
    const end = Date.now() + timeout;
    while (Date.now() < end) {
        await sleep(200);
        // Checked from outside: inside a webview that has gone, WebDriver
        // answers every lookup with nothing rather than an error.
        await view.switchBack();
        if (title === undefined || !await isDialogOpen(title)) {
            return;
        }
        try {
            await view.switchToFrame(2000);
        } catch {
            // Closed between the look at the tabs and the switch.
            if (!await isDialogOpen(title)) {
                return;
            }
            continue;
        }
        const errors = await view.findWebElements(By.css(".message.error"));
        if (errors.length > 0) {
            const text = await errors[0].getText().catch(() => { return ""; });
            throw new Error(`The dialog refused to save: ${text}`);
        }
    }

    throw new Error(`The dialog "${title}" did not close within ${timeout} ms`);
};

/** Waits for a dialog to close itself, after its OK went through. */
export const waitDialogClosed = async (title: string): Promise<void> => {
    await waitFor(async () => {
        return !await isDialogOpen(title);
    }, `the dialog "${title}" to close`, SERVER_TIMEOUT);
};

/**
 * @param caption A field's caption, as the dialogs label their fields.
 *
 * @returns The locator of its input, select or textarea.
 */
export const byCaption = (caption: string): By => {
    return By.xpath(`//label[contains(@class,'field')][span[contains(@class,`
        + `'field-caption') and normalize-space()='${caption}']]`
        + "//*[self::input or self::select or self::textarea]");
};

/**
 * Types into a field found by its caption, replacing what it holds.
 *
 * @param view The dialog.
 * @param caption The field's caption.
 * @param text The new text.
 *
 * @returns Nothing.
 */
export const setFieldByCaption = async (
    view: WebView,
    caption: string,
    text: string,
): Promise<void> => {
    await typeInto(await view.findWebElement(byCaption(caption)), text);
};

/** Replaces what an input holds, firing the events Preact listens to. */
export const typeInto = async (field: WebElement, text: string): Promise<void> => {
    await field.click();
    const selectAll = process.platform === "darwin" ? Key.COMMAND : Key.CONTROL;
    await field.sendKeys(Key.chord(selectAll, "a"), Key.BACK_SPACE);
    if (text !== "") {
        await field.sendKeys(text);
    }
};

const byName = (name: string): By => {
    return By.css(`[name="${name}"]`);
};

/**
 * Types into a field, replacing what it holds.
 *
 * @param view The dialog.
 * @param name The field's name.
 * @param text The new text.
 *
 * @returns Nothing.
 */
export const setField = async (
    view: WebView,
    name: string,
    text: string,
): Promise<void> => {
    // Select all, then type over it: clear() does not fire the input
    // events Preact listens to.
    await typeInto(await view.findWebElement(byName(name)), text);
};

/**
 * @param view The dialog.
 * @param name A field's name.
 *
 * @returns Its value.
 */
export const fieldValue = async (
    view: WebView,
    name: string,
): Promise<string> => {
    return await (await view.findWebElement(byName(name)))
        .getAttribute("value") ?? "";
};

/**
 * Sets a checkbox.
 *
 * @param view The dialog.
 * @param name The checkbox's name.
 * @param on Whether it is to be ticked.
 *
 * @returns Nothing.
 */
export const setChecked = async (
    view: WebView,
    name: string,
    on: boolean,
): Promise<void> => {
    const box = await view.findWebElement(byName(name));
    if (await box.isSelected() !== on) {
        await box.click();
    }
};

/**
 * Picks an option of a select by its text.
 *
 * @param view The dialog.
 * @param name The select's name.
 * @param label The option's text.
 *
 * @returns Nothing.
 */
export const selectOption = async (
    view: WebView,
    name: string,
    label: string,
): Promise<void> => {
    const select = await view.findWebElement(byName(name));
    const option = await select.findElement(
        By.xpath(`.//option[normalize-space()='${label}']`));
    await option.click();
};

/**
 * Clicks a button by its text.
 *
 * @param view The dialog.
 * @param text The button's text.
 *
 * @returns Nothing.
 */
export const clickButton = async (view: WebView, text: string): Promise<void> => {
    const button = await view.findWebElement(
        By.xpath(`//button[normalize-space()='${text}']`));
    await button.click();
};

/**
 * Clicks a button by its accessible name, such as an icon button's.
 *
 * @param view The dialog.
 * @param label The aria-label.
 * @param index Which of the buttons with it, from 0.
 *
 * @returns Nothing.
 */
export const clickLabelled = async (
    view: WebView | WebElement,
    label: string,
    index = 0,
): Promise<void> => {
    // A WebView's own findElements looks under its frame's element in the
    // workbench, which is out of reach once switched into the frame.
    const locator = By.css(`[aria-label="${label}"]`);
    const buttons = await waitFor(async () => {
        const found = view instanceof WebView
            ? await view.findWebElements(locator)
            : await view.findElements(locator);

        return found.length > index ? found : undefined;
    }, `the ${label} button`);
    await buttons[index].click();
};

/**
 * Brings a tab of the dialog up.
 *
 * @param view The dialog.
 * @param name The tab's caption.
 *
 * @returns Nothing.
 */
export const selectTab = async (view: WebView, name: string): Promise<void> => {
    const tab = await view.findWebElement(
        By.xpath(`//button[@role='tab' and normalize-space()='${name}']`));
    await tab.click();
};

/**
 * @param view The dialog.
 *
 * @returns Its tabs' captions.
 */
export const tabNames = async (view: WebView): Promise<string[]> => {
    const tabs = await view.findWebElements(By.css("[role='tab']"));

    return Promise.all(tabs.map(async (tab) => { return await tab.getText(); }));
};

/**
 * @param view The dialog.
 *
 * @returns The error messages it shows.
 */
export const dialogErrors = async (view: WebView): Promise<string[]> => {
    const errors = await view.findWebElements(By.css(".message.error"));

    return Promise.all(errors.map(async (error) => {
        return await error.getText();
    }));
};

/**
 * @param view The dialog.
 *
 * @returns The text of the whole dialog.
 */
export const dialogText = async (view: WebView): Promise<string> => {
    return await (await view.findWebElement(By.css("body"))).getText();
};

// --- VS Code's own prompts -------------------------------------------

/**
 * Answers a modal dialog, such as a delete confirmation.
 *
 * @param button The button to press.
 *
 * @returns The dialog's message.
 */
export const answerModal = async (button: string): Promise<string> => {
    const dialog = await waitFor(async () => {
        const modal = new ModalDialog();

        return await modal.isDisplayed() ? modal : undefined;
    }, "a modal dialog");
    const message = await dialog.getMessage();
    await dialog.pushButton(button);

    return message;
};

/**
 * Picks an entry of the quick pick that is up.
 *
 * @param label The entry.
 *
 * @returns Nothing.
 */
export const pickQuick = async (label: string): Promise<void> => {
    const input = await InputBox.create(TIMEOUT);
    await input.selectQuickPick(label);
};

/**
 * Answers the input box that is up.
 *
 * @param text What to type; empty to accept it as it is.
 *
 * @returns Nothing.
 */
export const answerInput = async (text: string): Promise<void> => {
    const input = await InputBox.create(TIMEOUT);
    await input.setText(text);
    await input.confirm();
};

/**
 * Answers VS Code's simple file dialog with a path.
 *
 * @param path The file or folder.
 *
 * @returns Nothing.
 */
export const answerFileDialog = async (path: string): Promise<void> => {
    const input = await InputBox.create(TIMEOUT);
    await input.setText(path);
    await input.confirm();
};

/**
 * Waits for a notification saying something.
 *
 * @param text A part of its message.
 * @param timeout How long to wait.
 *
 * @returns Its whole message.
 */
export const waitForNotification = async (
    text: string,
    timeout = SERVER_TIMEOUT,
): Promise<string> => {
    return await waitFor(async () => {
        for (const notification of await new Workbench().getNotifications()) {
            const message = await notification.getMessage();
            if (message.includes(text)) {
                await notification.dismiss().catch(() => { /* gone */ });

                return message;
            }
        }

        return undefined;
    }, `a notification saying "${text}"`, timeout);
};

/**
 * Throws when an error notification is up: a command that failed reports
 * that way, and whatever the test is waiting for will not come.
 *
 * @returns Nothing.
 */
export const failOnErrorNotification = async (): Promise<void> => {
    for (const notification of await new Workbench().getNotifications()) {
        if (await notification.getType() === NotificationType.Error) {
            throw new Error(`Error notification: ${await notification.getMessage()}`);
        }
    }
};

/** Dismisses every notification, so the next wait sees only new ones. */
export const clearNotifications = async (): Promise<void> => {
    for (const notification of await new Workbench().getNotifications()) {
        await notification.dismiss().catch(() => { /* gone */ });
    }
};

/**
 * Runs a command by its palette title.
 *
 * @param title The command's title, `MariaDB: ...` included.
 *
 * @returns Nothing.
 */
export const runCommand = async (title: string): Promise<void> => {
    await new Workbench().executeCommand(title);
};

/** @returns The system clipboard's text (macOS). */
export const clipboard = (): string => {
    return execFileSync("pbpaste", { encoding: "utf8" });
};

/** Empties the system clipboard (macOS). */
export const clearClipboard = (): void => {
    execFileSync("pbcopy", { input: "" });
};
