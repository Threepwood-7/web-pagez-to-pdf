# Changelog

## 2026-03-11

### Editor Preview + Wizardry Removal

- Moved gutter placement to the left side in both editor print-preview thumbnails and PDF export placement math.
- Updated print preview thumbnails so header/footer rich text is rendered into page-margin regions with token resolution (`{title}`, `{page}`, `{pages}`, `{datetime}`).
- Expanded `Blank` and `Search` tooltips with explicit threshold/window behavior and tradeoff details.
- Removed the `Wizardry` UI group and deleted Wizardry-specific scrollbar/border trim implementations.
- Kept `Vertical Border Crop` as a selected-item editor action outside Wizardry.
- Reworked Editor tool flow so `Pan` is persistent while split marker add/remove and draw/finalize actions are one-shot and return to `Pan`.
- Made `Split Markers` a non-checkable section and moved split add/remove into one-shot armed actions.
- Unified auto-calculated and edited split markers in Editor: auto markers are now shown in the split list/canvas using the same marker style, and become persisted edits on first add/remove/move.
- Replaced `Split Y` numeric input with one-shot `Add Split Marker` / `Remove Split Marker` click-on-canvas or click-on-Y-ruler flow, and added `Reset Split Markers`.
- Restored split-marker drag/move via ruler triangle indicators for all visible split markers.
- Fixed split-marker drag rendering so moving a marker no longer leaves duplicate trailing split lines in preview.
- Removed `Preview Breaks`; `Reset Split Markers` is now the single explicit split reset/recompute action.
- Fixed Pan-mode event handling so panning stops immediately on mouse release/leave and cannot latch on unpressed mouse moves.
- Added `docs/REMOVEDFEATURES.md` to document removed Wizardry behavior and remaining alternatives.
- Updated GUI/unit coverage for the new control contract, left-gutter geometry, header/footer thumbnail rendering, and legacy Wizard operation no-op handling.

## 2026-03-09

### Archived: ROADMAPZ.md

Source: `ROADMAPZ.md`

### Editor Split + Preview UX Refactor Plan

#### Summary
Implement a coordinated Editor UX update that removes explicit Split Edit mode, keeps split-marker editing accessible via existing split controls, and redesigns `Page Layout & Print Preview` into a top two-column layout with live zoom tooling plus a clearer, zoomable full-width thumbnail preview row.

#### Implementation Changes
1. **Remove Split Edit mode and button**
1. Remove the `Split Edit` tool button from the Tools group and from tooltip/widget identity/test expectations.
2. Remove mode-switch dependency for split marker operations; split markers are no longer tied to a dedicated tool state.
3. Keep adding splits exclusively through existing split controls (`Split Y` + `Add Split Marker`).
4. Keep moving existing splits by drag in canvas, but only when `Pan` tool is active.
5. Keep split deletion via existing controls, and remove any right-click split-removal path that depended on Split Edit mode.

2. **Improve split marker visibility and ruler handles**
1. Increase split marker visual prominence in canvas overlays (thicker/high-contrast line styling).
2. Add Word-like triangular ruler knobs for each split marker on the ruler edge.
3. Make knobs draggable in `Pan` mode and keep drag behavior pixel-precise.
4. Keep undo/redo semantics unchanged for split marker move/add/remove actions.

3. **Restructure `Page Layout & Print Preview`**
1. Convert the top area into two columns:
1. Left column: existing layout controls (`Paper/Orientation`, `Margins + Gutter`, `Blank/Search`, Header/Footer editors).
2. Right column: new Quick Zoom panel top-aligned with left controls.
2. Keep the lower page thumbnail preview row full-width below both top columns.
3. Move `Header` and `Footer` labels above their rich text editors while preserving existing rich-text behavior.

4. **Quick Zoom + floating magnifier (main editor image)**
1. Add Quick Zoom panel controls with a magnification spinner; default `12x`.
2. Quick Zoom follows mouse movement over the main editor image.
3. Quick Zoom freezes last sampled patch when cursor leaves image bounds.
4. Add large floating magnifier preview left of cursor while hovering main editor image; clamp placement to screen bounds.
5. Draw crosshair in both Quick Zoom panel and floating magnifier for fine pixel alignment.

5. **Bottom thumbnail row: zoom + clear print guides + hover overlay**
1. Keep thumbnail row full-width and add dual zoom mechanisms:
1. Visible thumbnail zoom slider.
2. `Ctrl + mouse wheel` zoom on thumbnail list.
2. Add strong, high-contrast per-thumbnail overlays showing print border/margins/gutter and small legend text.
3. On thumbnail hover, temporarily overlay the main editor view with an enlarged thumbnail preview; restore normal editor view on hover leave.

6. **Public interface / behavior updates**
1. Remove `split_edit_tool_button` from main window UI contract and tests.
2. Introduce new UI controls for quick zoom and thumbnail zoom (spinner/slider) with stable widget IDs/tooltips.
3. Extend editor canvas behavior for always-available split dragging in Pan mode, ruler knobs, and magnifier rendering.
4. Keep existing settings keys unless new preview zoom preferences are explicitly introduced; if added, define defaults and roundtrip in settings/tests.

#### Test Plan
1. **Split marker workflow**
1. Assert no Split Edit button exists and no code path depends on split mode.
2. Assert `Add Split Marker` still creates markers.
3. Assert marker drag works in Pan mode and does not interfere with crop/redact tools.
4. Assert ruler knob rendering/drag updates marker positions and undo/redo.

2. **Layout + quick zoom UI**
1. Assert top `Page Layout & Print Preview` uses two columns with controls left and Quick Zoom right.
2. Assert Header/Footer labels are above editors.
3. Assert Quick Zoom follows cursor on main image and freezes last frame off-image.
4. Assert magnification spinner default is `12x` and changes sample scaling.

3. **Floating magnifier + thumbnail behavior**
1. Assert floating magnifier appears left of cursor and remains on-screen.
2. Assert thumbnail row remains full-width and supports slider zoom + `Ctrl+wheel`.
3. Assert strong margin/gutter/border overlays are visible in thumbnails.
4. Assert thumbnail hover temporarily overlays main editor and restores after leave.

4. **Regression coverage**
1. Update widget identity and tooltip coverage tests for removed/added controls.
2. Keep existing split marker, page preview, and export-related tests passing.
3. Ensure no regressions in existing editor tools and page slicing refresh behavior.

#### Assumptions and Defaults
1. Split dragging is enabled only in `Pan` tool to avoid conflicts with crop/redact interactions.
2. Quick Zoom default magnification is `12x`, adjustable via spinner.
3. Quick Zoom tracks main editor image hover; off-image behavior freezes last sampled frame.
4. Floating magnifier is main-editor-only and rendered left of cursor with screen-bound clamping.
5. Bottom thumbnail preview remains full-width, and thumbnail hover temporarily overlays the main editor view.

## 2026-03-10

### Editor Preview UX Cleanup + View Zoom Spinner

- Added a new editor view zoom spinner (`10..400%`) in the `View` group and synced it with fit/manual zoom actions, plus/minus buttons, and wheel-driven manual zoom updates.
- Reworked `Page Layout & Print Preview` controls into a single wide `QFormLayout` so controls no longer cluster only on the left.
- Updated thumbnail decoration legend so `Margins`, `Gutter`, and `Border` explanation colors match their visual cue colors.
- Changed thumbnail-hover behavior so the bottom thumbnail preview image remains unchanged, while the editor canvas hides the full captured image area when hover preview is active.
- Changed hover overlay generation to use the same decorated thumbnail imagery as the bottom thumbnail row (no hover-specific image masking).
- Replaced proportional in-image thumbnail guides with a virtual-page preview model based on export layout math (paper size, orientation, margins, and gutter).
- Updated thumbnail cues so border/margins/gutter are drawn from page geometry outside the image content when appropriate, with top-left anchored image placement matching export semantics.
- Added GUI coverage validating virtual-page canvas sizing, margin inset placement, short-slice blank area below content, and right-side gutter band placement.
- Added and updated GUI tests for widget identity, single-wide layout behavior, cue/legend color consistency, hover-overlay masking, zoom spinner sync, and tooltip coverage.
