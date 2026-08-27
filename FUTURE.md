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

## Per-package policy extensions

- Add ``**`` recursive glob support to policy patterns using
  ``PurePosixPath.full_match()`` (Python 3.13+).
- Support version-range selectors (e.g. ``">=4.0,<5.0"``) in
  policy files using ``packaging.specifiers.SpecifierSet``.
- Allow specifying expected dependency-metadata differences instead of
  the all-or-nothing ``ignore_dependency_metadata`` boolean.  For
  example, glob or requirement patterns matched against individual
  ``Requires-Dist`` / ``Provides-Extra`` entries, so an accidentally
  dropped *required* dependency is still surfaced while known
  downstream adjustments are accepted.

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
- Related: labextension `package.json` files embed a build hash and
  differ between rebuilds even when the paired bundle is unchanged;
  these should be treated as an expected difference too.
  Affects: `jupyter_leaflet`.

## Classify stray VCS files

- Files like `.gitignore`, `.gitattributes`, and `.keep` are
  occasionally shipped in an upstream wheel but dropped by the
  downstream build.  Add a built-in retread rule to classify these as
  a NOTICE (or ignore missing) rather than an error, instead of
  requiring a per-package policy.
  Affects: `chromadb` (`chromadb/proto/.gitignore`).

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
