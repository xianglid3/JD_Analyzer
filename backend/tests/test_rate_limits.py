USERS = [
    {"username": "ratelimitusera", "password": "pw123456"},
    {"username": "ratelimituserb", "password": "pw123456"},
]


def signup_and_login(client, credentials):
    assert client.post("/api/auth/signup", json=credentials).status_code == 201
    assert client.post("/api/auth/login", json=credentials).status_code == 200


def test_job_analysis_limit_is_isolated_per_user(client):
    signup_and_login(client, USERS[0])

    # Short descriptions avoid OpenAI while still counting authenticated attempts.
    for _ in range(5):
        assert client.post("/api/jobs", json={"description": "short"}).status_code == 400
    assert client.post("/api/jobs", json={"description": "short"}).status_code == 429

    # A second account on the same test IP has an independent allowance.
    signup_and_login(client, USERS[1])
    assert client.post("/api/jobs", json={"description": "short"}).status_code == 400
