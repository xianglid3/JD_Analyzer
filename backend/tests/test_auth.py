import datetime
import hashlib

import pytest

from routes import auth
from routes.auth import validate_credentials


USER = {"username": "alice", "password": "pw123456"}


def test_valid_credentials_allow_password_symbols():
    username, password, error = validate_credentials({
        "username": "  Alice123  ",
        "password": "safe!@#$Password",
    })

    assert (username, password, error) == (
        "Alice123",
        "safe!@#$Password",
        None,
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (None, "Username and password required"),
        ({"username": "alice"}, "Username and password required"),
        ({"username": "a", "password": "pw123456"}, "Username must be at least 3 characters"),
        ({"username": "alice_1", "password": "pw123456"}, "Username can only contain letters and numbers"),
        ({"username": "alice", "password": "pass word"}, "Password cannot contain spaces"),
        ({"username": "alice", "password": "x" * 73}, "Password is too long — please use something shorter"),
    ],
)
def test_invalid_credentials(data, message):
    assert validate_credentials(data) == (None, None, message)


def test_auth_routes_reject_malformed_json(client):
    headers = {"Content-Type": "application/json"}

    signup = client.post("/api/auth/signup", data="{", headers=headers)
    login = client.post("/api/auth/login", data="{", headers=headers)

    assert signup.status_code == 400
    assert signup.get_json()["error"] == "Username and password required"
    assert login.status_code == 400
    assert login.get_json()["error"] == "Username and password required"


def test_refresh_rotates_token_and_rejects_old_token(client):
    client.post("/api/auth/signup", json=USER)
    assert client.post("/api/auth/login", json=USER).status_code == 200

    old_token = client.get_cookie("refresh_token").value
    assert client.post("/api/auth/refresh").status_code == 200
    new_token = client.get_cookie("refresh_token").value

    assert new_token != old_token

    client.set_cookie("refresh_token", old_token)
    response = client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid refresh token"


def test_expired_refresh_token_is_rejected_and_deleted(client, _db):
    user = client.post("/api/auth/signup", json=USER).get_json()
    raw_token = "expired-test-token"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)

    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
            VALUES (%s, %s, %s)
            """,
            (user["id"], token_hash, expires_at),
        )

    client.set_cookie("refresh_token", raw_token)
    response = client.post("/api/auth/refresh")

    assert response.status_code == 401
    assert response.get_json()["error"] == "refresh token expired"

    with _db.cursor() as cur:
        cur.execute("SELECT id FROM refresh_tokens WHERE token_hash = %s", (token_hash,))
        assert cur.fetchone() is None


def test_login_cleans_all_expired_refresh_tokens(client, _db):
    user = client.post("/api/auth/signup", json=USER).get_json()
    expired_hash = hashlib.sha256(b"old-expired-token").hexdigest()
    expired_at = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(days=1)

    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
            VALUES (%s, %s, %s)
            """,
            (user["id"], expired_hash, expired_at),
        )

    assert client.post("/api/auth/login", json=USER).status_code == 200

    with _db.cursor() as cur:
        cur.execute(
            "SELECT id FROM refresh_tokens WHERE token_hash = %s",
            (expired_hash,),
        )
        assert cur.fetchone() is None


# ── signup friction (an account has to cost more than an HTTP request) ───────

def test_signup_requires_the_invite_code_when_one_is_set(client, monkeypatch):
    monkeypatch.setattr(auth, "SIGNUP_INVITE_CODE", "let-me-in")

    refused = client.post("/api/auth/signup",
                          json={"username": "uninvited", "password": "pw123456"})
    assert refused.status_code == 403

    accepted = client.post("/api/auth/signup", json={
        "username": "invited", "password": "pw123456", "invite_code": "let-me-in",
    })
    assert accepted.status_code == 201


def test_signups_stop_at_the_daily_ceiling(client, monkeypatch, _db):
    monkeypatch.setattr(auth, "MAX_SIGNUPS_PER_DAY", 1)

    first = client.post("/api/auth/signup", json={"username": "firstone", "password": "pw123456"})
    second = client.post("/api/auth/signup", json={"username": "secondone", "password": "pw123456"})

    assert first.status_code == 201
    assert second.status_code == 503
    # and it says nothing a script could use to time its next attempt
    assert "tomorrow" not in second.get_json()["error"]


def test_no_friction_is_configured_by_default(client):
    """Local development and the test suite must not need a code."""
    assert client.post("/api/auth/signup",
                       json={"username": "ordinary", "password": "pw123456"}).status_code == 201


def test_the_form_is_told_whether_a_code_is_needed_but_never_what_it_is(client, monkeypatch):
    monkeypatch.setattr(auth, "SIGNUP_INVITE_CODE", "let-me-in")

    body = client.get("/api/auth/config").get_json()

    assert body == {"invite_required": True}
    assert "let-me-in" not in str(body)


def test_an_open_instance_asks_for_no_code(client):
    assert client.get("/api/auth/config").get_json() == {"invite_required": False}
