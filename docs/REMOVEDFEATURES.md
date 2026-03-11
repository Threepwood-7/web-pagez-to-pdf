# Removed Features

## Wizardry Panel Removal (2026-03-11)

The `Wizardry` panel has been removed from the Editor tab.

### Removed behaviors

- `Apply to whole queue` toggle for Wizardry actions
- `Remove Vertical Scrollbar` action and its detection/trim implementation
- `Remove Window Border` action and its iterative border-peel implementation
- Wizard-specific `Undo` and `Redo` buttons

### Replacement / remaining behavior

- Global editor history is still available through:
  - `Edit -> Undo` / `Edit -> Redo`
  - `Ctrl+Z` / `Ctrl+Y`
- `Vertical Border Crop` is retained as a non-Wizard operation for the selected queue item only.
- Legacy persisted operations (`wizard_scrollbar_trim`, `wizard_border_trim`) are safely ignored at transform time.
