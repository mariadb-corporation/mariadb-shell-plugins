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

import * as THREE from "three";

import {
    DEFAULT_PAGE_SETTINGS,
    layoutPages,
    paperBounds,
    type IPageSettings,
    type IRect,
} from "../../../src/modeler/pageSettings.js";
import type {
    ICanvasEvents,
    ICanvasHost,
    ICanvasItem,
    ICanvasOverlay,
    IEditPlacement,
    IEditSpec,
    IElevateSession,
    IFigureSet,
    IHandle,
    IMenuEntry,
    IMoveSession,
    INavigatorState,
    IProbe,
    IResizeSession,
    ItemPlane,
    Tool,
    ViewMode,
} from "./canvasTypes.js";
import {
    affineTransform,
    boundsOf,
    customPreset,
    easeInOut,
    fitZoom,
    gridSpacing,
    isoPreset,
    isoQuaternion,
    liftFactor,
    MAX_TILT,
    orbitAngles,
    overlapsRect,
    TOP_VIEW,
    viewQuaternion,
    type IIsoAngles,
    type IPoint,
    type Segment,
} from "./geometry.js";
import { cssPixelsPerMm, pixelsPerUnit } from "./screen.js";
import type { IDiagramTheme } from "./theme.js";
import {
    disposeGroup,
    DRAW_ORDER,
    planeCenter,
    rectSegments,
    segmentsGeometry,
    setDrawOrder,
    Z,
} from "./threeHelpers.js";
import { UndoStack, type IHistoryEntry } from "./undoStack.js";

/**
 * The canvas: a page on a three.js scene, looked at from straight above
 * (2D) or from an angle (3D), that shows a figure set (see
 * `canvasTypes.ts` for the split between the two).
 *
 * One scene, one orthographic camera. The page lies flat in the world's
 * XZ plane; the 2D view looks straight down, the 3D view is the same
 * camera turned and tilted, with the figure set's lifted group raised off
 * the page. The switch slerps the camera over 400 ms, and since the camera
 * stays orthographic the zoom and the pan carry over unchanged. Cmd + drag
 * orbits freely.
 *
 * Rendering is on demand: a frame is drawn after input or while an
 * animation runs, never in a loop.
 */

/** How high the lifted group rises in the 3D view, in page units. */
const LIFT = 30;

/** How long the view transition takes. */
const VIEW_TRANSITION_MS = 400;

/** The camera's distance from the page; only its sign matters. */
const CAMERA_DISTANCE = 2000;

/** How strongly the sheets' edges show, dotted, where the sheets overlap. */
const INNER_EDGE_OPACITY = 0.3;

/** How strongly the grid's main lines and its sublines show. */
const GRID_MAIN_OPACITY = 0.38;
const GRID_SUB_OPACITY = 0.4;

/** The zoom levels the view can be set to. */
export const ZOOM_RANGE = { min: 0.05, max: 8 } as const;

/** The Navigator's margin around the paper, in its pixels. */
const NAVIGATOR_PADDING = 6;

/** A resize handle's size on the screen, in CSS pixels. */
const HANDLE_PX = 8;

/**
 * The menu button's size: a share of its band's height, so it scales with
 * the zoom and keeps its place on the item at every zoom. Smaller on the
 * screen than the least size, it is not drawn (the context menu remains).
 */
const MENU_BUTTON_SHARE = 0.6;
const MENU_BUTTON_MIN_PX = 8;

/**
 * The zoom the button's distance from the item's right edge is taken at:
 * half a handle and a 4px gap there, as page units at every zoom. It
 * looked best at 200% (the user's call, 2026-10-15).
 */
const MENU_BUTTON_REFERENCE_ZOOM = 2;

/** How far a press moves before it is a drag rather than a click. */
const DRAG_THRESHOLD_PX = 3;

/** How far the selection outline lies outside its item, in page units. */
const OUTLINE_OFFSET = 1.5;

/** The eight handles, clockwise from the top left corner. */
const HANDLES: readonly IHandle[] = [
    { fx: 0, fy: 0, cursor: "nwse-resize" },
    { fx: 0.5, fy: 0, cursor: "ns-resize" },
    { fx: 1, fy: 0, cursor: "nesw-resize" },
    { fx: 1, fy: 0.5, cursor: "ew-resize" },
    { fx: 1, fy: 1, cursor: "nwse-resize" },
    { fx: 0.5, fy: 1, cursor: "ns-resize" },
    { fx: 0, fy: 1, cursor: "nesw-resize" },
    { fx: 0, fy: 0.5, cursor: "ew-resize" },
];

/**
 * The colour around the paper: the theme's widget background, darkened -
 * more on a dark theme, where a little darkening shows less - so the
 * sheets stand out.
 *
 * @param theme The theme.
 *
 * @returns The colour.
 */
const outsideColor = (theme: IDiagramTheme): THREE.Color => {
    const factor = theme.dark ? 0.6 : 0.8;
    const color = new THREE.Color(theme.background)
        .getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);

    return new THREE.Color().setRGB(color.r * factor, color.g * factor,
        color.b * factor, THREE.SRGBColorSpace);
};

/**
 * Whether the webview runs on macOS, where Cmd is the primary modifier
 * (Undo is Cmd+Z); elsewhere it is Ctrl.
 */
const IS_MAC = /Mac/.test(globalThis.navigator?.userAgent ?? "");

/** The selection outline and handles of one selected item. */
interface IDecoration {
    group: THREE.Group;
    outline: THREE.LineLoop;
    handles: THREE.Group;
    plane: ItemPlane;
    builtSize?: string;
}

/** What the pointer is doing between a press and its release. */
type PointerState =
    | { kind: "pan"; last: IPoint }
    | {
        kind: "orbit";
        start: IPoint;
        /** The item under the press: a click without moving toggles it. */
        itemId?: string;
        /** The angles the orbit started from, once it has. */
        from?: Pick<IIsoAngles, "tilt" | "turn">;
    }
    | {
        kind: "move";
        last: IPoint;
        start: IPoint;
        plane: ItemPlane;
        /** The grabbed item's elevation: the plane the drag moves along. */
        elevation: number;
        session?: IMoveSession;
        before: unknown;
    }
    | {
        kind: "elevate";
        last: IPoint;
        session: IElevateSession;
        before: unknown;
    }
    | {
        kind: "resize";
        handle: IHandle;
        item: ICanvasItem;
        from: IPoint;
        rect: IRect;
        session: IResizeSession;
        before: unknown;
    };

export class Canvas {
    readonly #container: HTMLElement;
    readonly #events: ICanvasEvents;
    readonly #renderer: THREE.WebGLRenderer;
    readonly #scene = new THREE.Scene();
    readonly #camera: THREE.OrthographicCamera;
    /** Rotated so that the page's y runs along the world's +Z. */
    readonly #root = new THREE.Group();
    readonly #pageGroup = new THREE.Group();
    readonly #gridGroup = new THREE.Group();
    /** The figure set's group on the page. */
    readonly #baseGroup = new THREE.Group();
    /** The figure set's group that rises in the 3D view. */
    readonly #liftedGroup = new THREE.Group();
    readonly #raycaster = new THREE.Raycaster();
    readonly #resizeObserver: ResizeObserver;
    readonly #history = new UndoStack();
    readonly #host: ICanvasHost;

    #theme: IDiagramTheme;
    #set?: IFigureSet;
    #pageSettings?: IPageSettings;
    #pages: IRect = { left: 0, top: 0, width: 1, height: 1 };

    #tool: Tool = "pointer";
    #locked = false;
    #zoom = 1;
    /** The point of the page at the centre of the view. */
    #target = new THREE.Vector3();
    /** 0 in the 2D view, 1 in the 3D one, between while turning. */
    #viewT = 0;
    #viewMode: ViewMode = "2d";
    /** The 3D view's angles, and its orientation (turned when they change). */
    #isoAngles: Pick<IIsoAngles, "tilt" | "turn"> = isoPreset(undefined);
    #isoView = isoQuaternion(isoPreset(undefined));
    #animation?: number;
    #isoAnimation?: number;
    #frame?: number;
    #zoomTimer?: ReturnType<typeof setTimeout>;

    /** The dashed printable areas: hidden unless asked for. */
    #marginsVisible = false;
    #marginLine?: THREE.LineSegments;

    /** The grid's main spacing as built, in millimetres. */
    #gridMainMm?: number;
    /** The grid's sublines, faded as they come close. */
    #gridSub?: THREE.LineSegments<THREE.BufferGeometry,
        THREE.LineBasicMaterial>;

    #selected = new Set<string>();
    #decorations = new Map<string, IDecoration>();
    /** The item under the pointer, which shows its menu button. */
    #hover?: string;
    /** The cursor a resize handle under the pointer asks for. */
    #hoverCursor?: string;
    /** The lift and size the figure set last heard of. */
    #lastView?: string;

    /** The last report of each kind, as text, to skip one that repeats it. */
    #reported = new Map<string, string>();
    #edit?: IEditSpec;

    /**
     * The keys held that are not modifiers: Space and Z. Only their own
     * keydown and keyup tell about them, and macOS swallows the keyup of
     * any key released while Cmd is down, so they are also dropped when
     * Cmd or Ctrl goes up, and whenever the window loses focus or is
     * hidden.
     */
    #keys = new Set<string>();

    /**
     * The modifiers, as the last event said. Every keyboard, pointer and
     * wheel event carries them, so they are read off those rather than
     * tracked by keydown / keyup, which can be lost.
     */
    #modifiers = { meta: false, alt: false };

    #pointer?: PointerState;

    /** Where the Navigator's overview goes on the canvas, in CSS pixels. */
    #navigatorArea?: IRect;
    /** The whole Navigator panel, kept clear of menu buttons. */
    #navigatorPanel?: IRect;
    /** The camera that looks straight down at the paper for it. */
    readonly #navigatorCamera = new THREE.OrthographicCamera(-1, 1, 1, -1,
        1, 10000);

    public constructor(
        container: HTMLElement,
        theme: IDiagramTheme,
        events: ICanvasEvents,
    ) {
        this.#container = container;
        this.#theme = theme;
        this.#events = events;
        this.#history.onChange(() => {
            this.#events.onHistory?.(this.#history.entries);
        });

        this.#renderer = new THREE.WebGLRenderer({
            antialias: true,
            alpha: false,
        });
        this.#renderer.setPixelRatio(window.devicePixelRatio);
        this.#renderer.setClearColor(outsideColor(theme));
        container.appendChild(this.#renderer.domElement);

        this.#camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 10000);
        this.#root.rotation.x = -Math.PI / 2;
        this.#root.add(this.#pageGroup, this.#gridGroup, this.#baseGroup,
            this.#liftedGroup);
        this.#scene.add(this.#root);

        this.#host = this.#makeHost();

        this.#resizeObserver = new ResizeObserver(() => { this.#resize(); });
        this.#resizeObserver.observe(container);
        this.#resize();

        const canvas = this.#renderer.domElement;
        canvas.addEventListener("pointerdown", this.#pointerDown);
        canvas.addEventListener("pointermove", this.#pointerMove);
        canvas.addEventListener("pointerup", this.#pointerUp);
        canvas.addEventListener("pointercancel", this.#pointerUp);
        canvas.addEventListener("dblclick", this.#doubleClick);
        canvas.addEventListener("wheel", this.#wheel, { passive: false });
        canvas.addEventListener("contextmenu", this.#contextMenu);
        canvas.addEventListener("pointerenter", this.#pointerEnter);
        window.addEventListener("keydown", this.#keyDown);
        window.addEventListener("keyup", this.#keyUp);
        window.addEventListener("blur", this.#blur);
        document.addEventListener("visibilitychange", this.#blur);
    }

    // --- The public surface ---------------------------------------------

    public get zoom(): number {
        return this.#zoom;
    }

    public get viewMode(): ViewMode {
        return this.#viewMode;
    }

    public get tool(): Tool {
        return this.#tool;
    }

    public set tool(tool: Tool) {
        this.#tool = tool;
        this.#updateCursor();
    }

    public get locked(): boolean {
        return this.#locked;
    }

    public set locked(locked: boolean) {
        this.#locked = locked;
        this.#invalidate();
    }

    public get gridVisible(): boolean {
        return this.#gridGroup.visible;
    }

    public set gridVisible(visible: boolean) {
        this.#gridGroup.visible = visible;
        this.#invalidate();
    }

    /** Whether the sheets' printable areas are marked, dashed. */
    public get marginsVisible(): boolean {
        return this.#marginsVisible;
    }

    public set marginsVisible(visible: boolean) {
        this.#marginsVisible = visible;
        if (this.#marginLine !== undefined) {
            this.#marginLine.visible = visible;
        }
        this.#invalidate();
    }

    public get pagesVisible(): boolean {
        return this.#pageGroup.visible;
    }

    public set pagesVisible(visible: boolean) {
        this.#pageGroup.visible = visible;
        this.#invalidate();
    }

    /** @returns What was done, oldest first, for a list of it. */
    public get history(): IHistoryEntry[] {
        return this.#history.entries;
    }

    /**
     * Shows a figure set on paper. Another set than the one shown starts a
     * new history; the same one again (another of its diagrams, say) keeps
     * it.
     *
     * @param set What to show.
     * @param pageSettings The paper it lies on.
     *
     * @returns Once it is drawn.
     */
    public async load(set: IFigureSet, pageSettings: IPageSettings): Promise<void> {
        if (set !== this.#set) {
            this.#history.clear();
        }
        this.#unmount();
        this.#set = set;
        this.#pageSettings = pageSettings;
        this.#buildPaper();
        await set.mount(this.#host);
        this.#applyView();
        this.fit();
    }

    /**
     * Applies a theme: the colours of the paper, and the figure set drawn
     * again.
     *
     * @param theme The theme.
     *
     * @returns Once drawn again.
     */
    public async setTheme(theme: IDiagramTheme): Promise<void> {
        this.#theme = theme;
        this.#renderer.setClearColor(outsideColor(theme));
        const set = this.#set;
        if (set !== undefined) {
            const selected = [...this.#selected];
            this.#unmount();
            this.#set = set;
            this.#buildPaper();
            await set.mount(this.#host);
            this.#select(selected.filter((id) => {
                return set.item(id) !== undefined;
            }), false);
            this.#applyView();
        }
        this.#invalidate();
    }

    /**
     * Turns the view, animated.
     *
     * @param mode The view.
     *
     * @returns Nothing.
     */
    public setView(mode: ViewMode): void {
        if (mode === this.#viewMode) {
            return;
        }
        this.#viewMode = mode;
        const from = this.#viewT;
        const to = mode === "iso" ? 1 : 0;
        const started = performance.now();
        if (this.#animation !== undefined) {
            cancelAnimationFrame(this.#animation);
        }
        const step = (now: number): void => {
            const progress = Math.min(1, (now - started) / VIEW_TRANSITION_MS);
            this.#viewT = from + (to - from) * easeInOut(progress);
            this.#applyView();
            this.#render();
            this.#animation = progress < 1
                ? requestAnimationFrame(step)
                : undefined;
        };
        this.#animation = requestAnimationFrame(step);
    }

    /**
     * Sets the angles of the 3D view; turns to them, animated, where the
     * view is (or is turning) 3D.
     *
     * @param angles The angles.
     *
     * @returns Nothing.
     */
    public setIsoAngles(angles: Pick<IIsoAngles, "tilt" | "turn">): void {
        const from = this.#isoView.clone();
        const to = isoQuaternion(angles);
        this.#isoAngles = { tilt: angles.tilt, turn: angles.turn };
        if (this.#isoAnimation !== undefined) {
            cancelAnimationFrame(this.#isoAnimation);
            this.#isoAnimation = undefined;
        }
        if (this.#viewT === 0 && this.#viewMode === "2d") {
            this.#isoView.copy(to);

            return;
        }
        const started = performance.now();
        const step = (now: number): void => {
            const progress = Math.min(1, (now - started) / VIEW_TRANSITION_MS);
            this.#isoView.slerpQuaternions(from, to, easeInOut(progress));
            this.#applyView();
            this.#render();
            this.#isoAnimation = progress < 1
                ? requestAnimationFrame(step)
                : undefined;
        };
        this.#isoAnimation = requestAnimationFrame(step);
    }

    /**
     * Zooms so that the whole paper is in view.
     *
     * @returns Nothing.
     */
    public fit(): void {
        if (this.#pageSettings === undefined) {
            return;
        }
        const zoom = fitZoom(this.#viewport(), this.#pages,
            pixelsPerUnit(1, this.#pageSettings.scale));
        this.#target.set(this.#pages.left + this.#pages.width / 2, 0,
            this.#pages.top + this.#pages.height / 2);
        this.#setZoom(zoom);
    }

    /**
     * Sets the zoom, keeping the centre of the view where it is.
     *
     * @param zoom The zoom level, 1 being life size.
     *
     * @returns Nothing.
     */
    public zoomTo(zoom: number): void {
        this.#setZoom(zoom);
    }

    /**
     * Takes the last change back.
     *
     * @returns Whether there was one.
     */
    public undo(): boolean {
        // Locked, the diagram does not change: undoing is changing it too.
        return this.#pointer === undefined && !this.#locked
            && this.#history.undo() !== undefined;
    }

    /**
     * Makes the last change taken back again.
     *
     * @returns Whether there was one.
     */
    public redo(): boolean {
        return this.#pointer === undefined && !this.#locked
            && this.#history.redo() !== undefined;
    }

    /**
     * What an item's menu offers, as things stand.
     *
     * @param id The item.
     *
     * @returns The entries.
     */
    public menu(id: string): IMenuEntry[] {
        return this.#set?.menu(id) ?? [];
    }

    /**
     * Does what a menu entry says.
     *
     * @param id The item.
     * @param command The entry.
     *
     * @returns Nothing.
     */
    public runCommand(id: string, command: string): void {
        if (!this.#locked) {
            this.#set?.runCommand(id, command);
        }
    }

    /**
     * Makes the edit the input was opened for.
     *
     * @param text The text typed.
     *
     * @returns Nothing.
     */
    public commitEdit(text: string): void {
        const edit = this.#edit;
        this.#edit = undefined;
        edit?.commit(text);
    }

    /**
     * Drops the edit the input was opened for.
     *
     * @returns Nothing.
     */
    public cancelEdit(): void {
        this.#edit = undefined;
    }

    /**
     * Shows the Navigator's overview in an area of the canvas - left
     * clear by the panel over it - or no more.
     *
     * @param area The area, in CSS pixels relative to the canvas; nothing
     *             to show none.
     * @param panel The whole panel, which no menu button is drawn under.
     *
     * @returns Nothing.
     */
    public setNavigatorArea(area: IRect | undefined, panel?: IRect): void {
        this.#navigatorArea = area;
        this.#navigatorPanel = area === undefined ? undefined : panel;
        this.#invalidate();
    }

    /**
     * Pans the view by a distance dragged in the Navigator.
     *
     * @param dx Across, in the Navigator's pixels.
     * @param dy Down.
     *
     * @returns Nothing.
     */
    public navigatorPan(dx: number, dy: number): void {
        const map = this.#navigatorMap();
        if (map === undefined) {
            return;
        }
        this.#target.x += dx / map.scale;
        this.#target.z += dy / map.scale;
        this.#applyView();
        this.#invalidate();
    }

    /**
     * Centres the view on a point of the Navigator.
     *
     * @param x The point, in the Navigator's pixels.
     * @param y The point.
     *
     * @returns Nothing.
     */
    public navigatorCenter(x: number, y: number): void {
        const map = this.#navigatorMap();
        if (map === undefined) {
            return;
        }
        this.#target.x = map.centerX + (x - map.width / 2) / map.scale;
        this.#target.z = map.centerY + (y - map.height / 2) / map.scale;
        this.#applyView();
        this.#invalidate();
    }

    /**
     * How the paper maps into the Navigator's area: fitted, centred, with
     * a small margin.
     *
     * @returns The area's size, the scale in its pixels per page unit and
     *          the page point at its centre; nothing while it is not shown.
     */
    #navigatorMap(): {
        width: number;
        height: number;
        scale: number;
        centerX: number;
        centerY: number;
    } | undefined {
        const area = this.#navigatorArea;
        if (area === undefined || area.width <= 0 || area.height <= 0) {
            return undefined;
        }
        const pages = this.#pages;

        return {
            width: area.width,
            height: area.height,
            scale: Math.min(
                (area.width - 2 * NAVIGATOR_PADDING) / pages.width,
                (area.height - 2 * NAVIGATOR_PADDING) / pages.height),
            centerX: pages.left + pages.width / 2,
            centerY: pages.top + pages.height / 2,
        };
    }

    /**
     * Draws the overview into the Navigator's area of the canvas: the same
     * scene, from straight above - so it stays 2D in a 3D view - and
     * reports the part of the page in view.
     */
    #renderNavigator(): void {
        const map = this.#navigatorMap();
        const area = this.#navigatorArea;
        if (map === undefined || area === undefined) {
            return;
        }
        const camera = this.#navigatorCamera;
        camera.left = -map.width / 2 / map.scale;
        camera.right = map.width / 2 / map.scale;
        camera.top = map.height / 2 / map.scale;
        camera.bottom = -map.height / 2 / map.scale;
        camera.quaternion.copy(TOP_VIEW);
        camera.position.set(map.centerX, CAMERA_DISTANCE, map.centerY);
        camera.updateProjectionMatrix();
        camera.updateMatrixWorld();

        const { height } = this.#viewport();
        const bottom = height - area.top - area.height;
        this.#renderer.setScissorTest(true);
        this.#renderer.setViewport(area.left, bottom, area.width, area.height);
        this.#renderer.setScissor(area.left, bottom, area.width, area.height);
        this.#renderer.render(this.#scene, camera);
        this.#renderer.setScissorTest(false);
        const viewport = this.#viewport();
        this.#renderer.setViewport(0, 0, viewport.width, viewport.height);

        const corners = [
            { x: 0, y: 0 },
            { x: viewport.width, y: 0 },
            { x: viewport.width, y: viewport.height },
            { x: 0, y: viewport.height },
        ];
        const view: IPoint[] = [];
        for (const corner of corners) {
            const point = this.#pageAt(corner, "base");
            if (point === undefined) {
                return;
            }
            view.push({
                x: map.width / 2 + (point.x - map.centerX) * map.scale,
                y: map.height / 2 + (point.y - map.centerY) * map.scale,
            });
        }
        const state: INavigatorState = {
            view,
            aspect: this.#pages.height / this.#pages.width,
        };
        if (this.#changed("navigator", state)) {
            this.#events.onNavigator?.(state);
        }
    }

    /**
     * Releases the canvas and the listeners.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#resizeObserver.disconnect();
        window.removeEventListener("keydown", this.#keyDown);
        window.removeEventListener("keyup", this.#keyUp);
        window.removeEventListener("blur", this.#blur);
        document.removeEventListener("visibilitychange", this.#blur);
        for (const id of [this.#animation, this.#isoAnimation, this.#frame]) {
            if (id !== undefined) {
                cancelAnimationFrame(id);
            }
        }
        this.#unmount();
        disposeGroup(this.#pageGroup);
        disposeGroup(this.#gridGroup);
        this.#renderer.dispose();
        this.#renderer.domElement.remove();
    }

    // --- The host the figure set draws through ------------------------------

    #makeHost(): ICanvasHost {
        const canvas = this;

        return {
            base: this.#baseGroup,
            lifted: this.#liftedGroup,
            get theme(): IDiagramTheme { return canvas.#theme; },
            history: this.#history,
            get maxAnisotropy(): number {
                return canvas.#renderer.capabilities.getMaxAnisotropy();
            },
            scale: () => { return this.#scale(); },
            viewport: () => { return this.#viewport(); },
            lift: () => { return this.#lift(); },
            invalidate: () => { this.#invalidate(); },
            startEdit: (spec) => { this.#startEdit(spec); },
            select: (ids) => { this.#select(ids, false); },
            change: (label, change) => {
                const set = this.#set;
                if (set === undefined) {
                    return;
                }
                const before = set.snapshot();
                change();
                this.#recordSnapshot(label, before);
                this.#invalidate();
            },
        };
    }

    /**
     * Records what changed since a snapshot as one action.
     *
     * @param label What was done.
     * @param before The snapshot taken before.
     */
    #recordSnapshot(label: string, before: unknown): void {
        const set = this.#set;
        if (set === undefined) {
            return;
        }
        const after = set.snapshot();
        if (set.sameSnapshot(before, after)) {
            return;
        }
        this.#history.record({
            label,
            undo: () => { set.restore(before); this.#invalidate(); },
            redo: () => { set.restore(after); this.#invalidate(); },
        });
    }

    #unmount(): void {
        // A drag under way works on what is unmounted: dropped with it.
        this.#pointer = undefined;
        this.#lastView = undefined;
        this.#set?.unmount();
        this.#set = undefined;
        if (this.#edit !== undefined) {
            // What it edits is gone (a theme change draws it all again):
            // the input over it goes too.
            this.#edit = undefined;
            this.#events.onEditEnd?.();
        }
        this.#hover = undefined;
        this.#selected.clear();
        for (const decoration of this.#decorations.values()) {
            disposeGroup(decoration.group);
            decoration.group.removeFromParent();
        }
        this.#decorations.clear();
        disposeGroup(this.#baseGroup);
        disposeGroup(this.#liftedGroup);
    }

    // --- The paper ----------------------------------------------------------

    #buildPaper(): void {
        const set = this.#set;
        const settings = this.#pageSettings;
        disposeGroup(this.#pageGroup);
        disposeGroup(this.#gridGroup);
        this.#gridSub = undefined;
        this.#gridMainMm = undefined;
        if (set === undefined || settings === undefined) {
            return;
        }
        const pages = layoutPages(settings, set.extent());
        this.#pages = paperBounds(pages);
        const bounds = this.#pages;
        const paper = new THREE.Mesh(
            new THREE.PlaneGeometry(bounds.width, bounds.height),
            new THREE.MeshBasicMaterial({ color: this.#theme.paper }));
        const center = planeCenter(bounds);
        paper.position.set(center.x, center.y, Z.paper);
        this.#pageGroup.add(paper);

        // The sheets' edges, and the margins dashed inside them. Where the
        // sheets overlap, their edges run through the diagram: those are
        // faint, and only the paper's outline is drawn in full.
        const outline: Segment[] = [];
        const inner: Segment[] = [];
        const margins: Segment[] = [];
        const onOutline = ([from, to]: Segment): boolean => {
            const near = (a: number, b: number): boolean => {
                return Math.abs(a - b) < 0.01;
            };
            const right = bounds.left + bounds.width;
            const bottom = bounds.top + bounds.height;

            return (near(from.x, to.x) && (near(from.x, bounds.left)
                    || near(from.x, right)))
                || (near(from.y, to.y) && (near(from.y, bounds.top)
                    || near(from.y, bottom)));
        };
        for (const page of pages) {
            for (const segment of rectSegments(page.paper)) {
                (onOutline(segment) ? outline : inner).push(segment);
            }
            margins.push(...rectSegments(page.printable));
        }
        this.#pageGroup.add(new THREE.LineSegments(
            segmentsGeometry(outline, Z.sheetLines),
            new THREE.LineBasicMaterial({ color: this.#theme.grid })));
        // Dotted, apart from the printable areas' longer dashes.
        const innerLine = new THREE.LineSegments(
            segmentsGeometry(inner, Z.sheetLines),
            new THREE.LineDashedMaterial({
                color: this.#theme.grid,
                transparent: true,
                opacity: INNER_EDGE_OPACITY,
                dashSize: 1.5,
                gapSize: 4,
            }));
        innerLine.computeLineDistances();
        this.#pageGroup.add(innerLine);
        const marginLine = new THREE.LineSegments(
            segmentsGeometry(margins, Z.sheetLines),
            new THREE.LineDashedMaterial({
                color: this.#theme.grid,
                dashSize: 8,
                gapSize: 6,
            }));
        marginLine.computeLineDistances();
        marginLine.visible = this.#marginsVisible;
        this.#marginLine = marginLine;
        this.#pageGroup.add(marginLine);
        setDrawOrder(this.#pageGroup, DRAW_ORDER.pages);
        this.#updateGrid();
    }

    /**
     * Fits the grid to the zoom: built again where the level changes (see
     * `gridSpacing`), the sublines' opacity set otherwise.
     */
    #updateGrid(): void {
        const settings = this.#pageSettings;
        if (settings === undefined || this.#set === undefined) {
            return;
        }
        const spacing = gridSpacing(cssPixelsPerMm() * this.#zoom);
        if (spacing.mainMm !== this.#gridMainMm) {
            this.#gridMainMm = spacing.mainMm;
            this.#buildGrid(settings, spacing.mainMm, spacing.subMm);
        }
        if (this.#gridSub !== undefined) {
            this.#gridSub.material.opacity = GRID_SUB_OPACITY
                * spacing.subOpacity;
            this.#gridSub.visible = spacing.subOpacity > 0;
        }
    }

    /**
     * Draws the grid over the paper: main lines, and the sublines between
     * them.
     *
     * @param settings The page settings, for the units per millimetre.
     * @param mainMm The main lines' spacing.
     * @param subMm The sublines' spacing.
     */
    #buildGrid(settings: IPageSettings, mainMm: number, subMm: number): void {
        disposeGroup(this.#gridGroup);
        const bounds = this.#pages;
        const minor: Segment[] = [];
        const major: Segment[] = [];
        const minorStep = subMm * settings.scale;
        const majorStep = mainMm * settings.scale;
        const right = bounds.left + bounds.width;
        const bottom = bounds.top + bounds.height;
        const lines = (step: number, into: Segment[], skip: number): void => {
            for (let x = Math.ceil(bounds.left / step) * step; x <= right;
                x += step) {
                if (skip === 0 || Math.round(x / step) % skip !== 0) {
                    into.push([{ x, y: bounds.top }, { x, y: bottom }]);
                }
            }
            for (let y = Math.ceil(bounds.top / step) * step; y <= bottom;
                y += step) {
                if (skip === 0 || Math.round(y / step) % skip !== 0) {
                    into.push([{ x: bounds.left, y }, { x: right, y }]);
                }
            }
        };
        lines(minorStep, minor, Math.round(majorStep / minorStep));
        lines(majorStep, major, 0);
        const color = new THREE.Color(this.#theme.grid);
        this.#gridSub = new THREE.LineSegments(segmentsGeometry(minor, Z.grid),
            new THREE.LineBasicMaterial({
                color, transparent: true, opacity: GRID_SUB_OPACITY,
            }));
        const main = new THREE.LineSegments(segmentsGeometry(major, Z.grid),
            new THREE.LineBasicMaterial({
                color, transparent: true, opacity: GRID_MAIN_OPACITY,
            }));
        this.#gridGroup.add(this.#gridSub, main);
        setDrawOrder(this.#gridGroup, DRAW_ORDER.grid);
    }

    // --- The camera -----------------------------------------------------------

    #viewport(): { width: number; height: number } {
        return {
            width: Math.max(1, this.#container.clientWidth),
            height: Math.max(1, this.#container.clientHeight),
        };
    }

    #resize(): void {
        const { width, height } = this.#viewport();
        this.#renderer.setSize(width, height, false);
        this.#renderer.domElement.style.width = `${width}px`;
        this.#renderer.domElement.style.height = `${height}px`;
        this.#camera.left = -width / 2;
        this.#camera.right = width / 2;
        this.#camera.top = height / 2;
        this.#camera.bottom = -height / 2;
        this.#applyView();
        this.#invalidate();
    }

    /** @returns The paper's page units per millimetre. */
    #unitsPerMm(): number {
        return (this.#pageSettings ?? DEFAULT_PAGE_SETTINGS).scale;
    }

    /** @returns CSS pixels per page unit at the current zoom. */
    #scale(): number {
        return pixelsPerUnit(this.#zoom, this.#unitsPerMm());
    }

    /**
     * How far the figures have risen: with the view's turn to 3D, and
     * with the tilt - none looking straight down, so an orbit that starts
     * in the 2D view lifts them as the camera tips over.
     *
     * @returns 0..1.
     */
    #lift(): number {
        return this.#viewT * liftFactor(this.#isoAngles.tilt);
    }

    /**
     * The height of a plane above the page, in page units, and of what
     * stands on it at an elevation.
     *
     * @param plane The plane.
     * @param elevation The elevation above it, as the figure set has it.
     *
     * @returns The height.
     */
    #planeHeight(plane: ItemPlane, elevation = 0): number {
        return ((plane === "lifted" ? LIFT : 0) + elevation) * this.#lift();
    }

    /** @returns An item's elevation, as the figure set has it. */
    #elevationOf(item: { elevation?(): number }): number {
        return item.elevation?.() ?? 0;
    }

    /** Puts the camera where the zoom, the target and the view say. */
    #applyView(): void {
        const quaternion = viewQuaternion(this.#viewT, this.#isoView);
        this.#camera.quaternion.copy(quaternion);
        this.#camera.position.copy(this.#target).add(
            new THREE.Vector3(0, 0, CAMERA_DISTANCE).applyQuaternion(quaternion));
        this.#camera.zoom = this.#scale();
        this.#camera.updateProjectionMatrix();
        this.#camera.updateMatrixWorld();
        this.#liftedGroup.position.z = this.#planeHeight("lifted");
        // What the figure set draws depends on the lift and the canvas's
        // size, not on the pan or the zoom: a pan pixel costs it nothing.
        const { width, height } = this.#viewport();
        const view = `${String(this.#lift())}|${String(width)}x${String(height)}`;
        if (view !== this.#lastView) {
            this.#lastView = view;
            this.#set?.viewChanged?.();
        }
    }

    #setZoom(zoom: number): void {
        this.#zoom = Math.min(ZOOM_RANGE.max, Math.max(ZOOM_RANGE.min, zoom));
        this.#updateGrid();
        this.#applyView();
        this.#events.onZoom(this.#zoom);
        if (this.#zoomTimer !== undefined) {
            clearTimeout(this.#zoomTimer);
        }
        this.#zoomTimer = setTimeout(() => {
            this.#zoomTimer = undefined;
            this.#set?.zoomSettled?.();
        }, 150);
        this.#invalidate();
    }

    /**
     * Zooms about a point on the screen, which stays over the same point
     * of the page.
     */
    #zoomAt(screen: IPoint, factor: number): void {
        const before = this.#worldAt(screen, 0);
        this.#setZoom(this.#zoom * factor);
        const after = this.#worldAt(screen, 0);
        if (before !== undefined && after !== undefined) {
            this.#target.add(before.sub(after));
            this.#applyView();
            this.#invalidate();
        }
    }

    /**
     * Moves the VIEW by a screen distance: a wheel's delta scrolls the way
     * a page scrolls, and a hand drag passes the opposite of the pointer's
     * movement so that the content follows the hand.
     */
    #panByPixels(dx: number, dy: number): void {
        const { width, height } = this.#viewport();
        const center = { x: width / 2, y: height / 2 };
        const from = this.#worldAt(center, 0);
        const to = this.#worldAt({ x: center.x + dx, y: center.y + dy }, 0);
        if (from !== undefined && to !== undefined) {
            this.#target.add(to.sub(from));
            this.#applyView();
            this.#invalidate();
        }
    }

    /** Points the raycaster from the camera through a point of the screen. */
    #aim(screen: IPoint): void {
        const { width, height } = this.#viewport();
        this.#raycaster.setFromCamera(new THREE.Vector2(
            (screen.x / width) * 2 - 1, -(screen.y / height) * 2 + 1),
        this.#camera);
    }

    /**
     * The point of a horizontal plane under a point on the screen.
     *
     * @param screen The point, in CSS pixels relative to the canvas.
     * @param height The plane's height above the page.
     *
     * @returns The world point, or nothing where the ray misses the plane.
     */
    #worldAt(screen: IPoint, height: number): THREE.Vector3 | undefined {
        this.#aim(screen);
        const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -height);
        const point = new THREE.Vector3();

        return this.#raycaster.ray.intersectPlane(plane, point) ?? undefined;
    }

    /**
     * The page point of a plane under a point on the screen.
     *
     * @param screen The point.
     * @param plane The plane.
     * @param elevation How far above the plane.
     *
     * @returns The page point; world X is the page's x, world Z its y.
     */
    #pageAt(
        screen: IPoint,
        plane: ItemPlane,
        elevation = 0,
    ): IPoint | undefined {
        const world = this.#worldAt(screen,
            this.#planeHeight(plane, elevation));

        return world === undefined ? undefined : { x: world.x, y: world.z };
    }

    /** Where a world point lands on the screen. */
    #screenAt(world: THREE.Vector3): IPoint {
        const { width, height } = this.#viewport();
        const projected = world.clone().project(this.#camera);

        return {
            x: (projected.x + 1) / 2 * width,
            y: (1 - projected.y) / 2 * height,
        };
    }

    /**
     * Where a page point of a plane lands on the screen.
     *
     * @param x The page's x.
     * @param y The page's y.
     * @param plane The plane.
     * @param elevation How far above the plane.
     *
     * @returns The screen point.
     */
    #screenOfPage(
        x: number,
        y: number,
        plane: ItemPlane,
        elevation: number,
    ): IPoint {
        // On the surface of what is drawn there.
        const lift = plane === "lifted" ? Z.faceText : Z.plateText;

        return this.#screenAt(new THREE.Vector3(x,
            this.#planeHeight(plane, elevation) + lift, y));
    }

    /**
     * The CSS transform that lays a box of a size onto a rectangle of a
     * plane (see `IEditPlacement`).
     *
     * @param rect The rectangle on the page.
     * @param plane Its plane.
     * @param elevation How far above the plane.
     * @param width The box's width, before the transform.
     * @param height Its height.
     *
     * @returns The transform.
     */
    #transformOnto(
        rect: IRect,
        plane: ItemPlane,
        elevation: number,
        width: number,
        height: number,
    ): string {
        const [topLeft, topRight, , bottomLeft] = this.#screenCorners(rect,
            plane, elevation);

        return affineTransform(topLeft, topRight, bottomLeft, width, height);
    }

    /**
     * Where a rectangle of a plane lands on the screen.
     *
     * @param rect The rectangle on the page.
     * @param plane Its plane.
     * @param elevation How far above the plane.
     *
     * @returns Its corners, clockwise from the top left.
     */
    #screenCorners(rect: IRect, plane: ItemPlane, elevation: number): IPoint[] {
        const right = rect.left + rect.width;
        const bottom = rect.top + rect.height;

        return [
            this.#screenOfPage(rect.left, rect.top, plane, elevation),
            this.#screenOfPage(right, rect.top, plane, elevation),
            this.#screenOfPage(right, bottom, plane, elevation),
            this.#screenOfPage(rect.left, bottom, plane, elevation),
        ];
    }

    #probe(screen: IPoint): IProbe {
        this.#aim(screen);

        return {
            raycaster: this.#raycaster,
            pointOn: (plane, elevation) => {
                return this.#pageAt(screen, plane, elevation);
            },
        };
    }

    // --- Rendering --------------------------------------------------------

    #invalidate(): void {
        if (this.#frame === undefined) {
            this.#frame = requestAnimationFrame(() => {
                this.#frame = undefined;
                this.#render();
            });
        }
    }

    #render(): void {
        this.#updateDecorations();
        this.#renderer.render(this.#scene, this.#camera);
        this.#renderNavigator();
        // What the viewer shows over the canvas is reported only where it
        // changed: each report is a render of the viewer.
        const overlays = this.#overlays();
        if (this.#changed("overlays", overlays)) {
            this.#events.onOverlays?.(overlays);
        }
        if (this.#edit !== undefined) {
            const placement = this.#editPlacement(this.#edit);
            if (placement !== undefined
                && this.#changed("placement", placement)) {
                this.#events.onEditPlacement(placement);
            }
        }
    }

    /**
     * Whether a report differs from the last one of its kind, keeping it.
     *
     * @param kind What is reported.
     * @param value What it is now: plain data.
     *
     * @returns Whether it changed.
     */
    #changed(kind: string, value: unknown): boolean {
        const text = JSON.stringify(value);
        if (this.#reported.get(kind) === text) {
            return false;
        }
        this.#reported.set(kind, text);

        return true;
    }

    // --- Selection, handles and menu buttons ----------------------------------

    #select(ids: readonly string[], toggle: boolean): void {
        if (!toggle) {
            this.#selected.clear();
        }
        for (const id of ids) {
            if (toggle && this.#selected.has(id)) {
                this.#selected.delete(id);
            } else {
                this.#selected.add(id);
            }
        }
        this.#events.onSelection?.([...this.#selected]);
        this.#invalidate();
    }

    /**
     * Brings the selection outlines and handles in line with the selected
     * items: made for an item newly selected, dropped for one no longer
     * selected or gone, and put where each item is now.
     */
    #updateDecorations(): void {
        for (const [id, decoration] of this.#decorations) {
            if (!this.#selected.has(id) || this.#set?.item(id) === undefined) {
                disposeGroup(decoration.group);
                decoration.group.removeFromParent();
                this.#decorations.delete(id);
            }
        }
        const scale = this.#scale();
        for (const id of this.#selected) {
            const item = this.#set?.item(id);
            if (item === undefined) {
                continue;
            }
            let decoration = this.#decorations.get(id);
            if (decoration === undefined || decoration.plane !== item.plane) {
                if (decoration !== undefined) {
                    disposeGroup(decoration.group);
                    decoration.group.removeFromParent();
                }
                decoration = this.#buildDecoration(item.plane);
                this.#decorations.set(id, decoration);
            }
            const rect = item.rect();
            const center = planeCenter(rect);
            decoration.group.position.set(center.x, center.y,
                this.#elevationOf(item) * this.#lift()
                + (item.plane === "lifted" ? Z.liftedDecoration
                    : Z.baseDecoration));
            const size = `${rect.width}x${rect.height}`;
            if (decoration.builtSize !== size) {
                decoration.builtSize = size;
                const halfWidth = rect.width / 2 + OUTLINE_OFFSET;
                const halfHeight = rect.height / 2 + OUTLINE_OFFSET;
                decoration.outline.geometry.dispose();
                decoration.outline.geometry = new THREE.BufferGeometry()
                    .setAttribute("position", new THREE.BufferAttribute(
                        new Float32Array([
                            -halfWidth, halfHeight, 0,
                            halfWidth, halfHeight, 0,
                            halfWidth, -halfHeight, 0,
                            -halfWidth, -halfHeight, 0,
                        ]), 3));
            }
            decoration.handles.visible = item.resizable && !this.#locked;
            const handleSize = HANDLE_PX / scale;
            decoration.handles.children.forEach((handle, index) => {
                const { fx, fy } = HANDLES[index];
                handle.position.set((fx - 0.5) * rect.width,
                    (0.5 - fy) * rect.height, 0);
                handle.scale.set(handleSize, handleSize, 1);
            });
        }
    }

    /**
     * Makes a selection outline and eight handles: small squares filled
     * with the paper and edged in the selection colour.
     *
     * @param plane The plane the item lies on.
     *
     * @returns The decoration.
     */
    #buildDecoration(plane: ItemPlane): IDecoration {
        const group = new THREE.Group();
        const line = new THREE.LineBasicMaterial({
            color: this.#theme.selection,
        });
        const outline = new THREE.LineLoop(new THREE.BufferGeometry(), line);
        const handles = new THREE.Group();
        const square = new THREE.PlaneGeometry(1, 1);
        const edge = new THREE.BufferGeometry().setAttribute("position",
            new THREE.BufferAttribute(new Float32Array([
                -0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0,
            ]), 3));
        const fill = new THREE.MeshBasicMaterial({ color: this.#theme.paper });
        for (let index = 0; index < HANDLES.length; index += 1) {
            const handle = new THREE.Group();
            const border = new THREE.LineLoop(edge, line);
            border.position.z = 0.01;
            handle.add(new THREE.Mesh(square, fill), border);
            handles.add(handle);
        }
        group.add(outline, handles);
        setDrawOrder(group, DRAW_ORDER.decorations);
        (plane === "lifted" ? this.#liftedGroup : this.#baseGroup).add(group);

        return { group, outline, handles, plane };
    }

    /**
     * The resize handle under a point of the screen, of the selected
     * items in front first.
     *
     * @param screen The point, relative to the canvas.
     *
     * @returns The handle and its item, or nothing.
     */
    #handleAt(
        screen: IPoint,
    ): { item: ICanvasItem; handle: IHandle } | undefined {
        if (this.#locked || this.#set === undefined) {
            return undefined;
        }
        // A little more than the square, so it is easy to take.
        const reach = (HANDLE_PX / 2 + 2) / this.#scale();
        const items = [...this.#selected]
            .map((id) => { return this.#set?.item(id); })
            .filter((item): item is ICanvasItem => {
                return item?.resizable === true;
            })
            // The lifted plane is in front.
            .sort((a, b) => {
                return a.plane === b.plane ? 0 : a.plane === "lifted" ? -1 : 1;
            });
        for (const item of items) {
            const point = this.#pageAt(screen, item.plane,
                this.#elevationOf(item));
            if (point === undefined) {
                continue;
            }
            const rect = item.rect();
            for (const handle of HANDLES) {
                const x = rect.left + handle.fx * rect.width;
                const y = rect.top + handle.fy * rect.height;
                if (Math.abs(point.x - x) <= reach
                    && Math.abs(point.y - y) <= reach) {
                    return { item, handle };
                }
            }
        }

        return undefined;
    }

    /**
     * Where the menu buttons go: one for every selected item and the one
     * under the pointer, inside the item's menu band at its right, clear
     * of the corner handle, at a size usable on the screen. None for an
     * item too small on the screen to hold one; the right mouse button
     * still opens its menu.
     *
     * @returns The buttons.
     */
    #overlays(): ICanvasOverlay[] {
        const set = this.#set;
        if (set === undefined) {
            return [];
        }
        const scale = this.#scale();
        const ids = new Set(this.#selected);
        if (this.#hover !== undefined && this.#pointer === undefined) {
            ids.add(this.#hover);
        }
        const overlays: ICanvasOverlay[] = [];
        for (const id of ids) {
            const item = set.item(id);
            const band = item?.menuBand;
            if (item === undefined || band === undefined) {
                continue;
            }
            const rect = item.rect();
            // Whole device pixels, so that the bars drawn in it line up.
            const ratio = globalThis.devicePixelRatio || 1;
            const size = Math.round(band.height * scale * MENU_BUTTON_SHARE
                * ratio) / ratio;
            if (size < MENU_BUTTON_MIN_PX || rect.width * scale < size * 3
                || rect.height * scale < size) {
                continue;
            }
            // The button's square on the page, its right edge at a fixed
            // distance from the item's in page units: the same place on
            // the item at every zoom.
            const side = size / scale;
            const inset = (HANDLE_PX / 2 + 4) / pixelsPerUnit(
                MENU_BUTTON_REFERENCE_ZOOM, this.#unitsPerMm());
            const right = rect.left + rect.width - inset;
            const square = {
                left: right - side,
                top: rect.top + band.top + (band.height - side) / 2,
                width: side,
                height: side,
            };
            const corners = this.#screenCorners(square, item.plane,
                this.#elevationOf(item));
            // Never under the Navigator, whose overview area is clear.
            if (this.#navigatorPanel !== undefined && overlapsRect(
                boundsOf(corners), this.#navigatorPanel)) {
                continue;
            }
            const [topLeft, topRight, , bottomLeft] = corners;
            overlays.push({
                id,
                size,
                // In the 2D view a plain move to whole pixels, so the
                // bars stay crisp; laid onto the item in a 3D one.
                transform: this.#viewT === 0
                    ? `translate(${Math.round(topLeft.x * ratio) / ratio}px, `
                        + `${Math.round(topLeft.y * ratio) / ratio}px)`
                    : affineTransform(topLeft, topRight, bottomLeft, size,
                        size),
                color: band.color,
            });
        }

        return overlays;
    }

    // --- Editing in place -------------------------------------------------

    #startEdit(spec: IEditSpec): void {
        const placement = this.#editPlacement(spec);
        if (placement === undefined) {
            return;
        }
        this.#edit = spec;
        this.#reported.set("placement", JSON.stringify(placement));
        this.#events.onEditRequest({
            text: spec.text,
            label: spec.label,
            bold: spec.bold,
            placement,
        });
    }

    /**
     * Where the input over the text goes now: sized as the text is in the
     * 2D view at the zoom, laid onto its plane by a transform.
     *
     * @param spec The text.
     *
     * @returns The placement, or nothing where the text is gone.
     */
    #editPlacement(spec: IEditSpec): IEditPlacement | undefined {
        const area = spec.area();
        if (area === undefined) {
            return undefined;
        }
        const scale = this.#scale();
        const width = area.width * scale;
        const height = area.height * scale;

        return {
            width,
            height,
            fontSize: spec.fontSize * scale,
            transform: this.#transformOnto(area, spec.plane,
                spec.elevation?.() ?? 0, width, height),
        };
    }

    // --- Input ------------------------------------------------------------

    #canvasPoint(event: MouseEvent): IPoint {
        const bounds = this.#renderer.domElement.getBoundingClientRect();

        return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    }

    #itemAt(screen: IPoint): ICanvasItem | undefined {
        return this.#set?.itemAt(this.#probe(screen));
    }

    #panning(): boolean {
        return this.#tool === "hand" || this.#keys.has(" ");
    }

    /**
     * Whether a click zooms: the zoom tool, Z held, or Space + Cmd/Ctrl
     * (the classic scheme, where macOS leaves it to the browser).
     */
    #zooming(): boolean {
        return this.#tool === "zoom" || this.#keys.has("z")
            || (this.#keys.has(" ") && this.#modifiers.meta);
    }

    #updateCursor(): void {
        const canvas = this.#renderer.domElement;
        const pointer = this.#pointer;
        if (pointer?.kind === "pan" || (pointer?.kind === "orbit"
            && pointer.from !== undefined)) {
            canvas.style.cursor = "grabbing";
        } else if (this.#zooming()) {
            // Before the pan: Space + Cmd zooms although Space pans.
            canvas.style.cursor = this.#modifiers.alt ? "zoom-out" : "zoom-in";
        } else if (this.#panning()) {
            canvas.style.cursor = "grab";
        } else if (pointer?.kind === "resize") {
            canvas.style.cursor = pointer.handle.cursor;
        } else {
            canvas.style.cursor = this.#hoverCursor ?? "default";
        }
    }

    /**
     * Takes the modifiers off an event, and puts the cursor right where
     * they changed.
     *
     * @param event Any keyboard, pointer or wheel event.
     */
    #syncModifiers(event: KeyboardEvent | MouseEvent): void {
        const meta = event.metaKey || event.ctrlKey;
        if (meta !== this.#modifiers.meta || event.altKey !== this.#modifiers.alt) {
            this.#modifiers = { meta, alt: event.altKey };
            this.#updateCursor();
        }
    }

    #pointerEnter = (event: PointerEvent): void => {
        this.#syncModifiers(event);
    };

    #pointerDown = (event: PointerEvent): void => {
        this.#syncModifiers(event);
        if (event.button !== 0) {
            return;
        }
        const screen = this.#canvasPoint(event);
        this.#renderer.domElement.setPointerCapture(event.pointerId);

        if (this.#zooming()) {
            this.#zoomAt(screen, event.altKey ? 1 / 1.25 : 1.25);

            return;
        }

        if (this.#panning()) {
            this.#pointer = { kind: "pan", last: screen };
            this.#updateCursor();

            return;
        }

        // Cmd + drag orbits; Cmd + click toggles the item under it.
        if (event.metaKey || event.ctrlKey) {
            this.#pointer = {
                kind: "orbit",
                start: screen,
                itemId: this.#itemAt(screen)?.id,
            };

            return;
        }

        const grip = this.#handleAt(screen);
        const set = this.#set;
        if (grip !== undefined && set !== undefined) {
            const from = this.#pageAt(screen, grip.item.plane,
                this.#elevationOf(grip.item));
            if (from !== undefined) {
                const before = set.snapshot();
                this.#pointer = {
                    kind: "resize",
                    handle: grip.handle,
                    item: grip.item,
                    from,
                    rect: grip.item.rect(),
                    session: set.beginResize(grip.item.id),
                    before,
                };
                this.#updateCursor();

                return;
            }
        }

        const item = this.#itemAt(screen);
        if (item === undefined) {
            this.#select([], false);

            return;
        }
        // Option + drag raises or lowers an item that can stand higher,
        // where heights show: in the 3D view.
        if (event.altKey && item.elevatable === true && this.#viewT > 0
            && !this.#locked && set?.beginElevate !== undefined) {
            this.#select([item.id], false);
            const before = set.snapshot();
            this.#pointer = {
                kind: "elevate",
                last: screen,
                session: set.beginElevate(item.id),
                before,
            };

            return;
        }
        if (event.shiftKey) {
            this.#select([item.id], true);
        } else if (!this.#selected.has(item.id)) {
            this.#select([item.id], false);
        }
        if (!this.#locked && set !== undefined && this.#selected.has(item.id)) {
            this.#pointer = {
                kind: "move",
                last: screen,
                start: screen,
                plane: item.plane,
                elevation: this.#elevationOf(item),
                before: set.snapshot(),
            };
        }
    };

    #pointerMove = (event: PointerEvent): void => {
        this.#syncModifiers(event);
        const pointer = this.#pointer;
        const screen = this.#canvasPoint(event);
        if (pointer === undefined) {
            const cursor = this.#handleAt(screen)?.handle.cursor;
            if (cursor !== this.#hoverCursor) {
                this.#hoverCursor = cursor;
                this.#updateCursor();
            }
            const hover = this.#itemAt(screen)?.id;
            if (hover !== this.#hover) {
                this.#hover = hover;
                this.#invalidate();
            }

            return;
        }

        switch (pointer.kind) {
            case "pan": {
                this.#panByPixels(pointer.last.x - screen.x,
                    pointer.last.y - screen.y);
                pointer.last = screen;
                break;
            }

            case "orbit": {
                this.#orbit(pointer, screen);
                break;
            }

            case "resize": {
                this.#resizeTo(pointer, screen);
                break;
            }

            case "elevate": {
                this.#elevateTo(pointer, screen);
                break;
            }

            default: {
                this.#moveTo(pointer, screen);
            }
        }
    };

    /**
     * Orbits the view while Cmd is held: once the pointer has moved far
     * enough, from the 3D view's angles - or from straight above, where
     * the view was 2D, which turns it 3D at once.
     */
    #orbit(
        pointer: Extract<PointerState, { kind: "orbit" }>,
        screen: IPoint,
    ): void {
        const dx = screen.x - pointer.start.x;
        const dy = screen.y - pointer.start.y;
        if (pointer.from === undefined) {
            if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) {
                return;
            }
            for (const id of [this.#animation, this.#isoAnimation]) {
                if (id !== undefined) {
                    cancelAnimationFrame(id);
                }
            }
            this.#animation = undefined;
            this.#isoAnimation = undefined;
            pointer.from = this.#viewMode === "2d"
                ? { tilt: MAX_TILT, turn: 0 }
                : { ...this.#isoAngles };
            if (this.#viewMode === "2d") {
                this.#viewMode = "iso";
                this.#events.onViewMode?.("iso");
            }
            this.#viewT = 1;
            this.#updateCursor();
        }
        const angles = orbitAngles(pointer.from, dx, dy);
        this.#isoAngles = { tilt: angles.tilt, turn: angles.turn };
        this.#isoView.copy(isoQuaternion(angles));
        this.#applyView();
        this.#invalidate();
        this.#events.onIsoAngles?.(angles, false);
    }

    #moveTo(
        pointer: Extract<PointerState, { kind: "move" }>,
        screen: IPoint,
    ): void {
        if (pointer.session === undefined) {
            if (Math.hypot(screen.x - pointer.start.x,
                screen.y - pointer.start.y) < DRAG_THRESHOLD_PX) {
                return;
            }
            pointer.session = this.#set?.beginMove([...this.#selected]);
        }
        const from = this.#pageAt(pointer.last, pointer.plane,
            pointer.elevation);
        const to = this.#pageAt(screen, pointer.plane, pointer.elevation);
        if (from !== undefined && to !== undefined) {
            pointer.session?.moveBy(to.x - from.x, to.y - from.y);
            this.#invalidate();
        }
        pointer.last = screen;
    }

    /**
     * Raises or lowers an item with the pointer's movement up or down the
     * screen: a page unit up shows as cos(tilt) of a unit on the screen,
     * at the lift reached so far.
     */
    #elevateTo(
        pointer: Extract<PointerState, { kind: "elevate" }>,
        screen: IPoint,
    ): void {
        const perUnit = this.#scale() * Math.max(0.2,
            Math.cos(this.#isoAngles.tilt)) * Math.max(0.1, this.#lift());
        pointer.session.elevateBy((pointer.last.y - screen.y) / perUnit);
        pointer.last = screen;
        this.#invalidate();
    }

    /**
     * Resizes an item to where a handle was dragged: the edges the handle
     * does not hold stay put, and the item keeps its least size.
     */
    #resizeTo(
        pointer: Extract<PointerState, { kind: "resize" }>,
        screen: IPoint,
    ): void {
        const at = this.#pageAt(screen, pointer.item.plane,
            this.#elevationOf(pointer.item));
        if (at === undefined) {
            return;
        }
        const dx = at.x - pointer.from.x;
        const dy = at.y - pointer.from.y;
        const { handle, rect, item } = pointer;
        let { left, top, width, height } = rect;
        if (handle.fx === 0) {
            width = Math.max(item.minWidth, rect.width - dx);
            left = rect.left + rect.width - width;
        } else if (handle.fx === 1) {
            width = Math.max(item.minWidth, rect.width + dx);
        }
        if (handle.fy === 0) {
            height = Math.max(item.minHeight, rect.height - dy);
            top = rect.top + rect.height - height;
        } else if (handle.fy === 1) {
            height = Math.max(item.minHeight, rect.height + dy);
        }
        pointer.session.resizeTo({ left, top, width, height }, handle);
        this.#invalidate();
    }

    #pointerUp = (event: PointerEvent): void => {
        const pointer = this.#pointer;
        if (pointer === undefined) {
            return;
        }
        this.#renderer.domElement.releasePointerCapture(event.pointerId);
        this.#pointer = undefined;
        switch (pointer.kind) {
            case "orbit": {
                if (pointer.from === undefined) {
                    if (pointer.itemId !== undefined) {
                        this.#select([pointer.itemId], true);
                    }
                } else {
                    this.#events.onIsoAngles?.(customPreset(
                        this.#isoAngles.tilt, this.#isoAngles.turn), true);
                }
                break;
            }

            case "move": {
                if (pointer.session !== undefined) {
                    this.#recordSnapshot(pointer.session.finish(),
                        pointer.before);
                }
                break;
            }

            case "resize":
            case "elevate": {
                this.#recordSnapshot(pointer.session.finish(), pointer.before);
                break;
            }

            default:
        }
        this.#updateCursor();
        this.#invalidate();
    };

    #contextMenu = (event: MouseEvent): void => {
        event.preventDefault();
        const screen = this.#canvasPoint(event);
        const item = this.#itemAt(screen);
        if (item === undefined) {
            return;
        }
        if (!this.#selected.has(item.id)) {
            this.#select([item.id], false);
        }
        this.#events.onContextMenu?.(item.id, screen.x, screen.y);
    };

    #doubleClick = (event: MouseEvent): void => {
        if (this.#locked || this.#panning() || this.#tool !== "pointer") {
            return;
        }
        const screen = this.#canvasPoint(event);
        const item = this.#itemAt(screen);
        if (item !== undefined) {
            this.#set?.doubleClick?.(item.id, this.#probe(screen));
        }
    };

    #wheel = (event: WheelEvent): void => {
        event.preventDefault();
        this.#syncModifiers(event);
        // A pinch on a trackpad arrives as a wheel with ctrlKey set.
        if (event.ctrlKey || event.metaKey) {
            const factor = Math.exp(-event.deltaY * 0.01);
            this.#zoomAt(this.#canvasPoint(event), factor);
        } else {
            this.#panByPixels(event.deltaX, event.deltaY);
        }
    };

    #keyDown = (event: KeyboardEvent): void => {
        this.#syncModifiers(event);
        const target = event.target as HTMLElement | null;
        if (target !== null && (target.tagName === "INPUT"
            || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) {
            return;
        }
        // Undo and Redo: Cmd+Z / Cmd+Shift+Z on macOS, Ctrl elsewhere.
        // An input in focus keeps them for its own text (returned above).
        const primary = IS_MAC ? event.metaKey : event.ctrlKey;
        if (primary && !event.altKey && event.key.toLowerCase() === "z") {
            event.preventDefault();
            if (event.shiftKey) {
                this.redo();
            } else {
                this.undo();
            }

            return;
        }
        const meta = event.metaKey || event.ctrlKey;
        if (meta && event.key === "0") {
            event.preventDefault();
            this.fit();

            return;
        }
        if (meta && event.key === "1") {
            event.preventDefault();
            this.zoomTo(1);

            return;
        }
        const key = event.key.toLowerCase();
        if (key === " ") {
            // Keeps the page from scrolling.
            event.preventDefault();
        }
        if ((key === " " || key === "z") && !this.#keys.has(key)) {
            this.#keys.add(key);
            this.#updateCursor();
        }
    };

    #keyUp = (event: KeyboardEvent): void => {
        this.#syncModifiers(event);
        const key = event.key.toLowerCase();
        if (key === "meta" || key === "control") {
            // A key released while Cmd was down sent no keyup of its own.
            this.#keys.clear();
        } else {
            this.#keys.delete(key);
        }
        this.#updateCursor();
    };

    #blur = (): void => {
        this.#keys.clear();
        this.#modifiers = { meta: false, alt: false };
        this.#updateCursor();
    };
}
