# Future plans

## METADATA validation

- Report differences in the single-value core fields (`Name`,
  `Version`) when METADATA comparison triggers an error. Differences in
  the multi-value fields (`Requires-Dist`, `Provides-Extra`) are already
  reported per-entry via `metadata_field_diffs`.
- Validate METADATA structure and content (PEP 566 / PEP 643 / PEP 685
  conformance, required fields, well-formed values).

## Auto-generated version files

- When METADATA declares `Dynamic: version`, upgrade version file
  severity from NOTICE to EXPECTED since a dynamic version makes
  version file differences a guaranteed consequence of rebuilding.

## Better upstream resolution errors

- Distinguish between: package does not exist on the upstream index,
  version does not exist (package found but not that version), and
  no matching wheel (version exists but no compatible wheel for the
  platform/ABI tags). Currently all three cases raise the same
  `WheelNotFoundError`.

## Per-package policy configuration

- Support external configuration files that define per-package
  policies for expected differences.  Some packages have
  package-specific patterns that retread cannot handle with generic
  rules (bundled native binaries, vendored C headers, stripped data
  files, etc.).  A policy file could specify:
  - File glob patterns to ignore or reclassify (e.g.
    `cmake/data/bin/*` as EXPECTED, `lxml/includes/**/*.h` as NOTICE).
  - Expected missing files or directories per package.
  - Custom severity overrides for specific classifications.
- This would replace the need for many package-specific heuristics
  in retread's core logic.
  Examples: `cmake-4.4.2` bundles native executables in
  `cmake/data/bin/`; `lxml` bundles C headers in `lxml/includes/`
  that downstream strips when building against system libraries;
  `triton-3.7.1` bundles the NVIDIA toolchain in
  `triton/backends/nvidia/` that downstream strips.

## Flag .pth files as a warning

- `.pth` files (other than namespace package `-nspkg.pth`) can execute
  arbitrary code at interpreter startup.  Wheels containing `.pth`
  files should be flagged with a warning since they can be abused for
  code injection.  Namespace `-nspkg.pth` files are already classified
  as NOTICE and are not affected.

## Match content-hashed filenames in data directories

- Some wheels include content-hashed asset filenames (e.g.
  `remoteEntry.9aa97e6313eff609d36c.js`).  When the downstream rebuild
  produces the same file with a different hash in the filename (e.g.
  `remoteEntry.3248bfcd141f101e93c4.js`), retread reports both as
  missing rather than pairing them.  Match files by stripping the hash
  portion from the filename.
  Affects: `bqscales`, `jupyter_leaflet`.

## Detect native executables in data scripts [low]

- Consider checking file magic bytes or shebangs to distinguish
  native executables from interpreted scripts in `data/scripts/`.
  The current heuristic uses a file size threshold (64 KiB) which
  may still produce false positives for very large scripts.

## Missing license files

- Detect when upstream includes a license file (LICENSE, LICENCE,
  COPYING, or files in `licenses/`) and the downstream rebuild is
  missing it.  Report as ERROR since license files must not be
  stripped from redistributed packages.
- Handle PEP 639 license file relocation: newer build backends move
  license files from the package directory into
  `{dist}-{version}.dist-info/licenses/`.  When the file moves
  between directories but is still present, classify as NOTICE
  rather than a missing-file error.

## JSON report tool

- Tool to consume a JSON report (from `retread compare -f json`) and
  print it in the same format as `retread compare` text output.
