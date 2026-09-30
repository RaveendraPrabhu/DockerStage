"""Shared data models for the parsing layer (spec §4 output structure, §5.3).

These dataclasses are the stable contract that every downstream module consumes.
Downstream code must never touch raw parser internals — only these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Sentinel used when an ARG-based FROM cannot be resolved (spec §4 item 1).
UNKNOWN_BASE_IMAGE = "unknown_base_image"


# --------------------------------------------------------------------------- #
# §4a — Base image decomposition
# --------------------------------------------------------------------------- #
@dataclass
class ImageRef:
    """Decomposed base-image reference (spec §4a).

    `python_version` is (major, minor) or None. When None, the classifier must
    obtain it from --python-version (spec §4a "Python version fallback").
    """

    raw: str
    registry: Optional[str] = None
    image_name: Optional[str] = None
    tag: Optional[str] = None
    digest: Optional[str] = None
    python_version: Optional[tuple] = None
    python_version_source: str = "unknown"  # "base_image_tag" | "unknown"
    libc_family: str = "unknown"            # "glibc" | "musl" | "unknown"
    distro_family: str = "unknown"          # "debian" | "alpine" | "ubuntu" | "unknown"
    variant: Optional[str] = None           # slim|alpine|full|runtime|devel|None
    resolved: bool = True                   # False if ARG resolution failed
    libc_detection_source: str = "unknown"  # base_image_tag|apk_command|apt_command|unknown

    @property
    def is_unknown(self) -> bool:
        return self.raw == UNKNOWN_BASE_IMAGE or not self.resolved


# --------------------------------------------------------------------------- #
# Instruction-level extractions
# --------------------------------------------------------------------------- #
@dataclass
class SystemPackage:
    """A system package named in an apt-get/apk install line (spec §4 item 4)."""

    name: str               # normalised package name (version pin stripped)
    pkg_manager: str        # "apt" | "apk"
    raw: str                # original token, e.g. "libpq-dev=13.1"
    line: int
    stage_index: int = 0


@dataclass
class RunCommand:
    raw: str                # full RUN argument text (continuations joined)
    subcommands: list = field(default_factory=list)  # split on && || ;
    line: int = 0
    stage_index: int = 0


@dataclass
class PipInstall:
    """A `pip install` invocation (spec §4 item 5)."""

    raw: str
    line: int
    pip_executable: str = "pip"                 # pip | pip3 | python -m pip
    requirement_files: list = field(default_factory=list)  # from -r / --requirement
    inline_packages: list = field(default_factory=list)    # packages named on the line
    stage_index: int = 0


@dataclass
class CopyInstruction:
    sources: list = field(default_factory=list)
    dest: Optional[str] = None
    from_stage: Optional[str] = None   # value of --from=
    chown: Optional[str] = None
    is_add: bool = False               # True for ADD, False for COPY
    line: int = 0
    stage_index: int = 0


@dataclass
class ShellOrExec:
    """CMD / ENTRYPOINT / HEALTHCHECK CMD — preserves exec vs shell form (spec §9.3)."""

    raw: str                     # argument text exactly as written
    form: str = "shell"          # "exec" | "shell"
    argv: Optional[list] = None  # parsed list for exec form, else None


@dataclass
class Stage:
    """One build stage (spec §4 item 2 — multi-stage split)."""

    index: int
    base_image: ImageRef
    alias: Optional[str] = None
    from_line: int = 0


# --------------------------------------------------------------------------- #
# Top-level parsed Dockerfile
# --------------------------------------------------------------------------- #
@dataclass
class ParsedDockerfile:
    """Structured Dockerfile (spec §4 "Output structure").

    `base_image` is the effective runtime base = the last stage's FROM. For the
    tool's primary single-stage input, that is the only stage.
    """

    path: Optional[str] = None
    raw_text: str = ""
    stages: list = field(default_factory=list)          # list[Stage]
    is_multistage: bool = False

    run_commands: list = field(default_factory=list)    # list[RunCommand]
    system_packages: list = field(default_factory=list)  # list[SystemPackage]
    pip_installs: list = field(default_factory=list)    # list[PipInstall]
    copies: list = field(default_factory=list)          # list[CopyInstruction]

    env: dict = field(default_factory=dict)
    args: dict = field(default_factory=dict)
    workdir: Optional[str] = None
    exposed_ports: list = field(default_factory=list)   # e.g. ["8501/tcp"]
    cmd: Optional[ShellOrExec] = None
    entrypoint: Optional[ShellOrExec] = None
    healthcheck: Optional[str] = None                   # raw args, or "NONE"
    user: Optional[str] = None

    tokenizer_used: Optional[str] = None                # "dockerfile-parse" | "stdlib"
    warnings: list = field(default_factory=list)

    @property
    def base_image(self) -> Optional[ImageRef]:
        return self.stages[-1].base_image if self.stages else None

    @property
    def builder_base_image(self) -> Optional[ImageRef]:
        return self.stages[0].base_image if self.stages else None

    @property
    def has_nonroot_user(self) -> bool:
        return self.user not in (None, "", "root", "0")

    def apt_packages(self) -> list:
        return [p for p in self.system_packages if p.pkg_manager == "apt"]

    def apk_packages(self) -> list:
        return [p for p in self.system_packages if p.pkg_manager == "apk"]


# --------------------------------------------------------------------------- #
# §5 — Manifest models
# --------------------------------------------------------------------------- #
@dataclass
class Requirement:
    """One declared dependency from a Tier-1 manifest (spec §5.3)."""

    name: str                              # project name as written (raw)
    normalized_name: str                   # PEP 503 canonical form (lookup key)
    version_specifier: str = ""            # e.g. ">=4.9.0"; "" if unconstrained
    extras: list = field(default_factory=list)     # sorted list, e.g. ["cuda"]
    markers: Optional[str] = None          # PEP 508 marker string, or None
    raw_line: str = ""
    is_resolvable: bool = True             # False for git/URL/editable/unparseable
    kind: str = "pypi"                     # pypi|url|vcs|local|include|constraint|option|unparsed


@dataclass
class Tier2Manifest:
    """A detected-but-unparsed modern manifest (spec §5.4, G3)."""

    filename: str
    path: str
    format: str                            # pep621|poetry|pipfile|lock|setup.py|setup.cfg|conda
    declares_dependencies: bool
    detection_fidelity: str = "parsed"     # "parsed" | "regex-fallback"
