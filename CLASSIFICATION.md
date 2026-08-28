# Classifying wheel differences

When retread compares an upstream wheel against a downstream rebuild, every
differing or missing file is assigned a **severity** and a **classification**
label.  Additional structural checks validate RECORD integrity, METADATA
consistency, and platform/ABI correctness.

## Severity

| Severity   | Meaning |
|------------|---------|
| `expected` | Always-expected difference (RECORD, WHEEL, compiled binaries). |
| `notice`   | Acceptable difference -- the rebuild is still considered good. |
| `error`    | Unexpected difference -- the rebuild may be broken or contain unwanted changes. |

A comparison exits with code 0 when all differences are expected or notices,
and code 2 when at least one error, RECORD mismatch, or platform warning is
present.

## File classification

Classification labels are shown in square brackets in CLI output
(e.g. `[dist-info RECORD]`, `[extension module]`).  They are derived from
the [binary distribution format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)
using the distribution name and version extracted verbatim from the wheel
filename (no normalisation).

### dist-info files

The `{dist}-{version}.dist-info/` directory contains package metadata.

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `dist-info RECORD` | `RECORD` | expected | expected |
| `dist-info WHEEL` | `WHEEL` | expected | expected |
| `dist-info METADATA` | `METADATA` | notice | notice |
| `sbom` | `sboms/...` | notice | notice |
| `license` | `licenses/...` | notice | notice |
| `dist-info` | anything else | notice | notice |

RECORD and WHEEL always differ when any file changes or the build
environment differs.  METADATA differences are notices unless core
fields differ (see [METADATA validation](#metadata-validation) below).

### Data directory

The `{dist}-{version}.data/` directory contains files installed into
non-purelib locations as defined by the wheel spec (`scripts`, `data`,
`headers`, `purelib`, `platlib`).

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `data scripts` | `scripts/...` | expected | error |
| `data` | any other subdir | notice | error |

Binary differences in data scripts are expected (compiled executables).
Missing files are errors because they indicate the downstream wheel is
incomplete.

### Shared libraries

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `auditwheel` | `{dist}.libs/...` | notice | notice |
| `extension module` | `*.so` (not in `*.libs/`) | expected | error |

**auditwheel** vendors external shared libraries into a `{dist}.libs/`
directory.  These may appear, disappear, or change between builds -- all
notices.

**Extension modules** (`.so` files in the package tree) are expected to
differ in content (compiled from source) but must be present on both
sides.

### Static libraries

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `static library` | `lib*.a` | expected | error |

Static archive files (`lib*.a`) are compiled at build time and their
content is expected to differ between builds.  A missing static library
is an error.

### Auto-generated version files

| Classification | Path pattern | Severity (diff) | Severity (missing) |
|----------------|--------------|------------------|--------------------|
| `version file` | `_version.py`, `__version__.py`, `version.py`, `__config__.py` | notice | notice |

Build tools like setuptools-scm, hatch-vcs, and versioningit auto-generate
version files at build time.  Their content (layout, variable names,
embedded VCS info) commonly differs between upstream and downstream builds.
meson-python generates `__config__.py` files (used by NumPy and other
projects) that record build-time configuration (compiler flags, library
paths) and differ for the same reason.  These differences are always
notices.

### Other

| Classification | Meaning |
|----------------|---------|
| `other` | Any file that does not match the patterns above.  Always an error. |

## METADATA validation

After initial file comparison, retread reads the `METADATA` file from
both sides and compares the following core fields:

- `Name` (single-value, canonicalized per PEP 503 so that hyphens,
  underscores, and case differences are ignored)
- `Version` (single-value, exact match)
- `Requires-Dist` (multi-value, each entry normalized via
  `packaging.requirements.Requirement` with canonicalized distribution
  names and version specifiers normalized via
  `packaging.utils.canonicalize_version` so that equivalent spellings
  like `<5` and `<5.0` compare equal; compared as a set -- whitespace,
  quoting, name spelling such as hyphens vs underscores, and order
  differences are ignored)
- `Provides-Extra` (multi-value, each name canonicalized per PEP 685 so
  that `code_syntax_highlighting` and `code-syntax-highlighting` compare
  equal; compared as a set -- order differences are ignored)

If the core fields match, the METADATA diff stays at `notice` severity.
If they differ, the severity is upgraded to `error`.  Non-core fields
(Description, Author, classifiers, etc.) are ignored.

When upstream and downstream use different dist-info prefixes (e.g.
different local-version segments), the METADATA files appear in
`only_upstream` / `only_downstream` rather than `different`.  Core
fields are still compared and severities are updated accordingly.

### Field-level differences

For the multi-value fields `Requires-Dist` and `Provides-Extra`,
retread reports the normalized entries present on only one side as
**metadata field differences** (`metadata_field_diffs` in the result
and JSON output).  Entries are normalized before comparison, so
cosmetic differences (whitespace, quoting, dependency name spelling,
version spelling, extra name spelling, ordering) are not reported --
only genuine additions or removals.  This
pinpoints *which* requirements or extras changed rather than merely
flagging that METADATA differs.

## RECORD validation

Each wheel's `RECORD` file (CSV manifest) is cross-validated against
the ZIP central directory.  Issues are reported as **RECORD mismatches**
and always trigger an error exit.

### Structure checks

- Each CSV row must have exactly 3 fields: `filename,hash,size`.
- Hash must use `algorithm=digest` format (e.g. `sha256=...`).
- Size must be a valid integer.
- Only the RECORD file itself and its deprecated signatures
  (`RECORD.p7s`, `RECORD.jws`) may have empty hash and size columns.

### Consistency checks

- Files present in the ZIP but not listed in RECORD.
- Files listed in RECORD but not present in the ZIP.
- Size mismatches between RECORD and the ZIP central directory.
- Missing or empty RECORD file.

## Platform and ABI checks

Each wheel is independently validated for internal consistency between
its filename tags, `WHEEL` file properties, and actual file contents.
Issues are reported as **platform warnings** and trigger an error exit.

### WHEEL Tag vs filename tag alignment

The `Tag` entries in the `WHEEL` file must match the tags encoded in
the wheel filename.  A mismatch indicates the wheel was repacked or
renamed incorrectly.  The warning lists tags present only in the
WHEEL file and tags present only in the filename.

### Root-Is-Purelib consistency

If a wheel contains shared libraries (`.so` files, excluding
auditwheel `*.libs/` directories) but the `WHEEL` file sets
`Root-Is-Purelib: true`, a warning is raised.  Shared libraries
require platlib installation.

### Platform tag consistency

If a wheel contains shared libraries but all filename tags use the
`any` platform, a warning is raised.  Shared libraries are
platform-specific and need a concrete platform tag (e.g.
`linux_x86_64`, `manylinux_2_28_aarch64`).

Conversely, if a wheel has platform-specific tags but contains no
shared libraries or native extensions, a warning is raised.  This
typically indicates a pure Python package that was incorrectly built
as a platform wheel.

### CPython extension version matching

Extension modules with cpython-specific suffixes like
`.cpython-312-x86_64-linux-gnu.so` embed the Python version.  The
wheel's filename tags must include the corresponding interpreter
(e.g. `cp312`).  A mismatch indicates the wheel was built for a
different Python version than its tags claim.

### Stable ABI (abi3 / abi3t) consistency

Extensions using the stable ABI (`.abi3.so`) or the free-threaded
stable ABI (`.abi3t.so`) require:

- At least one filename tag with `abi3` as the ABI component.
- A CPython interpreter (`cp3*`) in the filename tags.

### Version normalization

The version in the wheel filename must be PEP 440 normalized
(`str(Version(v)) == v`).  Path construction for dist-info and data
directories assumes normalized versions; a non-normalized version
would produce wrong lookup paths.  A warning is raised when the raw
filename version differs from its normalized form (e.g. `1.0.0`
vs `1.0`).

### METADATA Name and Version consistency

The `Name` and `Version` fields in the wheel's `METADATA` file must be
present and match the distribution and version from the wheel filename.
`Name` is compared after PEP 503 normalization (case, hyphen vs
underscore differences ignored); `Version` must match the normalized
filename version exactly.  A mismatch or a missing field indicates the
wheel was built or repacked incorrectly.  These checks are skipped only
when the METADATA file itself is not available.

### Data scripts heuristic

If `{dist}-{version}.data/scripts/` contains files larger than 8 KiB
and no shared libraries are otherwise present, but the wheel claims
`Root-Is-Purelib: true` or uses the `any` platform tag, a warning is
raised.  Large script files suggest native executables that require a
platform-specific tag.  This check is a heuristic and may produce
false positives for wheels with large shell scripts.

## Bundled virtual environments

Files under a `lib/python3.*/site-packages/` directory (for example
`.venv/lib/python3.11/site-packages/`) indicate that a virtual
environment was accidentally swept into the wheel during the build.
Each distinct `site-packages/` directory is reported once as a
**bundled virtual environment**.

A bundled environment in the **upstream** wheel is a NOTICE (a
pre-existing upstream packaging bug that the rebuild is not
responsible for).  A bundled environment in the **downstream** rebuild
is an ERROR, since a rebuild must not reproduce the packaging mistake.

The individual files inside a bundled environment are **not** listed in
the per-file diff (`only upstream` / `only downstream` / `different`).
They are collapsed into this single per-environment report so the
output is not flooded with hundreds of `site-packages/` entries.

## Example output

```
Errors (1):
  upstream only:
    PIL/_avif.cpython-312-x86_64-linux-gnu.so [extension module]

Notices (2):
  upstream only:
    pillow.libs/libXau-154567c4.so.6.0.0 [auditwheel]
    pillow-12.3.0.dist-info/sboms/auditwheel.cdx.json [sbom]

Expected (2):
  different:
    PIL/_imaging.cpython-312-x86_64-linux-gnu.so (3402345 -> 1990328 bytes) [extension module]
    pillow-12.3.0.dist-info/RECORD (11349 -> 9920 bytes) [dist-info RECORD]
```
