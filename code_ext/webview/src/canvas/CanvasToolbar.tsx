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

import type { ComponentChildren, JSX } from "preact";

import type { Tool, ViewMode } from "./canvasTypes.js";
import { HamburgerButton, MENU_BUTTON_SIZE } from "./CanvasMenu.js";
import type { IIsoAngles } from "./geometry.js";
import { SplitButton } from "./SplitButton.js";

/**
 * The canvas's toolbar, laid out as the MySQL Shell's schema diagram
 * designer laid out its own: sections headed by a label - Tools, View -
 * with the same icons, plus the projection (2D or a 3D view) and the
 * Navigator. What a figure set adds (the diagram to show, say) follows,
 * as the toolbar's children.
 */

export interface ICanvasToolbarProps {
    tool: Tool;
    onTool: (tool: Tool) => void;
    view: ViewMode;
    onView: (view: ViewMode) => void;
    gridVisible: boolean;
    onGridVisible: (visible: boolean) => void;
    pagesVisible: boolean;
    onPagesVisible: (visible: boolean) => void;
    locked: boolean;
    onLocked: (locked: boolean) => void;
    navigatorVisible: boolean;
    onNavigatorVisible: (visible: boolean) => void;
    onFit: () => void;
    onActualSize: () => void;
    /** The zoom level, 1 being life size. */
    zoom: number;
    /** The 3D views to choose from, and the one last used. */
    isoPresets: readonly IIsoAngles[];
    isoPreset: string;
    onIsoPreset: (id: string) => void;
    /**
     * Opens the toolbar's menu under its button.
     *
     * @param button The button, to place the menu under.
     */
    onMenu: (button: HTMLElement) => void;
    menuOpen: boolean;
    /** The figure set's own sections. */
    children?: ComponentChildren;
}

interface IIconButtonProps {
    icon: string;
    title: string;
    toggled?: boolean;
    onClick: () => void;
}

const IconButton = (props: IIconButtonProps): JSX.Element => {
    return <button
        type="button"
        className={`toolbarIcon ${props.icon}`
            + (props.toggled === true ? " toggled" : "")}
        title={props.title}
        aria-label={props.title}
        aria-pressed={props.toggled}
        onClick={props.onClick}
    />;
};

/**
 * A section of the toolbar: a label, then its controls.
 *
 * @param props The label and the controls.
 *
 * @returns The section.
 */
export const ToolbarSection = (props: {
    label: string;
    children: ComponentChildren;
}): JSX.Element => {
    return <>
        <span className="toolbarLabel">{props.label}</span>
        {props.children}
    </>;
};

export const CanvasToolbar = (props: ICanvasToolbarProps): JSX.Element => {
    return <div className="toolbar" role="toolbar">
        <ToolbarSection label="Tools:">
            <IconButton icon="toolPointer" title="Select Objects"
                toggled={props.tool === "pointer"}
                onClick={() => { props.onTool("pointer"); }} />
            <IconButton icon="toolHand" title="Pan (or hold Space)"
                toggled={props.tool === "hand"}
                onClick={() => { props.onTool("hand"); }} />
            <IconButton icon="toolZoom"
                title="Zoom in; Alt + click to zoom out (or hold Z)"
                toggled={props.tool === "zoom"}
                onClick={() => { props.onTool("zoom"); }} />
        </ToolbarSection>
        <span className="toolbarDivider" />
        <ToolbarSection label="View:">
            <IconButton icon="viewReset" title="Fit the pages (Cmd/Ctrl + 0)"
                onClick={props.onFit} />
            <button type="button" className="toolbarText"
                title="Actual size: a page as large as the paper (Cmd/Ctrl + 1)"
                onClick={props.onActualSize}>
                {`${Math.round(props.zoom * 100)}%`}
            </button>
            <IconButton icon="viewGrid"
                title={props.gridVisible ? "Hide Grid" : "Show Grid"}
                toggled={props.gridVisible}
                onClick={() => { props.onGridVisible(!props.gridVisible); }} />
            <IconButton icon="viewRuler"
                title={props.pagesVisible ? "Hide Pages" : "Show Pages"}
                toggled={props.pagesVisible}
                onClick={() => { props.onPagesVisible(!props.pagesVisible); }} />
            <IconButton icon="viewLock"
                title={props.locked ? "Unlock Diagram" : "Lock Diagram"}
                toggled={props.locked}
                onClick={() => { props.onLocked(!props.locked); }} />
            <button type="button"
                className={"toolbarText" + (props.navigatorVisible
                    ? " toggled" : "")}
                aria-pressed={props.navigatorVisible}
                title={props.navigatorVisible ? "Hide the Navigator"
                    : "Show the Navigator"}
                onClick={() => {
                    props.onNavigatorVisible(!props.navigatorVisible);
                }}>Navigator</button>
        </ToolbarSection>
        <span className="toolbarDivider" />
        <ToolbarSection label="Projection:">
            <button type="button"
                className={"toolbarText" + (props.view === "2d" ? " toggled" : "")}
                aria-pressed={props.view === "2d"}
                onClick={() => { props.onView("2d"); }}>2D</button>
            <SplitButton
                caption="Isometric"
                title={props.isoPresets.find((preset) => {
                    return preset.id === props.isoPreset;
                })?.caption ?? "Isometric"}
                toggled={props.view === "iso"}
                onClick={() => { props.onView("iso"); }}
                menuLabel="Choose the 3D View"
                items={props.isoPresets}
                selected={props.isoPreset}
                onSelect={props.onIsoPreset}
            />
        </ToolbarSection>
        {props.children}
        <HamburgerButton
            size={MENU_BUTTON_SIZE}
            label="More Options"
            open={props.menuOpen}
            className="toolbarMenuButton"
            onOpen={props.onMenu}
        />
    </div>;
};
