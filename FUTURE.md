# Future plans

## Version validation

- Verify that wheel filename versions are PEP 440 normalized
  (`str(Version(v)) == v`).  Path construction for dist-info / data
  directories assumes normalized versions; a non-normalized version
  would produce wrong lookup paths.

## JSON report tool

- Tool to consume a JSON report (from `retread compare -f json`) and
  print it in the same format as `retread compare` text output.

## CLI and library improvements

- Consider using pydantic for data validation and serialization.
- Consider using click instead of argparse for the CLI.
