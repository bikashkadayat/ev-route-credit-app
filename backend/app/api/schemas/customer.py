"""Customer schemas — Doc 06 §6.4.

The national identifier is accepted on create and **never returned**: responses carry a
masked form only (Doc 09 §9.7.4).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import EmailStr, Field, model_validator

from app.api.schemas.common import ApiModel, PublicId

ApplicantType = Literal[
    "INDIVIDUAL_DRIVER", "OWNER_DRIVER", "CORPORATE", "FLEET_OPERATOR", "SME",
    "TRANSPORT_COMPANY",
]
IdType = Literal["CITIZENSHIP", "PASSPORT", "PAN", "COMPANY_REG"]
Gender = Literal["MALE", "FEMALE", "OTHER"]
ApplicantStatus = Literal["ACTIVE", "BLACKLISTED", "INACTIVE"]

INDIVIDUAL_TYPES = {"INDIVIDUAL_DRIVER", "OWNER_DRIVER"}
BUSINESS_TYPES = {"CORPORATE", "FLEET_OPERATOR", "SME", "TRANSPORT_COMPANY"}


class CustomerBase(ApiModel):
    applicant_type: ApplicantType
    full_name: Annotated[str, Field(min_length=2, max_length=150)]
    date_of_birth: date | None = None
    gender: Gender | None = None
    phone: Annotated[str, Field(min_length=7, max_length=20)]
    alt_phone: Annotated[str, Field(max_length=20)] | None = None
    email: EmailStr | None = None

    province: Annotated[str, Field(max_length=40)]
    district: Annotated[str, Field(max_length=60)]
    municipality: Annotated[str, Field(max_length=80)]
    ward_no: Annotated[int, Field(ge=1, le=40)] | None = None
    address_line: Annotated[str, Field(max_length=200)] | None = None

    total_experience_years: Annotated[Decimal, Field(ge=0, le=70)] = Decimal("0")
    driving_experience_years: Annotated[Decimal, Field(ge=0, le=70)] | None = None
    commercial_driving_years: Annotated[Decimal, Field(ge=0, le=70)] | None = None
    business_experience_years: Annotated[Decimal, Field(ge=0, le=99)] | None = None
    licence_category: Annotated[str, Field(max_length=20)] | None = None
    licence_expiry: date | None = None
    has_previous_ev_experience: bool = False

    company_reg_number: Annotated[str, Field(max_length=40)] | None = None
    company_reg_date: date | None = None
    fleet_size: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _type_specific_requirements(self) -> CustomerBase:
        """Doc 06 §6.4 — the required field set depends on the applicant type."""
        if self.applicant_type in INDIVIDUAL_TYPES:
            if self.date_of_birth is None:
                raise ValueError("date_of_birth is required for individual applicants")
            if self.driving_experience_years is None:
                raise ValueError(
                    "driving_experience_years is required for individual applicants"
                )
        if self.applicant_type in BUSINESS_TYPES:
            if not self.company_reg_number:
                raise ValueError("company_reg_number is required for business applicants")
            if self.business_experience_years is None:
                raise ValueError(
                    "business_experience_years is required for business applicants"
                )
        if self.licence_expiry and self.date_of_birth:
            if self.licence_expiry <= self.date_of_birth:
                raise ValueError("licence_expiry must be after date_of_birth")
        return self


class CustomerCreate(CustomerBase):
    id_type: IdType
    id_number: Annotated[str, Field(min_length=4, max_length=40)] = Field(
        description="Stored encrypted and never returned; responses carry a masked form."
    )
    pan_number: Annotated[str, Field(max_length=40)] | None = None


class CustomerUpdate(CustomerBase):
    """The identifier and applicant code are immutable and are ignored if supplied."""

    pan_number: Annotated[str, Field(max_length=40)] | None = None


class CustomerSummary(ApiModel):
    id: PublicId
    applicant_code: str
    applicant_type: str
    full_name: str
    phone: str
    province: str
    district: str
    status: str
    created_at: datetime


class FinancialProfileOut(ApiModel):
    version_no: int
    is_current: bool
    monthly_income: Decimal
    monthly_business_revenue: Decimal
    other_monthly_income: Decimal
    monthly_household_expenses: Decimal
    monthly_business_expenses: Decimal
    total_monthly_income: Decimal
    total_monthly_expenses: Decimal
    existing_loan_count: int
    total_existing_emi: Decimal
    total_existing_outstanding: Decimal
    avg_bank_balance_6m: Decimal
    dependants_count: int
    income_proof_type: str | None = None
    income_verified: bool
    created_at: datetime


class ObligationIn(ApiModel):
    lender_name: Annotated[str, Field(max_length=120)]
    loan_type: Annotated[str, Field(max_length=40)]
    original_amount: Annotated[Decimal, Field(ge=0)]
    outstanding_amount: Annotated[Decimal, Field(ge=0)]
    monthly_emi: Annotated[Decimal, Field(ge=0)]
    remaining_tenure_months: Annotated[int, Field(ge=0)] | None = None
    is_overdue: bool = False
    days_past_due: Annotated[int, Field(ge=0)] = 0
    source: Literal["DECLARED", "CIB"] = "DECLARED"


class FinancialProfileCreate(ApiModel):
    """Creates a new version. Totals are derived from ``obligations``, never accepted."""

    monthly_income: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    monthly_business_revenue: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    other_monthly_income: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    monthly_household_expenses: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    monthly_business_expenses: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    avg_bank_balance_6m: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    bank_account_count: Annotated[int, Field(ge=0)] = 0
    dependants_count: Annotated[int, Field(ge=0)] = 0
    has_guarantor: bool = False
    income_proof_type: Literal[
        "SALARY_SLIP", "BANK_STATEMENT", "SELF_DECLARED", "AUDITED_FINANCIALS"
    ] = "SELF_DECLARED"
    income_verified: bool = False
    notes: Annotated[str, Field(max_length=1000)] | None = None
    obligations: list[ObligationIn] = Field(default_factory=list)


class ObligationOut(ObligationIn):
    pass


class CustomerDetail(CustomerSummary):
    id_type: str
    id_number_masked: str = Field(
        description="Doc 09 §9.7.4 — the raw identifier is never returned"
    )
    date_of_birth: date | None = None
    gender: str | None = None
    email: str | None = None
    alt_phone: str | None = None
    municipality: str
    ward_no: int | None = None
    address_line: str | None = None
    total_experience_years: Decimal
    driving_experience_years: Decimal | None = None
    commercial_driving_years: Decimal | None = None
    business_experience_years: Decimal | None = None
    licence_category: str | None = None
    licence_expiry: date | None = None
    has_previous_ev_experience: bool
    company_reg_number: str | None = None
    fleet_size: int | None = None
    updated_at: datetime
    current_financial_profile: FinancialProfileOut | None = None
    obligations: list[ObligationOut] = Field(default_factory=list)
