"""Base-image decomposition (spec §4a).

Splits a raw FROM image reference into registry / image_name / tag / digest and
infers python_version, libc_family, distro_family, and variant.

libc_family inference — the six rules (spec §4a):
  1. tag/image indicates Alpine            -> musl
  2. tag/image indicates Debian            -> glibc
  3. tag/image indicates Ubuntu            -> glibc
  4. official language image, no distro cue, not Alpine (e.g. python:3.11-slim,
     bare python:3.11) -> Debian-based     -> glibc
  5. Google distroless (debian-based)      -> glibc
  6. anything unrecognised                 -> unknown  (NEVER guessed as glibc)

Rules 1-6 use only the image reference. The Dockerfile parser refines this in a
second pass from observed apk/apt usage (spec §4a "detection source"): seeing
`apk add` forces musl, `apt-get install` forces glibc. That refinement lives in
dockerfile_parser.py, not here, so this function stays pure.
"""

from __future__ import annotations

import re

from ..models import ImageRef, UNKNOWN_BASE_IMAGE

# Debian release codename -> glibc version. Not stored on ImageRef (glibc version
# is resolved at synthesis, spec §9.1), but kept here as the authoritative map so
# a downstream caller never has to hard-code it. bookworm=12, bullseye=11, buster=10.
DEBIAN_CODENAME_GLIBC = {
    "bookworm": "2.36",
    "bullseye": "2.31",
    "buster": "2.28",
    "trixie": "2.41",
}

_DEBIAN_CODENAMES = set(DEBIAN_CODENAME_GLIBC)

# Docker official language images that default to a Debian base when no distro
# cue is present in the tag (rule 4).
_DEBIAN_DEFAULT_OFFICIAL = {
    "python", "node", "ruby", "php", "openjdk", "eclipse-temurin",
    "golang", "rust", "perl", "pypy",
}

_VARIANT_KEYWORDS = ("slim", "alpine", "runtime", "devel", "full", "bookworm", "bullseye")

_PY_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.\d+)?")
_UBUNTU_VERSION_RE = re.compile(r"ubuntu[-]?(\d{2}\.\d{2})")


def _looks_like_registry(component: str) -> bool:
    """Docker's own rule: the first path component is a registry iff it contains
    a '.' or ':' or equals 'localhost'."""
    return "." in component or ":" in component or component == "localhost"


def _split_registry(ref: str):
    """Return (registry, remainder) where remainder is image_name[:tag][@digest]."""
    if "/" in ref:
        head, _, tail = ref.partition("/")
        if _looks_like_registry(head):
            return head, tail
    return None, ref


def _split_digest(ref: str):
    if "@" in ref:
        name_tag, _, digest = ref.partition("@")
        return name_tag, digest or None
    return ref, None


def _split_tag(name_tag: str):
    """Split image_name and tag. The last ':' after the last '/' is the tag sep."""
    # Guard against registry ports already stripped: operate on the segment only.
    if ":" in name_tag:
        name, _, tag = name_tag.rpartition(":")
        return name, tag or None
    return name_tag, None


def _detect_distro(image_name: str, tag: str) -> str:
    name = (image_name or "").lower()
    t = (tag or "").lower()
    hay = f"{name} {t}"

    if "alpine" in hay:
        return "alpine"
    if "ubuntu" in hay or _UBUNTU_VERSION_RE.search(hay):
        return "ubuntu"
    if any(cn in t for cn in _DEBIAN_CODENAMES) or "debian" in hay:
        return "debian"
    if "distroless" in name:
        # gcr.io/distroless/*-debianNN — debian based
        return "debian"
    # Rule 4: official language image with no distro cue -> Debian default.
    short = name.rsplit("/", 1)[-1]
    if short in _DEBIAN_DEFAULT_OFFICIAL:
        return "debian"
    return "unknown"


def _distro_to_libc(distro: str) -> str:
    if distro == "alpine":
        return "musl"
    if distro in ("debian", "ubuntu"):
        return "glibc"
    return "unknown"


def _detect_python_version(image_name: str, tag: str):
    """Return ((major, minor), source) or (None, 'unknown').

    Only trusted when the image is a Python image; other images may carry
    unrelated version numbers in their tag (e.g. cuda 11.8.0)."""
    if not tag:
        return None, "unknown"
    short = (image_name or "").rsplit("/", 1)[-1].lower()
    is_python_image = short == "python" or short.startswith("python")
    if not is_python_image:
        return None, "unknown"
    m = _PY_VERSION_RE.search(tag)
    if m:
        return (int(m.group(1)), int(m.group(2))), "base_image_tag"
    return None, "unknown"


def _detect_variant(tag: str):
    if not tag:
        return None
    t = tag.lower()
    for kw in ("slim", "alpine", "runtime", "devel"):
        if kw in t:
            return kw
    if "full" in t:
        return "full"
    return None


def decompose_image(raw: str) -> ImageRef:
    """Decompose a raw FROM image reference into an ImageRef (spec §4a)."""
    if raw is None:
        raw = UNKNOWN_BASE_IMAGE

    raw = raw.strip()

    if raw == UNKNOWN_BASE_IMAGE or raw == "":
        return ImageRef(raw=UNKNOWN_BASE_IMAGE, resolved=False)

    # An unresolved ARG placeholder that slipped through (e.g. "${BASE}") is unknown.
    if raw.startswith("$") or "${" in raw:
        return ImageRef(raw=raw, resolved=False)

    registry, remainder = _split_registry(raw)
    name_tag, digest = _split_digest(remainder)
    image_name, tag = _split_tag(name_tag)

    distro = _detect_distro(image_name, tag)
    libc = _distro_to_libc(distro)
    py_version, py_source = _detect_python_version(image_name, tag)
    variant = _detect_variant(tag)

    return ImageRef(
        raw=raw,
        registry=registry,
        image_name=image_name,
        tag=tag,
        digest=digest,
        python_version=py_version,
        python_version_source=py_source,
        libc_family=libc,
        distro_family=distro,
        variant=variant,
        resolved=True,
        libc_detection_source="base_image_tag" if libc != "unknown" else "unknown",
    )
