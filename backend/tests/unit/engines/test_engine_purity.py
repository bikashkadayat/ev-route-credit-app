"""Architectural enforcement of the purity contract — TRD §2.4.3, §15.2 import rules.

The engines are the product. If one of them ever reaches for a database session, a wall
clock or a random number, scoring stops being reproducible and a historical decision can
no longer be explained. That is a correctness failure, not a style failure, so it is
enforced by a test that walks the AST rather than by code review.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ENGINES_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "engines"
RISK_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "risk"

FORBIDDEN_MODULE_PREFIXES = (
    "sqlalchemy", "asyncpg", "psycopg", "random", "requests", "httpx",
    "app.models", "app.repositories", "app.services", "app.integrations", "app.core.database",
)

FORBIDDEN_CALLS = {
    "now": "datetime.now()",
    "utcnow": "datetime.utcnow()",
    "today": "date.today()",
    "random": "random()",
    "uuid4": "uuid4()",
    "monotonic": "time.monotonic()",
}

# The clock may be *typed* (a date parameter) but never *read* inside an engine.
ALLOWED_IMPORTS = {"datetime", "decimal", "dataclasses", "typing", "abc", "enum",
                   "functools", "json", "pathlib", "string", "collections", "__future__"}


def _engine_modules() -> list[pathlib.Path]:
    return sorted(
        p for p in list(ENGINES_DIR.glob("*.py")) + list(RISK_DIR.glob("*.py"))
        if p.name != "__init__.py"
    )


@pytest.mark.parametrize("module_path", _engine_modules(), ids=lambda p: p.name)
def test_engine_module_imports_nothing_impure(module_path: pathlib.Path):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for name in imported:
        for forbidden in FORBIDDEN_MODULE_PREFIXES:
            assert not name.startswith(forbidden), (
                f"{module_path.name} imports {name!r}. Engines must be pure: no database, "
                f"no network, no randomness. See TRD §2.4.3."
            )


@pytest.mark.parametrize("module_path", _engine_modules(), ids=lambda p: p.name)
def test_engine_module_never_reads_the_clock_or_randomness(module_path: pathlib.Path):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name in FORBIDDEN_CALLS:
                raise AssertionError(
                    f"{module_path.name} line {node.lineno} calls {FORBIDDEN_CALLS[name]}. "
                    f"Engines are pure functions of (payload, config); time and randomness "
                    f"must be passed in by the caller. See TRD §2.4.3."
                )


#: The only modules permitted to read from disk. Both do exactly one thing: load the
#: shipped default configuration (scorecards / rule catalogue) at import time. The reads
#: are read-only, deterministic and idempotent, so they do not compromise reproducibility.
LOADER_MODULES = {"config_loader.py", "loader.py"}


def test_only_the_loaders_touch_the_filesystem():
    """Loading the shipped defaults is the single permitted I/O in the pure layers."""
    offenders = []
    for path in _engine_modules():
        if path.name in LOADER_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        if "open(" in source or "read_text" in source or "Path(" in source:
            offenders.append(path.name)
    assert not offenders, (
        f"filesystem access outside {sorted(LOADER_MODULES)}: {offenders}. "
        f"Engines and rules must be pure functions of their inputs."
    )


def test_loaders_only_read_never_write():
    """A loader that wrote to disk would make scoring depend on machine state."""

    for name in LOADER_MODULES:
        for base in (ENGINES_DIR, RISK_DIR):
            candidate = base / name
            if not candidate.exists():
                continue
            source = candidate.read_text(encoding="utf-8")
            assert "write_text" not in source and "write_bytes" not in source, (
                f"{candidate.name} writes to disk"
            )


def test_every_engine_declares_a_semantic_version():
    """Doc 15 §15.8 — an engine change must bump its version so stored scores stay
    attributable to the exact logic that produced them."""
    import re

    from app.engines.customer_engine import CustomerScoringEngine
    from app.engines.final_engine import FinalRiskEngine
    from app.engines.route_engine import RouteScoringEngine
    from app.risk.engine import ENGINE_VERSION as RULE_ENGINE_VERSION

    assert re.fullmatch(r"[a-z-]+@\d+\.\d+\.\d+", RULE_ENGINE_VERSION)

    for engine in (RouteScoringEngine, CustomerScoringEngine, FinalRiskEngine):
        assert re.fullmatch(r"[a-z-]+@\d+\.\d+\.\d+", engine.engine_version), (
            f"{engine.__name__}.engine_version must look like 'name@1.0.0', "
            f"got {engine.engine_version!r}"
        )
