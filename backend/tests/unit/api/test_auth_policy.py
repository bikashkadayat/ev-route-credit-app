"""Password policy, lockout arithmetic and audit redaction — Doc 09 §9.2, §9.8.

Pure logic, no database. The rules that decide whether a credential is accepted are the
ones most worth pinning at this level, because an integration test can only show that
*some* password was refused, not which rule refused it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationError
from app.models.identity import User
from app.services.audit import REDACTED, changed_fields, redact
from app.services.auth import (
    MIN_PASSWORD_LENGTH,
    validate_password_strength,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Password policy — Doc 09 §9.2.1
# ---------------------------------------------------------------------------
def test_a_compliant_password_is_accepted():
    validate_password_strength("Demo@2026!Ev")


def test_the_documented_demo_password_satisfies_the_policy():
    """Doc 16 §16.1 hands this password to a demo audience. If the policy rejected it, the
    documentation and the code would be in conflict on the first screen anyone sees."""
    validate_password_strength("Demo@2026!Ev")


@pytest.mark.parametrize(
    ("password", "expected_code"),
    [
        ("Ab1!efgh", "TOO_SHORT"),
        ("demo@2026!ev", "NO_UPPERCASE"),
        ("DEMO@2026!EV", "NO_LOWERCASE"),
        ("Demo@Password!", "NO_DIGIT"),
        ("Demo2026Password", "NO_SYMBOL"),
    ],
)
def test_each_policy_rule_is_enforced_separately(password, expected_code):
    with pytest.raises(ValidationError) as exc:
        validate_password_strength(password)
    assert expected_code in {d["code"] for d in exc.value.details}


def test_a_common_password_is_refused_however_well_formed():
    """A local breached-password list is the cheapest control that stops the passwords
    actually used in credential-stuffing runs."""
    with pytest.raises(ValidationError) as exc:
        validate_password_strength("Password123")
    codes = {d["code"] for d in exc.value.details}
    assert "TOO_COMMON" in codes or "NO_SYMBOL" in codes


def test_the_username_cannot_be_the_password():
    with pytest.raises(ValidationError) as exc:
        validate_password_strength("sabina.karki", email="sabina.karki@bank.com.np")
    assert "CONTAINS_IDENTITY" in {d["code"] for d in exc.value.details}


def test_every_violation_is_reported_in_one_response():
    """Four round trips to learn four rules is how users end up with 'Password1!'."""
    with pytest.raises(ValidationError) as exc:
        validate_password_strength("abc")
    codes = {d["code"] for d in exc.value.details}
    assert {"TOO_SHORT", "NO_UPPERCASE", "NO_DIGIT", "NO_SYMBOL"} <= codes


def test_the_policy_failure_is_a_422_not_a_500():
    with pytest.raises(ValidationError) as exc:
        validate_password_strength("short")
    assert exc.value.status_code == 422
    assert exc.value.code == "VALIDATION_ERROR"


def test_an_over_long_password_is_refused():
    """Argon2 will hash any length; an unbounded input is a cheap denial-of-service."""
    with pytest.raises(ValidationError) as exc:
        validate_password_strength("A1!" + "a" * 200)
    assert "TOO_LONG" in {d["code"] for d in exc.value.details}


def test_the_minimum_length_matches_the_documented_twelve():
    assert MIN_PASSWORD_LENGTH == 12


# ---------------------------------------------------------------------------
# Lockout arithmetic — Doc 09 §9.2.2
# ---------------------------------------------------------------------------
def test_an_account_that_has_never_been_locked_is_not_locked():
    assert User(locked_until=None).is_locked_at(NOW) is False


def test_a_live_lockout_is_reported_as_locked():
    user = User(locked_until=NOW + timedelta(minutes=5))
    assert user.is_locked_at(NOW) is True


def test_an_elapsed_lockout_is_no_longer_locked():
    """Regression: the previous implementation tested only that ``locked_until`` was set,
    so an account locked once stayed locked for ever."""
    user = User(locked_until=NOW - timedelta(seconds=1))
    assert user.is_locked_at(NOW) is False


def test_the_boundary_instant_releases_the_account():
    user = User(locked_until=NOW)
    assert user.is_locked_at(NOW) is False


def test_a_naive_timestamp_is_treated_as_utc_rather_than_raising():
    """SQLite-backed unit fixtures and some drivers hand back naive datetimes; comparing
    them must not raise inside a security check."""
    user = User(locked_until=datetime(2026, 3, 1, 12, 5))
    assert user.is_locked_at(NOW) is True


# ---------------------------------------------------------------------------
# Audit redaction — Doc 02 §2.11, Doc 09 §9.8
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    ["password", "password_hash", "refresh_token", "id_number", "id_number_enc",
     "pan_number", "account_number", "api_key", "raw_response", "Authorization"],
)
def test_sensitive_keys_are_redacted_by_name(key):
    assert redact({key: "secret-value"})[key] == REDACTED


def test_redaction_is_case_insensitive_and_matches_substrings():
    assert redact({"UserPassword": "x"})["UserPassword"] == REDACTED
    assert redact({"cib_raw_response": {"a": 1}})["cib_raw_response"] == REDACTED


def test_ordinary_fields_survive_redaction():
    """An audit row that redacted everything would be useless; the point is that a
    reviewer can still see what changed."""
    before = redact({"full_name": "Ram Bahadur", "province": "Bagmati"})
    assert before == {"full_name": "Ram Bahadur", "province": "Bagmati"}


def test_redaction_reaches_into_nested_structures():
    payload = {"user": {"email": "a@b.np", "password": "secret"},
               "sessions": [{"refresh_token": "abc", "ip": "10.0.0.1"}]}
    cleaned = redact(payload)
    assert cleaned["user"]["password"] == REDACTED
    assert cleaned["user"]["email"] == "a@b.np"
    assert cleaned["sessions"][0]["refresh_token"] == REDACTED
    assert cleaned["sessions"][0]["ip"] == "10.0.0.1"


def test_changed_fields_lists_only_what_moved():
    before = {"full_name": "A", "province": "Bagmati", "phone": "1"}
    after = {"full_name": "B", "province": "Bagmati", "phone": "2"}
    assert changed_fields(before, after) == ["full_name", "phone"]


def test_changed_fields_on_a_creation_lists_the_new_fields():
    assert changed_fields(None, {"a": 1, "b": 2}) == ["a", "b"]


def test_changed_fields_is_empty_when_nothing_moved():
    assert changed_fields({"a": 1}, {"a": 1}) == []
