"""Engine contracts — TRD §2.4.3.

PURITY CONTRACT
---------------
Nothing in ``app.engines`` may import a database session, a repository, an
integration adapter, ``datetime.now()`` or ``random``. Every input arrives through
the payload; every piece of policy arrives through the ``ScoringConfig``.

This is enforced mechanically by ``tests/unit/engines/test_engine_purity.py``, which
walks the AST of every module in this package. A pull request that makes an engine
reach for the database fails the build rather than the code review.
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.core.types import ONE_HUNDRED, Q4, ZERO, D, clamp, q2, q3

FactorType = Literal["RISK", "POSITIVE"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
Impact = Literal["HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Result value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Factor:
    """A single named contributor to (or detractor from) a score."""

    code: str
    type: FactorType
    message: str
    severity: Severity | None = None
    metric: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "type": self.type, "message": self.message}
        if self.severity:
            out["severity"] = self.severity
        if self.metric:
            out["metric"] = self.metric
        return out


@dataclass(frozen=True, slots=True)
class SubFactorResult:
    code: str
    value: Decimal | str | bool | None
    score: Decimal
    weight: Decimal
    contribution: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "value": str(self.value) if isinstance(self.value, Decimal) else self.value,
            "score": float(self.score),
            "weight": float(self.weight),
            "contribution": float(self.contribution),
        }


@dataclass(frozen=True, slots=True)
class ComponentResult:
    code: str
    label: str
    raw_inputs: dict[str, Any]
    normalized_score: Decimal  # 0-100, quantised to 2dp (as persisted)
    weight: Decimal
    weighted_score: Decimal  # normalized_score * weight, quantised to 3dp
    explanation: str
    display_order: int = 0
    sub_factors: tuple[SubFactorResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "raw_inputs": self.raw_inputs,
            "normalized_score": float(self.normalized_score),
            "weight": float(self.weight),
            "weighted_score": float(self.weighted_score),
            "explanation": self.explanation,
            "display_order": self.display_order,
            "sub_factors": [s.to_dict() for s in self.sub_factors],
        }


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total_score: Decimal
    grade: str
    grade_label: str
    risk_level: str
    components: tuple[ComponentResult, ...]
    risk_factors: tuple[Factor, ...]
    positive_factors: tuple[Factor, ...]
    explanation: str
    config_version_id: int | None
    config_version_no: int | None
    engine_version: str
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in (*self.risk_factors, *self.positive_factors))

    def component(self, code: str) -> ComponentResult:
        for c in self.components:
            if c.code == code:
                return c
        raise KeyError(f"No component {code!r} in this result")

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": float(self.total_score),
            "grade": self.grade,
            "grade_label": self.grade_label,
            "risk_level": self.risk_level,
            "components": [c.to_dict() for c in self.components],
            "risk_factors": [f.to_dict() for f in self.risk_factors],
            "positive_factors": [f.to_dict() for f in self.positive_factors],
            "explanation": self.explanation,
            "config_version": self.config_version_no,
            "engine_version": self.engine_version,
            **self.extras,
        }


# ---------------------------------------------------------------------------
# Configuration value objects (built from scoring_configurations rows)
# ---------------------------------------------------------------------------
class ConfigurationError(ValueError):
    """Raised when a scoring configuration is structurally invalid."""


@dataclass(frozen=True, slots=True)
class GradeBand:
    grade: str
    min: Decimal
    max: Decimal
    label: str
    risk_level: str
    recommendation: str | None = None

    def contains(self, score: Decimal) -> bool:
        return self.min <= score <= self.max


@dataclass(frozen=True, slots=True)
class ComponentConfig:
    code: str
    label: str
    weight: Decimal
    display_order: int
    scoring_rules: dict[str, Any]
    description: str | None = None
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """An immutable, versioned scorecard — Doc 05 §5.3.15/16, Doc 07 §7.1 principle 2."""

    id: int | None
    config_type: Literal["ROUTE", "CUSTOMER", "FINAL"]
    version_no: int
    name: str
    components: tuple[ComponentConfig, ...]
    grade_bands: tuple[GradeBand, ...]
    decision_rules: dict[str, Any] = field(default_factory=dict)

    # -- validation ---------------------------------------------------------
    WEIGHT_SUM_TOLERANCE = Decimal("0.00005")

    def __post_init__(self) -> None:
        if not self.components:
            raise ConfigurationError(f"{self.config_type} configuration has no components")
        self.validate_weights()
        self.validate_bands()

    def validate_weights(self) -> Decimal:
        """Weights must sum to exactly 1.0000 — Doc 01 FR-9.1, Doc 06 §6.11 WEIGHTS_NOT_100."""
        total = sum((c.weight for c in self.active_components), start=ZERO)
        if abs(total - Decimal("1")) > self.WEIGHT_SUM_TOLERANCE:
            raise ConfigurationError(
                f"{self.config_type} configuration v{self.version_no}: component weights sum to "
                f"{total.quantize(Q4)} (must be 1.0000)"
            )
        return total

    def validate_bands(self) -> None:
        bands = sorted(self.grade_bands, key=lambda b: b.min)
        if not bands:
            raise ConfigurationError("configuration has no grade bands")
        if bands[0].min > ZERO or bands[-1].max < ONE_HUNDRED:
            raise ConfigurationError("grade bands must cover the whole 0-100 range")
        for lower, upper in itertools.pairwise(bands):
            if lower.max >= upper.min:
                raise ConfigurationError(
                    f"grade bands overlap: {lower.grade} ends at {lower.max}, "
                    f"{upper.grade} starts at {upper.min}"
                )

    # -- accessors ----------------------------------------------------------
    @property
    def active_components(self) -> tuple[ComponentConfig, ...]:
        return tuple(
            sorted((c for c in self.components if c.is_active), key=lambda c: c.display_order)
        )

    def component(self, code: str) -> ComponentConfig:
        for c in self.components:
            if c.code == code:
                return c
        raise ConfigurationError(f"configuration has no component {code!r}")

    def band_for(self, score: Decimal) -> GradeBand:
        for band in self.grade_bands:
            if band.contains(score):
                return band
        # Defensive: validate_bands() guarantees full coverage, so this is unreachable
        # unless a band table was mutated after construction.
        raise ConfigurationError(f"no grade band covers score {score}")

    def rule(self, path: str, default: Any = None) -> Any:
        """Read a dotted path out of decision_rules, e.g. ``rule('approve.min_dscr')``."""
        node: Any = self.decision_rules
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def decimal_rule(self, path: str, default: Decimal) -> Decimal:
        raw = self.rule(path)
        return default if raw is None else D(raw)

    # -- construction from persisted rows -----------------------------------
    @classmethod
    def from_rows(
        cls,
        *,
        id: int | None,
        config_type: str,
        version_no: int,
        name: str,
        grade_thresholds: Sequence[dict[str, Any]],
        components: Sequence[dict[str, Any]],
        decision_rules: dict[str, Any] | None = None,
    ) -> ScoringConfig:
        return cls(
            id=id,
            config_type=config_type,  # type: ignore[arg-type]
            version_no=version_no,
            name=name,
            components=tuple(
                ComponentConfig(
                    code=c["component_code"],
                    label=c["label"],
                    weight=D(c["weight"]),
                    display_order=int(c.get("display_order", 0)),
                    scoring_rules=c["scoring_rules"],
                    description=c.get("description"),
                    is_active=bool(c.get("is_active", True)),
                )
                for c in components
            ),
            grade_bands=tuple(
                GradeBand(
                    grade=b["grade"],
                    min=D(b["min"]),
                    max=D(b["max"]),
                    label=b.get("label", b["grade"]),
                    risk_level=b.get("risk_level", ""),
                    recommendation=b.get("recommendation"),
                )
                for b in grade_thresholds
            ),
            decision_rules=dict(decision_rules or {}),
        )


# ---------------------------------------------------------------------------
# Engine ABC
# ---------------------------------------------------------------------------
class ScoringEngine(ABC):
    """A pure scorer. ``score`` must be a function of (payload, config) alone."""

    engine_version: str
    config_type: str

    @abstractmethod
    def score(self, payload: Any, config: ScoringConfig) -> ScoreResult:  # pragma: no cover
        ...

    # -- shared helpers used by concrete engines ----------------------------
    @staticmethod
    def aggregate(components: Sequence[ComponentResult]) -> Decimal:
        """Total = q2(sum of already-quantised weighted contributions).

        Quantising each component before weighting is deliberate: the persisted
        ``normalized_score`` is NUMERIC(5,2), so the stored breakdown and the stored
        total must reconcile exactly. This makes the Doc 07 invariant
        ``|Σ weighted − total| ≤ 0.01`` true by construction rather than by luck.
        """
        return q2(clamp(sum((c.weighted_score for c in components), start=ZERO)))

    @staticmethod
    def make_component(
        cfg: ComponentConfig,
        normalized: Decimal,
        raw_inputs: dict[str, Any],
        explanation: str,
        sub_factors: Sequence[SubFactorResult] = (),
    ) -> ComponentResult:
        norm = q2(clamp(normalized))
        return ComponentResult(
            code=cfg.code,
            label=cfg.label,
            raw_inputs=raw_inputs,
            normalized_score=norm,
            weight=cfg.weight,
            weighted_score=q3(norm * cfg.weight),
            explanation=explanation,
            display_order=cfg.display_order,
            sub_factors=tuple(sub_factors),
        )
