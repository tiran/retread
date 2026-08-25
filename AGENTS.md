# retread

Compare downstream rebuilds of upstream Python wheels to detect differences
and bugs. Uses [zipwire](https://github.com/tiran/zipwire) for efficient
remote wheel access via HTTP range requests and
[pypi-simple](https://github.com/jwodder/pypi-simple) to interact with
package indexes.

## Source layout

- `src/retread/` - main package (comparison logic, CLI, resolution)
- `src/retread/backends/` - HTTP backend adapters (requests, httpx2, aiohttp)
- `tests/` - pytest test suite (async tests use `pytest-asyncio`)

## Virtual environment rules

- **Always** use the `.venv` virtual environment in the project root.
- Create it with `uv venv .venv` if it does not exist.
- **Never** install packages into the system Python or any other environment.
- Install the project: `uv pip install -e ".[all]" --python .venv/bin/python`
- Install dev deps: `uv pip install "pytest>=8.0" "pytest-asyncio>=0.24" "coverage[toml]>=7.0" --python .venv/bin/python`
- Run tests directly: `.venv/bin/python -m pytest tests/`
- Run any Python tool via `.venv/bin/python -m <tool>` or `.venv/bin/<tool>`.
- Use `uv pip install --python .venv/bin/python` for all pip operations.

## Code style rules

- **No local imports.** All imports must be at the top of the file.
  Do not use `from foo import bar` inside functions, methods, or test bodies.
  Exception: lazy imports of optional backend dependencies (aiohttp, httpx2,
  requests, zipwire.backends) are allowed inside properties and methods to
  avoid import errors when the dependency is not installed.

## Git rules

- **Always** sign off commits with `git commit -s`.
  Every commit message must include a `Signed-off-by:` line.

## Commands

```bash
# lint (ruff check + format)
uvx --with tox-uv tox run -e lint

# run tests (single Python version)
uvx --with tox-uv tox run -e py314

# run tests directly with .venv
.venv/bin/python -m pytest tests/ -v

# run full test matrix
uvx --with tox-uv tox run -e py312,py313,py314,py315

# run CLI
uv run retread compare <downstream-wheel-url>
```
