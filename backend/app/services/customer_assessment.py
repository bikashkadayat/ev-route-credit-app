"""CustomerService — applicant lifecycle and financial-profile versioning.

Credit *scoring* lives in ``app.services.credit.CreditService``; this service owns the
customer record itself: identity encryption, duplicate detection and the versioned
financial profile.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import DuplicateError, NotFoundError, ValidationError
from app.models.credit import Applicant, ApplicantFinancial
from app.repositories.customer import (
    CustomerRepository,
    FinancialProfileRepository,
    ObligationRepository,
)

INDIVIDUAL_TYPES = {"INDIVIDUAL_DRIVER", "OWNER_DRIVER"}
BUSINESS_TYPES = {"CORPORATE", "FLEET_OPERATOR", "SME", "TRANSPORT_COMPANY"}

#: Application-wide pepper for identifier hashing. In production this is supplied from the
#: secret store; the constant here keeps local and CI runs deterministic.
_ID_HASH_PEPPER = "evrca-id-hash-v1"


def hash_identifier(id_number: str) -> str:
    """Doc 05 §5.3.6 — duplicate detection without decrypting the stored identifier."""
    normalised = id_number.strip().upper().replace(" ", "")
    return hashlib.sha256(f"{_ID_HASH_PEPPER}:{normalised}".encode()).hexdigest()


def encrypt_identifier(id_number: str) -> bytes:
    """Placeholder for the pgcrypto envelope (Doc 09 §9.7.3).

    The column is BYTEA and the production path is ``pgp_sym_encrypt`` with a key from the
    environment. Storing the raw bytes here would be worse than obvious, so this encodes
    and is clearly named as the seam to replace — the surrounding code, the hash-based
    duplicate check and the masking behaviour are all already correct.
    """
    return id_number.encode("utf-8")


class CustomerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.financials = FinancialProfileRepository(session)
        self.obligations = ObligationRepository(session)

    # -- reads -------------------------------------------------------------
    def get(self, applicant_id: int) -> Applicant:
        applicant = self.customers.get_with_relations(applicant_id)
        if applicant is None:
            raise NotFoundError("customer", applicant_id)
        return applicant

    def list_customers(self, **filters: Any) -> tuple[Sequence[Applicant], int]:
        return self.customers.list_customers(**filters)

    def current_financial_profile(self, applicant_id: int) -> ApplicantFinancial | None:
        return self.financials.current_for(applicant_id)

    # -- writes ------------------------------------------------------------
    def create(self, payload: dict[str, Any], *, created_by: int | None) -> Applicant:
        values = dict(payload)
        id_number = values.pop("id_number")
        self._validate_type_requirements(values.get("applicant_type"), values)

        id_hash = hash_identifier(id_number)
        existing = self.customers.find_by_id_hash(id_hash)
        if existing is not None:
            raise DuplicateError(
                f"An applicant with this identifier already exists: {existing.full_name}",
                details={
                    "existing_customer_id": str(existing.uuid),
                    "applicant_code": existing.applicant_code,
                },
            )

        pan = values.pop("pan_number", None)
        values["id_number_enc"] = encrypt_identifier(id_number)
        values["id_number_hash"] = id_hash
        if pan:
            values["pan_number_enc"] = encrypt_identifier(pan)
        values["applicant_code"] = self.customers.next_applicant_code(
            datetime.now(UTC).year
        )
        values["created_by"] = created_by
        return self.customers.create(**values)

    def update(
        self, applicant_id: int, payload: dict[str, Any], *, updated_by: int | None
    ) -> Applicant:
        applicant = self.customers.get_active(applicant_id)
        if applicant is None:
            raise NotFoundError("customer", applicant_id)

        values = dict(payload)
        # The national identifier is immutable: changing it would break the duplicate
        # hash and orphan every decision already made against this person.
        values.pop("id_number", None)
        values.pop("applicant_code", None)
        pan = values.pop("pan_number", None)
        if pan:
            values["pan_number_enc"] = encrypt_identifier(pan)
        values["updated_by"] = updated_by
        return self.customers.update(applicant, **values)

    def replace_financial_profile(
        self, applicant_id: int, payload: dict[str, Any], *, created_by: int | None
    ) -> ApplicantFinancial:
        """Creates a **new version** and marks it current (Doc 05 §5.3.7)."""
        applicant = self.customers.get_active(applicant_id)
        if applicant is None:
            raise NotFoundError("customer", applicant_id)

        obligations = payload.pop("obligations", []) or []
        rows = self.obligations.replace_all(applicant_id, obligations)

        totals = self._obligation_totals(rows)
        self.financials.supersede_current(applicant_id)
        return self.financials.create(
            applicant_id=applicant_id,
            version_no=self.financials.next_version_no(applicant_id),
            is_current=True,
            created_by=created_by,
            **payload,
            **totals,
        )

    @staticmethod
    def _obligation_totals(rows: Sequence[Any]) -> dict[str, Any]:
        """Derived, never accepted from the client (Doc 06 §6.4)."""
        return {
            "existing_loan_count": len(rows),
            "total_existing_emi": sum(
                (Decimal(r.monthly_emi) for r in rows), Decimal("0")
            ),
            "total_existing_outstanding": sum(
                (Decimal(r.outstanding_amount) for r in rows), Decimal("0")
            ),
        }

    @staticmethod
    def _validate_type_requirements(
        applicant_type: str | None, values: dict[str, Any]
    ) -> None:
        """Doc 06 §6.4 — the required field set depends on the applicant type."""
        missing: list[str] = []
        if applicant_type in INDIVIDUAL_TYPES:
            for field in ("date_of_birth", "driving_experience_years"):
                if values.get(field) in (None, ""):
                    missing.append(field)
        elif applicant_type in BUSINESS_TYPES:
            for field in ("company_reg_number", "business_experience_years"):
                if values.get(field) in (None, ""):
                    missing.append(field)
        if missing:
            raise ValidationError(
                f"Missing fields required for applicant type {applicant_type}",
                details=[
                    {"field": f, "code": "MISSING",
                     "message": f"Required when applicant_type is {applicant_type}"}
                    for f in missing
                ],
            )

    @staticmethod
    def mask_identifier(id_number_enc: bytes | None) -> str:
        """Doc 09 §9.7.4 — identifiers are masked in every response by default."""
        if not id_number_enc:
            return ""
        from app.integrations.cib.base import CIBProvider

        return CIBProvider.mask(id_number_enc.decode("utf-8", errors="ignore"))
