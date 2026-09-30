"""Custom exceptions for the parsing layer (spec §4 error handling)."""

from __future__ import annotations


class DockerfileParseError(Exception):
    """Base class for all parser errors."""


class MissingFromError(DockerfileParseError):
    """Dockerfile has instructions but no FROM line (spec §4 — malformed input)."""


class EmptyDockerfileError(DockerfileParseError):
    """Dockerfile is empty or comment/whitespace only.

    Not always fatal — the parser returns a graceful empty object for this case
    and records a warning instead of raising, per spec §4. Reserved for callers
    that want strict behaviour.
    """


class ManifestParseError(Exception):
    """Base class for manifest-layer errors (spec §5)."""


class ManifestNotFoundError(ManifestParseError):
    """A referenced requirements file (-r target) could not be located."""
