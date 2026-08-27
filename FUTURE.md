# Future plans

## METADATA validation

- Better track and report which core fields differ when METADATA
  comparison triggers an error (currently only reports that core fields
  differ, not which ones or how).
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

## Cross-case data prefix matching

- When upstream and downstream use differently-cased dist names in the
  wheel filename (e.g. `ImageHash` vs `imagehash`, `SCons` vs `scons`,
  `InquirerPy` vs `inquirerpy`), the `.data/` prefixes may differ.
  Files that appear in `only_upstream` and `only_downstream` with
  matching paths under the respective data prefixes should be paired
  and compared rather than flagged as missing.

## Normalize compound wheel tags

- The WHEEL file may list compound tags like
  `cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64` while the
  filename splits them into separate tags `manylinux_2_17_x86_64` and
  `manylinux2014_x86_64`.  These are equivalent per PEP 600 but the
  current tag comparison treats them as mismatched.  Normalize compound
  tags before comparing.
  Affects: `base2048`, `fastuuid`, `openai_harmony`, `tlparse`,
  `minify_html`, `polars`.
- The same issue applies to interpreter compound tags: the WHEEL file
  may list `py2.py3-none-any` while the filename splits it into
  `py2-none-any` and `py3-none-any`.
  Affects: `pysnow`.

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

## Detect bundled virtual environments

- Detect files matching `**/lib/python3.*/site-packages/` inside
  wheels.  This pattern indicates a virtual environment was
  accidentally bundled into the wheel.
  Example: `nemoguardrails-0.21.0` upstream includes
  `.venv/lib/python3.11/site-packages/` with pip and other installed
  packages.
- Report as a warning for upstream wheels and an error for downstream
  wheels (a rebuild should not reproduce this packaging mistake).

## Improve data scripts heuristic

- The large scripts heuristic flags `data/scripts/` files larger than
  8 KiB as potential native executables, but produces false positives
  for large shell or Python scripts.
  Affects: `pdfminer_six`, `pyelftools`, `xlrd`.
- Consider checking file magic bytes or shebangs to distinguish native
  executables from interpreted scripts.

## JSON report tool

- Tool to consume a JSON report (from `retread compare -f json`) and
  print it in the same format as `retread compare` text output.
