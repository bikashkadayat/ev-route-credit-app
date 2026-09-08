"""The error envelope, pagination and the repository query primitives — Doc 06 §6.1.

Forty-odd endpoints share one error shape and one pagination contract. Both are defined in
exactly one place, so both are testable in exactly one place.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.dependencies import Pagination, pagination_params
from app.core.errors import (
    ERROR_CODES,
    AppError,
    ConfigurationNotPublishedError,
    ConflictError,
    DuplicateError,
    IncompleteDataError,
    NotFoundError,
    PermissionDeniedError,
    ProviderUnavailableError,
    StaleVersionError,
    UnauthorizedError,
    ValidationError,
    error_payload,
)
from app.models.catalog import Route
from app.repositories.base import apply_soft_delete_filter, apply_sort


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------
def test_the_envelope_always_has_the_same_five_keys():
    payload = error_payload(code="VALIDATION_ERROR", message="nope", request_id="abc")
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {
        "code", "message", "details", "request_id", "timestamp"
    }


def test_details_default_to_an_empty_object_never_null():
    """A client can always read ``error.details`` without a null check."""
    assert error_payload(code="INTERNAL_ERROR", message="x")["error"]["details"] == {}


def test_the_timestamp_is_utc_and_zulu_suffixed():
    stamp = error_payload(code="INTERNAL_ERROR", message="x")["error"]["timestamp"]
    assert stamp.endswith("Z") and "+" not in stamp


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_code"),
    [
        (ValidationError("bad"), 422, "VALIDATION_ERROR"),
        (UnauthorizedError("no token"), 401, "INVALID_CREDENTIALS"),
        (PermissionDeniedError("route:assess"), 403, "PERMISSION_DENIED"),
        (DuplicateError("exists"), 409, "DUPLICATE_RESOURCE"),
        (ConflictError("closed"), 409, "INVALID_STATE_TRANSITION"),
        (StaleVersionError("stale"), 409, "STALE_VERSION"),
        (ConfigurationNotPublishedError("ROUTE"), 409, "CONFIG_NOT_PUBLISHED"),
        (ProviderUnavailableError("cib down"), 503, "EXTERNAL_PROVIDER_UNAVAILABLE"),
    ],
)
def test_each_typed_error_carries_its_documented_status_and_code(
    exception, expected_status, expected_code
):
    assert exception.status_code == expected_status
    assert exception.code == expected_code
    assert exception.to_payload()["error"]["code"] == expected_code


def test_every_declared_code_is_in_the_documented_closed_set():
    """Doc 06 §6.1.5 lists the codes clients may switch on; a new one must be added there
    before it can be raised."""
    for exception in (
        ValidationError("x"), UnauthorizedError("x"), PermissionDeniedError("p"),
        DuplicateError("x"), ConflictError("x"), StaleVersionError("x"),
        ConfigurationNotPublishedError("ROUTE"), ProviderUnavailableError("x"),
        IncompleteDataError("x", ["a"]), AppError("x"),
    ):
        assert exception.code in ERROR_CODES, exception.code


def test_not_found_is_specific_enough_for_a_client_to_act_on():
    error = NotFoundError("route", "abc-123")
    assert error.status_code == 404
    assert error.code == "ROUTE_NOT_FOUND"
    assert error.details == {"resource": "route", "id": "abc-123"}


def test_a_multi_word_resource_becomes_a_single_code():
    assert NotFoundError("loan application", 7).code == "LOAN_APPLICATION_NOT_FOUND"


def test_not_found_without_an_identifier_omits_the_id():
    assert NotFoundError("alert").details == {"resource": "alert"}


def test_out_of_scope_resources_are_404_not_403():
    """Doc 09 §9.3.2 — a 403 would confirm the id exists to a caller who may not see it."""
    assert NotFoundError("customer", 99).status_code == 404


def test_incomplete_data_lists_every_missing_field_at_once():
    """One round trip tells the caller everything to fix, not the first problem only."""
    error = IncompleteDataError("route is incomplete", ["max_charging_gap_km", "avg_fare"])
    assert error.status_code == 422
    assert [d["field"] for d in error.details] == ["max_charging_gap_km", "avg_fare"]
    assert all(d["code"] == "MISSING" for d in error.details)


def test_an_explicit_code_and_status_override_the_class_defaults():
    error = AppError("x", code="RATE_LIMIT_EXCEEDED", status_code=429)
    assert (error.code, error.status_code) == ("RATE_LIMIT_EXCEEDED", 429)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def test_the_default_page_size_comes_from_settings():
    assert pagination_params().page_size == settings.default_page_size


def test_offset_is_derived_from_the_page_number():
    assert Pagination(page=1, page_size=20).offset == 0
    assert Pagination(page=3, page_size=20).offset == 40


def test_page_size_is_capped_server_side():
    """The query parameter is also bounded by FastAPI, but the cap is enforced here too so
    an internal caller cannot ask for the whole book in one page."""
    assert pagination_params(page=1, page_size=10_000).page_size == settings.max_page_size


def test_a_smaller_page_size_is_honoured():
    assert pagination_params(page=2, page_size=5).page_size == 5


def test_pagination_is_immutable():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        Pagination(page=1, page_size=20).page = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Repository query primitives
# ---------------------------------------------------------------------------
ROUTE_SORTS = frozenset({"latest_score", "route_name", "created_at"})


def test_sorting_is_restricted_to_an_allow_list():
    """Doc 06 §6.1.2 — an arbitrary column would let a caller order by an unindexed or
    sensitive field."""
    with pytest.raises(ValidationError) as exc:
        apply_sort(select(Route), Route, "id_number", ROUTE_SORTS)
    assert exc.value.status_code == 422
    assert exc.value.details[0]["code"] == "NOT_SORTABLE"


def test_the_rejection_tells_the_caller_what_is_allowed():
    with pytest.raises(ValidationError) as exc:
        apply_sort(select(Route), Route, "secret", ROUTE_SORTS)
    assert "latest_score" in exc.value.details[0]["message"]


def test_a_leading_minus_means_descending():
    ascending = str(apply_sort(select(Route), Route, "latest_score", ROUTE_SORTS))
    descending = str(apply_sort(select(Route), Route, "-latest_score", ROUTE_SORTS))
    assert "ORDER BY routes.latest_score" in ascending
    assert "DESC" in descending and "DESC" not in ascending


def test_no_sort_leaves_the_statement_untouched():
    stmt = select(Route)
    assert apply_sort(stmt, Route, None, ROUTE_SORTS) is stmt
    assert apply_sort(stmt, Route, "", ROUTE_SORTS) is stmt


def test_soft_deleted_rows_are_filtered_out():
    """Doc 05 §5.1 principle 6 — a deleted route must be invisible to every query."""
    assert "deleted_at IS NULL" in str(apply_soft_delete_filter(select(Route), Route))


def test_the_soft_delete_filter_is_a_no_op_for_a_table_without_the_column():
    from app.models.system import AuditLog

    stmt = select(AuditLog)
    assert apply_soft_delete_filter(stmt, AuditLog) is stmt


# ---------------------------------------------------------------------------
# Service-level derivations (pure helpers, no session)
# ---------------------------------------------------------------------------
def test_charging_density_is_stations_per_ten_kilometres():
    from app.services.route_assessment import RouteAssessmentService

    assert RouteAssessmentService._density(6, Decimal("30")) == Decimal("2.000")


def test_charging_density_of_a_zero_length_route_is_zero_not_a_crash():
    from app.services.route_assessment import RouteAssessmentService

    assert RouteAssessmentService._density(3, Decimal("0")) == Decimal("0")


def test_identifier_hashing_is_stable_and_normalised():
    """Duplicate detection must survive whitespace and case differences in data entry."""
    from app.services.customer_assessment import hash_identifier

    assert hash_identifier("27-01-70-01234") == hash_identifier(" 27-01-70-01234 ")
    assert hash_identifier("ab-1") == hash_identifier("AB-1")
    assert hash_identifier("27-01-70-01234") != hash_identifier("27-01-70-01235")
    assert len(hash_identifier("x")) == 64


def test_a_masked_identifier_keeps_only_the_leading_segments():
    """Doc 09 §9.7.4 — enough for an officer to recognise the record, not enough to reuse."""
    from app.services.customer_assessment import CustomerService

    masked = CustomerService.mask_identifier(b"27-01-70-01234")
    assert masked.startswith("27-01-70-")
    assert "01234" not in masked


def test_masking_a_missing_identifier_returns_an_empty_string():
    from app.services.customer_assessment import CustomerService

    assert CustomerService.mask_identifier(None) == ""
    assert CustomerService.mask_identifier(b"") == ""


def test_licence_months_remaining_is_measured_from_the_business_date():
    """The engine is pure, so the business date is resolved by the service and passed in."""
    from datetime import date

    from app.services.credit import CreditService

    applicant = type("A", (), {"licence_expiry": date(2027, 1, 1)})()
    assert CreditService._licence_months_remaining(applicant, date(2026, 1, 1)) == Decimal(
        "12.0"
    )


def test_a_missing_licence_expiry_yields_none_not_zero():
    """A null must reach the engine as "unknown"; zero would read as "expired today"."""
    from datetime import date

    from app.services.credit import CreditService

    applicant = type("A", (), {"licence_expiry": None})()
    assert CreditService._licence_months_remaining(applicant, date(2026, 1, 1)) is None


def test_applicant_ages_are_derived_at_application_and_at_maturity():
    from datetime import date

    from app.services.credit import CreditService

    applicant = type("A", (), {"date_of_birth": date(1992, 1, 1)})()
    application = type("L", (), {"requested_tenure_months": 60})()
    age, at_maturity = CreditService._ages(applicant, application, date(2026, 1, 1))
    assert age == Decimal("34.0")
    assert at_maturity == Decimal("39.0")


def test_a_missing_date_of_birth_yields_none_for_both_ages():
    from datetime import date

    from app.services.credit import CreditService

    applicant = type("A", (), {"date_of_birth": None})()
    application = type("L", (), {"requested_tenure_months": 60})()
    assert CreditService._ages(applicant, application, date(2026, 1, 1)) == (None, None)


def test_the_charging_baseline_is_reconstructed_from_the_stored_change_percentage():
    from app.services.alert import AlertService

    snap = type("S", (), {"charging_sessions_30d": 15, "charging_change_percent": -50})()
    assert AlertService._charging_baseline(snap) == Decimal("30")


@pytest.mark.parametrize(
    "snap",
    [
        None,
        type("S", (), {"charging_sessions_30d": None, "charging_change_percent": -50})(),
        type("S", (), {"charging_sessions_30d": 10, "charging_change_percent": None})(),
        type("S", (), {"charging_sessions_30d": 10, "charging_change_percent": -100})(),
    ],
    ids=["no-snapshot", "no-sessions", "no-change", "total-collapse"],
)
def test_an_underivable_charging_baseline_is_none_so_the_rule_cannot_fire(snap):
    """Doc 08 §8.4 — a missing metric never fires a rule. A defaulted zero would."""
    from app.services.alert import AlertService

    assert AlertService._charging_baseline(snap) is None
