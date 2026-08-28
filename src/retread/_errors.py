"""Exception hierarchy for retread."""


class RetreadError(Exception):
    """Base exception for all retread errors."""


class WheelNotFoundError(RetreadError):
    """No matching wheel was found in the package index."""

    def __init__(self, filename: str, index: str) -> None:
        self.filename = filename
        self.index = index
        super().__init__(f"No matching wheel found for {filename} on {index}")


class InvalidWheelError(RetreadError):
    """The wheel filename is not valid."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(f"Invalid wheel filename: {filename}")


class ComparisonError(RetreadError):
    """An error occurred during wheel comparison."""


class PolicyError(RetreadError):
    """An error occurred loading or applying a policy."""
