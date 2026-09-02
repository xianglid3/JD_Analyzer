"""Rate limits on the unauthenticated routes.

These can't use the per-user key — there is no user yet — which is why they were missed when
per-user limits went in everywhere else.
"""

USER = {"username": "limituser", "password": "pw123456"}


def test_password_guessing_is_cut_off(client):
    client.post("/api/auth/signup", json=USER)
    wrong = {"username": USER["username"], "password": "wrongpassword"}

    codes = [client.post("/api/auth/login", json=wrong).status_code for _ in range(8)]

    assert 429 in codes, "unlimited guessing against one account"
    assert codes.count(401) <= 5


def test_one_account_cannot_be_locked_out_by_guessing_another(client):
    client.post("/api/auth/signup", json=USER)
    client.post("/api/auth/signup", json={"username": "victim", "password": "pw123456"})

    for _ in range(6):
        client.post("/api/auth/login", json={"username": "victim", "password": "nope"})

    # same IP, different account: the credential key is (ip, username), so this still works
    assert client.post("/api/auth/login", json=USER).status_code == 200


def test_signup_flooding_is_cut_off(client):
    codes = [
        client.post("/api/auth/signup", json={"username": f"flood{i}", "password": "pw123456"}).status_code
        for i in range(8)
    ]

    assert 429 in codes


def test_a_normal_login_is_unaffected(client):
    client.post("/api/auth/signup", json=USER)

    assert client.post("/api/auth/login", json=USER).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_limiter_storage_comes_from_the_environment():
    """In-memory counters are per-process; production points this at Redis instead."""
    from extensions import limiter

    assert limiter._storage_uri == "memory://"     # the local default
