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
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry }
    from "three/examples/jsm/lines/LineSegmentsGeometry.js";

import type {
    IModelConnection,
    IModelDiagram,
    IModelFigure,
    IModelLayer,
} from "../../../src/modeler/modelFormat.js";
import type { IRect } from "../../../src/modeler/pageSettings.js";
import type {
    ICanvasHost,
    ICanvasItem,
    IEditSpec,
    IElevateSession,
    IFigureSet,
    IMenuEntry,
    IMoveSession,
    IProbe,
    IResizeSession,
} from "../canvas/canvasTypes.js";
import type { IPoint, Segment } from "../canvas/geometry.js";
import {
    disposeGroup,
    DRAW_ORDER,
    planeCenter,
    polylineSegments,
    roundedRect,
    setDrawOrder,
} from "../canvas/threeHelpers.js";
import type { ITableCatalog } from "./catalog.js";
import {
    crowsFoot,
    oneMarker,
    routeConnection,
    splitStubs,
} from "./connectionGeometry.js";
import {
    containsPoint,
    containsRect,
    fitAround,
    layerAtPoint,
    layerRect,
    layerUnder,
    MIN_LAYER_HEIGHT,
    MIN_LAYER_WIDTH,
} from "./layerRules.js";
import {
    CARD,
    cardItemAt,
    contrastingText,
    drawLabel,
    drawTableCard,
    editRect,
    layoutTableCard,
    MIN_FIGURE_WIDTH,
    type CardIcons,
    type ICardItem,
    type ICardLayout,
} from "./tableCard.js";

/**
 * The EER diagram as a figure set of the canvas: layers on the page, the
 * tables (Workbench's table figures) on them rising in the 3D view, and
 * the foreign keys between the tables in the crow's foot notation.
 *
 * One set per model: it shows one of the model's diagrams at a time
 * (`show`), and keeps the model's history across them, since a table
 * renamed on one diagram is renamed on all.
 *
 * The rules of tables and layers are Workbench's (see `layerRules.ts`): a
 * table lies on the smallest layer it lies fully inside, and is filed
 * under it with its position relative to it. Moving a layer moves every
 * table lying fully on it; raising one (Option + drag, 3D) raises the
 * tables filed under it.
 */

/**
 * How wide the connections are drawn, in CSS pixels. Fat lines: WebGL's
 * own are a pixel wide whatever is asked.
 */
const CONNECTION_WIDTH = 0.8;

/** How thick a card is: the slab under it, in page units. */
const CARD_THICKNESS = 8;

/** How far a card's slab lies inside the card's edge, in page units. */
const SLAB_INSET = 1;

/** How far a card's shadow reaches past the card on each side. */
const SHADOW_SPREAD = 2;

/** The corner radius of a layer: a little less than a card's. */
const LAYER_RADIUS = 5;

/** A layer's name: where it sits, in page units from the layer's corner. */
const LAYER_LABEL = { left: 6, top: 4, height: 18, fontSize: 12 } as const;

/** How high a layer can be raised, in page units. */
export const MAX_LAYER_ELEVATION = 240;

/** The texture scales a card is drawn at: device pixels per unit. */
const RASTER_STEPS = [0.25, 0.5, 1, 2, 3, 4];

/** The colour of a figure that has none. */
const DEFAULT_FIGURE_COLOR = "#98BFDA";

/**
 * Where the light comes from that shades the slabs' sides, in the plane's
 * coordinates (x right, y up the page, z towards the 2D camera): from the
 * upper left, so the left sides are lit and the front ones, facing the 3D
 * camera at the page's bottom, are in shade.
 */
const SLAB_LIGHT = new THREE.Vector3(-0.7, 0.3, 0.65).normalize();

/**
 * A table's shadow: a rounded rectangle around the card's, centred on the
 * origin, its corners concentric with the card's rounded ones.
 *
 * @param width The card's width.
 * @param height The card's height.
 *
 * @returns The geometry.
 */
const shadowGeometry = (width: number, height: number): THREE.BufferGeometry => {
    const w = width + 2 * SHADOW_SPREAD;
    const h = height + 2 * SHADOW_SPREAD;

    return new THREE.ShapeGeometry(
        roundedRect(-w / 2, -h / 2, w, h, CARD.radius + SHADOW_SPREAD), 6);
};

/**
 * A card's slab: the card's rounded outline extruded downwards, centred on
 * the origin with its top at z = 0, its sides shaded by the way they face.
 * The canvas's materials are unlit, so the shading is baked into vertex
 * colours.
 *
 * @param width The card's width.
 * @param height The card's height.
 * @param color The slab's colour, fully lit.
 *
 * @returns The geometry.
 */
const slabGeometry = (
    width: number,
    height: number,
    color: THREE.Color,
): THREE.BufferGeometry => {
    // Inset a unit, inside the card's border: the card's anti-aliased
    // corner pixels are partly clear, and a slab as large as the card
    // showed through them as a light fringe outside the border.
    const geometry = new THREE.ExtrudeGeometry(
        roundedRect(-width / 2 + SLAB_INSET, -height / 2 + SLAB_INSET,
            width - 2 * SLAB_INSET, height - 2 * SLAB_INSET,
            CARD.radius - SLAB_INSET),
        { depth: CARD_THICKNESS, bevelEnabled: false, curveSegments: 8 });
    geometry.translate(0, 0, -CARD_THICKNESS);
    geometry.computeVertexNormals();
    const normals = geometry.getAttribute("normal");
    const colors = new Float32Array(normals.count * 3);
    const normal = new THREE.Vector3();
    for (let index = 0; index < normals.count; index += 1) {
        normal.fromBufferAttribute(normals, index);
        const light = 0.55 + 0.45 * Math.max(0, normal.dot(SLAB_LIGHT));
        colors.set([color.r * light, color.g * light, color.b * light],
            index * 3);
    }
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    return geometry;
};

/**
 * A colour laid over another at an opacity, mixed in sRGB as the
 * framebuffer blends a transparent material.
 *
 * @param color The colour on top.
 * @param under The colour under it.
 * @param opacity How much of the top colour shows.
 *
 * @returns The mixed colour.
 */
const tintOver = (
    color: THREE.Color,
    under: THREE.Color,
    opacity: number,
): THREE.Color => {
    const top = color.getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);
    const base = under.getRGB({ r: 0, g: 0, b: 0 }, THREE.SRGBColorSpace);
    const mix = (a: number, b: number): number => {
        return b + (a - b) * opacity;
    };

    return new THREE.Color().setRGB(mix(top.r, base.r), mix(top.g, base.g),
        mix(top.b, base.b), THREE.SRGBColorSpace);
};

/**
 * Fat line geometry for segments whose height varies along them: each
 * point at its own z.
 *
 * @param segments The segments, with a height per end.
 *
 * @returns The geometry.
 */
const sloped = (
    segments: Array<[IPoint & { z: number }, IPoint & { z: number }]>,
): LineSegmentsGeometry => {
    const positions = new Float32Array(segments.length * 6);
    segments.forEach(([from, to], index) => {
        positions.set([from.x, -from.y, from.z, to.x, -to.y, to.z], index * 6);
    });

    return new LineSegmentsGeometry().setPositions(positions);
};

/** What a user can change of a table figure. */
type FigureState = Pick<IModelFigure, "left" | "top" | "width" | "height"
    | "manualSizing" | "expanded" | "indicesExpanded"
    | "foreignKeysExpanded" | "layer">;

/** What a user can change of a layer. */
type LayerState = Pick<IModelLayer, "left" | "top" | "width" | "height"
    | "name" | "elevation">;

/** The shown diagram's figures and layers, as they are. */
interface ISnapshot {
    figures: Array<[IModelFigure, FigureState]>;
    layers: Array<[IModelLayer, LayerState]>;
}

const figureState = (figure: IModelFigure): FigureState => {
    return {
        left: figure.left,
        top: figure.top,
        width: figure.width,
        height: figure.height,
        manualSizing: figure.manualSizing,
        expanded: figure.expanded,
        indicesExpanded: figure.indicesExpanded,
        foreignKeysExpanded: figure.foreignKeysExpanded,
        layer: figure.layer,
    };
};

const layerState = (layer: IModelLayer): LayerState => {
    return {
        left: layer.left,
        top: layer.top,
        width: layer.width,
        height: layer.height,
        name: layer.name,
        elevation: layer.elevation,
    };
};

/**
 * @param a A state.
 * @param b Another of the same kind.
 *
 * @returns Whether they are the same.
 */
const sameState = <T extends object>(a: T, b: T): boolean => {
    return (Object.keys(a) as Array<keyof T>).every((key) => {
        return a[key] === b[key];
    });
};

/** A table on the canvas. */
interface ITableNode {
    figure: IModelFigure;
    /** The layer the table is filed under, if any. */
    layer?: IModelLayer;
    layout: ICardLayout;
    mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
    slab: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    shadow: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    /** The size the geometries were made for, so a move keeps them. */
    builtSize?: string;
    /** Device pixels per unit the texture was drawn at. */
    rasterScale: number;
    item: ICanvasItem;
}

/** A layer on the canvas. */
interface ILayerNode {
    layer: IModelLayer;
    plate: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    border: THREE.LineLoop<THREE.BufferGeometry, THREE.LineBasicMaterial>;
    label: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
    labelWidth: number;
    builtName?: string;
    builtSize?: string;
    item: ICanvasItem;
}

/** A foreign key on the canvas. */
interface IConnectionNode {
    connection: IModelConnection;
    start: ITableNode;
    end: ITableNode;
    line: LineSegments2;
    mandatory: boolean;
    referencedMandatory: boolean;
}

/** What the set needs to be made. */
export interface IEerFigureSetOptions {
    /** Where the tables' columns, keys and indices come from. */
    catalog: ITableCatalog;
    /** Every diagram of the model: a renamed table is renamed on all. */
    diagrams: IModelDiagram[];
    /** Loads the card icons in a colour. */
    loadIcons: (color: string) => Promise<CardIcons>;
}

export class EerFigureSet implements IFigureSet {
    readonly #options: IEerFigureSetOptions;
    #diagram: IModelDiagram;
    #host?: ICanvasHost;
    #icons?: CardIcons;
    #headerIcons = new Map<string, CardIcons>();
    #tables = new Map<string, ITableNode>();
    #layers = new Map<string, ILayerNode>();
    #connections: IConnectionNode[] = [];
    /** What this set drew into the canvas's groups, to remove it. */
    #base = new THREE.Group();
    #lifted = new THREE.Group();

    public constructor(options: IEerFigureSetOptions) {
        this.#options = options;
        this.#diagram = options.diagrams[0];
    }

    /** The diagram shown. */
    public get diagram(): IModelDiagram {
        return this.#diagram;
    }

    /**
     * Picks the diagram to show; the canvas's next `load` draws it.
     *
     * @param diagram One of the model's diagrams.
     */
    public show(diagram: IModelDiagram): void {
        this.#diagram = diagram;
    }

    // --- IFigureSet: building -----------------------------------------------

    public async mount(host: ICanvasHost): Promise<void> {
        this.#host = host;
        // The theme may have changed since the last mount.
        this.#icons = await this.#options.loadIcons(host.theme.foreground);
        this.#headerIcons.clear();
        host.base.add(this.#base);
        host.lifted.add(this.#lifted);
        for (const layer of this.#diagram.layers) {
            this.#buildLayer(layer);
        }
        for (const figure of this.#diagram.figures) {
            this.#buildTable(figure);
        }
        for (const connection of this.#diagram.connections) {
            this.#buildConnection(connection);
        }
        this.viewChanged();
        host.invalidate();
    }

    public unmount(): void {
        disposeGroup(this.#base);
        disposeGroup(this.#lifted);
        this.#base.removeFromParent();
        this.#lifted.removeFromParent();
        this.#tables.clear();
        this.#layers.clear();
        this.#connections = [];
        this.#host = undefined;
    }

    public extent(): { width: number; height: number } {
        return { width: this.#diagram.width, height: this.#diagram.height };
    }

    // --- Items ----------------------------------------------------------------

    public item(id: string): ICanvasItem | undefined {
        return this.#tables.get(id)?.item ?? this.#layers.get(id)?.item;
    }

    public itemAt(probe: IProbe): ICanvasItem | undefined {
        const table = this.#tableHit(probe);
        if (table !== undefined) {
            return table.node.item;
        }

        return this.#layerAt(probe)?.item;
    }

    /** The table card under the pointer, and the point on it. */
    #tableHit(
        probe: IProbe,
    ): { node: ITableNode; uv: THREE.Vector2 } | undefined {
        const meshes = [...this.#tables.values()].map((node) => {
            return node.mesh;
        });
        const hit = probe.raycaster.intersectObjects(meshes, false)[0];
        if (hit === undefined || hit.uv === undefined) {
            return undefined;
        }
        const node = this.#tables.get(
            (hit.object.userData as { figureId: string }).figureId);

        return node === undefined ? undefined : { node, uv: hit.uv };
    }

    /** The layer under the pointer: the inner one, at its own height. */
    #layerAt(probe: IProbe): ILayerNode | undefined {
        let found: ILayerNode | undefined;
        for (const node of this.#layers.values()) {
            const point = probe.pointOn("base", this.#elevation(node.layer));
            if (point !== undefined
                && layerAtPoint(point, [node.layer]) !== undefined
                && (found === undefined || node.layer.width * node.layer.height
                    < found.layer.width * found.layer.height)) {
                found = node;
            }
        }

        return found;
    }

    /** @returns A layer's elevation, kept in its range. */
    #elevation(layer: IModelLayer | undefined): number {
        return Math.min(MAX_LAYER_ELEVATION,
            Math.max(0, layer?.elevation ?? 0));
    }

    /** @returns Where a table is on the page. */
    #tableRect(node: ITableNode): IRect {
        return {
            left: node.figure.left + (node.layer?.left ?? 0),
            top: node.figure.top + (node.layer?.top ?? 0),
            width: node.layout.width,
            height: node.layout.height,
        };
    }

    /** @returns The layer with an id on the shown diagram. */
    #layerById(id: string | undefined): IModelLayer | undefined {
        return id === undefined ? undefined : this.#layers.get(id)?.layer;
    }

    // --- Layers -----------------------------------------------------------

    #buildLayer(layer: IModelLayer): void {
        const theme = this.#host!.theme;
        const color = new THREE.Color(layer.color || "#e0e0e0");
        // Solid, so that the grid lies under the layer rather than showing
        // through it: the layer's tint over the paper, mixed the way the
        // see-through plate it replaces was blended.
        const plate = new THREE.Mesh(new THREE.BufferGeometry(),
            new THREE.MeshBasicMaterial({
                color: tintOver(color, new THREE.Color(theme.paper),
                    theme.dark ? 0.35 : 0.6),
            }));
        const border = new THREE.LineLoop(new THREE.BufferGeometry(),
            new THREE.LineBasicMaterial({
                color, transparent: true, opacity: 0.9,
            }));
        const label = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
            new THREE.MeshBasicMaterial({ transparent: true }));
        setDrawOrder(plate, DRAW_ORDER.base);
        // A layer's border and name go over its plate.
        setDrawOrder(border, DRAW_ORDER.base + 0.5);
        setDrawOrder(label, DRAW_ORDER.base + 0.5);
        this.#base.add(plate, border, label);

        const node: ILayerNode = {
            layer, plate, border, label, labelWidth: 0,
            item: {
                id: layer.id,
                plane: "base",
                rect: () => { return layerRect(layer); },
                resizable: true,
                minWidth: MIN_LAYER_WIDTH,
                minHeight: MIN_LAYER_HEIGHT,
                elevation: () => { return this.#elevation(layer); },
                elevatable: true,
                menuBand: {
                    top: LAYER_LABEL.top,
                    height: LAYER_LABEL.height,
                    color: theme.foreground,
                },
            },
        };
        this.#layers.set(layer.id, node);
        this.#placeLayer(node);
    }

    /** Puts a layer's plate, border and name where the layer is. */
    #placeLayer(node: ILayerNode): void {
        const { layer } = node;
        const size = `${layer.width}x${layer.height}`;
        if (node.builtSize !== size) {
            node.builtSize = size;
            const shape = roundedRect(-layer.width / 2, -layer.height / 2,
                layer.width, layer.height, LAYER_RADIUS);
            node.plate.geometry.dispose();
            node.plate.geometry = new THREE.ShapeGeometry(shape, 6);
            node.border.geometry.dispose();
            node.border.geometry = new THREE.BufferGeometry()
                .setFromPoints(shape.getPoints(6));
        }
        if (node.builtName !== layer.name) {
            node.builtName = layer.name;
            const label = drawLabel(layer.name,
                `bold ${LAYER_LABEL.fontSize}px ${this.#host!.theme.fontFamily}`,
                this.#host!.theme.foreground,
                Math.max(1, window.devicePixelRatio) * 2, LAYER_LABEL.height);
            const texture = new THREE.CanvasTexture(label.canvas);
            texture.colorSpace = THREE.SRGBColorSpace;
            node.label.material.map?.dispose();
            node.label.material.map = texture;
            node.label.material.needsUpdate = true;
            node.label.geometry.dispose();
            node.label.geometry = new THREE.PlaneGeometry(
                Math.max(1, label.width), LAYER_LABEL.height);
            node.labelWidth = label.width;
        }
        const center = planeCenter(layerRect(layer));
        const z = this.#elevation(layer) * (this.#host?.lift() ?? 0);
        node.plate.position.set(center.x, center.y,
            z - 1 + this.#stackHeight(layer));
        node.border.position.set(center.x, center.y, z - 0.9);
        node.label.position.set(
            layer.left + LAYER_LABEL.left + node.labelWidth / 2,
            -(layer.top + LAYER_LABEL.top + LAYER_LABEL.height / 2), z - 0.8);
    }

    /**
     * How far a layer's plate lies above the others of its height: the
     * plates are solid, and two in one plane would fight over each pixel,
     * so the smaller a layer, the higher - an inner layer always over the
     * one around it. Small enough to stay under the borders and names.
     *
     * @param layer The layer.
     *
     * @returns The offset, in page units.
     */
    #stackHeight(layer: IModelLayer): number {
        const area = layer.width * layer.height;
        const larger = this.#diagram.layers.filter((other) => {
            return other.width * other.height > area;
        }).length;

        return larger * 0.003;
    }

    /**
     * Files a table under the layer it lies on now, keeping its place on
     * the page.
     *
     * @param node The table.
     */
    #fileUnderLayer(node: ITableNode): void {
        const rect = this.#tableRect(node);
        const target = layerUnder(rect, this.#diagram.layers);
        if (target?.id === node.figure.layer) {
            return;
        }
        node.figure.left = rect.left - (target?.left ?? 0);
        node.figure.top = rect.top - (target?.top ?? 0);
        node.figure.layer = target?.id;
        node.layer = target;
        this.#placeTable(node);
    }

    /**
     * @param layer A layer.
     *
     * @returns The tables lying fully on it, whatever they are filed under.
     */
    #tablesOn(layer: IModelLayer): ITableNode[] {
        const rect = layerRect(layer);

        return [...this.#tables.values()].filter((node) => {
            return containsRect(rect, this.#tableRect(node));
        });
    }

    // --- Tables -----------------------------------------------------------

    #buildTable(figure: IModelFigure): void {
        const host = this.#host!;
        const table = this.#options.catalog.table(figure.table ?? figure.name);
        const layout = layoutTableCard(table, figure);
        keepAutoHeight(figure, layout);
        const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
            new THREE.MeshBasicMaterial({ transparent: true }));
        mesh.userData = { figureId: figure.id };
        const slab = new THREE.Mesh(new THREE.BufferGeometry(),
            new THREE.MeshBasicMaterial({ vertexColors: true }));
        // The card is drawn after every slab, so that a slab in front
        // hides a card behind it, but no slab its own card.
        setDrawOrder(slab, DRAW_ORDER.lifted);
        setDrawOrder(mesh, DRAW_ORDER.lifted + 0.5);
        this.#lifted.add(slab, mesh);

        const shadow = new THREE.Mesh(new THREE.BufferGeometry(),
            new THREE.MeshBasicMaterial({
                color: host.theme.shadow,
                transparent: true,
                opacity: 0.18,
                depthWrite: false,
            }));
        setDrawOrder(shadow, DRAW_ORDER.shadows);
        this.#base.add(shadow);

        const color = figure.color || DEFAULT_FIGURE_COLOR;
        const node: ITableNode = {
            figure,
            layer: this.#layerById(figure.layer),
            layout,
            mesh,
            slab,
            shadow,
            rasterScale: 0,
            item: {
                id: figure.id,
                plane: "lifted",
                rect: () => { return this.#tableRect(node); },
                resizable: true,
                minWidth: MIN_FIGURE_WIDTH,
                minHeight: CARD.headerHeight,
                elevation: () => { return this.#elevation(node.layer); },
                menuBand: {
                    top: 0,
                    height: CARD.headerHeight,
                    color: contrastingText(color),
                },
            },
        };
        this.#tables.set(figure.id, node);
        this.#placeTable(node);
        void this.#raster(node);
    }

    /** Puts a table's card, slab and shadow where its rectangle is. */
    #placeTable(node: ITableNode): void {
        const rect = this.#tableRect(node);
        const center = planeCenter(rect);

        // The shapes depend on the size alone; a move only moves them.
        const size = `${rect.width}x${rect.height}`;
        if (node.builtSize !== size) {
            node.builtSize = size;
            node.mesh.geometry.dispose();
            node.mesh.geometry = new THREE.PlaneGeometry(rect.width,
                rect.height);
            node.slab.geometry.dispose();
            node.slab.geometry = slabGeometry(rect.width, rect.height,
                new THREE.Color(node.figure.color || DEFAULT_FIGURE_COLOR));
            node.shadow.geometry.dispose();
            node.shadow.geometry = shadowGeometry(rect.width, rect.height);
        }

        const lift = this.#host?.lift() ?? 0;
        const z = this.#elevation(node.layer) * lift;
        node.mesh.position.set(center.x, center.y, z);
        // Just below the card, so the card's face wins the depth test.
        node.slab.position.set(center.x, center.y, z - 0.05);
        // Flat in the 2D view, growing as the tables rise; never quite
        // zero, which would leave the geometry degenerate.
        node.slab.scale.z = Math.max(0.001, lift);
        node.shadow.position.set(center.x + 1, center.y - 2, z - 0.5);
    }

    /** Lays a table out again, after a change to what it shows. */
    #relayout(node: ITableNode): void {
        const table = this.#options.catalog.table(node.figure.table
            ?? node.figure.name);
        node.layout = layoutTableCard(table, node.figure);
        keepAutoHeight(node.figure, node.layout);
        this.#placeTable(node);
        node.rasterScale = 0;
        void this.#raster(node);
        this.#routeAll();
    }

    /** The texture scale the zoom calls for. */
    #wantedRasterScale(): number {
        const needed = (this.#host?.scale() ?? 1) * window.devicePixelRatio;

        return RASTER_STEPS.find((step) => { return step >= needed; })
            ?? RASTER_STEPS[RASTER_STEPS.length - 1];
    }

    async #headerIconsFor(color: string): Promise<CardIcons> {
        let icons = this.#headerIcons.get(color);
        if (icons === undefined) {
            icons = await this.#options.loadIcons(color);
            this.#headerIcons.set(color, icons);
        }

        return icons;
    }

    /** Draws a table's card at the zoom's scale, where it is not yet. */
    async #raster(node: ITableNode): Promise<void> {
        const host = this.#host;
        const scale = this.#wantedRasterScale();
        if (host === undefined || node.rasterScale === scale
            || this.#icons === undefined) {
            return;
        }
        // In the header's TEXT colour: the figure's own colour is the
        // header's background, on which the icon would not show.
        const headerIcons = await this.#headerIconsFor(
            contrastingText(node.figure.color || DEFAULT_FIGURE_COLOR));
        if (this.#tables.get(node.figure.id) !== node) {
            return;
        }
        node.rasterScale = scale;
        const canvas = drawTableCard(node.layout, node.figure, host.theme,
            this.#icons, headerIcons, scale);
        const texture = new THREE.CanvasTexture(canvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = host.maxAnisotropy;
        node.mesh.material.map?.dispose();
        node.mesh.material.map = texture;
        node.mesh.material.needsUpdate = true;
        host.invalidate();
    }

    // --- Connections ----------------------------------------------------------

    #buildConnection(connection: IModelConnection): void {
        const start = this.#tables.get(connection.startFigure);
        const end = this.#tables.get(connection.endFigure);
        if (start === undefined || end === undefined) {
            return;
        }
        const foreignKey = this.#options.catalog.table(connection.table)
            ?.foreignKeys.find((candidate) => {
                return candidate.name === connection.foreignKey;
            });
        const line = new LineSegments2(new LineSegmentsGeometry(),
            new LineMaterial({
                color: new THREE.Color(this.#host!.theme.connection),
                linewidth: CONNECTION_WIDTH,
            }));
        line.visible = connection.visible !== false;
        setDrawOrder(line, DRAW_ORDER.connections);
        this.#lifted.add(line);
        const node: IConnectionNode = {
            connection, start, end, line,
            mandatory: foreignKey?.mandatory ?? true,
            referencedMandatory: foreignKey?.referencedMandatory ?? true,
        };
        this.#connections.push(node);
        this.#route(node);
    }

    /**
     * Draws a foreign key along its route, sloping from the height of the
     * table it starts at to that of the one it ends at.
     */
    #route(node: IConnectionNode): void {
        const route = routeConnection(this.#tableRect(node.start),
            this.#tableRect(node.end));
        const lift = this.#host?.lift() ?? 0;
        const startZ = this.#elevation(node.start.layer) * lift + 0.2;
        const endZ = this.#elevation(node.end.layer) * lift + 0.2;
        const path = node.connection.drawSplit === true
            ? splitStubs(route)
            : polylineSegments(route.points);
        // Each end at the height of its own table; only the middle segment
        // spans the difference (a split connection's stubs have none).
        const middle = Math.floor((path.length - 1) / 2);
        const segments: Array<[IPoint & { z: number }, IPoint & { z: number }]> =
            node.connection.drawSplit === true
                ? [
                    [{ ...path[0][0], z: startZ }, { ...path[0][1], z: startZ }],
                    [{ ...path[1][0], z: endZ }, { ...path[1][1], z: endZ }],
                ]
                : path.map(([from, to], index) => {
                    const a = index <= middle ? startZ : endZ;
                    const b = index < middle ? startZ : endZ;

                    return [{ ...from, z: a }, { ...to, z: b }];
                });
        const at = (z: number) => {
            return ([from, to]: Segment): [IPoint & { z: number },
                IPoint & { z: number }] => {
                return [{ ...from, z }, { ...to, z }];
            };
        };
        segments.push(
            ...crowsFoot(route.points[0], route.startSide, node.mandatory)
                .map(at(startZ)),
            ...oneMarker(route.points[route.points.length - 1], route.endSide,
                node.referencedMandatory).map(at(endZ)),
        );
        node.line.geometry.dispose();
        node.line.geometry = sloped(segments);
    }

    #routeAll(): void {
        for (const node of this.#connections) {
            this.#route(node);
        }
    }

    // --- IFigureSet: the view -----------------------------------------------

    public viewChanged(): void {
        // Fat lines are drawn against the canvas's size, which may have
        // changed with the view.
        const viewport = this.#host?.viewport();
        if (viewport !== undefined) {
            for (const node of this.#connections) {
                node.line.material.resolution.set(viewport.width,
                    viewport.height);
            }
        }
        for (const node of this.#layers.values()) {
            this.#placeLayer(node);
        }
        for (const node of this.#tables.values()) {
            this.#placeTable(node);
        }
        this.#routeAll();
    }

    public zoomSettled(): void {
        for (const node of this.#tables.values()) {
            void this.#raster(node);
        }
    }

    // --- IFigureSet: moving, resizing, raising ------------------------------

    public beginMove(ids: readonly string[]): IMoveSession {
        const layers = ids.map((id) => { return this.#layers.get(id); })
            .filter((node): node is ILayerNode => { return node !== undefined; });
        const tables = ids.map((id) => { return this.#tables.get(id); })
            .filter((node): node is ITableNode => { return node !== undefined; });
        // What moves: the selected tables, and every table lying fully on
        // a selected layer.
        const carried = new Set(tables);
        for (const layer of layers) {
            for (const node of this.#tablesOn(layer.layer)) {
                carried.add(node);
            }
        }
        const moving = new Set(layers.map((node) => { return node.layer.id; }));

        return {
            moveBy: (dx, dy) => {
                for (const node of layers) {
                    node.layer.left += dx;
                    node.layer.top += dy;
                    this.#placeLayer(node);
                }
                for (const node of this.#tables.values()) {
                    // A table's position is its layer's plus its own: one
                    // carried along keeps its layer's share, one whose
                    // layer moves without it gives that share back.
                    const isCarried = carried.has(node);
                    const layerMoves = node.figure.layer !== undefined
                        && moving.has(node.figure.layer);
                    const share = (isCarried ? 1 : 0) - (layerMoves ? 1 : 0);
                    if (share !== 0) {
                        node.figure.left += share * dx;
                        node.figure.top += share * dy;
                    }
                    if (isCarried || layerMoves) {
                        this.#placeTable(node);
                    }
                }
                this.#routeAll();
            },
            finish: () => {
                for (const node of carried) {
                    this.#fileUnderLayer(node);
                }
                this.#routeAll();

                if (layers.length === 1 && tables.length === 0) {
                    return `Move Layer ${layers[0].layer.name}`;
                }
                if (tables.length === 1 && layers.length === 0) {
                    return `Move ${tables[0].figure.name}`;
                }

                return layers.length === 0
                    ? `Move ${tables.length} Tables`
                    : `Move ${layers.length + tables.length} Items`;
            },
        };
    }

    public beginResize(id: string): IResizeSession {
        const layer = this.#layers.get(id);
        if (layer !== undefined) {
            return this.#resizeLayer(layer);
        }
        const node = this.#tables.get(id)!;

        return {
            resizeTo: (rect, handle) => {
                const figure = node.figure;
                figure.left = rect.left - (node.layer?.left ?? 0);
                figure.top = rect.top - (node.layer?.top ?? 0);
                figure.width = rect.width;
                // The top or bottom edge sizes the height by hand from
                // then on; the left and right edges the width alone.
                if (handle.fy !== 0.5) {
                    figure.manualSizing = true;
                    figure.height = rect.height;
                }
                this.#relayout(node);
            },
            finish: () => {
                this.#fileUnderLayer(node);
                this.#routeAll();

                return `Resize ${node.figure.name}`;
            },
        };
    }

    /**
     * A layer's resize: the tables filed under it keep their places on the
     * page as its corner moves, and are filed again at the end - under it,
     * where they now lie fully on it, or elsewhere where they no longer do.
     */
    #resizeLayer(node: ILayerNode): IResizeSession {
        const { layer } = node;

        return {
            resizeTo: (rect) => {
                const dx = rect.left - layer.left;
                const dy = rect.top - layer.top;
                Object.assign(layer, {
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height,
                });
                for (const table of this.#tables.values()) {
                    if (table.figure.layer === layer.id) {
                        table.figure.left -= dx;
                        table.figure.top -= dy;
                        this.#placeTable(table);
                    }
                }
                // A new size can change the stacking of every layer.
                for (const other of this.#layers.values()) {
                    this.#placeLayer(other);
                }
                this.#routeAll();
            },
            finish: () => {
                const rect = layerRect(layer);
                for (const table of this.#tables.values()) {
                    if (table.figure.layer === layer.id
                        || containsRect(rect, this.#tableRect(table))) {
                        this.#fileUnderLayer(table);
                    }
                }
                this.#routeAll();

                return `Resize Layer ${layer.name}`;
            },
        };
    }

    public beginElevate(id: string): IElevateSession {
        const node = this.#layers.get(id)!;
        // The drag's own height, before the range cuts it, so that moving
        // back past the limit does not move the layer before the pointer
        // is back.
        let wanted = this.#elevation(node.layer);

        return {
            elevateBy: (dz) => {
                wanted += dz;
                node.layer.elevation = Math.min(MAX_LAYER_ELEVATION,
                    Math.max(0, wanted));
                this.viewChanged();
            },
            finish: () => {
                return node.layer.elevation === 0
                    ? `Lower Layer ${node.layer.name}`
                    : `Raise Layer ${node.layer.name}`;
            },
        };
    }

    // --- IFigureSet: undo -------------------------------------------------

    public snapshot(): ISnapshot {
        return {
            figures: this.#diagram.figures.map((figure) => {
                return [figure, figureState(figure)];
            }),
            layers: this.#diagram.layers.map((layer) => {
                return [layer, layerState(layer)];
            }),
        };
    }

    public sameSnapshot(a: unknown, b: unknown): boolean {
        const first = a as ISnapshot;
        const second = b as ISnapshot;

        return first.figures.length === second.figures.length
            && first.layers.length === second.layers.length
            && first.figures.every(([figure, state], index) => {
                const [other, otherState] = second.figures[index];

                return figure === other && sameState(state, otherState);
            })
            && first.layers.every(([layer, state], index) => {
                const [other, otherState] = second.layers[index];

                return layer === other && sameState(state, otherState);
            });
    }

    public restore(snapshot: unknown): void {
        const { figures, layers } = snapshot as ISnapshot;
        for (const [layer, state] of layers) {
            Object.assign(layer, state);
        }
        const changed = new Set<IModelFigure>();
        for (const [figure, state] of figures) {
            if (!sameState(figureState(figure), state)) {
                Object.assign(figure, state);
                changed.add(figure);
            }
        }
        // Drawn again where shown: the snapshot may be of another diagram.
        for (const node of this.#layers.values()) {
            this.#placeLayer(node);
        }
        for (const node of this.#tables.values()) {
            node.layer = this.#layerById(node.figure.layer);
            if (changed.has(node.figure)) {
                this.#relayout(node);
            } else {
                this.#placeTable(node);
            }
        }
        this.#routeAll();
    }

    // --- IFigureSet: menus ------------------------------------------------

    public menu(id: string): IMenuEntry[] {
        const layer = this.#layers.get(id);
        if (layer !== undefined) {
            return [
                { id: "renameLayer", caption: "Rename Layer" },
                { id: "selectTables", caption: "Select Tables on Layer",
                    disabled: this.#tablesOn(layer.layer).length === 0 },
                { id: "fitToTables", caption: "Fit to Tables",
                    disabled: this.#tablesOn(layer.layer).length === 0 },
            ];
        }
        const node = this.#tables.get(id);
        if (node === undefined) {
            return [];
        }
        const figure = node.figure;
        const expanded = figure.expanded !== false;

        return [
            { id: "toggleExpanded", caption: expanded ? "Collapse" : "Expand" },
            { id: "separator1" },
            {
                id: "toggleIndices",
                caption: "Show Indexes",
                checked: figure.indicesExpanded === true,
                disabled: !expanded,
            },
            {
                id: "toggleForeignKeys",
                caption: "Show Foreign Keys",
                checked: figure.foreignKeysExpanded === true,
                disabled: !expanded,
            },
            { id: "separator2" },
            {
                id: "autoSize",
                caption: "Auto-Size Height",
                disabled: figure.manualSizing !== true,
            },
            {
                id: "rename",
                caption: "Rename Table",
                disabled: node.layout.items[0]?.editable !== true,
            },
        ];
    }

    public runCommand(id: string, command: string): void {
        const host = this.#host;
        if (host === undefined) {
            return;
        }
        const layer = this.#layers.get(id);
        if (layer !== undefined) {
            this.#runLayerCommand(host, layer, command);

            return;
        }
        const node = this.#tables.get(id);
        if (node === undefined) {
            return;
        }
        const name = node.figure.name;
        const change = (label: string, apply: (figure: IModelFigure) => void) => {
            host.change(label, () => {
                apply(node.figure);
                this.#relayout(node);
            });
        };
        switch (command) {
            case "toggleExpanded": {
                const expanded = node.figure.expanded !== false;
                change(`${expanded ? "Collapse" : "Expand"} ${name}`,
                    (figure) => { figure.expanded = !expanded; });
                break;
            }

            case "toggleIndices": {
                const shown = node.figure.indicesExpanded === true;
                change(`${shown ? "Hide" : "Show"} Indexes of ${name}`,
                    (figure) => { figure.indicesExpanded = !shown; });
                break;
            }

            case "toggleForeignKeys": {
                const shown = node.figure.foreignKeysExpanded === true;
                change(`${shown ? "Hide" : "Show"} Foreign Keys of ${name}`,
                    (figure) => { figure.foreignKeysExpanded = !shown; });
                break;
            }

            case "autoSize": {
                change(`Auto-Size ${name}`,
                    (figure) => { figure.manualSizing = false; });
                break;
            }

            case "rename": {
                const title = node.layout.items[0];
                if (title?.editable === true) {
                    this.#editCardItem(node, title);
                }
                break;
            }

            default:
        }
    }

    #runLayerCommand(host: ICanvasHost, node: ILayerNode, command: string): void {
        switch (command) {
            case "renameLayer": {
                this.#editLayerName(node);
                break;
            }

            case "selectTables": {
                host.select(this.#tablesOn(node.layer).map((table) => {
                    return table.figure.id;
                }));
                break;
            }

            case "fitToTables": {
                const tables = this.#tablesOn(node.layer);
                const rect = fitAround(tables.map((table) => {
                    return this.#tableRect(table);
                }));
                if (rect === undefined) {
                    return;
                }
                host.change(`Fit Layer ${node.layer.name} to Tables`, () => {
                    const session = this.#resizeLayer(node);
                    session.resizeTo(rect, { fx: 0, fy: 0, cursor: "" });
                    session.finish();
                });
                break;
            }

            default:
        }
    }

    // --- Editing names ------------------------------------------------------

    public doubleClick(id: string, probe: IProbe): void {
        const hit = this.#tableHit(probe);
        if (hit !== undefined && hit.node.figure.id === id) {
            const rect = this.#tableRect(hit.node);
            // The plane's UV runs from its bottom left; the card's items
            // are measured from its top left.
            const item = cardItemAt(hit.node.layout, hit.uv.x * rect.width,
                (1 - hit.uv.y) * rect.height);
            if (item?.editable === true) {
                this.#editCardItem(hit.node, item);
            }

            return;
        }
        const layer = this.#layers.get(id);
        if (layer !== undefined) {
            const point = probe.pointOn("base", this.#elevation(layer.layer));
            const band = {
                ...layerRect(layer.layer),
                height: LAYER_LABEL.top + LAYER_LABEL.height + 4,
            };
            if (point !== undefined && containsPoint(band, point)) {
                this.#editLayerName(layer);
            }
        }
    }

    /** Opens the input over a table's or column's name. */
    #editCardItem(node: ITableNode, item: ICardItem): void {
        const title = item.kind === "title";
        const spec: IEditSpec = {
            text: item.text,
            label: title ? "Table name" : "Column name",
            bold: title,
            fontSize: title ? CARD.titleFontSize : CARD.rowFontSize,
            plane: "lifted",
            elevation: () => { return this.#elevation(node.layer); },
            area: () => {
                if (this.#tables.get(node.figure.id) !== node) {
                    return undefined;
                }
                const rect = this.#tableRect(node);
                const area = editRect(item);

                return { ...area, left: rect.left + area.left,
                    top: rect.top + area.top };
            },
            commit: (text) => { this.#renameCardItem(node, item, text); },
        };
        this.#host?.startEdit(spec);
    }

    /** Opens the input over a layer's name. */
    #editLayerName(node: ILayerNode): void {
        const { layer } = node;
        this.#host?.startEdit({
            text: layer.name,
            label: "Layer name",
            bold: true,
            fontSize: LAYER_LABEL.fontSize,
            plane: "base",
            elevation: () => { return this.#elevation(layer); },
            area: () => {
                if (this.#layers.get(layer.id) !== node) {
                    return undefined;
                }

                return {
                    left: layer.left + LAYER_LABEL.left - 2,
                    top: layer.top + LAYER_LABEL.top,
                    width: Math.max(40, Math.min(
                        Math.max(node.labelWidth + 40, 120),
                        layer.width - LAYER_LABEL.left - 30)),
                    height: LAYER_LABEL.height,
                };
            },
            commit: (text) => {
                const name = text.trim();
                const old = layer.name;
                if (name === "" || name === old) {
                    return;
                }
                this.#host?.change(`Rename Layer ${old} to ${name}`, () => {
                    layer.name = name;
                    this.#placeLayer(node);
                });
            },
        });
    }

    /** Renames a table or a column, as one action of the history. */
    #renameCardItem(node: ITableNode, item: ICardItem, text: string): void {
        const host = this.#host;
        const catalog = this.#options.catalog;
        const name = text.trim();
        if (host === undefined || name === "" || name === item.text) {
            return;
        }
        const tableName = node.figure.table ?? node.figure.name;
        if (catalog.table(tableName) === undefined) {
            return;
        }
        if (item.kind === "title") {
            if (catalog.table(name) !== undefined) {
                // Another table has that name; two would be one.
                return;
            }
            host.history.execute({
                label: `Rename Table ${tableName} to ${name}`,
                undo: () => { this.#renameTable(name, tableName); },
                redo: () => { this.#renameTable(tableName, name); },
            });
        } else if (item.kind === "column" && item.columnIndex !== undefined) {
            const index = item.columnIndex;
            const old = item.text;
            const figure = node.figure;
            host.history.execute({
                label: `Rename Column ${tableName}.${old} to ${name}`,
                // The table may have been renamed since; the column is
                // found through the figure, which follows the table.
                undo: () => { this.#renameColumn(figure, index, old); },
                redo: () => { this.#renameColumn(figure, index, name); },
            });
        }
    }

    /**
     * Renames a table: in the catalog, and on every figure and connection
     * of every diagram that names it.
     */
    #renameTable(from: string, to: string): void {
        this.#options.catalog.renameTable(from, to);
        for (const diagram of this.#options.diagrams) {
            for (const figure of diagram.figures) {
                if ((figure.table ?? figure.name) === from) {
                    figure.table = to;
                    figure.name = to;
                }
            }
            for (const connection of diagram.connections) {
                if (connection.table === from) {
                    connection.table = to;
                }
            }
        }
        this.#relayoutTable(to);
    }

    /** Renames a column of the table a figure shows. */
    #renameColumn(figure: IModelFigure, index: number, name: string): void {
        const tableName = figure.table ?? figure.name;
        const column = this.#options.catalog.table(tableName)?.columns[index];
        if (column !== undefined) {
            column.name = name;
            this.#relayoutTable(tableName);
        }
    }

    /** Draws every shown figure of a table again. */
    #relayoutTable(tableName: string): void {
        for (const node of this.#tables.values()) {
            if ((node.figure.table ?? node.figure.name) === tableName) {
                this.#relayout(node);
            }
        }
        this.#host?.invalidate();
    }
}

/**
 * Keeps a figure's height in the model where it follows its content, so
 * that what is stored is what is drawn.
 *
 * @param figure The figure.
 * @param layout Its card's layout.
 */
const keepAutoHeight = (figure: IModelFigure, layout: ICardLayout): void => {
    if (figure.manualSizing !== true) {
        figure.height = layout.height;
    }
};
