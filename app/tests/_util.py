"""Shared test helpers usable under both pytest and the stdlib offline harness.

Nothing here depends on pytest, so tests importing only these helpers run in
either runner.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "test_fixtures"


def fixture(*parts) -> str:
    return str(FIXTURES.joinpath(*parts))


@contextlib.contextmanager
def raises(exc):
    try:
        yield
    except exc:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"expected {exc.__name__}, but nothing was raised")


def available_tokenizers():
    """stdlib is always present; dockerfile-parse only when importable. On the
    user's machine this yields both, so the same assertions cross-check the two
    front-ends; offline it yields just stdlib."""
    toks = ["stdlib"]
    try:
        import dockerfile_parse  # noqa: F401
        toks.insert(0, "dockerfile-parse")
    except Exception:
        pass
    return toks
