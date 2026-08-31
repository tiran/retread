"""Distinct string types for retread.

``typing.NewType`` aliases that record whether a ``str`` holds a URL or a
filename.  They carry no runtime cost (a ``NewType`` is the identity at
runtime) but let signatures and dataclass fields document which flavour of
string they expect.
"""

from __future__ import annotations

import typing

Url = typing.NewType("Url", str)
Filename = typing.NewType("Filename", str)
