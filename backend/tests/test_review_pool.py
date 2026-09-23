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
    REWRITE,
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


# ── concurrent calls, serialized persistence ─────────────────────────────────
# The calls are independent — one bullet each — and I/O-bound, so they run several at a time.
# Everything that is not a call stays on the orchestrator thread. A worker that persisted its own
# result would have four threads mutating one dict, renewing one lease and opening their own
# transactions; keeping writes on one thread means completion order is the only thing concurrency
# changes, and results are keyed by bullet id, so it changes nothing.

import threading                                                   # noqa: E402


def test_calls_run_concurrently(monkeypatch):
    """Four bullets, four workers, and none of them waits for the one before it."""
    started = threading.Barrier(4, timeout=5)

    def request_review(job, tasks, budget=None, model=None):
        started.wait()          # only returns if all four are inside the call at once
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    tasks = [task(f"b{i}") for i in range(4)]

    results = review_bullets(JOB, tasks, concurrency=4)

    assert set(results) == {t["bullet_id"] for t in tasks}
    assert all(r["decision"] == KEEP for r in results.values())


def test_only_the_orchestrator_thread_persists(monkeypatch):
    """The property the rest of the design rests on: a worker makes its call and returns. Every
    write, and the lease renewal the caller does inside `on_chunk`, happens on one thread."""
    caller = threading.current_thread().name
    call_threads, persist_threads = set(), set()

    def request_review(job, tasks, budget=None, model=None):
        call_threads.add(threading.current_thread().name)
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    review_bullets(
        JOB, [task(f"b{i}") for i in range(6)], concurrency=3,
        on_chunk=lambda i, chunk, reviews: persist_threads.add(threading.current_thread().name),
    )

    assert persist_threads == {caller}, "persistence ran off the orchestrator thread"
    assert call_threads - {caller}, "the calls did not leave the orchestrator thread"


def test_completion_order_cannot_change_the_result(monkeypatch):
    """Results are keyed by bullet id, so finishing out of order is not observable."""
    import time as _time

    order = {"a": 0.03, "b": 0.0, "c": 0.015}       # c finishes second, a last

    def request_review(job, tasks, budget=None, model=None):
        _time.sleep(order[tasks[0]["bullet_id"]])
        return [{"bullet": "b1", "decision": "REWRITE",
                 "recruiter_doubt": {"type": "clarification", "specific_problem": "Wording."},
                 "anchor": "the payments",
                 "rewrite_instruction": "Lead with the work done and cut the filler.",
                 "expected_resume_improvement": "It reads as a contribution, not a category.",
                 "decision_reason": "The facts are there."}]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    landed = []
    results = review_bullets(
        JOB, [task("a"), task("b"), task("c")], concurrency=3,
        on_chunk=lambda i, chunk, reviews: landed.append(chunk[0]["bullet_id"]),
    )

    assert landed == ["b", "c", "a"], "they really did land out of order"
    assert sorted(results) == ["a", "b", "c"]
    assert all(r["decision"] == REWRITE for r in results.values())


def test_one_worker_uses_the_sequential_path(monkeypatch, calls):
    """A single worker is not a pool: the tests that script one review must not depend on one."""
    caller = threading.current_thread().name
    seen_threads = []

    def request_review(job, tasks, budget=None, model=None):
        seen_threads.append(threading.current_thread().name)
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    review_bullets(JOB, [task("a"), task("b")], concurrency=1)

    assert seen_threads == [caller, caller]


# ── rate limits are one bullet's problem ─────────────────────────────────────

class _Limited(Exception):
    """What the SDK raises for 429; matched by name, not by a status code we may not see."""
    def __init__(self):
        super().__init__("RateLimitError: too many requests")


def test_a_rate_limited_bullet_is_retried_alone(monkeypatch):
    monkeypatch.setattr(bullet_review, "RATE_LIMIT_BACKOFF", 0.0)
    seen = []

    def request_review(job, tasks, budget=None, model=None):
        bullet = tasks[0]["bullet_id"]
        seen.append(bullet)
        if bullet == "b" and seen.count("b") == 1:
            raise _Limited()
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    results = review_bullets(JOB, [task("a"), task("b"), task("c")], concurrency=1)

    assert seen == ["a", "b", "b", "c"], "only the limited bullet was asked again"
    assert all(r["decision"] == KEEP for r in results.values())
    assert results["b"]["decision"] == KEEP


def test_a_sustained_rate_limit_ends_that_bullet_not_the_pool(monkeypatch):
    monkeypatch.setattr(bullet_review, "RATE_LIMIT_BACKOFF", 0.0)
    seen = []

    def request_review(job, tasks, budget=None, model=None):
        bullet = tasks[0]["bullet_id"]
        seen.append(bullet)
        if bullet == "b":
            raise _Limited()
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    results = review_bullets(JOB, [task("a"), task("b"), task("c")], concurrency=1)

    assert seen.count("b") <= bullet_review.RATE_LIMIT_ATTEMPTS + 1, "it did not spin"
    assert results["b"]["decision"] == REVIEW_UNAVAILABLE
    assert "rate" in results["b"]["unavailable_reason"].lower()
    # and its siblings were unaffected
    assert results["a"]["decision"] == KEEP and results["c"]["decision"] == KEEP


def test_a_rate_limit_is_told_apart_from_a_bad_response():
    assert bullet_review.is_rate_limit(_Limited())
    assert bullet_review.is_rate_limit(RuntimeError("Service Unavailable"))
    assert not bullet_review.is_rate_limit(ReviewUnavailable("the response was not valid JSON"))


def test_the_run_reports_review_progress_while_it_is_partway_through(world, _db):
    """What the two-phase progress display counts. No new column: it is the pool row compared
    against the reviews stored so far, which is the same comparison recovery uses."""
    from services.tailoring_agent import load_run

    with _db.cursor() as cur:
        ids = bullets_for_review(cur, world["user_id"])["bullet_ids"]
        bullet_review.record_pool(cur, world["run_id"], ids)
        bullet_review.record(
            cur, world["run_id"], [{"bullet_id": ids[0]}],
            {ids[0]: bullet_review.validate({"decision": "KEEP", "decision_reason": "Fine."},
                                            task(ids[0]))},
            chunk=ids[0],
        )
        bullet_review.record(
            cur, world["run_id"], [{"bullet_id": ids[1]}],
            {ids[1]: bullet_review.unavailable("rate limited after 3 attempts")},
            chunk=ids[1],
        )
    _db.commit()

    with _db.cursor() as cur:
        run = load_run(cur, world["user_id"], world["run_id"])

    assert run["review_progress"] == {
        "reviewed": 2, "total": len(ids), "unavailable": 1,
    }, "a bullet nobody could review still counts as read — it will not be tried again"


def test_a_run_with_no_pool_reports_no_review_progress(world, _db):
    """`None`, not zero: a run that has not started reviewing is not a run 0% through it, and
    an older run never had a pool at all."""
    from services.tailoring_agent import load_run

    with _db.cursor() as cur:
        assert load_run(cur, world["user_id"], world["run_id"])["review_progress"] is None


def test_a_fatal_error_keeps_the_reviews_already_paid_for(monkeypatch):
    """Calls not yet started cost nothing and are cancelled. Ones already in flight are paid
    for whether or not anybody reads them, so dropping their results means the next attempt
    buys the same reviews again."""
    import threading as _threading

    started = _threading.Event()

    def request_review(job, tasks, budget=None, model=None):
        bullet = tasks[0]["bullet_id"]
        if bullet == "boom":
            started.wait(timeout=5)         # let the good one finish first
            raise RuntimeError("the database went away")
        started.set()
        return [kept("b1")]

    monkeypatch.setattr(bullet_review, "request_review", request_review)
    persisted = {}
    with pytest.raises(RuntimeError):
        review_bullets(
            JOB, [task("good"), task("boom")], concurrency=2,
            on_chunk=lambda i, chunk, reviews: persisted.update(reviews),
        )

    assert "good" in persisted, "a review we had already bought was thrown away"
    assert persisted["good"]["decision"] == KEEP
