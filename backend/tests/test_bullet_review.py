"""The server's reading of a recruiter review.

The model's judgment is measured by `evals/run_review_eval.py` against the real thing. What is
pinned here is what the backend does with whatever comes back: a question has to be anchored in
the bullet's own words and may not name evidence nobody gave, a rewrite has to quote what it
fixes, and anything that cannot show its work becomes a keep.
"""

import pytest

from services.bullet_review import ASK, KEEP, REWRITE, anchored_in, unsupported_technologies, validate


BULLET = "Created backend functionality for managing users, messages, and channels."
TASK = {
    "bullet_id": "b",
    "text": BULLET,
    "siblings": ["Developed a secure messaging application using React and Flask."],
    "answers": [],
}

GOOD_ASK = {
    "decision": "ASK",
    "job_relevance": {"requirement": "backend API development", "reason": "The job is backend service work."},
    "established_facts": [{"fact": "They worked on the chat backend", "supporting_text": "Created backend functionality"}],
    "recruiter_doubt": {"type": "contribution",
                        "specific_problem": "It never names the endpoint or service they wrote."},
    "anchor": "backend functionality",
    "missing_fact": "which endpoint or service they personally built",
    "question": "Which endpoint or service did you personally build as part of that backend functionality for users, messages, and channels?",
    "expected_resume_improvement": "The bullet can name a concrete backend component instead of 'functionality'.",
    "rewrite_instruction": None,
    "facts_to_preserve": ["users, messages and channels"],
    "decision_reason": "One named component would make this bullet concrete.",
}

GOOD_REWRITE = {
    "decision": "REWRITE",
    "job_relevance": {"requirement": "backend API development", "reason": "Backend work."},
    "recruiter_doubt": {"type": None, "specific_problem": None},
    "anchor": "Created backend functionality",
    "missing_fact": None,
    "question": None,
    "expected_resume_improvement": "The bullet reads as a contribution rather than a category.",
    "rewrite_instruction": "Cut 'created backend functionality for' and lead with the work, keeping users, messages and channels.",
    "facts_to_preserve": ["users, messages and channels"],
    "decision_reason": "The facts are there; the wording buries them.",
}


def test_a_justified_ask_survives():
    result = validate(GOOD_ASK, TASK)
    assert result["decision"] == ASK
    assert result["question"] == GOOD_ASK["question"]
    assert result["doubt_type"] == "contribution"


def test_a_justified_rewrite_survives():
    result = validate(GOOD_REWRITE, TASK)
    assert result["decision"] == REWRITE
    assert result["rewrite_instruction"].startswith("Cut")


# ── the question has to be about this bullet ─────────────────────────────────
# Free-form questions produced "What tools did you use?" and "Why did you choose that database
# architecture?" against bullets that say neither. Quoting the bullet is what makes that
# impossible, without a list of banned words.

def test_a_question_that_quotes_nothing_from_the_bullet_is_refused():
    astray = {**GOOD_ASK, "question": "What tools did you use on this project, and why those?"}
    result = validate(astray, TASK)
    assert result["decision"] == KEEP
    assert "quote" in result["downgraded"]


def test_a_question_may_quote_a_different_phrase_than_the_anchor():
    """The model names one phrase in `anchor` and writes about another — both of them the
    bullet's own words. That is the same guarantee by a different route."""
    elsewhere = {**GOOD_ASK, "anchor": "backend functionality",
                 "question": "Which part of managing users, messages, and channels did you build yourself?"}
    assert validate(elsewhere, TASK)["decision"] == ASK


@pytest.mark.parametrize("anchor", [None, "", "the database architecture"])
def test_a_useless_anchor_does_not_sink_a_question_that_quotes_the_bullet(anchor):
    """The anchor is the model's account of what it is asking about; the question quoting the
    bullet is the guarantee. A rewrite is the other way round — see below."""
    assert validate({**GOOD_ASK, "anchor": anchor}, TASK)["decision"] == ASK


def test_a_question_may_not_name_evidence_nobody_gave():
    """"Why did you choose that database architecture?" about a bullet with no database."""
    invented = {**GOOD_ASK,
                "question": "Which Redis cache did you use for that backend functionality?"}
    result = validate(invented, TASK)
    assert result["decision"] == KEEP and "redis" in result["downgraded"]


def test_a_sibling_bullets_technology_is_not_this_bullets_premise():
    """A sibling is a different piece of work in the same project. "Developed a messaging app
    using React and Flask" beside this bullet does not make Flask part of this one."""
    assert unsupported_technologies("Which Flask route serves that backend functionality?", TASK) == ["flask"]


def test_this_runs_answer_may_be_named_in_a_question():
    answered = {**TASK, "answers": ["I wrote the Flask routes for channels."]}
    assert unsupported_technologies("Which Flask route serves that backend functionality?", answered) == []


def test_two_filler_words_are_not_a_quote():
    """"for the" appears in every bullet and every question; a pair of them proves nothing."""
    filler_only = {**GOOD_ASK, "question": "What did you personally do for the project overall?"}
    assert validate(filler_only, TASK)["decision"] == KEEP


@pytest.mark.parametrize("question", [
    "What was the impact of that backend functionality?",
    "Can you elaborate on that backend functionality?",
    "Which technologies did you use for that backend functionality?",
])
def test_generic_questions_are_refused_even_when_anchored(question):
    assert validate({**GOOD_ASK, "question": question}, TASK)["decision"] == KEEP


def test_two_questions_are_not_one_question():
    double = {**GOOD_ASK,
              "question": "Which endpoint did you build for that backend functionality? Who reviewed it?"}
    assert validate(double, TASK)["decision"] == KEEP


@pytest.mark.parametrize("field", ["specific_problem", "missing_fact", "expected_resume_improvement"])
def test_an_ask_that_cannot_show_its_work_is_kept(field):
    thin = {**GOOD_ASK}
    if field == "specific_problem":
        thin["recruiter_doubt"] = {"type": "contribution", "specific_problem": None}
    else:
        thin[field] = None
    assert validate(thin, TASK)["decision"] == KEEP


def test_an_ask_without_a_doubt_type_is_kept():
    typeless = {**GOOD_ASK, "recruiter_doubt": {"type": None, "specific_problem": "Unclear."}}
    assert validate(typeless, TASK)["decision"] == KEEP


def test_a_failed_ask_becomes_a_rewrite_when_one_was_safely_described():
    """Only when the review also described an evidence-only improvement it could quote."""
    both = {**GOOD_ASK, "question": "What was the impact?",
            "rewrite_instruction": GOOD_REWRITE["rewrite_instruction"],
            "anchor": "Created backend functionality"}
    result = validate(both, TASK)
    assert result["decision"] == REWRITE and result["question"] is None


# ── rewrites ─────────────────────────────────────────────────────────────────

def test_a_rewrite_must_quote_the_words_it_fixes():
    assert validate({**GOOD_REWRITE, "anchor": "the wording generally"}, TASK)["decision"] == KEEP


def test_a_rewrite_must_name_the_change():
    assert validate({**GOOD_REWRITE, "rewrite_instruction": "Improve it."}, TASK)["decision"] == KEEP


def test_a_rewrite_that_also_filled_the_ask_fields_keeps_the_rewrite():
    """Schema noise, not a safety problem: the instruction is sound, so the question is dropped
    rather than the rewrite. Losing it was a real eval failure on a plainly verbose bullet."""
    asking = {**GOOD_REWRITE, "question": "Which endpoint was it?", "missing_fact": "the endpoint"}
    result = validate(asking, TASK)
    assert result["decision"] == REWRITE
    assert result["question"] is None and result["missing_fact"] is None


# ── keeps ────────────────────────────────────────────────────────────────────

def test_a_keep_carries_no_work():
    kept = {"decision": "KEEP", "decision_reason": "The bullet already names the work.",
            "question": "Which endpoint?", "rewrite_instruction": "Tighten it."}
    result = validate(kept, TASK)
    assert result["decision"] == KEEP
    assert result["question"] is None and result["rewrite_instruction"] is None


def test_anything_unreadable_is_a_keep():
    assert validate(None, TASK)["decision"] == KEEP
    assert validate({"decision": "OVERHAUL"}, TASK)["decision"] == KEEP


def test_anchored_in_is_word_for_word_not_fuzzy():
    assert anchored_in("backend functionality", BULLET)
    assert not anchored_in("backend features", BULLET)


# ── the task the reviewer is given ───────────────────────────────────────────
# Moved here with `build_tasks` when `bullet_diagnosis` was deleted. One task per bullet,
# every requirement that cites it, and never a bullet id the model could invent.

import json                                                    # noqa: E402

from db import get_cursor                                      # noqa: E402
from services.bullet_review import build_tasks, payload        # noqa: E402
from services.openai_services import ResumeEntryExtraction, ResumeStructure  # noqa: E402
from services.resume_evidence import save_resume_evidence      # noqa: E402

VAGUE = "Worked on backend services for the ordering team"
SIBLING = "Wrote the retry queue for the payment service in Go"


@pytest.fixture
def world(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('reviewuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Backend Engineer', 'Globex', 'Owns services', '["backend","go"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern",
                                  bullets=[VAGUE, SIBLING]),
        ]))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s", (user_id,))
        bullets = {text: str(bid) for bid, text in cur.fetchall()}
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
            VALUES (%s, %s, 'gpt-4o-mini', 12, 'running') RETURNING id
            """,
            (user_id, job_id),
        )
        run_id = cur.fetchone()[0]
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "run_id": run_id, "b": bullets}


def _assessment(world, labels):
    return {"requirements": [
        {"requirement": label, "agent_label": label, "state": "EXPLICIT", "importance": "required",
         "evidence": [{"bullet_id": world["b"][VAGUE], "text": VAGUE}]}
        for label in labels
    ]}


def test_one_task_per_bullet_carries_every_requirement_and_its_siblings(world):
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], world["run_id"],
                            _assessment(world, ["go", "backend"]),
                            [world["b"][VAGUE], world["b"][VAGUE]])
    assert len(tasks) == 1
    assert [r["requirement"] for r in tasks[0]["requirements"]] == ["backend", "go"]
    assert tasks[0]["siblings"] == [SIBLING]
    assert tasks[0]["entry"] == "Intern — Acme"


def test_requirement_order_does_not_change_what_the_model_is_shown(world):
    job = ("Backend Engineer", "Globex", "Owns services", ["backend", "go"])
    with get_cursor() as cur:
        one = build_tasks(cur, world["user_id"], world["run_id"],
                          _assessment(world, ["go", "backend"]), [world["b"][VAGUE]])
        two = build_tasks(cur, world["user_id"], world["run_id"],
                          _assessment(world, ["backend", "go"]), [world["b"][VAGUE]])
    assert json.dumps(payload(job, one)) == json.dumps(payload(job, two))


def test_the_model_never_sees_a_bullet_id(world):
    job = ("Backend Engineer", "Globex", "Owns services", [])
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], world["run_id"],
                            _assessment(world, ["go"]), [world["b"][VAGUE]])
    assert world["b"][VAGUE] not in json.dumps(payload(job, tasks))


def test_only_this_runs_answers_reach_the_reviewer(world, _db):
    """A bullet keeps its id when its wording is edited, so an older answer may have been
    given about a sentence that no longer exists."""
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status, completed_at)
            VALUES (%s, %s, 'm', 8, 'completed', now()) RETURNING id
            """,
            (world["user_id"], world["job_id"]),
        )
        older = cur.fetchone()[0]
        for run_id, answer in [(world["run_id"], "This run: I wrote the order service."),
                               (older, "An older run: something else entirely.")]:
            cur.execute(
                """
                INSERT INTO tailoring_detail_requests (run_id, user_id, bullet_id, requirement,
                    question, answer, status, intent)
                VALUES (%s, %s, %s, 'backend', 'q', %s, 'answered', 'implementation')
                """,
                (run_id, world["user_id"], world["b"][VAGUE], answer),
            )
    _db.commit()
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], world["run_id"],
                            _assessment(world, ["backend"]), [world["b"][VAGUE]])
    assert tasks[0]["answers"] == ["This run: I wrote the order service."]
