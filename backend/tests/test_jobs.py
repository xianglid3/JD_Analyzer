
"""The saved jobs list."""

from tests.test_field_validation import login


def test_the_list_returns_the_posting_link(client, insert_job):
    """The dashboard shows and edits it inline, so a list without it renders "No link" for every
    job no matter what was saved."""
    user = login(client)
    job_id = insert_job(user["id"])
    client.patch(f"/api/jobs/{job_id}", json={"source_url": "https://example.com/careers/42"})

    listed = client.get("/api/jobs").get_json()["jobs"]

    assert [job["source_url"] for job in listed] == ["https://example.com/careers/42"]
