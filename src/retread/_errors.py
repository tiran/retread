"""Exception hierarchy for retread."""


class RetreadError(Exception):
    """Base exception for all retread errors."""


class WheelNotFoundError(RetreadError):
    """No matching wheel was found in the package index.

    Base class for the more specific upstream-resolution failures
    (:class:`ProjectNotFoundError`, :class:`VersionNotFoundError`,
    :class:`NoWheelsError`), so callers can still catch the whole family with
    ``except WheelNotFoundError``.
    """

    def __init__(self, filename: str, index: str, message: str | None = None) -> None:
        self.filename = filename
        self.index = index
        super().__init__(message or f"No matching wheel found for {filename} on {index}")


class ProjectNotFoundError(WheelNotFoundError):
    """The project does not exist on the package index."""

    def __init__(self, filename: str, index: str, project: str) -> None:
        self.project = project
        super().__init__(filename, index, f"Project {project!r} not found on {index}")


class VersionNotFoundError(WheelNotFoundError):
    """The project exists but the requested version is not available."""

    def __init__(
        self,
        filename: str,
        index: str,
        project: str,
        version: str,
        available_versions: tuple[str, ...],
    ) -> None:
        self.project = project
        self.version = version
        self.available_versions = available_versions
        listed = ", ".join(available_versions) if available_versions else "none"
        super().__init__(
            filename,
            index,
            f"Version {version} of {project!r} not found on {index}; available versions: {listed}",
        )


class NoWheelsError(WheelNotFoundError):
    """The requested version exists but is published only as a source distribution."""

    def __init__(self, filename: str, index: str, project: str, version: str) -> None:
        self.project = project
        self.version = version
        super().__init__(
            filename,
            index,
            f"No wheels for {project} {version} on {index} (source distribution only)",
        )


class InvalidWheelError(RetreadError):
    """The wheel filename is not valid."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(f"Invalid wheel filename: {filename}")


class InvalidMetadataError(RetreadError):
    """A wheel's METADATA file is malformed (missing or invalid core fields)."""


class ComparisonError(RetreadError):
    """An error occurred during wheel comparison."""


class PolicyError(RetreadError):
    """An error occurred loading or applying a policy."""
