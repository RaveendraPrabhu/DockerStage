"""Tests for the knowledge base (spec §7.1) and its validator.

Covers the Phase 2 exit-gate items that are checkable offline:
schema validity + entry count, the psycopg2 build/runtime asymmetry,
psycopg2 vs psycopg2-binary divergence, and the negative-control count.
The remaining two gate items (import_names / wheel flags verified against a
live container and PyPI) are covered by tools/verify_imports.py and
tools/verify_wheels.py, which need network/Docker and are run by hand.
"""

from __future__ import annotations

from dockerstage.knowledge_base import load, validate, MIN_ENTRIES


def test_bundled_kb_is_schema_valid():
    errs = validate(load())
    assert errs == [], "KB schema violations:\n" + "\n".join(errs)


def test_entry_count_meets_gate():
    assert len(load()["entries"]) >= MIN_ENTRIES


def _by_name():
    return {e["name"]: e for e in load()["entries"]}


def test_psycopg2_build_runtime_asymmetry():
    e = _by_name()["psycopg2"]
    assert "libpq-dev" in e["build_time_system_deps"]["debian"]
    assert e["runtime_system_deps"]["debian"] == ["libpq5"]
    # the point of multi-stage: build dep name != runtime dep name
    assert set(e["build_time_system_deps"]["debian"]) != set(e["runtime_system_deps"]["debian"])


def test_psycopg2_vs_binary_differ():
    d = _by_name()
    src, binary = d["psycopg2"], d["psycopg2-binary"]
    # binary wheel bundles libpq: needs nothing to build or run
    assert binary["build_time_system_deps"]["debian"] == []
    assert binary["runtime_system_deps"]["debian"] == []
    assert src["runtime_system_deps"]["debian"] != binary["runtime_system_deps"]["debian"]


def test_at_least_ten_negative_controls():
    n = 0
    for e in load()["entries"]:
        if not e["build_time_system_deps"]["debian"] and not e["runtime_system_deps"]["debian"]:
            n += 1
    assert n >= 10, f"only {n} negative controls"


def test_validator_rejects_broken_entry():
    data = load()
    data["entries"][0] = {**data["entries"][0], "name": "Not_Normalised"}
    assert any("PEP 503" in e for e in validate(data))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok    {_name}")
    print("all passed")
