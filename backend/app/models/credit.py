"""Applicants, financials, bureau data, applications and decisions.

Doc 05 §5.3.6-5.3.10 and §5.3.19-5.3.23.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, LargeBinary, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    ActorMixin,
    Base,
    BigInteger,
    Boolean,
    CreatedAtMixin,
    Date,
    DateTime,
    Integer,
    OptimisticLockMixin,
    SmallInteger,
    SoftDeleteMixin,
    String,
    TimestampMixin,
    enum_column,
    external_uuid,
    money,
    ratio,
    score,
    small_money,
)


class Applicant(Base, TimestampMixin, SoftDeleteMixin, ActorMixin):
    __tablename__ = "applicants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    applicant_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    applicant_type: Mapped[str] = enum_column(
        "applicant_type_enum", "INDIVIDUAL_DRIVER", "OWNER_DRIVER", "CORPORATE",
        "FLEET_OPERATOR", "SME", "TRANSPORT_COMPANY", nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = enum_column("gender_enum", "MALE", "FEMALE", "OTHER")
    id_type: Mapped[str] = enum_column(
        "id_type_enum", "CITIZENSHIP", "PASSPORT", "PAN", "COMPANY_REG", nullable=False)
    # Doc 09 §9.7.3 — encrypted at rest; the hash enables duplicate detection without
    # decrypting anything.
    id_number_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_number_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pan_number_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    alt_phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(Text)  # CITEXT
    province: Mapped[str] = mapped_column(String(40), nullable=False)
    district: Mapped[str] = mapped_column(String(60), nullable=False)
    municipality: Mapped[str] = mapped_column(String(80), nullable=False)
    ward_no: Mapped[int | None] = mapped_column(SmallInteger)
    address_line: Mapped[str | None] = mapped_column(String(200))
    current_address_same: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true")
    current_address_line: Mapped[str | None] = mapped_column(String(200))
    total_experience_years: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), nullable=False, server_default="0")
    driving_experience_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    commercial_driving_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    business_experience_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    licence_category: Mapped[str | None] = mapped_column(String(20))
    licence_expiry: Mapped[date | None] = mapped_column(Date)
    has_previous_ev_experience: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    company_reg_number: Mapped[str | None] = mapped_column(String(40))
    company_reg_date: Mapped[date | None] = mapped_column(Date)
    fleet_size: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = enum_column(
        "applicant_status_enum", "ACTIVE", "BLACKLISTED", "INACTIVE",
        nullable=False, server_default="ACTIVE")

    financials: Mapped[list[ApplicantFinancial]] = relationship(back_populates="applicant")
    obligations: Mapped[list[ExistingObligation]] = relationship(
        back_populates="applicant", cascade="all, delete-orphan")
    documents: Mapped[list[ApplicantDocument]] = relationship(
        back_populates="applicant", cascade="all, delete-orphan")
    bureau_reports: Mapped[list[CreditBureauReport]] = relationship(
        back_populates="applicant")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_applicants_uuid"),
        Index("idx_applicants_type", "applicant_type"),
        Index("idx_applicants_geo", "province", "district"),
        Index("idx_applicants_phone", "phone"),
    )


class ApplicantFinancial(Base, TimestampMixin):
    """Versioned — Doc 05 §5.3.7. Decisions reference the exact version used."""

    __tablename__ = "applicant_financials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="RESTRICT"), nullable=False)
    version_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    monthly_income: Mapped[Decimal] = money(nullable=False, server_default="0")
    monthly_business_revenue: Mapped[Decimal] = money(nullable=False, server_default="0")
    monthly_household_expenses: Mapped[Decimal] = money(nullable=False, server_default="0")
    monthly_business_expenses: Mapped[Decimal] = money(nullable=False, server_default="0")
    other_monthly_income: Mapped[Decimal] = money(nullable=False, server_default="0")
    existing_loan_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    total_existing_emi: Mapped[Decimal] = money(nullable=False, server_default="0")
    total_existing_outstanding: Mapped[Decimal] = money(nullable=False, server_default="0")
    avg_bank_balance_6m: Mapped[Decimal] = money(nullable=False, server_default="0")
    bank_account_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    dependants_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    has_guarantor: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    income_proof_type: Mapped[str | None] = mapped_column(String(40))
    income_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)
    # generated columns — the database computes these, never the ORM
    total_monthly_income: Mapped[Decimal] = money(nullable=False)
    total_monthly_expenses: Mapped[Decimal] = money(nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    applicant: Mapped[Applicant] = relationship(back_populates="financials")

    __table_args__ = (
        UniqueConstraint("applicant_id", "version_no", name="uq_fin_applicant_version"),
        Index("idx_fin_applicant", "applicant_id"),
    )


class ExistingObligation(Base, TimestampMixin):
    __tablename__ = "existing_obligations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="CASCADE"), nullable=False)
    lender_name: Mapped[str] = mapped_column(String(120), nullable=False)
    loan_type: Mapped[str] = mapped_column(String(40), nullable=False)
    original_amount: Mapped[Decimal] = money(nullable=False)
    outstanding_amount: Mapped[Decimal] = money(nullable=False)
    monthly_emi: Mapped[Decimal] = money(nullable=False)
    remaining_tenure_months: Mapped[int | None] = mapped_column(SmallInteger)
    is_overdue: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    days_past_due: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="DECLARED")

    applicant: Mapped[Applicant] = relationship(back_populates="obligations")

    __table_args__ = (
        Index("idx_oblig_applicant", "applicant_id"),
        Index("idx_oblig_overdue", "applicant_id", "is_overdue"),
    )


class ApplicantDocument(Base, CreatedAtMixin, SoftDeleteMixin):
    __tablename__ = "applicant_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("loan_applications.id", ondelete="SET NULL"))
    document_type: Mapped[str] = enum_column(
        "document_type_enum", "CITIZENSHIP", "PAN", "LICENCE", "INCOME_PROOF",
        "BANK_STATEMENT", "VEHICLE_QUOTATION", "ROUTE_PERMIT", "PHOTO", "OTHER",
        nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    verified_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)

    applicant: Mapped[Applicant] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_doc_uuid"),
        Index("idx_doc_applicant", "applicant_id", "document_type"),
        Index("idx_doc_app", "application_id"),
    )


class CreditBureauReport(Base, CreatedAtMixin):
    __tablename__ = "credit_bureau_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="MOCK_CIB")
    enquiry_id: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    enquiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    bureau_score: Mapped[int | None] = mapped_column(SmallInteger)
    bureau_grade: Mapped[str | None] = mapped_column(String(5))
    credit_history_months: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    active_loan_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    total_outstanding: Mapped[Decimal] = money(nullable=False, server_default="0")
    total_monthly_emi: Mapped[Decimal] = money(nullable=False, server_default="0")
    previous_default_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    current_overdue_amount: Mapped[Decimal] = money(nullable=False, server_default="0")
    max_dpd_last_24m: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    is_blacklisted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    enquiries_last_6m: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    repayment_history: Mapped[dict | None] = mapped_column(JSONB)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    is_manual_entry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    fetched_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    applicant: Mapped[Applicant] = relationship(back_populates="bureau_reports")

    __table_args__ = (
        Index("idx_cib_applicant", "applicant_id", "enquiry_date"),
        Index("idx_cib_valid", "applicant_id", "valid_until"),
    )


class LoanApplication(Base, TimestampMixin, SoftDeleteMixin, ActorMixin, OptimisticLockMixin):
    __tablename__ = "loan_applications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    application_number: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="RESTRICT"), nullable=False)
    financial_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicant_financials.id", ondelete="RESTRICT"),
        nullable=False)
    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False)
    route_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("routes.id", ondelete="RESTRICT"), nullable=False)
    route_assessment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("route_assessments.id", ondelete="SET NULL"))
    credit_bureau_report_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credit_bureau_reports.id", ondelete="SET NULL"))
    requested_amount: Mapped[Decimal] = money(nullable=False)
    requested_tenure_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    proposed_interest_rate: Mapped[Decimal] = score(nullable=False)
    down_payment_amount: Mapped[Decimal] = money(nullable=False)
    vehicle_on_road_price: Mapped[Decimal] = money(nullable=False)
    expected_daily_km: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    expected_operating_days_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="26")
    expected_monthly_revenue: Mapped[Decimal | None] = small_money()
    purpose: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = enum_column(
        "application_status_enum", "DRAFT", "SUBMITTED", "UNDER_ASSESSMENT",
        "PENDING_DECISION", "APPROVED", "REJECTED", "WITHDRAWN", "DISBURSED", "EXPIRED",
        nullable=False, server_default="DRAFT")
    branch_code: Mapped[str | None] = mapped_column(String(20))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credit_scores: Mapped[list[CreditScore]] = relationship(back_populates="application")
    assessments: Mapped[list[LoanAssessment]] = relationship(back_populates="application")
    decisions: Mapped[list[UnderwritingDecision]] = relationship(
        back_populates="application")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_app_uuid"),
        Index("idx_app_applicant", "applicant_id"),
        Index("idx_app_status_date", "status", "created_at"),
        Index("idx_app_route", "route_id"),
        Index("idx_app_branch", "branch_code", "status"),
        Index("idx_app_creator", "created_by"),
    )


class CreditScore(Base):
    """Immutable — Doc 05 §5.3.20."""

    __tablename__ = "credit_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loan_applications.id", ondelete="CASCADE"), nullable=False)
    config_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scoring_configurations.id", ondelete="RESTRICT"),
        nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    total_score: Mapped[Decimal] = score(nullable=False)
    grade: Mapped[str] = mapped_column(String(2), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    dti_ratio: Mapped[Decimal] = ratio(nullable=False)
    foir_ratio: Mapped[Decimal] = ratio(nullable=False)
    disposable_income: Mapped[Decimal] = money(nullable=False)
    knockouts_triggered: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'[]'")
    risk_factors: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    positive_factors: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="'[]'")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    scored_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")

    application: Mapped[LoanApplication] = relationship(back_populates="credit_scores")
    components: Mapped[list[CreditScoreComponent]] = relationship(
        back_populates="credit_score", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_cs_uuid"),
        Index("idx_cs_app", "application_id", "scored_at"),
        Index("idx_cs_grade", "grade"),
    )


class CreditScoreComponent(Base):
    __tablename__ = "credit_score_components"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    credit_score_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("credit_scores.id", ondelete="CASCADE"), nullable=False)
    component_code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized_score: Mapped[Decimal] = score(nullable=False)
    weight: Mapped[Decimal] = ratio(nullable=False)
    weighted_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    credit_score: Mapped[CreditScore] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("credit_score_id", "component_code", name="uq_csc"),
        Index("idx_csc_score", "credit_score_id"),
    )


class LoanAssessment(Base):
    """Combined risk score — immutable, Doc 05 §5.3.22."""

    __tablename__ = "loan_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loan_applications.id", ondelete="CASCADE"), nullable=False)
    route_assessment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("route_assessments.id", ondelete="RESTRICT"), nullable=False)
    credit_score_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("credit_scores.id", ondelete="RESTRICT"), nullable=False)
    config_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scoring_configurations.id", ondelete="RESTRICT"),
        nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    route_score: Mapped[Decimal] = score(nullable=False)
    customer_score: Mapped[Decimal] = score(nullable=False)
    vehicle_economics_score: Mapped[Decimal] = score(nullable=False)
    route_weight: Mapped[Decimal] = ratio(nullable=False)
    customer_weight: Mapped[Decimal] = ratio(nullable=False)
    vehicle_weight: Mapped[Decimal] = ratio(nullable=False)
    final_score: Mapped[Decimal] = score(nullable=False)
    risk_grade: Mapped[str] = mapped_column(String(2), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    energy_cost_per_km: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    daily_net_contribution: Mapped[Decimal] = small_money(nullable=False)
    monthly_net_contribution: Mapped[Decimal] = small_money(nullable=False)
    recommended_amount: Mapped[Decimal] = money(nullable=False)
    max_ltv_percent: Mapped[Decimal] = score(nullable=False)
    applied_ltv_percent: Mapped[Decimal] = score(nullable=False)
    recommended_tenure_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recommended_interest_rate: Mapped[Decimal] = score(nullable=False)
    estimated_emi: Mapped[Decimal] = small_money(nullable=False)
    dscr: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    foir_post_loan: Mapped[Decimal] = ratio(nullable=False)
    payback_months: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    recommendation: Mapped[str] = enum_column(
        "decision_enum", "APPROVE", "MANUAL_REVIEW", "REJECT", nullable=False)
    reasons: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    assessed_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")

    application: Mapped[LoanApplication] = relationship(back_populates="assessments")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_la_uuid"),
        Index("idx_la_grade", "risk_grade"),
        Index("idx_la_reco", "recommendation"),
        Index("idx_la_date", "assessed_at"),
    )


class UnderwritingDecision(Base):
    """Immutable — Doc 05 §5.3.23. Overrides create a new row, never an edit."""

    __tablename__ = "underwriting_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loan_applications.id", ondelete="RESTRICT"), nullable=False)
    loan_assessment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loan_assessments.id", ondelete="RESTRICT"), nullable=False)
    system_recommendation: Mapped[str] = enum_column(
        "decision_enum", "APPROVE", "MANUAL_REVIEW", "REJECT", nullable=False)
    final_decision: Mapped[str] = enum_column(
        "decision_enum", "APPROVE", "MANUAL_REVIEW", "REJECT", nullable=False)
    is_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    override_justification: Mapped[str | None] = mapped_column(Text)
    approved_amount: Mapped[Decimal | None] = money()
    approved_tenure_months: Mapped[int | None] = mapped_column(SmallInteger)
    approved_interest_rate: Mapped[Decimal | None] = score()
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    reasons: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    decided_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
    second_approver_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"))
    second_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    application: Mapped[LoanApplication] = relationship(back_populates="decisions")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_ud_uuid"),
        Index("idx_ud_app", "application_id", "decided_at"),
        Index("idx_ud_decision", "final_decision", "decided_at"),
        Index("idx_ud_user", "decided_by"),
    )
