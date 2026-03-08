# web-pagez-to-pdf

Windows-first PySide app for capturing the second last active window to PNG.

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

- Captures the second last active window screenshot to PNG.
- Supports both button trigger and `Ctrl+Shift+S` shortcut.
- Automatically opens the saved PNG in the default image viewer.
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

- If capture fails, ensure there is another visible, non-minimized app window active before returning to this app.

---

<!-- legal-disclaimer:start -->
## Legal Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, STATUTORY, OR OTHERWISE, INCLUDING, WITHOUT LIMITATION, ANY IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT, ACCURACY, OR QUIET ENJOYMENT. TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, THE AUTHORS, CONTRIBUTORS, MAINTAINERS, DISTRIBUTORS, AND AFFILIATED PARTIES SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR ANY LOSS OF DATA, PROFITS, GOODWILL, BUSINESS OPPORTUNITY, OR SERVICE INTERRUPTION, ARISING OUT OF OR RELATING TO THE USE OF, OR INABILITY TO USE, THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THIS SOFTWARE HAS BEEN DEVELOPED, IN WHOLE OR IN PART, BY "INTELLIGENT TOOLS"; ACCORDINGLY, OUTPUTS MAY CONTAIN ERRORS OR OMISSIONS, AND YOU ASSUME FULL RESPONSIBILITY FOR INDEPENDENT VALIDATION, TESTING, LEGAL COMPLIANCE, AND SAFE OPERATION PRIOR TO ANY RELIANCE OR DEPLOYMENT.
<!-- legal-disclaimer:end -->
