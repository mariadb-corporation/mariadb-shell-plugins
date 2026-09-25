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

import type { RefObject } from "preact";
import { useEffect, useLayoutEffect, useState } from "preact/hooks";

/**
 * What the two dialogs - the connection editor and New Sandbox - are both
 * built from, so they lay out and behave alike.
 */

/** A labelled row, which is how every field is laid out. */
export const Field = (props: {
    caption: string;
    hint?: string;
    children: preact.ComponentChildren;
}): preact.JSX.Element => {
    return (
        <label class="field">
            <span class="field-caption">{props.caption}</span>
            {props.children}
            {props.hint === undefined
                ? null
                : <span class="field-hint">{props.hint}</span>}
        </label>
    );
};

/** Which edges of a scrolling area have content hidden past them. */
export interface IScrollFades {
    above: boolean;
    below: boolean;
    /**
     * How wide the area's scrollbar is, which the fades stop short of. Zero
     * where scrollbars overlay the content, as macOS draws them by default:
     * leaving room there would leave an unfaded strip down the edge.
     */
    scrollbar: number;
}

/**
 * Tracks whether an area can be scrolled further up or down, so a fade can
 * say so at the edge where fields are cut off. A tab's lower fields are
 * otherwise easy to miss: nothing about a half-empty looking panel says it
 * scrolls.
 *
 * Measured after every render - and once more on the frame after, since a
 * webview is still being sized, styled and given its fonts while the first
 * renders happen - and on every other thing that moves the answer: a scroll,
 * the area or its content resizing, the window resizing, the fonts arriving,
 * the content being swapped by a tab switch. The pointer entering the area
 * is the last resort: whatever was missed, the user is about to look there.
 *
 * @param area The scrolling element.
 *
 * @returns The edges with more beyond them.
 */
export const useScrollFades = (
    area: RefObject<HTMLElement>,
): IScrollFades => {
    const [fades, setFades] = useState<IScrollFades>({
        above: false, below: false, scrollbar: 0,
    });

    const measure = (): void => {
        const element = area.current;
        if (element === null) {
            return;
        }
        // A pixel of slack: zoomed layouts report fractional heights, and
        // a fade over nothing is worse than none.
        const above = element.scrollTop > 1;
        const below = element.scrollTop + element.clientHeight
            < element.scrollHeight - 1;
        // The borders are the only other thing between the two widths.
        const style = getComputedStyle(element);
        const scrollbar = Math.max(0, element.offsetWidth
            - element.clientWidth
            - (parseFloat(style.borderLeftWidth) || 0)
            - (parseFloat(style.borderRightWidth) || 0));
        setFades((current) => {
            return current.above === above && current.below === below
                && current.scrollbar === scrollbar
                ? current
                : { above, below, scrollbar };
        });
    };

    useLayoutEffect(() => {
        measure();
        const frame = requestAnimationFrame(measure);

        return () => { cancelAnimationFrame(frame); };
    });

    useEffect(() => {
        const element = area.current;
        if (element === null) {
            return undefined;
        }

        element.addEventListener("scroll", measure, { passive: true });
        element.addEventListener("pointerenter", measure);
        window.addEventListener("resize", measure);
        document.fonts?.addEventListener("loadingdone", measure);
        void document.fonts?.ready.then(measure);

        // Watching the content as well as the area: a group that appears
        // grows the content without resizing the area around it. The content
        // is re-watched whenever it is swapped, which a tab switch does.
        const resizes = typeof ResizeObserver === "undefined"
            ? undefined
            : new ResizeObserver(measure);
        const watchContent = (): void => {
            resizes?.disconnect();
            resizes?.observe(element);
            for (const child of element.children) {
                resizes?.observe(child);
            }
        };
        watchContent();
        const swaps = new MutationObserver(() => {
            watchContent();
            measure();
        });
        swaps.observe(element, { childList: true });

        return () => {
            element.removeEventListener("scroll", measure);
            element.removeEventListener("pointerenter", measure);
            window.removeEventListener("resize", measure);
            document.fonts?.removeEventListener("loadingdone", measure);
            resizes?.disconnect();
            swaps.disconnect();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return fades;
};
