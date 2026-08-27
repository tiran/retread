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

## Local version suffix stripping

- Support comparing downstream wheels that carry a midstream or local
  version suffix (PEP 440 `+local`), e.g.
  `autogluon_timeseries-1.5.0+rhaiv.5`. When resolving the upstream
  wheel, strip the local segment to find the base version (`1.5.0`)
  on PyPI. The differing dist-info prefix
  (`autogluon_timeseries-1.5.0+rhaiv.5.dist-info/` vs
  `autogluon_timeseries-1.5.0.dist-info/`) should be handled the
  same way as cross-version differences.

## Better upstream resolution errors

- Distinguish between: package does not exist on the upstream index,
  version does not exist (package found but not that version), and
  no matching wheel (version exists but no compatible wheel for the
  platform/ABI tags). Currently all three cases raise the same
  `WheelNotFoundError`.

## Ignore test directories

- Ignore changes and missing files inside `test/` and `tests/`
  subdirectories.  Some upstream wheels ship test suites that
  downstream rebuilds may modify or omit.  These differences are not
  meaningful for the rebuild comparison and should be classified as
  NOTICE rather than ERROR.

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

## Extension module name matching

- Handle the case where the upstream wheel uses a higher minimum
  CPython version with free-threaded stable ABI (e.g.
  `cp315-abi3.abi3t-manylinux_2_17_x86_64` with `.abi3t.so` extensions)
  while the downstream rebuild targets the regular stable ABI at a
  lower version (e.g. `cp39-abi3` with `.abi3.so` extensions).
  Currently the resolver picks the upstream wheel by tag compatibility
  and the comparison flags every extension module as mismatched because
  the suffixes differ (`.abi3t.so` vs `.abi3.so`).
  Example: `ast_serialize-0.8.0` upstream has
  `ast_serialize.abi3t.so` (cp315-abi3.abi3t) while downstream has
  `ast_serialize.abi3.so` (cp39-abi3) — same library, different
  stable ABI variant.
- When multiple abi3/abi3t wheels exist for the same version, prefer
  the wheel whose minimum CPython version is closest to (or matches)
  the downstream wheel, or treat the abi3/abi3t suffix difference as
  an expected platform difference rather than an error.
- Also handle the case where upstream uses stable ABI (`.abi3.so`)
  while downstream builds a version-specific extension
  (`.cpython-312-x86_64-linux-gnu.so`), or vice versa.  The extension
  module is the same code linked against different ABIs.
  Example: `pyzmq-27.2.0` upstream has `_zmq.abi3.so` (cp312-cp312)
  while downstream has `_zmq.cpython-312-x86_64-linux-gnu.so` — same
  module, different ABI linkage.

## Cross-case dist-info and data prefix matching

- When upstream and downstream use differently-cased dist names in the
  wheel filename (e.g. `ImageHash` vs `imagehash`, `SCons` vs `scons`,
  `InquirerPy` vs `inquirerpy`), the `.dist-info/` and `.data/`
  prefixes differ.  Files that appear in `only_upstream` and
  `only_downstream` with matching paths under the respective prefixes
  should be paired and compared rather than flagged as missing.
- Currently `_classify_file` only tries the downstream dist prefix, so
  upstream dist-info files with a different-case prefix fall through to
  `[other]` classification instead of their proper labels (RECORD,
  WHEEL, METADATA, etc.).  Classification should try both prefixes.
  Example: `InquirerPy-0.3.4.dist-info/LICENSE` is classified as
  `[other]` instead of `[license]`.

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

## Namespace package .pth files

- Namespace package `.pth` files embed the build-time Python version
  in their filename (e.g. `sphinxcontrib_jsmath-1.0.1-py3.7-nspkg.pth`
  vs `py3.12-nspkg.pth`).  These should be matched by stripping the
  Python version from the filename, or classified as NOTICE.
  Affects: `sphinxcontrib_jsmath`.

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

## Classify auto-generated source files

- Cython-generated `.c` and `.cpp` files are included in some wheels
  alongside the compiled extensions.  When the downstream rebuild uses
  a different Cython version, the generated sources differ even though
  the compiled extensions are functionally equivalent.  These files
  should be classified as NOTICE rather than ERROR.
  Affects: `biotite`, `biotraj`, `cftime`, `scikit_network`, `srsly`,
  `thriftpy2`, `spacy`.
- Similarly, ANTLR-generated parser files (lexers, parsers, listeners,
  visitors) differ when rebuilt with a different ANTLR version.
  Affects: `hydra_core`, `omegaconf`.
- Protobuf-generated `_pb2.py` and `_pb2_grpc.py` files differ when
  rebuilt with a different protobuf compiler version.  The generated
  code is functionally equivalent.
  Affects: `nvidia_riva_client`, `onnx`, `tensorflow_metadata`.

## JSON report tool

- Tool to consume a JSON report (from `retread compare -f json`) and
  print it in the same format as `retread compare` text output.
