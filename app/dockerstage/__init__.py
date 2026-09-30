"""dockerstage — refactor single-stage Python/ML Dockerfiles into multi-stage
builds with package-level dependency resolution.

Phase 1 exposes the parsing layer (Modules 1 & 2). Downstream phases import from
here.
"""

from __future__ import annotations

from .models import (
    ImageRef,
    SystemPackage,
    RunCommand,
    PipInstall,
    CopyInstruction,
    ShellOrExec,
    Stage,
    ParsedDockerfile,
    Requirement,
    Tier2Manifest,
    UNKNOWN_BASE_IMAGE,
)
from .errors import (
    DockerfileParseError,
    MissingFromError,
    EmptyDockerfileError,
    ManifestParseError,
    ManifestNotFoundError,
)
from .parser.dockerfile_parser import parse_dockerfile, parse_dockerfile_text, parse_many
from .parser.base_image import decompose_image
from .parser.manifest_parser import (
    parse_requirements_text,
    parse_requirements_file,
    detect_tier2_manifest,
)
from .knowledge_base import load as load_knowledge_base, validate as validate_knowledge_base

__all__ = [
    "ImageRef",
    "SystemPackage",
    "RunCommand",
    "PipInstall",
    "CopyInstruction",
    "ShellOrExec",
    "Stage",
    "ParsedDockerfile",
    "Requirement",
    "Tier2Manifest",
    "UNKNOWN_BASE_IMAGE",
    "DockerfileParseError",
    "MissingFromError",
    "EmptyDockerfileError",
    "ManifestParseError",
    "ManifestNotFoundError",
    "parse_dockerfile",
    "parse_dockerfile_text",
    "parse_many",
    "decompose_image",
    "parse_requirements_text",
    "parse_requirements_file",
    "detect_tier2_manifest",
    "load_knowledge_base",
    "validate_knowledge_base",
]
