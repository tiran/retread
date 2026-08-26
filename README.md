# retread

A retread (/ˈriːtrɛd/) is a used tire whose tread has been replaced --
**retread** checks the quality of rebuilt Python wheels.

**retread** inspects and compares Python wheels. Given a downstream
rebuild, it resolves the corresponding upstream wheel and reports which
files differ, are missing, or were added -- without downloading entire
wheel files.

It uses [zipwire](https://github.com/tiran/zipwire) for efficient
remote wheel access via HTTP range requests and
[pypi-simple](https://github.com/jwodder/pypi-simple) to interact with
package indexes.

> **Note:** retread does not rebuild wheels. It only inspects and
> compares existing wheels.

## Features

- Compare upstream and downstream wheels without downloading entire files
- Detect missing, extra, and modified files between wheel builds
- CRC-32 based fast comparison from ZIP central directory metadata
- Platform and ABI consistency checks (shared libraries, extension
  modules, `Root-Is-Purelib`, tag validation)
- Show unified diffs of individual files between wheels
- Both synchronous and asynchronous APIs
- Pluggable HTTP backends: requests (default), httpx2, aiohttp
- Text and JSON output formats

## Installation

```bash
pip install retread
```

With optional async HTTP backends:

```bash
pip install retread[aiohttp]    # aiohttp (async)
pip install retread[httpx2]     # httpx2 (async, HTTP/2)
pip install retread[all]        # all backends
```

## Usage

### Compare wheels

Compare a downstream wheel against its upstream source on PyPI:

```bash
# Compare by URL
retread compare https://rebuild.example/foo-1.0-py3-none-any.whl

# Compare a local wheel file
retread compare /path/to/foo-1.0-py3-none-any.whl

# Resolve from a downstream index
retread compare foo-1.0-py3-none-any.whl --downstream-index https://rebuild.example/simple/

# JSON output
retread compare foo-1.0-py3-none-any.whl --downstream-index https://rebuild.example/simple/ -f json
```

### Diff files

Show unified diffs of specific files between upstream and downstream wheels:

```bash
# Diff the METADATA file
retread diff https://rebuild.example/foo-1.0-py3-none-any.whl foo-1.0.dist-info/METADATA

# Diff multiple files
retread diff /path/to/foo-1.0-py3-none-any.whl foo-1.0.dist-info/METADATA foo-1.0.dist-info/WHEEL
```

## Limitations

- **CPython only** -- PyPy and other Python implementations are not supported.
- **Linux only** -- Windows and macOS platform tags are not supported.
- **Architecture-based matching** -- Upstream wheel matching uses CPU
  architecture suffixes (e.g. `x86_64`, `aarch64`) to fuzzy-match `linux`
  and `manylinux` platform tags. Manylinux version specifiers are ignored;
  a downstream `linux_x86_64` wheel will match an upstream
  `manylinux_2_28_x86_64` wheel. Musllinux wheels require an exact
  platform tag match.

## License

Apache-2.0
