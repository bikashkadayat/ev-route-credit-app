"""Architectural enforcement — TRD §2.4.3, §15.2 import rules.

Two boundaries are defended here, both mechanically rather than by code review:

1. **Engine purity.** Nothing in ``app/engines`` or ``app/risk`` may reach for a database,
   a clock or a random number. If one did, scoring would stop being reproducible and a
   historical decision could no longer be explained.
2. **API layer thinness.** Routers reach engines only through services. A router calling an
   engine directly is how orchestration, configuration resolution and persistence drift
   apart until two callers of the same engine disagree.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ENGINES_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "engines"
RISK_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "risk"
SERVICES_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "services"
API_DIR = pathlib.Path(__file__).resolve().parents[3] / "app" / "api"

FORBIDDEN_MODULE_PREFIXES = (
    "sqlalchemy", "asyncpg", "psycopg", "random", "requests", "httpx",
    "app.models", "app.repositories", "app.services", "app.integrations",
    "app.core.database",
)

FORBIDDEN_CALLS = {
    "now": "datetime.now()",
    "utcnow": "datetime.utcnow()",
    "today": "date.today()",
    "random": "random()",
    "uuid4": "uuid4()",
    "monotonic": "time.monotonic()",
}

#: The only modules permitted to read from disk. Both load the shipped default
#: configuration (scorecards / rule catalogue). The reads are read-only, deterministic and
#: idempotent, so they do not compromise reproducibility.
LOADER_MODULES = {"config_loader.py", "loader.py"}


def _engine_modules() -> list[pathlib.Path]:
    return sorted(
        p for p in list(ENGINES_DIR.glob("*.py")) + list(RISK_DIR.glob("*.py"))
        if p.name != "__init__.py"
    )


# ---------------------------------------------------------------------------
# 1. Engine purity
# ---------------------------------------------------------------------------
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
                f"no network, no randomness. See TRD 2.4.3."
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
                    f"must be passed in by the caller. See TRD 2.4.3."
                )


def test_only_the_loaders_touch_the_filesystem():
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
    """Doc 15 15.8 — an engine change must bump its version so stored scores stay
    attributable to the exact logic that produced them."""
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


# ---------------------------------------------------------------------------
# 2. API layer boundary
#
#     API  ->  Services  ->  Pure engines      (required)
#     API  ->  Pure engines                    (forbidden)
# ---------------------------------------------------------------------------
FORBIDDEN_API_IMPORTS = (
    "app.engines.route_engine",
    "app.engines.servicing",
    "app.engines.customer_engine",
    "app.engines.final_engine",
    "app.engines.normalizer",
    "app.engines.emi",
    "app.engines.decision_matrix",
    "app.engines.knockouts",
    "app.engines.structuring",
    "app.engines.vehicle_economics",
    "app.engines.factors",
    "app.engines.config_loader",
    "app.risk.engine",
    "app.risk.metrics",
    "app.risk.behaviour",
    "app.risk.loader",
    "app.risk.families",
)

FORBIDDEN_API_NAMES = frozenset({
    "normalize", "evaluate_condition", "evaluate_rule", "evaluate_rules",
    "allocate_payment", "loan_position", "classify", "days_past_due", "build_schedule",
    "overdue_amount", "outstanding_principal", "risk_status", "penalty_for",
    "resolve_supersession", "calculate_emi", "principal_from_emi", "decide", "build_reasons", "structure_loan", "evaluate_knockouts",
    "calculate_vehicle_economics", "build_metric_snapshot", "behaviour_score",
    "RouteScoringEngine", "CustomerScoringEngine", "FinalRiskEngine",
    "derive_route_inputs", "derive_customer_inputs", "composite_breakdown",
    "default_config", "default_rules",
})

#: ``app.api.mappers`` reshapes an already-computed ScoreResult for presentation, so it may
#: name the result *type*. The name check below still forbids calling anything.
ALLOWED_TYPE_ONLY_IMPORTS = {"app.engines.base", "app.risk.models"}


def _api_modules() -> list[pathlib.Path]:
    return sorted(p for p in API_DIR.rglob("*.py") if p.name != "__init__.py")


def test_api_layer_has_modules_to_check():
    """Guards the guard: a glob that found nothing would pass every test below."""
    modules = _api_modules()
    assert len(modules) >= 8, f"expected API modules, found {[m.name for m in modules]}"


@pytest.mark.parametrize("module_path", _api_modules(), ids=lambda p: p.name)
def test_api_layer_never_imports_a_pure_engine(module_path: pathlib.Path):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    offences: list[str] = []

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]

        for name in modules:
            if name in ALLOWED_TYPE_ONLY_IMPORTS:
                continue
            for forbidden in FORBIDDEN_API_IMPORTS:
                if name == forbidden or name.startswith(forbidden + "."):
                    offences.append(f"line {node.lineno}: imports {name!r}")

    assert not offences, (
        f"{module_path.name} reaches past the service layer: {offences}. "
        f"Routers call a service, which calls the engine. Move the orchestration into "
        f"app/services/."
    )


@pytest.mark.parametrize("module_path", _api_modules(), ids=lambda p: p.name)
def test_api_layer_never_names_an_engine_callable(module_path: pathlib.Path):
    """Catches re-exports and any other indirect route to an engine callable."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    offences: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_API_NAMES:
                    offences.append(f"line {node.lineno}: imports name {alias.name!r}")
        elif isinstance(node, ast.Call):
            func = node.func
            called = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if called in FORBIDDEN_API_NAMES:
                offences.append(f"line {node.lineno}: calls {called}()")

    assert not offences, f"{module_path.name} invokes engine logic directly: {offences}"


def test_services_are_the_layer_that_calls_the_engines():
    """The positive half of the rule. If no service imported an engine the guard above
    would pass trivially and the architecture would be undefended."""
    importers = []
    for path in SERVICES_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "app.engines" in source or "app.risk" in source:
            importers.append(path.name)
    assert {"route_assessment.py", "credit.py", "alert.py"} <= set(importers), (
        f"expected the assessment services to call the engines; found {sorted(importers)}"
    )


def test_routers_do_not_import_sqlalchemy_directly():
    """Database access belongs to repositories. A router holding a Session could bypass
    the service layer's transaction and audit guarantees."""
    offenders = []
    for path in (API_DIR / "routers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            if any(n and n.startswith("sqlalchemy") for n in names):
                offenders.append(path.name)
    assert not offenders, f"routers importing SQLAlchemy directly: {sorted(set(offenders))}"


def test_routers_do_not_import_orm_models_directly():
    """A router returning an ORM row would leak the schema into the API contract; every
    response is a Pydantic schema."""
    offenders = []
    for path in (API_DIR / "routers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            if any(n and n.startswith("app.models") for n in names):
                offenders.append(path.name)
    assert not offenders, f"routers importing ORM models: {sorted(set(offenders))}"


def test_services_are_the_layer_that_calls_the_servicing_engine():
    """The positive half for the servicing arithmetic: allocation, DPD and classification
    are engine functions, and the loan service is what calls them."""
    source = (SERVICES_DIR / "loan.py").read_text(encoding="utf-8")
    assert "app.engines.servicing" in source
    assert "allocate_payment" in source
    assert "loan_position" in source


def test_no_router_reimplements_the_allocation_order():
    """A router deciding what a payment pays off would be a second implementation of the
    documented order, reachable only through one endpoint."""
    forbidden = {"penalty_component", "interest_component", "principal_paid",
                 "interest_paid", "penalty_paid"}
    for path in (API_DIR / "routers").glob("*.py"):
        written = _assignment_targets(path) & forbidden
        assert not written, f"{path.name} allocates a payment itself: {sorted(written)}"


def test_no_router_writes_a_loan_position():
    """DPD, outstanding and classification are derived in one place. A router setting them
    could leave the three disagreeing with each other."""
    forbidden = {"days_past_due", "classification", "outstanding_principal",
                 "overdue_amount", "risk_status", "installments_overdue"}
    for path in (API_DIR / "routers").glob("*.py"):
        written = _assignment_targets(path) & forbidden
        assert not written, f"{path.name} writes servicing fields directly: {sorted(written)}"


def test_the_application_state_machine_lives_in_one_place():
    """A router that set a status directly could walk an application into a state the
    transition table forbids."""
    for path in (API_DIR / "routers").glob("*.py"):
        assert "ALLOWED_TRANSITIONS" not in _identifiers(path), (
            f"{path.name} inspects the state table"
        )
        assert "status" not in _assignment_targets(path), (
            f"{path.name} assigns a status directly"
        )


def _identifiers(path: pathlib.Path) -> set[str]:
    """Every name and attribute a module actually references.

    String literals are excluded deliberately. A router *documents* the errors it can
    return, so ``FOUR_EYES_REQUIRED`` legitimately appears in its OpenAPI description —
    what must not appear is the rule being *decided* there, and a decision is made with
    identifiers, not with prose.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _assignment_targets(path: pathlib.Path) -> set[str]:
    """Attribute names the module assigns to, e.g. ``loan.days_past_due = ...``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    targets.add(target.attr)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            if isinstance(node.target, ast.Attribute):
                targets.add(node.target.attr)
    return targets


def test_the_four_eyes_rule_is_not_enforced_in_a_router():
    """Enforced in the service, so it holds however the API is reached. A router-level
    check would be bypassed by any other caller."""
    for path in (API_DIR / "routers").glob("*.py"):
        names = _identifiers(path)
        assert "_enforce_maker_checker" not in names, (
            f"{path.name} runs the four-eyes check itself"
        )
        assert "created_by" not in names, f"{path.name} inspects the maker itself"


@pytest.mark.parametrize("module_path", sorted((SERVICES_DIR).glob("*.py")),
                         ids=lambda p: p.name)
def test_services_never_import_the_api_layer(module_path: pathlib.Path):
    """The dependency runs one way. A service importing a response schema would make the
    domain depend on its own presentation."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module] if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
        for name in names:
            assert not (name and name.startswith("app.api")), (
                f"{module_path.name} imports {name!r}: services must not know about the API"
            )


@pytest.mark.parametrize("module_path", sorted((SERVICES_DIR).glob("*.py")),
                         ids=lambda p: p.name)
def test_services_do_not_write_raw_sql(module_path: pathlib.Path):
    """Queries belong to repositories. A service issuing SQL puts persistence in two
    places, and only one of them is tested against the schema."""
    source = module_path.read_text(encoding="utf-8")
    assert "session.execute(" not in source, (
        f"{module_path.name} executes a query directly; move it to a repository"
    )
