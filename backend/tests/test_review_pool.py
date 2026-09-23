"""The complete review pool, and what happens when a chunk of it does not come back.

Two failures these pin, both found by reading the code rather than by a bad run:

* a bullet missing from an otherwise valid response was read as KEEP, so a response that
  dropped half its bullets looked like one that approved them;
* one stored review row meant "this run has been reviewed", so a crash after the first chunk
  left the rest of the resume unreviewed and nothing could tell.
"""

import pytest

from services import bullet_review
from services.bullet_review import (
    KEEP,
    REVIEW_UNAVAILABLE,
    ReviewUnavailable,
    chunks,
    review_bullets,
)


JOB = ("Backend Engineer", "Globex", "Owns the ordering services", ["python", "backend"])


def task(name, text="Worked on the payments backend for the ordering team"):
    return {"bullet_id": name, "text": text, "entry": "Acme — Intern",
            "siblings": [], "answers": [], "requirements": []}


def kept(key):
    return {"bullet": key, "decision": "KEEP", "decision_reason": "It already names the work."}


@pytest.fixture
def calls(monkeypatch):
    """Records each request and replies with whatever the scripted list says."""
    seen = []

    def script(replies):
        def request_review(job, tasks, budget=None, model=None):
            seen.append([t["bullet_id"] for t in tasks])
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(bullet_review, "request_review", request_review)
        return seen

    return script


# ── chunking ─────────────────────────────────────────────────────────────────

def test_the_pool_is_split_into_calls_of_at_most_the_chunk_size():
    assert [len(c) for c in chunks([task(f"b{i}") for i in range(15)], 6)] == [6, 6, 3]
    assert chunks([], 6) == []
    # a nonsense size must not mean "one call per bullet forever" or a division by zero
    assert [len(c) for c in chunks([task("a"), task("b")], 0)] == [1, 1]


def test_every_bullet_in_the_pool_comes_back_with_a_decision(calls):
    tasks = [task(f"b{i}") for i in range(7)]
    calls([[kept(f"b{i + 1}") for i in range(6)], [kept("b1")]])
    results = review_bullets(JOB, tasks, size=6)
    assert set(results) == {t["bullet_id"] for t in tasks}
    assert all(r["decision"] == KEEP for r in results.values())


def test_a_failed_chunk_costs_only_its_own_bullets(calls):
    """The whole-resume call was the reason to chunk: one truncated response lost everything."""
    tasks = [task(f"b{i}") for i in range(4)]
    seen = calls([
        [kept("b1"), kept("b2")],
        ReviewUnavailable("truncated"), ReviewUnavailable("truncated again"),
    ])
    results = review_bullets(JOB, tasks, size=2)
    assert [results[t["bullet_id"]]["decision"] for t in tasks] == [
        KEEP, KEEP, REVIEW_UNAVAILABLE, REVIEW_UNAVAILABLE,
    ]
    assert len(seen) == 3                       # the good chunk once, the bad one twice
    assert "truncated again" in results["b2"]["unavailable_reason"]


def test_a_failed_chunk_is_retried_once_and_not_forever(calls):
    seen = calls([ReviewUnavailable("slow"), [kept("b1"), kept("b2")]])
    results = review_bullets(JOB, [task("b0"), task("b1")], size=2)
    assert all(r["decision"] == KEEP for r in results.values())
    assert len(seen) == 2


# ── the silent drop ──────────────────────────────────────────────────────────

def test_a_bullet_missing_from_a_valid_response_is_retried(calls):
    """The response parses and answers two of three. The third is not a keep."""
    seen = calls([[kept("b1"), kept("b3")], [kept("b1")]])
    results = review_bullets(JOB, [task("a"), task("b"), task("c")], size=3)
    assert seen == [["a", "b", "c"], ["b"]]     # only the missing one is asked again
    assert results["b"]["decision"] == KEEP


def test_a_bullet_missing_twice_is_unavailable_not_kept(calls):
    calls([[kept("b1"), kept("b3")], []])
    results = review_bullets(JOB, [task("a"), task("b"), task("c")], size=3)
    assert results["a"]["decision"] == KEEP
    assert results["c"]["decision"] == KEEP
    assert results["b"]["decision"] == REVIEW_UNAVAILABLE
    assert results["b"]["unavailable_reason"]


def test_an_unavailable_review_carries_no_instruction_for_the_editor(calls):
    calls([ReviewUnavailable("x"), ReviewUnavailable("x")])
    result = review_bullets(JOB, [task("a")], size=1)["a"]
    assert result["decision"] not in bullet_review.DECISIONS
    assert result["question"] is None and result["rewrite_instruction"] is None
    assert result["facts_to_preserve"] == []


def test_each_chunk_is_handed_back_as_it_finishes(calls):
    """So a caller can store it before the next call is made — the crash case below."""
    calls([[kept("b1")], [kept("b1")]])
    handed = []
    review_bullets(JOB, [task("a"), task("b")], size=1,
                   on_chunk=lambda i, chunk, reviews: handed.append((i, sorted(reviews))))
    assert handed == [(1, ["a"]), (2, ["b"])]


# ── ranking ──────────────────────────────────────────────────────────────────

def test_an_unreadable_improvement_level_is_unranked_not_middling():
    """Defaulting it to `medium` let silence outrank an honest `low`: a review that said
    nothing about value would have taken a capped question slot from one that did."""
    asking = {
        "decision": "ASK",
        "recruiter_doubt": {"type": "contribution", "specific_problem": "No component is named."},
        "anchor": "payments backend",
        "missing_fact": "which part of the payments backend they wrote",
        "question": "Which part of the payments backend did you build or change yourself?",
        "expected_resume_improvement": "The bullet could name the component they built.",
        "improvement_level": "enormous",
    }
    assert bullet_review.validate(asking, task("a"))["improvement_level"] is None
    for given, expected in (("HIGH", "high"), (" low ", "low"), ("medium", "medium")):
        assert bullet_review.validate({**asking, "improvement_level": given},
                                      task("a"))["improvement_level"] == expected
    # the ask itself survives — an unranked question is asked last, never refused
    assert bullet_review.validate(asking, task("a"))["decision"] == bullet_review.ASK


def test_a_keep_is_not_ranked():
    result = bullet_review.validate(
        {"decision": "KEEP", "decision_reason": "It already names the work.",
         "improvement_level": "high"},
        task("a"),
    )
    assert result["improvement_level"] is None


# ── the pool, and recovering a half-finished review ──────────────────────────

from services.openai_services import ResumeEntryExtraction, ResumeStructure   # noqa: E402
from services.resume_evidence import save_resume_evidence                     # noqa: E402
from services.tailoring_agent import bullets_for_review                       # noqa: E402

RESUME = ResumeStructure(entries=[
    ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern",
                          bullets=["Worked on the payments backend", "Joined the on-call rota"]),
    ResumeEntryExtraction(kind="project", organization=None, title="ChatRoom",
                          bullets=["Created backend functionality for messages"]),
    ResumeEntryExtraction(kind="education", organization="State University", title="BSc",
                          bullets=["Relevant coursework: algorithms, databases"]),
    ResumeEntryExtraction(kind="skill", organization=None, title="Skills",
                          bullets=["Python, Go, PostgreSQL"]),
])


@pytest.fixture
def world(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('pooluser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Backend Engineer', 'Globex', 'Owns services', '["backend"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, RESUME)
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
            VALUES (%s, %s, 'gpt-4o-mini', 12, 'running') RETURNING id
            """,
            (user_id, job_id),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            "SELECT b.id, b.text FROM resume_bullets AS b WHERE b.user_id = %s", (user_id,)
        )
        bullets = {text: str(bid) for bid, text in cur.fetchall()}
    _db.commit()
    return {"user_id": user_id, "run_id": run_id, "b": bullets}


def test_the_pool_is_work_bullets_and_nothing_else(world, _db):
    with _db.cursor() as cur:
        pool = bullets_for_review(cur, world["user_id"])
    texts = {text for text, bid in world["b"].items() if bid in pool["bullet_ids"]}
    assert texts == {
        "Worked on the payments backend",
        "Joined the on-call rota",
        "Created backend functionality for messages",
    }
    # education coursework and the skills block are not work anyone can be asked about
    assert "Relevant coursework: algorithms, databases" not in texts
    assert "Python, Go, PostgreSQL" not in texts
    assert pool["omitted"] == []


def test_a_bullet_no_requirement_matches_is_still_in_the_pool(world, _db):
    """The whole point. "Joined the on-call rota" cites nothing and used to be invisible."""
    with _db.cursor() as cur:
        pool = bullets_for_review(cur, world["user_id"])
    assert world["b"]["Joined the on-call rota"] in pool["bullet_ids"]


def test_bullets_past_the_cap_are_reported_not_dropped(world, _db):
    with _db.cursor() as cur:
        pool = bullets_for_review(cur, world["user_id"], limit=2)
    assert len(pool["bullet_ids"]) == 2
    assert len(pool["omitted"]) == 1
    assert not set(pool["bullet_ids"]) & set(pool["omitted"])


def test_one_stored_chunk_does_not_mean_the_review_is_complete(world, _db):
    """The crash case: chunk one lands, the process dies, and the run resumes. Finding a review
    row cannot be the test for "already reviewed" — the pool is."""
    ids = None
    with _db.cursor() as cur:
        pool = bullets_for_review(cur, world["user_id"])
        ids = pool["bullet_ids"]
        bullet_review.record_pool(cur, world["run_id"], ids)
        bullet_review.record(
            cur, world["run_id"], [{"bullet_id": ids[0]}],
            {ids[0]: bullet_review.validate({"decision": "KEEP", "decision_reason": "Fine."},
                                            task(ids[0]))},
            chunk=1,
        )
    _db.commit()

    with _db.cursor() as cur:
        state = bullet_review.progress(cur, world["run_id"])
    assert state["pool"] == ids
    assert sorted(state["missing"]) == sorted(ids[1:])
    assert state["reviews"][ids[0]]["decision"] == KEEP
    assert state["unavailable"] == []


def test_chunks_are_merged_into_one_set_of_reviews(world, _db):
    with _db.cursor() as cur:
        ids = bullets_for_review(cur, world["user_id"])["bullet_ids"]
        bullet_review.record_pool(cur, world["run_id"], ids)
        for index, bullet_id in enumerate(ids, start=1):
            bullet_review.record(
                cur, world["run_id"], [{"bullet_id": bullet_id}],
                {bullet_id: bullet_review.validate(
                    {"decision": "KEEP", "decision_reason": "Fine."}, task(bullet_id))},
                chunk=index,
            )
    _db.commit()

    with _db.cursor() as cur:
        loaded = bullet_review.load(cur, world["run_id"])
        state = bullet_review.progress(cur, world["run_id"])
    assert sorted(loaded) == sorted(ids)
    assert state["missing"] == []


def test_a_run_with_no_review_at_all_is_none_not_empty(world, _db):
    """None means "this run predates the review, keep its own rules"; {} would mean "reviewed,
    nothing to do", which is a different claim."""
    with _db.cursor() as cur:
        assert bullet_review.load(cur, world["run_id"]) is None


def test_the_pool_row_alone_is_not_a_review(world, _db):
    with _db.cursor() as cur:
        bullet_review.record_pool(cur, world["run_id"], ["a", "b"])
    _db.commit()
    with _db.cursor() as cur:
        assert bullet_review.load(cur, world["run_id"]) is None
        assert bullet_review.progress(cur, world["run_id"])["missing"] == ["a", "b"]


def test_an_unavailable_bullet_is_separated_from_a_missing_one(world, _db):
    """One is work to redo, the other is work already given up on."""
    with _db.cursor() as cur:
        bullet_review.record_pool(cur, world["run_id"], ["a", "b"])
        bullet_review.record(cur, world["run_id"], [{"bullet_id": "a"}],
                             {"a": bullet_review.unavailable("truncated twice")}, chunk=1)
    _db.commit()
    with _db.cursor() as cur:
        state = bullet_review.progress(cur, world["run_id"])
    assert state["unavailable"] == ["a"]
    assert state["missing"] == ["b"]
