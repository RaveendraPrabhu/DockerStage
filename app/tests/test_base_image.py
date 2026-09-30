"""Tests for base-image decomposition (spec §4a). Covers the ROADMAP Phase 1
exit-gate libc cases plus registry/tag/digest/variant edge cases."""

from __future__ import annotations

from dockerstage.parser.base_image import decompose_image
from dockerstage.models import UNKNOWN_BASE_IMAGE


def test_python_slim_is_glibc_debian():
    img = decompose_image("python:3.11-slim")
    assert img.registry is None
    assert img.image_name == "python"
    assert img.tag == "3.11-slim"
    assert img.python_version == (3, 11)
    assert img.python_version_source == "base_image_tag"
    assert img.libc_family == "glibc"
    assert img.distro_family == "debian"
    assert img.variant == "slim"
    assert img.libc_detection_source == "base_image_tag"


def test_python_alpine_is_musl():
    img = decompose_image("python:3.11-alpine")
    assert img.libc_family == "musl"
    assert img.distro_family == "alpine"
    assert img.python_version == (3, 11)
    assert img.variant == "alpine"


def test_ubuntu_is_glibc_but_python_version_indeterminate():
    img = decompose_image("ubuntu:22.04")
    assert img.libc_family == "glibc"
    assert img.distro_family == "ubuntu"
    assert img.python_version is None
    assert img.python_version_source == "unknown"


def test_unrecognised_base_is_unknown_never_guessed_glibc():
    img = decompose_image("mycompany/mysterybase:1.4")
    assert img.libc_family == "unknown"
    assert img.distro_family == "unknown"
    assert img.libc_detection_source == "unknown"
    assert img.resolved is True
    assert img.is_unknown is False  # resolved, just unclassified


def test_sentinel_is_unknown_and_unresolved():
    img = decompose_image(UNKNOWN_BASE_IMAGE)
    assert img.is_unknown is True
    assert img.resolved is False


def test_unresolved_arg_placeholder_is_unknown():
    img = decompose_image("${BASE_IMAGE}")
    assert img.resolved is False
    assert img.is_unknown is True


def test_bullseye_codename_is_debian_glibc():
    img = decompose_image("python:3.10-slim-bullseye")
    assert img.distro_family == "debian"
    assert img.libc_family == "glibc"
    assert img.python_version == (3, 10)
    assert img.variant == "slim"


def test_registry_with_dot_is_split():
    img = decompose_image("gcr.io/distroless/python3-debian12")
    assert img.registry == "gcr.io"
    assert img.image_name == "distroless/python3-debian12"
    assert img.distro_family == "debian"
    assert img.libc_family == "glibc"


def test_registry_with_port():
    img = decompose_image("myreg.io:5000/python:3.11-slim")
    assert img.registry == "myreg.io:5000"
    assert img.image_name == "python"
    assert img.tag == "3.11-slim"
    assert img.python_version == (3, 11)


def test_dockerhub_org_image_has_no_registry():
    img = decompose_image("nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04")
    assert img.registry is None
    assert img.image_name == "nvidia/cuda"
    assert img.distro_family == "ubuntu"       # tag carries ubuntu22.04
    assert img.libc_family == "glibc"
    assert img.python_version is None          # not a python image
    assert img.variant == "runtime"


def test_digest_is_captured():
    img = decompose_image("python@sha256:abc123")
    assert img.image_name == "python"
    assert img.digest == "sha256:abc123"


def test_bare_python_defaults_to_debian_glibc():
    img = decompose_image("python:3.12")
    assert img.distro_family == "debian"
    assert img.libc_family == "glibc"
    assert img.python_version == (3, 12)
    assert img.variant is None
