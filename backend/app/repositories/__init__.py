"""Repositories — the only place SQLAlchemy queries live (Doc 02 §2.4.1)."""

from app.repositories.alert import (
    AlertRepository,
    LoanRepository,
    MonitoringSnapshotRepository,
)
from app.repositories.base import BaseRepository
from app.repositories.config import RiskRuleRepository, ScoringConfigRepository
from app.repositories.credit import (
    CreditScoreRepository,
    LoanApplicationRepository,
    LoanAssessmentRepository,
)
from app.repositories.customer import (
    BureauReportRepository,
    CustomerRepository,
    FinancialProfileRepository,
    ObligationRepository,
)
from app.repositories.portfolio import PortfolioRepository, TelemetryRepository
from app.repositories.route import RouteAssessmentRepository, RouteRepository

__all__ = [
    "AlertRepository", "BaseRepository", "BureauReportRepository", "CreditScoreRepository",
    "CustomerRepository", "FinancialProfileRepository", "LoanApplicationRepository",
    "LoanAssessmentRepository", "LoanRepository", "MonitoringSnapshotRepository",
    "ObligationRepository", "PortfolioRepository", "RiskRuleRepository",
    "RouteAssessmentRepository", "RouteRepository", "ScoringConfigRepository",
    "TelemetryRepository",
]
