"""ORM models. Importing this package registers every table on ``Base.metadata``.

The authority on DDL is ``db/schema.sql``; Alembic migration 001 applies it verbatim.
These classes mirror it for query and typing purposes, and
``tests/integration/test_model_schema_parity.py`` compares the two so they cannot drift.
"""

from app.models.base import Base
from app.models.catalog import (
    ChargingStation,
    MaintenanceEvent,
    Route,
    RouteAssessment,
    RouteScoreComponent,
    ScoringConfigComponent,
    ScoringConfiguration,
    Vehicle,
    VehicleModel,
)
from app.models.credit import (
    Applicant,
    ApplicantDocument,
    ApplicantFinancial,
    CreditBureauReport,
    CreditScore,
    CreditScoreComponent,
    ExistingObligation,
    LoanApplication,
    LoanAssessment,
    UnderwritingDecision,
)
from app.models.identity import Permission, Role, RolePermission, User, UserSession
from app.models.portfolio import (
    AlertActivity,
    BatteryMetric,
    ChargingSession,
    Loan,
    LoanMonitoringSnapshot,
    Repayment,
    RepaymentSchedule,
    RiskAlert,
    RiskRule,
    RiskRuleCondition,
    VehicleTelemetry,
)
from app.models.system import AuditLog, IdempotencyKey, JobRun, Notification, SystemSetting

__all__ = [
    "AlertActivity", "Applicant", "ApplicantDocument", "ApplicantFinancial", "AuditLog",
    "Base", "BatteryMetric", "ChargingSession", "ChargingStation", "CreditBureauReport",
    "CreditScore", "CreditScoreComponent", "ExistingObligation", "IdempotencyKey",
    "JobRun", "Loan", "LoanApplication", "LoanAssessment", "LoanMonitoringSnapshot",
    "MaintenanceEvent", "Notification", "Permission", "Repayment", "RepaymentSchedule",
    "RiskAlert", "RiskRule", "RiskRuleCondition", "Role", "RolePermission", "Route",
    "RouteAssessment", "RouteScoreComponent", "ScoringConfigComponent",
    "ScoringConfiguration", "SystemSetting", "UnderwritingDecision", "User",
    "UserSession", "Vehicle", "VehicleModel", "VehicleTelemetry",
]
