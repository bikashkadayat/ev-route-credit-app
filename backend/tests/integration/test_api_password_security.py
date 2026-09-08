"""Password reset, reuse history and rate limiting — FR-1.2, FR-1.6, Doc 09 §9.6.

These close the two `[M]` requirements that were previously blocked on storage Doc 05 did
not define, plus the abuse control that stops a guessing run being spread thinly across
many accounts.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import ratelimit
from app.core.security import hash_password

_SEQ = itertools.count(1)
STRONG = "Str0ng!Passw0rd#26"


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Counters are process-local; one test's burst must not fail the next."""
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def account(engine) -> dict:
    """A throwaway user, so the shared demo accounts stay usable by other suites."""
    email = f"resettest{next(_SEQ):04d}@bank.com.np"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (email, password_hash, full_name, role_id, is_active,"
                " must_change_password) SELECT :e, :h, 'Reset Test', r.id, true, false"
                " FROM roles r WHERE r.code = 'VIEWER'"
            ),
            {"e": email, "h": hash_password(STRONG)},
        )
    return {"email": email, "password": STRONG}


def request_reset(client: TestClient, email: str):
    """The endpoint returns 204 and nothing else — that is the documented contract."""
    return client.post("/api/v1/auth/forgot-password", json={"email": email})


def issue_token(engine, email: str) -> str | None:
    """Get a usable reset token for a test.

    The HTTP endpoint deliberately never returns one: putting it in the response would
    make a registered address distinguishable from an unregistered one, which is the whole
    property `/auth/forgot-password` exists to protect. So a test calls the service
    directly for this step, then exercises the real HTTP redeem path with the result.
    """
    from sqlalchemy.orm import Session

    from app.services.auth import AuthService

    with Session(engine) as session:
        token = AuthService(session).request_password_reset(email=email)
        session.commit()
    return token


def login(client: TestClient, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# Requesting a reset — FR-1.6
# ---------------------------------------------------------------------------
def test_a_reset_token_is_issued(client: TestClient, account, engine):
    assert request_reset(client, account["email"]).status_code == 204
    token = issue_token(engine, account["email"])
    assert token and len(token) >= 20


def test_only_the_hash_is_stored(client: TestClient, account, engine):
    """A stored plaintext reset token is a password equivalent: anyone with a database
    copy could sign in as anyone."""
    token = issue_token(engine, account["email"])
    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT token_hash FROM password_reset_tokens ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    assert stored != token
    assert len(stored) == 64  # SHA-256 hex


def test_an_unknown_address_looks_identical(client: TestClient, account):
    """Doc 09 §9.2.1 — anything else makes this an account-enumeration oracle."""
    known = request_reset(client, account["email"])
    unknown = request_reset(client, "nobody-at-all@bank.com.np")
    assert known.status_code == unknown.status_code == 204
    # Byte-identical, not merely the same status: a body difference would be the leak.
    assert known.content == unknown.content == b""


def test_requesting_again_retires_the_earlier_token(
    client: TestClient, account, engine
):
    """Two live links would mean an older email still works after the user asked again —
    exactly the window an attacker who saw the first one wants."""
    first = issue_token(engine, account["email"])
    issue_token(engine, account["email"])

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": first, "new_password": "An0ther!Passw0rd#26"},
    )
    assert response.status_code == 401


def test_the_request_is_audit_logged(client: TestClient, account, engine):
    request_reset(client, account["email"])
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs "
                 "WHERE action = 'PASSWORD_RESET_REQUESTED' AND user_email = :e"),
            {"e": account["email"]},
        ).scalar_one()
    assert count >= 1


# ---------------------------------------------------------------------------
# Redeeming — FR-1.6
# ---------------------------------------------------------------------------
def test_a_token_resets_the_password(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Br@ndNew!Passw0rd26"},
    )
    assert response.status_code == 204
    assert login(client, account["email"], "Br@ndNew!Passw0rd26").status_code == 200
    assert login(client, account["email"], STRONG).status_code == 401


def test_a_token_works_exactly_once(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "F1rst!Passw0rd#26"},
    ).status_code == 204

    replay = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Sec0nd!Passw0rd#26"},
    )
    assert replay.status_code == 401


def test_an_expired_token_is_refused(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE password_reset_tokens SET expires_at = now() - interval '1 minute'")
        )
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Exp1red!Passw0rd26"},
    )
    assert response.status_code == 401


def test_every_failure_reads_the_same(client: TestClient, account, engine):
    """A caller who cannot tell an expired token from an unknown one cannot use this
    endpoint to probe which tokens exist."""
    unknown = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "x" * 40, "new_password": "Unkn0wn!Passw0rd26"},
    )
    token = issue_token(engine, account["email"])
    with engine.begin() as conn:
        conn.execute(text("UPDATE password_reset_tokens SET used_at = now()"))
    used = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Us3d!Passw0rd#2026"},
    )
    assert unknown.status_code == used.status_code == 401
    assert unknown.json()["error"]["message"] == used.json()["error"]["message"]


def test_the_new_password_must_meet_the_policy(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    response = client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": "short"}
    )
    assert response.status_code == 422
    assert "TOO_SHORT" in {d["code"] for d in response.json()["error"]["details"]}


def test_a_reset_revokes_every_session(client: TestClient, account, engine):
    """The old credential may already be in someone else's hands."""
    tokens = login(client, account["email"], STRONG).json()
    reset_token = issue_token(engine, account["email"])
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "Rev0ked!Passw0rd26"},
    )
    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 401


def test_a_reset_is_audit_logged(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Aud1ted!Passw0rd26"},
    )
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT after_state FROM audit_logs WHERE action = 'PASSWORD_CHANGED' "
                 "AND user_email = :e ORDER BY id DESC LIMIT 1"),
            {"e": account["email"]},
        ).scalar_one()
    assert row["via"] == "RESET_TOKEN"


# ---------------------------------------------------------------------------
# Password reuse — FR-1.2
# ---------------------------------------------------------------------------
def test_the_previous_password_cannot_be_reused(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "Interm3diate!Pw26"},
    )

    second = issue_token(engine, account["email"])
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": second, "new_password": STRONG},
    )
    assert response.status_code == 422
    assert "RECENTLY_USED" in {d["code"] for d in response.json()["error"]["details"]}


def test_the_last_five_are_remembered(client: TestClient, account, engine):
    """FR-1.2 — five deep, so a user cannot cycle back to a favourite after two changes."""
    passwords = [f"Rotat3d!Passw0rd{n:02d}" for n in range(1, 6)]
    for password in passwords:
        token = issue_token(engine, account["email"])
        assert client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": password},
        ).status_code == 204

    token = issue_token(engine, account["email"])
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": passwords[0]},
    )
    assert response.status_code == 422


def test_a_password_older_than_the_window_can_be_used_again(
    client: TestClient, account, engine
):
    """The rule is "last five", not "never again" — the history is pruned to match."""
    passwords = [f"Cycl3d!Passw0rd{n:02d}" for n in range(1, 8)]
    for password in passwords:
        token = issue_token(engine, account["email"])
        client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": password},
        )

    token = issue_token(engine, account["email"])
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": STRONG},
    )
    assert response.status_code == 204


def test_change_password_enforces_the_same_history(client: TestClient, account):
    """The rule must not be bypassable by using the other endpoint."""
    tokens = login(client, account["email"], STRONG).json()
    client.post(
        "/api/v1/auth/change-password",
        json={"current_password": STRONG, "new_password": "Ch@nged!Passw0rd26"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    fresh = login(client, account["email"], "Ch@nged!Passw0rd26").json()
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "Ch@nged!Passw0rd26", "new_password": STRONG},
        headers={"Authorization": f"Bearer {fresh['access_token']}"},
    )
    assert response.status_code == 422
    assert "RECENTLY_USED" in {d["code"] for d in response.json()["error"]["details"]}


def test_the_history_stores_only_hashes(client: TestClient, account, engine):
    token = issue_token(engine, account["email"])
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "H@shedOnly!Pw2026"},
    )
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT password_hash FROM password_history")
        ).scalars().all()
    assert rows
    assert all(r.startswith("$argon2") for r in rows)
    assert all(STRONG not in r for r in rows)


# ---------------------------------------------------------------------------
# Rate limiting — Doc 09 §9.6
# ---------------------------------------------------------------------------
def test_a_burst_of_sign_in_attempts_is_throttled(client: TestClient, account):
    """Five per minute per IP. The per-account lockout alone would not stop a run spread
    across many accounts, because each account would only see one failure."""
    for _ in range(5):
        login(client, account["email"], "WrongPassword1!")

    response = login(client, account["email"], "WrongPassword1!")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert response.json()["error"]["details"]["retry_after_seconds"] > 0


def test_the_throttle_counts_the_caller_not_the_account(client: TestClient, account, engine):
    """A distributed guessing run uses one address against many accounts; counting per
    account would miss it entirely."""
    other = f"spread{next(_SEQ):04d}@bank.com.np"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (email, password_hash, full_name, role_id, is_active,"
                 " must_change_password) SELECT :e, :h, 'Spread', r.id, true, false"
                 " FROM roles r WHERE r.code = 'VIEWER'"),
            {"e": other, "h": hash_password(STRONG)},
        )

    for _ in range(3):
        login(client, account["email"], "WrongPassword1!")
    for _ in range(2):
        login(client, other, "WrongPassword1!")

    assert login(client, other, "WrongPassword1!").status_code == 429


def test_reset_requests_are_throttled(client: TestClient, account):
    for _ in range(3):
        request_reset(client, account["email"])
    assert request_reset(client, account["email"]).status_code == 429


def test_the_limit_clears_after_its_window():
    """The window is fixed, so a legitimate user is not locked out permanently."""
    ratelimit.reset()
    for i in range(5):
        ratelimit.check("login", "10.0.0.1", ratelimit.LOGIN_LIMIT, now=float(i))

    with pytest.raises(ratelimit.RateLimitExceeded):
        ratelimit.check("login", "10.0.0.1", ratelimit.LOGIN_LIMIT, now=5.0)

    # 61 seconds later the window has rolled.
    ratelimit.check("login", "10.0.0.1", ratelimit.LOGIN_LIMIT, now=61.0)


def test_the_throttle_does_not_block_a_different_caller(client: TestClient, account):
    ratelimit.reset()
    for i in range(5):
        ratelimit.check("login", "10.0.0.9", ratelimit.LOGIN_LIMIT, now=float(i))
    ratelimit.check("login", "10.0.0.10", ratelimit.LOGIN_LIMIT, now=5.0)


def test_a_successful_sign_in_still_counts_toward_the_limit(client: TestClient, account):
    """Counting only failures would let an attacker who guesses correctly hammer the
    endpoint freely afterwards."""
    ratelimit.reset()
    for _ in range(5):
        login(client, account["email"], STRONG)
    assert login(client, account["email"], STRONG).status_code == 429
