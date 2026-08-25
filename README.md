# retread

A retread is a rebuilt tire -- **retread** rebuilds wheels and checks quality.

**retread** compares downstream rebuilds of upstream Python wheels to detect
differences and bugs. It uses [zipwire](https://github.com/tiran/zipwire) for
efficient remote wheel access via HTTP range requests and
[pypi-simple](https://github.com/jwodder/pypi-simple) to interact with
package indexes.

## Features

- Compare upstream and downstream wheels without downloading entire files
- Detect missing, extra, and modified files between wheel builds
- CRC-32 based fast comparison from ZIP central directory metadata
- Both synchronous and asynchronous APIs
- Pluggable HTTP backends: requests (default), httpx2, aiohttp
- CLI for quick comparisons

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
