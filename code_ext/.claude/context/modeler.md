# The schema model viewer

An EER diagram canvas on three.js that switches between a 2D and a 3D
view, showing a live schema's diagrams. Started on `wip/canvas`
(2026-10-15) from the design discussion saved in
`~/Downloads/eer-modeler-threejs-discussion.md`.

## Where the diagrams come from (2026-10-15)

- **A schema's `msm_schema_diagrams` view** (`name`, `format`,
  `document`), as MSM projects carry it (the MRS metadata schema's is
  section `185_schema_diagrams.sql` in the mariadb-shell repo): one row
  `schema_definition` in format `mwb-schema/1` (the tables), and one row
  per EER diagram in format `mwb-diagram/1` (id, name, size, layers,
  figures, connections, notations). `assembleSchemaModel`
  (`src/modeler/modelFormat.ts`) puts them together into the viewer's
  `ISchemaModel`; a diagram is named by its row, other formats are
  skipped. The old single-document `mwb-diagrams/1` file, its SQL parser
  and the temporary toolbar button are gone.
- **The Connections view.** Expanding a schema looks for the view
  (`hasDiagramsSql`: one `information_schema.TABLES` lookup, on the UI
  Backend session; a failure just means no folder) and, where it is there,
  puts a **Diagrams** folder first, before the object groups
  (`IDiagramsNode`, `mariadbDiagrams`). Expanding it lists
  `SELECT name ... WHERE format LIKE '%diagram%'` (`IDiagramNode`,
  `mariadbDiagram`). A diagram opens by its inline button, its menu, or a
  double-click (`DoubleClick`, `src/tree/doubleClick.ts`: the row's
  command reports every click; the second on the same row within 500 ms
  opens).
- **The Diagram session.** Listing a schema's diagrams and reading them
  run on a session of their own, "Diagram" (`DiagramService`,
  `src/modeler/diagramService.ts`), and every such query is a row in the
  connection's Actions: the spinner while it runs, then what it read or
  its error, the statement beside it, "Diagram" in the Conn column (the
  user's wish: "log the queries in the Action tab, as Diagram").
- **The viewer** is one panel per schema of a connection
  (`ModelerPanel.show(extensionUri, {connection, schema, diagram}, host)`),
  titled "diagram (schema)" (escaped in the page's `<title>` by
  `buildWebviewHtml`: a diagram's name is the database's). It reads every row of the view when its page
  is ready and opens on the diagram asked for (`load.diagram`); opening
  another diagram of that schema reuses it (`showDiagram`), and the
  viewer's own Diagram list retitles it (`diagramShown`). A spinner shows
  in the middle of the canvas where loading takes longer than 0.3 s.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## The split: a canvas, and figure sets (2026-10-15)

The user wants the canvas used for different purposes with different
figure sets, so the canvas knows nothing of tables or layers:

- **`webview/src/canvas/`, the base.** `Canvas.ts`: the camera and its
  2D / 3D views, Cmd + drag orbiting, zoom and pan, the paper and its
  grid, rendering on demand, the keyboard, undo / redo, selection with
  its outline and resize handles (drawn by the canvas for any item), moves
  and resizes, hover, the menu buttons, context menus, editing text in
  place, the Navigator's overview. `canvasTypes.ts` is the contract;
  `geometry.ts` (views, presets, orbit, grid spacing, `affineTransform`),
  `threeHelpers.ts` (`DRAW_ORDER`, shapes, `disposeGroup`), `screen.ts`,
  `undoStack.ts`, `theme.ts`, and the UI: `CanvasToolbar.tsx` (with
  `ToolbarSection` for a set's own sections as children),
  `SplitButton.tsx`, `CanvasMenu.tsx` (`CanvasMenu`, `MenuButton`),
  `Navigator.tsx`.
- **A figure set** (`IFigureSet`) draws into the canvas's two groups -
  `base` on the page, `lifted` rising in 3D - and answers for its items:
  `itemAt(probe)`, `item(id)` (an `ICanvasItem`: id, plane, `rect()`,
  `resizable`, least size, `elevation()`, `elevatable`, `menuBand`),
  `beginMove` / `beginResize` / `beginElevate` sessions (the set decides
  what moves along), `snapshot` / `sameSnapshot` / `restore` (the canvas
  records every move, resize, raise and `host.change(...)` as one undo
  step from them), `menu` / `runCommand`, `doubleClick` (which may call
  `host.startEdit(spec)`), `viewChanged`, `zoomSettled`.
- **`webview/src/modeler/`, the EER diagram.** `EerFigureSet.ts` (one per
  model; `show(diagram)` picks the diagram, `canvas.load(set, paper)`
  draws it; history kept across a model's diagrams, cleared for another
  set), `tableCard.ts`, `connectionGeometry.ts` (routes, crow's foot,
  split stubs), `layerRules.ts`, `catalog.ts`, and `ModelViewer.tsx`,
  which puts canvas, set, toolbar, Navigator, menus and the input
  together.

## What the user decided (2026-10-15)

- **three.js, not Pixi.** One scene, one `OrthographicCamera`; the 2D view
  looks straight down, the 3D one is the same camera turned and tilted
  (`TOP_VIEW`, `isoQuaternion`, slerped over 400 ms). Presets
  (`ISO_PRESETS`): True Isometric (35.26° tilt, 45° turn), Steep (45°,
  45°), Upright (50°, 30°, the default). True isometric looked pinched to
  the user (58% of the page's depth kept; the others 71% and 77%).
- **The Isometric button is a split button**: the main part goes to the
  3D view last chosen, only the chevron opens the list; a pick also goes
  to the 3D view. Kept in the webview's state (`saveState` / `loadState`).
- **Cmd + drag orbits freely** (canvas `#orbit`, `orbitAngles`): across
  turns, down tilts towards straight above, 0.4° per pixel, tilt 10°..90°.
  Starting in 2D tips the camera over from straight above (the view turns
  3D at once; `onViewMode`). The figures' lift follows the tilt
  (`liftFactor`: none at 90°, full from 60° down, so every preset lifts
  fully). Orbiting makes "Custom (x° tilt, y° turn)" in the Isometric
  list, selected and updated as the drag goes, kept with its angles.
  Cmd + click without moving still toggles an item's selection; Shift +
  click does too.
- **The figure design and the toolbar are the MySQL Shell's**
  (`mysql-shell-plugins/gui/frontend/src/modules/db-editor/DiagramDesigner`):
  header strip in the figure's colour with the table icon and name,
  columns with key / not-null / nullable icons and italic dimmed types,
  Indexes and Foreign Keys where expanded; Tools (pointer, hand, zoom) and
  View (reset, grid, rulers = pages, lock, Navigator) with the same icons.
- **Names are edited in place on a double-click** (table, column, layer).
  The input is sized as the text is in 2D at the zoom and laid onto its
  plane by one CSS `matrix()` (`affineTransform`; the camera is
  orthographic, so a page rectangle lands on the screen as a
  parallelogram), at the item's elevation, re-placed after every render.
  Drawn as the Explorer's rename box: `outline: 1px solid
  var(--vscode-focusBorder)`, `outline-offset: -1px`, radius
  `var(--vscode-cornerRadius-small, 4px)` (checked in the installed VS
  Code's workbench CSS).
- **The pages are kept** for printing / PDF: `src/modeler/pageSettings.ts`
  (pure, shared): the page's coordinate space is the sheets' printable
  areas tiled from its origin. Defaults are the model's `app.PageSettings`
  (read from the `.mwb`): A4 portrait, margins 6.35 / 6.35 / 6.35 / 14.46
  mm, 5 units per mm - the MRS diagram is exactly two sheets wide. The
  model JSON does not carry the page settings yet.
- **100% is life size** on the user's Apple Studio Display (`screen.ts`:
  218 ppi constant, divided by `devicePixelRatio`; 297 mm = 1274.5 CSS px
  at 2x).
- **The table list in the JSON is a stopgap**: the objects will come from
  the CREATE statements through a language server the user will write.
  The set reads tables through `ITableCatalog` only.

## Tables and layers

- **Workbench's rules** (`layerRules.ts`, pure, tested): a table lies ON
  a layer when fully inside it, and is filed (`figure.layer`) under the
  SMALLEST layer it lies on, its `left` / `top` relative to that layer.
  A dropped or resized table is filed again (`#fileUnderLayer`), keeping
  its place on the page.
- **Layers are items** on the base plane: selectable (the inner one where
  they overlap), movable, resizable (least 80 x 40). Moving a layer moves
  every table lying fully on it, whatever it is filed under; tables only
  partly on it stay (their relative position is compensated). Resizing
  from the left or top keeps the filed tables in place on the page.
- **Option + drag raises or lowers a layer** in the 3D view
  (`beginElevate`; `layer.elevation`, the viewer's own field, 0..240),
  with the tables filed under it. Elevation scales with the lift, so 2D
  stays flat; a foreign key between tables at different heights keeps
  its first and last segments at their tables' heights and spans the
  difference in its middle segment alone. Everything the canvas places (outline, handles, menu
  button, input, hit tests, drags) uses an item's own height.
- **Menus.** A table's: Collapse / Expand, Show Indexes, Show Foreign
  Keys, Auto-Size Height, Rename Table. A layer's: Rename Layer, Select
  Tables on Layer, Fit to Tables (`fitAround`: 20 around, 26 above for the
  name). Opened by a right-click, or the hamburger an item shows while
  selected OR under the pointer (`menuBand`: the header for a table, the
  name's band for a layer), 60% of the band's height (so it scales with
  the zoom; not drawn under 8px), its right edge a fixed distance from the
  item's in page units - what half a handle and 4px are at 200%, where it
  looked best (`MENU_BUTTON_REFERENCE_ZOOM`) - so it keeps its place on the
  item at every zoom, its three bars drawn as shares
  of it, kept clear of the corner handle. The Navigator's and the toolbar's
  hamburgers (20px, `MENU_BUTTON_SIZE`) draw the same bars (`menuBarStyle`).
  The button and its bars are in whole DEVICE pixels (`menuBars` in
  `CanvasMenu.tsx`: 8% thick, 16% apart, 56% wide, at least a device
  pixel clear between bars), and in 2D the button is moved by a
  `translate` rounded to device pixels rather than a matrix: percentage
  bars at subpixel offsets came out uneven, and whole CSS pixels made a
  small button's bars too thin and too far apart on a Retina screen. Buttons
  whose screen box (`ICanvasOverlay.bounds`) touches the Navigator panel
  are not drawn - its overview area is transparent, so they would show
  through it.
- **Heights follow `manualSizing`** (Workbench's flag): content height,
  written back to `figure.height`, unless sized by hand from the top or
  bottom edge; cut-off content is not hit.
- **Undo.** Snapshot-based for moves, resizes, raises and menu changes
  (`FigureState` incl. `layer`; `LayerState` incl. `name`, `elevation`);
  renames of tables and columns are actions of their own, covering every
  diagram and connection of the model. Cmd+Z / Cmd+Shift+Z (Ctrl
  elsewhere); the history is kept (`entries`, `onHistory`) for a list the
  user wants later.

## The Navigator (2026-10-15)

A panel 15px in from the canvas's top right corner, 192px wide (`Navigator.tsx`;
20% under the first 240px, to cover less of the diagram):
title "Navigator" with a hamburger (Fit Diagram, Actual Size, Hide
Navigator; the toolbar's Navigator button brings it back, kept in the
webview state), a zoom slider (log scale over `ZOOM_RANGE`) with the zoom's value as a
split button (`SplitButton`, as the Isometric one: the value goes to the
zoom last picked, 100% at first; its chevron lists 50 / 75 / 100 / 150%,
`ZOOM_PRESETS`) and the
overview. The panel draws no overview: its area is transparent and the
canvas renders the scene into that part of its own WebGL canvas with a
second, top-down camera (`#renderNavigator`, scissor + viewport), so it
stays 2D in a 3D view without a second WebGL context. The part in view is
the four screen corners on the page (`INavigatorState.view`): a rectangle
in 2D, a quadrilateral in 3D, drawn as an SVG polygon in
`--vscode-focusBorder`, 1px, over black at 7%. Dragging pans
(`navigatorPan`); a press outside it centres the view there first
(`navigatorCenter`).

## Rendering notes

- **Grid** like Illustrator's (`gridSpacing`): 10 mm main / 2 mm sub at
  the base level, five per cell; sublines closer than 6 px are dropped and
  the main lines become the sublines of a grid five times wider; they fade
  in over 8 px. Rebuilt only on a level change.
- **Cards are Canvas2D textures**, not troika-three-text: troika's CDN
  font, blob worker and missing font file do not pass the webview's CSP.
  Redrawn at a step of device pixels per unit 150 ms after the zoom
  settles (`zoomSettled`).
- **Each card has a slab** (8 units, shaded by baked vertex colours from
  a light at the upper left), flat in 2D, growing with the lift. It is
  inset a unit inside the card (`SLAB_INSET`): as large as the card, its
  light top showed through the card's anti-aliased corners as a fringe.
  Rounded rectangles (`roundedRect`) use circular arcs, as Canvas2D's
  `roundRect` does; quadratic corners bulged past the card's.
- **Draw order is fixed** (`DRAW_ORDER`: pages, grid, base, shadows,
  lifted, connections, decorations), since three.js sorts transparent
  meshes by distance, which put tilted plates over shadows.
- **Connections are fat lines** (`LineSegments2` / `LineMaterial`, 0.8 CSS
  px, `CONNECTION_WIDTH`): WebGL's own lines are a pixel wide whatever is
  asked. Their material needs the canvas's size (`host.viewport()`, set
  in `viewChanged`); in the Navigator they come out thinner.
- Connections ignore `middleSegmentOffset`; `drawSplit` ones are a 40-unit
  stub out of each table.

- **Layer plates are solid**: the layer's tint over the paper, mixed in
  sRGB as the old 35% / 60% see-through plate was blended (`tintOver`),
  so the grid lies under the layers rather than showing through. Nested
  plates would fight in one plane, so a smaller layer lies a little
  higher (`#stackHeight`, 0.003 per larger layer).
- **The grid's main lines** are at 38%, the sublines at 40% (before
  their fade, which at 100% leaves about a third of it).
- **The printable areas' dashed lines are hidden by default**
  (`Canvas.marginsVisible`); the toolbar's hamburger at its right end
  (`CanvasToolbar` `onMenu`) offers "Show Printable Area", kept in the
  webview state (`margins`).
- **Sheet edges:** the paper's outline in full, the sheets' edges where
  they overlap (inside the diagram) dotted (1.5 on, 4 off) at 30%
  (`INNER_EDGE_OPACITY`).
- **The paper** is the editor's background, lifted 8% towards white on a
  dark theme (`paperColor` in `theme.ts`; `#1e1e1e` gives `#303030`), so
  it does not look black; the layer tints and handles take it from there.
- **Around the paper** is the theme's widget background darkened to 60%
  (dark themes) or 80% (light) in sRGB (`outsideColor`), the Navigator's
  margin too.

## Shortcuts

Space + drag pans (hand tool too); Space + Cmd + click zooms in, with Alt
out; Z + click zooms in, Alt + Z out (zoom tool too); Cmd/Ctrl + wheel or
a pinch zooms about the cursor, a plain wheel pans; Cmd/Ctrl + 0 fits,
Cmd/Ctrl + 1 is 100%; Cmd + drag orbits; Option + drag on a layer (3D)
raises it; Shift or Cmd + click toggles the selection. Modifiers are read
off every event (macOS swallows keyups while Cmd is down); Space and Z
are dropped when Cmd goes up, on blur and when hidden.

## Known gaps / next steps

- Committed and pushed as `39c889e1` on `wip/canvas` (2026-10-15); no PR
  yet. The user looked at the rendering in a running VS Code throughout
  (cards, menu buttons, grid, paper, Navigator, 3D view), with the MRS
  model from the old file preview. NOT yet tried by the user: the
  Connections view's Diagrams folder against a live schema with the view,
  the double-click, the pre-selected diagram, the Actions rows on the
  "Diagram" session, the Navigator's zoom list, layer moves with their
  tables and Option + drag.
- A diagram row has the Diagrams folder's icon (`type-hierarchy-sub`)
  until the user designs one of its own.
- Nothing writes the model back; the history is not shown anywhere yet.
- No rubber-band selection, no connection captions, no mark on a card
  that cuts content off.
- PDF export / printing per page: the page model is there, nothing
  renders it yet. `DISPLAY_PPI` should become a setting.
- The grid's spacing ignores the 3D view's foreshortening.
