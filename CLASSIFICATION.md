# Classifying wheel differences

When retread compares an upstream wheel against a downstream rebuild, every
differing or missing file is assigned a **severity** (`notice` or `error`) and
a **classification** label that explains *why* the difference is expected or
not.

## Severity

| Severity | Meaning |
|----------|---------|
| `notice` | Expected difference -- the rebuild is still considered good. |
| `error`  | Unexpected difference -- the rebuild may be broken or contain unwanted changes. |

A comparison exits with code 0 when all differences are notices, and code 2
when at least one error is present.

## Classification labels

Classification labels are shown in square brackets in CLI output
(e.g. `[dist-info RECORD]`, `[extension module]`).  They are derived from
the [binary distribution format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)
using the distribution name and version extracted verbatim from the wheel
filename (no normalisation).

### dist-info files

The `{dist}-{version}.dist-info/` directory contains package metadata.
All differences here are notices because downstream rebuilds routinely
modify these files.

| Classification | Path pattern | Notes |
|----------------|--------------|-------|
| `dist-info METADATA` | `{dist}-{version}.dist-info/METADATA` | Core metadata fields (Name, Version, Requires-Dist, Provides-Extra) are compared separately; if they differ the severity is upgraded to error. |
| `dist-info RECORD` | `{dist}-{version}.dist-info/RECORD` | File hash manifest -- always differs when any file changes. |
| `dist-info WHEEL` | `{dist}-{version}.dist-info/WHEEL` | Wheel metadata (generator tag, etc.). |
| `sbom` | `{dist}-{version}.dist-info/sboms/...` | Software bill of materials files. |
| `license` | `{dist}-{version}.dist-info/licenses/...` | License files. |
| `dist-info` | Any other `{dist}-{version}.dist-info/...` | Catch-all for other dist-info files (e.g. fromager build metadata). |

### Data directory

The `{dist}-{version}.data/` directory contains files installed into
non-purelib locations as defined by the wheel spec (`scripts`, `data`,
`headers`, `purelib`, `platlib`).

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `data scripts` | `{dist}-{version}.data/scripts/...` | notice | error |
| `data` | `{dist}-{version}.data/{other}/...` | notice | error |

Binary differences in data files are expected (compiled executables,
platform-specific resources).  Missing files are errors because they
indicate the downstream wheel is incomplete.

### Shared libraries

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `auditwheel` | `{dist}.libs/...` | notice | notice |
| `extension module` | `*.so` (not in `*.libs/`) | notice | error |

**auditwheel** vendors external shared libraries into a `{dist}.libs/`
directory.  These may appear, disappear, or change between builds -- all
notices.

**Extension modules** (`.so` files in the package tree) are expected to
differ in content (compiled from source) but must be present on both
sides.

### Other

| Classification | Meaning |
|----------------|---------|
| `other` | Any file that does not match the patterns above.  Always an error. |

## Example output

```
Errors (1):
  - PIL/_avif.cpython-312-x86_64-linux-gnu.so (upstream only) [extension module]

Notices (4):
  - pillow.libs/libXau-154567c4.so.6.0.0 (upstream only) [auditwheel]
  - pillow-12.3.0.dist-info/sboms/auditwheel.cdx.json (upstream only) [sbom]
  ~ PIL/_imaging.cpython-312-x86_64-linux-gnu.so (3402345 -> 1990328 bytes) [extension module]
  ~ pillow-12.3.0.dist-info/RECORD (11349 -> 9920 bytes) [dist-info RECORD]
```
