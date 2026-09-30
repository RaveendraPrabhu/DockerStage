"""Stdlib test harness — runs the pytest-style tests without pytest.

Why this exists: the primary test runner is pytest (`cd app && pytest`). But in
constrained/offline environments where pytest is not installed, this harness
discovers and runs the same `test_*` functions using only the standard library,
so the suite is always runnable. It is not a pytest replacement — it ignores
fixtures and markers — but the Phase 1 tests use neither.

Usage:  python tests/run_offline.py
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent

for p in (str(APP_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

TEST_MODULES = [
    "test_base_image",
    "test_dockerfile_parser",
    "test_manifest_parser",
    "test_knowledge_base",
]


def _load(mod_name: str):
    path = TESTS_DIR / f"{mod_name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    passed = failed = 0
    failures = []
    for mod_name in TEST_MODULES:
        mod = _load(mod_name)
        fns = sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n)))
        for fn_name in fns:
            fn = getattr(mod, fn_name)
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                failed += 1
                failures.append((f"{mod_name}::{fn_name}", e, traceback.format_exc()))
                print(f"FAIL  {mod_name}::{fn_name}  -> {type(e).__name__}: {e}")
            else:
                passed += 1
                print(f"ok    {mod_name}::{fn_name}")

    print("\n" + "=" * 60)
    print(f"passed: {passed}    failed: {failed}")
    if failures:
        print("\n--- failure detail ---")
        for name, _e, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
