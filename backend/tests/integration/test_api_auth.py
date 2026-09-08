"""Authentication end to end — Doc 06 §6.2, Doc 09 §9.2, PRD FR-1.1 to FR-1.7.

These run against the seeded users, so they also prove the thing the MVP Phase 1 definition
of done actually asks for: that the credentials printed in Doc 16 §16.1 let a real person
sign in. Before this suite existed the seed shipped placeholder hashes, and no account in
the system could authenticate at all.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import ratelimit

DEMO_PASSWORD = "Demo@2026!Ev"
RISK_MANAGER = "sabina.karki@bank.com.np"
OFFICER = "ramesh.adhikari@bank.com.np"
VIEWER = "anita.thapa@bank.com.np"

#: Each password test needs its own account; the shared demo users must stay usable.
_PASSWORD_USER_SEQUENCE = itertools.count(1)


def login(client: TestClient, email: str = RISK_MANAGER, password: str = DEMO_PASSWORD):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.fixture
def session_tokens(client: TestClient) -> dict:
    response = login(client)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _reset_lockouts(engine):
    """Each test starts from a clean lockout state; the counter is deliberately durable,
    so it would otherwise leak between tests."""
    yield
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET failed_login_attempts = 0, locked_until = NULL")
        )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_a_seeded_user_can_sign_in_with_the_documented_password(client: TestClient):
    """Regression for a seed that shipped `REPLACE_IN_PRODUCTION` as the password hash:
    every documented demo account was unusable."""
    response = login(client)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == 900


@pytest.mark.parametrize("email", [RISK_MANAGER, OFFICER, VIEWER, "admin@bank.com.np"])
def test_every_seeded_role_can_sign_in(client: TestClient, email):
    """MVP Phase 1 definition of done: one working login per seeded role."""
    assert login(client, email).status_code == 200


def test_the_login_response_carries_the_users_real_permissions(client: TestClient):
    """Read from role_permissions, not hard-coded — a client renders its navigation from
    this list."""
    body = login(client).json()
    permissions = set(body["user"]["permissions"])
    assert "route:assess" in permissions
    assert "risk:config:publish" in permissions
    assert body["user"]["role"]["code"] == "RISK_MANAGER"


def test_a_viewer_receives_only_read_permissions(client: TestClient):
    body = login(client, VIEWER).json()
    assert all(p.endswith(":read") for p in body["user"]["permissions"])


def test_the_response_never_contains_password_material(client: TestClient):
    body = login(client).json()
    assert "password" not in str(body).lower().replace("must_change_password", "")
    assert "password_hash" not in body["user"]


def test_the_login_identity_is_a_uuid_not_a_row_id(client: TestClient):
    import uuid

    uuid.UUID(login(client).json()["user"]["id"])


def test_the_issued_token_actually_opens_the_api(client: TestClient, session_tokens):
    """The whole point. Before this endpoint existed nothing could mint a token, so no
    endpoint in the system was reachable."""
    response = client.get(
        "/api/v1/routes",
        headers={"Authorization": f"Bearer {session_tokens['access_token']}"},
    )
    assert response.status_code == 200


def test_the_token_carries_the_permissions_the_endpoint_checks(client: TestClient):
    viewer = login(client, VIEWER).json()
    denied = client.post(
        "/api/v1/routes",
        json={},
        headers={"Authorization": f"Bearer {viewer['access_token']}"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_a_wrong_password_is_401(client: TestClient):
    response = login(client, RISK_MANAGER, "WrongPassword1!")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_an_unknown_email_is_indistinguishable_from_a_wrong_password(client: TestClient):
    """Doc 09 §9.2.1 — otherwise the endpoint enumerates which accounts exist."""
    unknown = login(client, "nobody@bank.com.np", "WrongPassword1!")
    wrong = login(client, RISK_MANAGER, "WrongPassword1!")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_the_error_body_never_says_which_half_was_wrong(client: TestClient):
    message = login(client, RISK_MANAGER, "WrongPassword1!").json()["error"]["message"]
    assert "password" not in message.lower() or "email or password" in message.lower()


def test_a_malformed_email_is_rejected_before_any_lookup(client: TestClient):
    response = client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "x" * 12}
    )
    assert response.status_code == 422


def test_login_validates_password_length_only(client: TestClient):
    """Doc 06 §6.2 — the policy must not be published to an unauthenticated caller, so a
    weak-but-long password gets a 401, not a 422 explaining the rules."""
    response = login(client, RISK_MANAGER, "alllowercase")
    assert response.status_code == 401


def test_an_inactive_account_cannot_sign_in(client: TestClient, engine):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET is_active = false WHERE email = :e"), {"e": VIEWER}
        )
    try:
        response = login(client, VIEWER)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET is_active = true WHERE email = :e"), {"e": VIEWER}
            )


def test_an_inactive_account_with_a_wrong_password_still_says_401(
    client: TestClient, engine
):
    """The inactive check runs after the password check, so a wrong password cannot be
    used to discover which accounts are deactivated."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET is_active = false WHERE email = :e"), {"e": VIEWER}
        )
    try:
        assert login(client, VIEWER, "WrongPassword1!").status_code == 401
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET is_active = true WHERE email = :e"), {"e": VIEWER}
            )


# ---------------------------------------------------------------------------
# Lockout — Doc 09 §9.2.2, FR-1.5
# ---------------------------------------------------------------------------
def test_five_failures_lock_the_account_for_fifteen_minutes(client: TestClient, engine):
    for _ in range(5):
        assert login(client, OFFICER, "WrongPassword1!").status_code == 401

    # Two controls guard this endpoint and both fire on the sixth attempt: the per-IP
    # throttle (Doc 09 §9.6) and the per-account lockout (§9.2.2). The throttle is cleared
    # here so the lockout's own response is the one under test; the throttle is asserted
    # in test_api_password_security.py.
    ratelimit.reset("login")
    locked = login(client, OFFICER, "WrongPassword1!")
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"

    retry_after = locked.json()["error"]["details"]["retry_after_seconds"]
    assert 0 < retry_after <= 15 * 60


def test_a_locked_account_refuses_even_the_correct_password(client: TestClient):
    for _ in range(5):
        login(client, OFFICER, "WrongPassword1!")
    ratelimit.reset("login")
    assert login(client, OFFICER, DEMO_PASSWORD).status_code == 423


def test_the_failed_attempt_counter_survives_the_rejected_request(
    client: TestClient, engine
):
    """The 401 rolls the request transaction back, so the counter has to be committed
    deliberately or the lockout would never accumulate."""
    login(client, OFFICER, "WrongPassword1!")
    with engine.begin() as conn:
        attempts = conn.execute(
            text("SELECT failed_login_attempts FROM users WHERE email = :e"),
            {"e": OFFICER},
        ).scalar_one()
    assert attempts == 1


def test_a_successful_login_resets_the_counter(client: TestClient, engine):
    for _ in range(3):
        login(client, OFFICER, "WrongPassword1!")
    assert login(client, OFFICER).status_code == 200

    with engine.begin() as conn:
        attempts = conn.execute(
            text("SELECT failed_login_attempts FROM users WHERE email = :e"),
            {"e": OFFICER},
        ).scalar_one()
    assert attempts == 0


def test_an_expired_lockout_lets_the_user_back_in(client: TestClient, engine):
    """The stamp is left in place as evidence, so expiry has to be decided by comparison,
    not by the presence of the value."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 minute' "
                 "WHERE email = :e"),
            {"e": OFFICER},
        )
    assert login(client, OFFICER).status_code == 200


def test_the_lockout_is_recorded_in_the_audit_trail(client: TestClient, engine):
    for _ in range(5):
        login(client, OFFICER, "WrongPassword1!")

    with engine.begin() as conn:
        locked = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'ACCOUNT_LOCKED' "
                 "AND user_email = :e"),
            {"e": OFFICER},
        ).scalar_one()
    assert locked >= 1


# ---------------------------------------------------------------------------
# Refresh rotation and reuse detection — Doc 09 §9.2.3
# ---------------------------------------------------------------------------
def test_a_refresh_returns_a_new_pair(client: TestClient, session_tokens):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": session_tokens["refresh_token"]},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["refresh_token"] != session_tokens["refresh_token"], "token must rotate"
    assert body["access_token"]


def test_the_rotated_access_token_works(client: TestClient, session_tokens):
    rotated = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": session_tokens["refresh_token"]},
    ).json()
    response = client.get(
        "/api/v1/routes",
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    )
    assert response.status_code == 200


def test_reusing_a_rotated_token_revokes_the_whole_family(
    client: TestClient, session_tokens
):
    """Doc 09 §9.2.3 — this is what turns a stolen refresh token from a persistent
    backdoor into a single-use event that trips an alarm."""
    first = session_tokens["refresh_token"]
    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first}
    ).json()["refresh_token"]

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"

    # The token the thief did not have is dead too — the family, not the row, is revoked.
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert after.status_code == 401


def test_reuse_writes_the_named_security_event(client: TestClient, session_tokens, engine):
    first = session_tokens["refresh_token"]
    client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    client.post("/api/v1/auth/refresh", json={"refresh_token": first})

    with engine.begin() as conn:
        events = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'SECURITY_REFRESH_REUSE'")
        ).scalar_one()
    assert events >= 1


def test_an_unknown_refresh_token_is_401(client: TestClient):
    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "x" * 40}
    )
    assert response.status_code == 401


def test_an_expired_session_cannot_be_refreshed(client: TestClient, session_tokens, engine):
    with engine.begin() as conn:
        conn.execute(text("UPDATE user_sessions SET expires_at = now() - interval '1 day'"))
    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session_tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_a_session_past_its_absolute_lifetime_cannot_be_refreshed(
    client: TestClient, session_tokens, engine
):
    """Doc 09 §9.2.4 — 12 hours regardless of activity, measured from the family's first
    token so that rotating cannot extend a session indefinitely."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE user_sessions SET issued_at = now() - interval '13 hours'"))
    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session_tokens["refresh_token"]}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
def test_logout_revokes_the_session(client: TestClient, session_tokens):
    assert client.post(
        "/api/v1/auth/logout", json={"refresh_token": session_tokens["refresh_token"]}
    ).status_code == 204

    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session_tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_logging_out_an_unknown_token_still_succeeds(client: TestClient):
    """A logout that 404s tells an attacker which tokens are real, and the caller's intent
    is satisfied either way."""
    assert client.post(
        "/api/v1/auth/logout", json={"refresh_token": "y" * 40}
    ).status_code == 204


def test_logout_all_revokes_every_session(client: TestClient, engine):
    first = login(client).json()
    second = login(client).json()

    response = client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert response.status_code == 204

    for tokens in (first, second):
        assert client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code == 401


def test_logout_all_requires_authentication(client: TestClient):
    assert client.post("/api/v1/auth/logout-all").status_code == 401


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------
def test_me_returns_the_signed_in_user(client: TestClient, session_tokens):
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {session_tokens['access_token']}"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["email"] == RISK_MANAGER
    assert body["role"]["code"] == "RISK_MANAGER"
    assert body["last_login_at"]


def test_me_reads_permissions_from_the_database_not_the_token(
    client: TestClient, session_tokens, engine
):
    """A permission revoked by an administrator has to be visible before the 15-minute
    access token expires, or a removal takes effect only when the user happens to refresh.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM role_permissions WHERE role_id = "
                "(SELECT id FROM roles WHERE code = 'RISK_MANAGER') AND permission_id = "
                "(SELECT id FROM permissions WHERE code = 'risk:waiver')"
            )
        )
    try:
        body = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {session_tokens['access_token']}"},
        ).json()
        assert "risk:waiver" not in body["permissions"]
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "SELECT r.id, p.id FROM roles r, permissions p "
                    "WHERE r.code = 'RISK_MANAGER' AND p.code = 'risk:waiver' "
                    "ON CONFLICT DO NOTHING"
                )
            )


def test_me_requires_authentication(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_never_returns_the_password_hash(client: TestClient, session_tokens):
    body = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {session_tokens['access_token']}"},
    ).json()
    assert "password_hash" not in body


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------
@pytest.fixture
def throwaway_user(engine, client: TestClient) -> dict:
    """A user this test may change the password of, so the shared demo accounts stay
    usable by every other test in the session."""
    email = f"pwtest{next(_PASSWORD_USER_SEQUENCE):04d}@bank.com.np"
    from app.core.security import hash_password

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (email, password_hash, full_name, role_id, is_active,"
                " must_change_password) SELECT :e, :h, 'Password Test', r.id, true, false"
                " FROM roles r WHERE r.code = 'VIEWER'"
            ),
            {"e": email, "h": hash_password(DEMO_PASSWORD)},
        )
    return {"email": email, "password": DEMO_PASSWORD}


def test_a_user_can_change_their_password(client: TestClient, throwaway_user):
    tokens = login(client, throwaway_user["email"]).json()
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": "N3w!Passw0rd#2026"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 204
    assert login(client, throwaway_user["email"], "N3w!Passw0rd#2026").status_code == 200
    assert login(client, throwaway_user["email"], DEMO_PASSWORD).status_code == 401


def test_changing_the_password_revokes_the_other_sessions(
    client: TestClient, throwaway_user
):
    """Doc 09 §9.2.4 — the old credential may already be in someone else's hands."""
    first = login(client, throwaway_user["email"]).json()
    second = login(client, throwaway_user["email"]).json()

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": "An0ther!Passw0rd#26"},
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    ).status_code == 401


def test_the_wrong_current_password_is_refused(client: TestClient, throwaway_user):
    tokens = login(client, throwaway_user["email"]).json()
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "NotTheOne1!", "new_password": "N3w!Passw0rd#2026"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 401


def test_a_weak_new_password_is_refused_with_the_reasons(
    client: TestClient, throwaway_user
):
    """Here the policy *is* explained: the caller is authenticated, so there is nothing to
    leak, and a user setting a password needs to know why it was refused."""
    tokens = login(client, throwaway_user["email"]).json()
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": "short"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422
    codes = {d["code"] for d in response.json()["error"]["details"]}
    assert "TOO_SHORT" in codes


def test_reusing_the_current_password_is_refused(client: TestClient, throwaway_user):
    tokens = login(client, throwaway_user["email"]).json()
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": DEMO_PASSWORD},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422
    assert "SAME_AS_CURRENT" in {
        d["code"] for d in response.json()["error"]["details"]
    }


def test_changing_a_password_clears_the_must_change_flag(
    client: TestClient, throwaway_user, engine
):
    """FR-1.7 — an admin-created account is forced to change on first login; completing
    that has to clear the flag or the user is trapped in the loop."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET must_change_password = true WHERE email = :e"),
            {"e": throwaway_user["email"]},
        )
    tokens = login(client, throwaway_user["email"]).json()
    assert tokens["user"]["must_change_password"] is True

    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": "Cl3ared!Passw0rd#26"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    after = login(client, throwaway_user["email"], "Cl3ared!Passw0rd#26").json()
    assert after["user"]["must_change_password"] is False


def test_change_password_requires_authentication(client: TestClient):
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "x", "new_password": "N3w!Passw0rd#2026"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------
def test_forgot_password_never_reveals_whether_the_account_exists(client: TestClient):
    known = client.post("/api/v1/auth/forgot-password", json={"email": RISK_MANAGER})
    unknown = client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@bank.com.np"}
    )
    assert known.status_code == unknown.status_code == 204
    assert known.content == unknown.content


def test_a_reset_request_is_audit_logged(client: TestClient, engine):
    client.post("/api/v1/auth/forgot-password", json={"email": RISK_MANAGER})
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs "
                 "WHERE action = 'PASSWORD_RESET_REQUESTED' AND user_email = :e"),
            {"e": RISK_MANAGER},
        ).scalar_one()
    assert count >= 1


# ---------------------------------------------------------------------------
# Audit trail — FR-9.6, Doc 09 §9.8
# ---------------------------------------------------------------------------
def test_a_successful_login_is_audit_logged_with_the_actor(client: TestClient, engine):
    login(client)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT user_email, user_role, status, ip_address FROM audit_logs "
                 "WHERE action = 'LOGIN_SUCCESS' AND user_email = :e "
                 "ORDER BY id DESC LIMIT 1"),
            {"e": RISK_MANAGER},
        ).one()
    assert row[0] == RISK_MANAGER
    assert row[1] == "RISK_MANAGER"
    assert row[2] == "SUCCESS"


def test_a_failed_login_is_audit_logged_as_a_failure(client: TestClient, engine):
    login(client, RISK_MANAGER, "WrongPassword1!")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, error_message FROM audit_logs "
                 "WHERE action = 'LOGIN_FAILURE' AND user_email = :e "
                 "ORDER BY id DESC LIMIT 1"),
            {"e": RISK_MANAGER},
        ).one()
    assert row[0] == "FAILURE"
    assert row[1] == "BAD_PASSWORD"


def test_a_login_attempt_on_an_unknown_email_is_still_recorded(
    client: TestClient, engine
):
    """Credential stuffing shows up as failures against addresses that do not exist; a
    trail that only records known users cannot see it."""
    login(client, "attacker-probe@bank.com.np", "WrongPassword1!")
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'LOGIN_FAILURE' "
                 "AND user_email = :e"),
            {"e": "attacker-probe@bank.com.np"},
        ).scalar_one()
    assert count == 1


def test_the_audit_trail_never_stores_the_password(client: TestClient, engine):
    login(client, RISK_MANAGER, "SuperSecretGuess1!")
    with engine.begin() as conn:
        blob = conn.execute(
            text("SELECT coalesce(before_state::text,'') || coalesce(after_state::text,'') "
                 "FROM audit_logs WHERE action = 'LOGIN_FAILURE' ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    assert "SuperSecretGuess1!" not in blob


def test_audit_rows_are_immutable(client: TestClient, engine):
    """The database refuses the update even to the application role (Doc 09 §9.8)."""
    import sqlalchemy

    login(client)
    with pytest.raises(sqlalchemy.exc.DatabaseError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_logs SET action = 'TAMPERED'"))


def test_the_audit_row_commits_with_the_change_it_describes(
    client: TestClient, engine, throwaway_user
):
    """The password change and its audit row are one unit of work: a committed change
    cannot be missing its entry."""
    tokens = login(client, throwaway_user["email"]).json()
    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEMO_PASSWORD, "new_password": "Atom1c!Passw0rd#26"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    with engine.begin() as conn:
        changed = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'PASSWORD_CHANGED' "
                 "AND user_email = :e AND status = 'SUCCESS'"),
            {"e": throwaway_user["email"]},
        ).scalar_one()
        password_now = conn.execute(
            text("SELECT password_changed_at IS NOT NULL FROM users WHERE email = :e"),
            {"e": throwaway_user["email"]},
        ).scalar_one()
    assert changed == 1
    assert password_now is True
