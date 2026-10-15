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

import type * as THREE from "three";

import type { IRect } from "../../../src/modeler/pageSettings.js";
import type { IIsoAngles, IPoint } from "./geometry.js";
import type { IDiagramTheme } from "./theme.js";
import type { IHistoryEntry, UndoStack } from "./undoStack.js";

/**
 * The contract between the canvas and what it shows.
 *
 * The canvas (`Canvas.ts`) is the base: the camera and its 2D and 3D
 * views, zoom and pan, the paper and its grid, rendering on demand, the
 * keyboard, undo and redo, selection, moving and resizing with handles,
 * the menu buttons and context menus, and editing text in place. It knows
 * nothing of what it shows.
 *
 * A figure set (`IFigureSet`) is what it shows: it draws its figures into
 * the canvas's two groups and answers the canvas's questions about them -
 * which item is under the pointer, what moves along when one is moved,
 * what its menu offers. The EER diagram's tables, layers and connections
 * are one figure set (`modeler/EerFigureSet.ts`); others can follow.
 */

/**
 * The two planes items lie on: the page itself, and the plane the figures
 * rise to in the 3D view. Both are the same in the 2D view.
 */
export type ItemPlane = "base" | "lifted";

/** A resize handle: where it sits, as fractions of the item's size. */
export interface IHandle {
    fx: 0 | 0.5 | 1;
    fy: 0 | 0.5 | 1;
    cursor: string;
}

/** Something on the canvas that can be selected, moved and resized. */
export interface ICanvasItem {
    readonly id: string;
    readonly plane: ItemPlane;
    /** @returns Where the item is on the page, now. */
    rect(): IRect;
    readonly resizable: boolean;
    readonly minWidth: number;
    readonly minHeight: number;
    /**
     * How far the item stands above its plane in the 3D view, in page
     * units (it scales with the lift, so the 2D view stays flat). Left
     * out, none.
     */
    elevation?(): number;
    /** Whether Option + drag raises and lowers it (see `beginElevate`). */
    readonly elevatable?: boolean;
    /**
     * The band along the item's top its menu button sits in, at the right:
     * from `top` below the item's top, `height` high, the button drawn in
     * `color`. An item without one has no button (but a context menu).
     */
    readonly menuBand?: { top: number; height: number; color: string };
}

/** What the pointer is over, for a figure set to find its item. */
export interface IProbe {
    /** A raycaster set from the camera through the pointer. */
    readonly raycaster: THREE.Raycaster;
    /**
     * @param plane A plane.
     * @param elevation How far above the plane, in page units (scaled by
     *                  the lift as the canvas draws it).
     *
     * @returns The page point of that plane under the pointer, or nothing
     *          where the ray misses it.
     */
    pointOn(plane: ItemPlane, elevation?: number): IPoint | undefined;
}

/** A move under way, from the press to the release. */
export interface IMoveSession {
    /**
     * Moves the items by a distance on the page, since the last call.
     *
     * @param dx Across.
     * @param dy Down.
     */
    moveBy(dx: number, dy: number): void;
    /**
     * Ends the move.
     *
     * @returns What was done, for the history: "Move service".
     */
    finish(): string;
}

/** A resize under way, from the press to the release. */
export interface IResizeSession {
    /**
     * Sizes the item to a rectangle the canvas worked out from the handle.
     *
     * @param rect The item's new rectangle on the page.
     * @param handle The handle dragged.
     */
    resizeTo(rect: IRect, handle: IHandle): void;
    /**
     * Ends the resize.
     *
     * @returns What was done, for the history.
     */
    finish(): string;
}

/** A raise or lower under way (Option + drag), from press to release. */
export interface IElevateSession {
    /**
     * Raises the item by a distance, since the last call; negative
     * lowers it. The set keeps it in its range.
     *
     * @param dz Up, in page units.
     */
    elevateBy(dz: number): void;
    /**
     * Ends it.
     *
     * @returns What was done, for the history.
     */
    finish(): string;
}

/** An entry of an item's menu; a separator where it has no caption. */
export interface IMenuEntry {
    id: string;
    caption?: string;
    /** For an entry that is switched on or off. */
    checked?: boolean;
    disabled?: boolean;
}

/** Text to edit in place, as a figure set asks for it. */
export interface IEditSpec {
    text: string;
    /** What the input is called, for its accessible name. */
    label: string;
    bold: boolean;
    /** The text's size, in page units. */
    fontSize: number;
    readonly plane: ItemPlane;
    /** @returns How far above its plane the text stands (see items). */
    elevation?(): number;
    /**
     * @returns Where the text is on the page, now - it follows its item -
     *          or nothing once the item is gone.
     */
    area(): IRect | undefined;
    /**
     * Makes the edit.
     *
     * @param text The text typed.
     */
    commit(text: string): void;
}

/** What the canvas gives the figure set it shows. */
export interface ICanvasHost {
    /** The group on the page's plane. */
    readonly base: THREE.Group;
    /** The group the figures rise in, in the 3D view. */
    readonly lifted: THREE.Group;
    readonly theme: IDiagramTheme;
    /** The history; a figure set records what the canvas does not. */
    readonly history: UndoStack;
    /** The anisotropy textures can use. */
    readonly maxAnisotropy: number;
    /** @returns CSS pixels per page unit, at the zoom. */
    scale(): number;
    /**
     * @returns The canvas's size in CSS pixels: what lines of a width in
     *          pixels are drawn against.
     */
    viewport(): { width: number; height: number };
    /**
     * @returns How far the figures have risen, 0 in the 2D view, 1 in the
     *          3D one, between while the view turns.
     */
    lift(): number;
    /** Asks for a frame. */
    invalidate(): void;
    /**
     * Opens an input over text, laid onto the page.
     *
     * @param spec The text.
     */
    startEdit(spec: IEditSpec): void;
    /**
     * Selects items, and only those.
     *
     * @param ids The items.
     */
    select(ids: readonly string[]): void;
    /**
     * Makes a change and records it, as one action that puts back
     * everything the figure set's snapshot holds.
     *
     * @param label What is done.
     * @param change The change.
     */
    change(label: string, change: () => void): void;
}

/** What the canvas shows, and how it answers for it. */
export interface IFigureSet {
    /**
     * Draws the figures into the host's groups.
     *
     * @param host The canvas.
     *
     * @returns Once drawn.
     */
    mount(host: ICanvasHost): Promise<void>;
    /** Removes what it drew. */
    unmount(): void;
    /** @returns The size of the content on the page: the paper fits it. */
    extent(): { width: number; height: number };
    /**
     * @param id An item's id.
     *
     * @returns The item, or nothing.
     */
    item(id: string): ICanvasItem | undefined;
    /**
     * @param probe What the pointer is over.
     *
     * @returns The item in front under the pointer, or nothing.
     */
    itemAt(probe: IProbe): ICanvasItem | undefined;
    /**
     * Starts moving items. What else moves along is the set's to say.
     *
     * @param ids The selected items.
     *
     * @returns The move.
     */
    beginMove(ids: readonly string[]): IMoveSession;
    /**
     * Starts resizing an item.
     *
     * @param id The item.
     *
     * @returns The resize.
     */
    beginResize(id: string): IResizeSession;
    /**
     * Starts raising or lowering an elevatable item.
     *
     * @param id The item.
     *
     * @returns The session.
     */
    beginElevate?(id: string): IElevateSession;
    /** @returns What a move or resize can change, to put it back. */
    snapshot(): unknown;
    /**
     * @param a A snapshot.
     * @param b Another.
     *
     * @returns Whether they hold the same.
     */
    sameSnapshot(a: unknown, b: unknown): boolean;
    /**
     * Puts back what a snapshot holds, and draws it.
     *
     * @param snapshot The snapshot.
     */
    restore(snapshot: unknown): void;
    /**
     * @param id An item.
     *
     * @returns What its menu offers, as things stand.
     */
    menu(id: string): IMenuEntry[];
    /**
     * Does what a menu entry says.
     *
     * @param id The item.
     * @param command The entry's id.
     */
    runCommand(id: string, command: string): void;
    /**
     * An item was double-clicked: the set may start an edit.
     *
     * @param id The item.
     * @param probe Where.
     */
    doubleClick?(id: string, probe: IProbe): void;
    /** The view turned, or the figures rose: the lift changed. */
    viewChanged?(): void;
    /** The zoom settled: textures may be drawn again at the new scale. */
    zoomSettled?(): void;
}

/** Where an item's menu button goes. */
export interface ICanvasOverlay {
    id: string;
    /** The button's side before the transform, in CSS pixels. */
    size: number;
    /** The CSS transform about its top left corner (see `IEditPlacement`). */
    transform: string;
    /** The colour of its icon. */
    color: string;
}

/**
 * Where an input laid over text goes: sized as the text is in the 2D view
 * at the zoom, then put onto the screen by a CSS transform. The camera is
 * orthographic, so a rectangle of the page lands on the screen as a
 * parallelogram - which one `matrix()` reproduces, tilted and skewed in
 * the 3D view and a plain translation in the 2D one.
 */
export interface IEditPlacement {
    width: number;
    height: number;
    fontSize: number;
    transform: string;
}

/** An input to open over text. */
export interface IEditRequest {
    text: string;
    label: string;
    bold: boolean;
    placement: IEditPlacement;
}

/** The tool the pointer works as. */
export type Tool = "pointer" | "hand" | "zoom";

/** Which way the page is looked at. */
export type ViewMode = "2d" | "iso";

/** What the canvas reports. */
export interface ICanvasEvents {
    onZoom(zoom: number): void;
    onSelection?(ids: string[]): void;
    /** The history changed: an action made, undone or redone. */
    onHistory?(entries: IHistoryEntry[]): void;
    /** Where the menu buttons go now; an empty list once there are none. */
    onOverlays?(overlays: ICanvasOverlay[]): void;
    /** An item was right-clicked, at a point relative to the canvas. */
    onContextMenu?(id: string, x: number, y: number): void;
    onEditRequest(request: IEditRequest): void;
    /** The text being edited moved on the screen. */
    onEditPlacement(placement: IEditPlacement): void;
    /** The canvas dropped the edit itself: the input is to close. */
    onEditEnd?(): void;
    /** The view changed without the toolbar: an orbit left the 2D view. */
    onViewMode?(mode: ViewMode): void;
    /**
     * The user orbited to angles of their own: on every move, and once
     * more, `final`, when the orbit ended - what is worth keeping.
     */
    onIsoAngles?(angles: IIsoAngles, final: boolean): void;
    /** What the Navigator shows, after every frame while it is shown. */
    onNavigator?(state: INavigatorState): void;
}

/** What the Navigator shows. */
export interface INavigatorState {
    /**
     * The part of the page in view, as a polygon in the Navigator's area's
     * pixels: a rectangle in the 2D view, a quadrilateral in a 3D one.
     */
    view: IPoint[];
    /** The paper's height over its width, for the area's shape. */
    aspect: number;
}
