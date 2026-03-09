# web-pagez-to-pdf

Windows-first PySide app for browser-window capture, full-page scroll stitching, and multi-format document export.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)

- [Usage](#usage)
- [Configuration](#configuration)
- [Logging](#logging)

- [Project Structure](#project-structure)
- [Architecture Patterns](#architecture-patterns)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Legal Disclaimer](#legal-disclaimer)

## Features

- Select capture targets using a window list picker or crosshair picker.
- Global shortcuts:
  - `Ctrl+Shift+C` capture selected viewport
  - `Ctrl+Shift+S` full-page auto-scroll capture
  - `Ctrl+Shift+X` stop active full capture
- Floating always-on-top stop badge: hovering mouse over it requests stop.
- Full-page capture includes repeated-frame end detection and max-page safety cap.
- Session queue supports import, reorder, remove, combine export mode, and single-item mode.
- Editor controls include zoom, rotate, crop, split markers, and navigation auto-crop suggestion.
- Export targets in one run:
  - PDF
  - Paged PNG images (`..._p001.png`, `..._p002.png`, ...)
  - Long PNG
  - Multipage TIFF
  - DOCX
  - PPTX
  - XLSX
- Pro print controls:
  - full paper set, orientation, margins, gutter
  - blank-row split threshold/search window
  - rich header/footer templates with tokens
- Runtime configuration path support through `threep-commons`.

## Requirements

- **Python 3.13+**

- **Windows** (10 or later)


## Installation

```bat
python scripts\windows\setup_env.py
```

Creates the local `.venv` by running `uv sync --locked` and falls back to `uv sync` when no lockfile is available yet.

Manual development alternative:

```bat
uv sync --group dev
```


## Usage

### Recommended

```bat
pyw scripts\windows\run_app_gui.pyw
```

### Direct

```bat
python -m web_pagez_to_pdf
```

### Typical Workflow

1. Pick a browser target window (list or crosshair).
2. Capture viewport (`Ctrl+Shift+C`) or full scroll (`Ctrl+Shift+S`).
3. Optional: hover red stop badge or press `Ctrl+Shift+X` to stop long capture.
4. Refine image in editor controls (crop/splits/zoom/rotate).
5. Select output formats and export.

## Configuration

Application identity is defined in `src/web_pagez_to_pdf/constants.py` and passed directly to `threep_commons`.

- configure QSettings with `threep_commons.paths.configure_qsettings(APP_IDENTITY, config_dir_override=...)`
- resolve runtime storage with `threep_commons.paths.resolve_app_data_dir(APP_IDENTITY, override_dir=...)`
- use `CONFIG_DIR` and `DATA_DIR` for environment overrides

## Logging

Initialize runtime logging directly through `threep_commons.logging`.

```python
from web_pagez_to_pdf.constants import APP_IDENTITY
from threep_commons.logging import setup_logging_from_identity

setup_logging_from_identity(APP_IDENTITY)
```


## Project Structure

```text
web-pagez-to-pdf/
|-- pyproject.toml
|-- uv.lock
|-- src/
|   `-- web_pagez_to_pdf/
|       |-- __init__.py
|       |-- __main__.py

|       |-- constants.py

|       `-- py.typed
|-- scripts/
|   |-- policy/
|   |   `-- check_standard.py
|   `-- windows/

|       |-- run_app.py
|       |-- run_app_gui.pyw

|       |-- run_tests.py
|       `-- setup_env.py
|-- tests/
|   |-- __init__.py

|   |-- conftest.py

|   |-- unit/
|   |   `-- __init__.py
|   `-- integration/

|       |-- __init__.py
|   `-- gui/
|       `-- __init__.py

`-- .pre-commit-config.yaml
```

## Architecture Patterns

- Keep top-level window/dialog classes thin and delegate feature logic to focused collaborators.
- Split large concerns into separate modules (actions/layout/operations/persistence/status) while preserving public imports.
- Split GUI tests by feature domain instead of building one large end-to-end test file.
- See `docs/architecture/qt_composition_playbook.md` for the reusable Qt decomposition workflow.

## Future Work

- Add an optional Chromium CDP capture backend (when a debugging endpoint is available) while keeping the current Win32 client-area pipeline as the default and fallback.

## Development

```bat
hatch run test
hatch run test-cov
hatch run lint:check
hatch run lint:fmt
hatch run lint:types
hatch run lint:policy
hatch run lint:all
hatch build
```

## Troubleshooting

- For full-scroll capture, ensure selected target is a visible browser window and can receive PageDown input.
- If capture does not stop on page end soon enough, lower `max_capture_pages` or use `Ctrl+Shift+X`.

---

<!-- legal-disclaimer:start -->
## Legal Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, STATUTORY, OR OTHERWISE, INCLUDING, WITHOUT LIMITATION, ANY IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT, ACCURACY, OR QUIET ENJOYMENT. TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, THE AUTHORS, CONTRIBUTORS, MAINTAINERS, DISTRIBUTORS, AND AFFILIATED PARTIES SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR ANY LOSS OF DATA, PROFITS, GOODWILL, BUSINESS OPPORTUNITY, OR SERVICE INTERRUPTION, ARISING OUT OF OR RELATING TO THE USE OF, OR INABILITY TO USE, THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THIS SOFTWARE HAS BEEN DEVELOPED, IN WHOLE OR IN PART, BY "INTELLIGENT TOOLS"; ACCORDINGLY, OUTPUTS MAY CONTAIN ERRORS OR OMISSIONS, AND YOU ASSUME FULL RESPONSIBILITY FOR INDEPENDENT VALIDATION, TESTING, LEGAL COMPLIANCE, AND SAFE OPERATION PRIOR TO ANY RELIANCE OR DEPLOYMENT.
<!-- legal-disclaimer:end -->
