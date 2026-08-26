# Future plans

## CLI

- `diff` subcommand to show diff / content of text files

## WHEEL property validation

- Validate `Root-Is-Purelib` property against wheel tags.
- Validate that `Tag` property and wheel filename tags are aligned.

## Version validation

- Verify that wheel filename versions are PEP 440 normalized
  (`str(Version(v)) == v`).  Path construction for dist-info / data
  directories assumes normalized versions; a non-normalized version
  would produce wrong lookup paths.

## Platform and ABI checks

- Wheels with shared libraries must be platlib and have a platform-specific
  tag (not `any`).
- If a wheel contains a file like `.cpython-314-x86_64-linux-gnu.so`, then
  it must be Python version specific (see `importlib.machinery.EXTENSION_SUFFIXES`).
- If a wheel contains a file like `.abi3.so` or `.abi3t.so`, then the first
  tag entry must be `cp3xx`, second either `cp3xx` or `abi3`.
- `.so` or `lib*.so` or `lib*.so.*` implies platlib.
- If a wheel has a `scripts` directory with a reasonably large file, then it
  can be platlib too. This is a weak heuristic.
