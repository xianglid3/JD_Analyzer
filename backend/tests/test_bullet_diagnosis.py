"""The per-bullet decision: keep, rewrite, or ask — and the server's reading of it.

The model's diagnosis is scripted here. What these pin is what the backend does with it: an
ask that cannot show its work is downgraded, a kept bullet never reaches the editor, a
planned question is the only question, and the decision does not depend on the order a
posting lists its requirements in. Whether the model's judgment is any good is the
real-model case set in `evals/tailoring_cases.json`, not this file.
"""

import json
import types

import pytest

from db import get_cursor
from services import bullet_diagnosis, tailoring_agent
from services import tailoring_candidates as candidates_state
from services.bullet_diagnosis import (
    ASK, KEEP, REWRITE, build_tasks, payload, question_for, validate,
)
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.tailoring_agent import load_run, resume_run, run_tailoring


VAGUE = "Worked on backend services for the ordering team"
SIBLING = "Wrote the retry queue for the payment service in Go"
TASK = {"text": VAGUE, "siblings": [SIBLING], "answers": []}

GOOD_ASK = {
    "decision": "ask",
    "problem_type": "unclear_artifact",
    "recruiter_reaction": "Worked on which service, doing what?",
    "specific_problem": "It never says which service was theirs.",
    "evidence_already_present": ["backend work for the ordering team"],
    "missing_fact": "which ordering service they personally built",
    "job_relevance": "The job is backend ownership of services.",
    "expected_improvement": "The bullet can name the service and their part in it.",
    "subject": "backend services",
    "span": "Worked on",
}
PLANNED = question_for("unclear_artifact", "backend services")


# ── validation ───────────────────────────────────────────────────────────────

def test_a_justified_ask_gets_the_servers_question():
    result = validate(GOOD_ASK, TASK)
    assert result["decision"] == ASK
    assert result["question"] == PLANNED
    assert "backend services" in result["question"]


# ── adversarial: a convincing justification around a different question ─────
# Review reproduced these passing when the model wrote the question itself. Nothing below
# bans a word: the model can no longer write the sentence, only point into the bullet.

@pytest.mark.parametrize("question", [
    "What tools did you use?",
    "What framework did you use?",
    "What was the impact of this work?",
    "How many requests did the services handle?",
])
def test_the_models_own_question_is_never_used(question):
    result = validate({**GOOD_ASK, "question": question}, TASK)
    assert result["question"] == PLANNED


@pytest.mark.parametrize("subject", [
    "database architecture",         # the bullet never mentions a database
    "the tools you used",            # a paraphrase, not the bullet's words
    "",
    "worked on backend services for the ordering team and more",   # too long to be a subject
])
def test_a_question_about_something_the_bullet_does_not_say_is_not_asked(subject):
    result = validate({**GOOD_ASK, "problem_type": "unclear_decision", "subject": subject}, TASK)
    assert result["decision"] == KEEP and result["question"] is None


def test_a_fact_a_sibling_already_gives_is_kept_not_rewritten():
    """A single-bullet rewrite cannot cite the sibling, so "rewrite from it" is not possible."""
    result = validate({**GOOD_ASK, "missing_fact": "the retry queue for the payment service"}, TASK)
    assert result["decision"] == KEEP
    assert "sibling" in result["downgraded"]


def test_a_fact_an_earlier_answer_gave_is_kept_not_rewritten():
    """An earlier answer proves the question was asked. It is not evidence the editor holds."""
    task = {**TASK, "siblings": [], "answers": ["I built the order-status service in Flask."]}
    # phrased generically, as the model tends to: no overlap check could match it
    result = validate({**GOOD_ASK, "missing_fact": "the specific service they personally built"}, task)
    assert result["decision"] == KEEP
    assert "already asked" in result["downgraded"]


def test_sharing_topic_words_with_the_bullet_is_not_already_answered():
    """The first real run downgraded "which part of the checkout flow they changed" because
    "checkout" and "flow" are in the bullet — as a missing fact about that bullet must be."""
    task = {"text": "Helped improve the checkout flow for the web store backend.",
            "siblings": ["Joined the weekly on-call rotation for the storefront."], "answers": []}
    ask = {**GOOD_ASK, "problem_type": "unclear_contribution", "subject": "the checkout flow",
           "missing_fact": "which part of the checkout flow they personally changed"}
    assert validate(ask, task)["decision"] == ASK


def test_an_ask_that_cannot_show_its_work_is_kept():
    thin = {"decision": "ask", "question": "Which part was yours?"}
    result = validate(thin, TASK)
    assert result["decision"] == KEEP and result["question"] is None


def test_a_rewrite_must_name_what_it_fixes():
    assert validate({"decision": "rewrite", "specific_problem": ""}, TASK)["decision"] == KEEP
    # and what kind of problem it is
    untyped = {"decision": "rewrite", "specific_problem": "It opens with worked on.",
               "span": "Worked on"}
    assert validate(untyped, TASK)["decision"] == KEEP
    typed = {**untyped, "problem_type": "vague_wording"}
    assert validate(typed, TASK)["decision"] == REWRITE


@pytest.mark.parametrize("problem_type", sorted(__import__("services.bullet_diagnosis", fromlist=["x"]).ASKABLE))
def test_a_missing_fact_is_never_reworded(problem_type):
    """The first real run: "Worked on backend services" (which service?) was diagnosed
    unclear_artifact and rewritten as "Developed backend services" — the fact still missing,
    the ownership raised. Rewording cannot supply a fact, so it is asked or kept."""
    result = validate({"decision": "rewrite", "problem_type": problem_type,
                       "specific_problem": "It never says which service was theirs."}, TASK)
    assert result["decision"] == KEEP


@pytest.mark.parametrize("problem_type", ["weak_result_claim", "meaningless_metric", "unsupported_claim"])
def test_result_and_claim_problems_are_rewritten_never_asked(problem_type):
    """A vague result or a number that measures nothing is fixed from the evidence. Asking
    "what was the result?" is how an unmeasured metric gets invented."""
    result = validate({**GOOD_ASK, "problem_type": problem_type}, TASK)
    assert result["decision"] == REWRITE and result["question"] is None
    assert result["problem_type"] == problem_type


def test_an_ask_must_say_what_kind_of_problem_it_is():
    for problem_type in (None, "vibes"):
        result = validate({**GOOD_ASK, "problem_type": problem_type}, TASK)
        assert result["decision"] == KEEP and result["problem_type"] is None


def test_anything_unreadable_is_a_keep():
    assert validate(None, TASK)["decision"] == KEEP
    assert validate({"decision": "overhaul"}, TASK)["decision"] == KEEP




# ── tasks ────────────────────────────────────────────────────────────────────

@pytest.fixture
def world(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('diaguser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Backend Engineer', 'Globex', 'Owns services', %s::jsonb)
            RETURNING id
            """,
            (user_id, json.dumps(["backend", "go"])),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern",
                                  bullets=[VAGUE, SIBLING]),
        ]))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s", (user_id,))
        bullets = {text: str(bid) for bid, text in cur.fetchall()}
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "b": bullets}


def _assessment(world, labels):
    return {"requirements": [
        {"requirement": label, "agent_label": label, "state": "explicit", "importance": "required",
         "evidence": [{"bullet_id": world["b"][VAGUE], "text": VAGUE}]}
        for label in labels
    ]}


def test_one_task_per_bullet_carries_every_requirement_and_its_siblings(world):
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], _assessment(world, ["go", "backend"]),
                            [world["b"][VAGUE], world["b"][VAGUE]])
    assert len(tasks) == 1
    assert [r["requirement"] for r in tasks[0]["requirements"]] == ["backend", "go"]
    assert tasks[0]["siblings"] == [SIBLING]
    assert tasks[0]["entry"] == "Intern — Acme"


def test_requirement_order_does_not_change_what_the_model_is_shown(world):
    job = ("Backend Engineer", "Globex", "Owns services", ["backend", "go"])
    with get_cursor() as cur:
        one = build_tasks(cur, world["user_id"], _assessment(world, ["go", "backend"]),
                          [world["b"][VAGUE]])
        two = build_tasks(cur, world["user_id"], _assessment(world, ["backend", "go"]),
                          [world["b"][VAGUE]])
    assert json.dumps(payload(job, one)) == json.dumps(payload(job, two))


def test_the_model_never_sees_a_bullet_id(world):
    job = ("Backend Engineer", "Globex", "Owns services", [])
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], _assessment(world, ["go"]), [world["b"][VAGUE]])
    assert world["b"][VAGUE] not in json.dumps(payload(job, tasks))


# ── a run that follows the diagnosis ─────────────────────────────────────────

def call(name, arguments, call_id):
    return types.SimpleNamespace(
        id=call_id, function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def response(tool_calls=None, content=None):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


def editor(monkeypatch, *turns):
    sent, queue = [], list(turns)

    def fake(messages):
        sent.append(list(messages))
        return queue.pop(0)

    monkeypatch.setattr(tailoring_agent, "complete", fake)
    return sent


def diagnosis(monkeypatch, decide):
    """Turn the step on, and answer it with `decide(task) -> raw diagnosis`."""
    calls = []
    monkeypatch.setattr(bullet_diagnosis, "ENABLED", True)

    def fake(job, tasks, budget=None):
        calls.append(tasks)
        return [{"bullet": f"b{i + 1}", **decide(task)} for i, task in enumerate(tasks)]

    monkeypatch.setattr(bullet_diagnosis, "request_diagnosis", fake)
    return calls


def test_a_kept_bullet_never_reaches_the_editor(monkeypatch, world):
    diagnosis(monkeypatch, lambda task: {"decision": "keep"})
    sent = editor(monkeypatch)

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert sent == []
    assert result["status"] == "completed"
    with get_cursor() as cur:
        run = load_run(cur, world["user_id"], result["run_id"])
        states = {item["status"] for item in candidates_state.load(cur, result["run_id"])}
    assert states <= {"kept"}
    assert run["outcomes"][0]["action"] == "keep"
    assert result["summary"].startswith("Every bullet already reads clearly")


def test_the_planned_question_is_the_only_question(monkeypatch, world):
    diagnosis(monkeypatch, lambda task: GOOD_ASK)
    editor(
        monkeypatch,
        response([call("request_detail", {
            "requirement": "backend", "bullet_id": world["b"][VAGUE], "intent": "impact",
            "question": "What was the impact?",
        }, "c1")]),
    )

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert result["status"] == "waiting_for_user"
    with get_cursor() as cur:
        cur.execute("SELECT question, intent FROM tailoring_detail_requests WHERE run_id = %s",
                    (result["run_id"],))
        assert cur.fetchall() == [(PLANNED, "implementation")]


def test_a_rewrite_bullet_cannot_be_asked_about(monkeypatch, world):
    diagnosis(monkeypatch, lambda task: {
        "decision": "rewrite", "problem_type": "vague_wording", "span": "Worked on",
        "specific_problem": "Opens with worked on instead of the action.",
    })
    editor(
        monkeypatch,
        response([call("request_detail", {
            "requirement": "backend", "bullet_id": world["b"][VAGUE], "intent": "implementation",
            "question": "Which service did you build?",
        }, "c1")]),
        response(content="Done."),
    )

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    with get_cursor() as cur:
        cur.execute(
            "SELECT error_message FROM tool_calls WHERE run_id = %s AND status = 'failed'",
            (result["run_id"],),
        )
        refusals = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT count(*) FROM tailoring_detail_requests WHERE run_id = %s",
                    (result["run_id"],))
        assert cur.fetchone()[0] == 0
    assert any("no question was planned" in message for message in refusals)


def test_the_brief_states_each_decision(monkeypatch, world):
    diagnosis(monkeypatch, lambda task: GOOD_ASK)
    sent = editor(monkeypatch, response(content="Stopping."))

    run_tailoring(get_cursor, world["user_id"], world["job_id"])

    brief = sent[0][1]["content"]
    assert "Decision: ask" in brief and PLANNED in brief


def test_the_diagnosis_is_made_once_and_reused_on_resume(monkeypatch, world):
    calls = diagnosis(monkeypatch, lambda task: GOOD_ASK)
    editor(monkeypatch, response(content="Stopping."))
    first = run_tailoring(get_cursor, world["user_id"], world["job_id"])
    assert len(calls) == 1

    editor(monkeypatch, response(content="Still stopping."))
    job_id, steps_used = resume_run(get_cursor, world["user_id"], first["run_id"])
    tailoring_agent.execute_run(get_cursor, world["user_id"], job_id, first["run_id"],
                                resume_from=steps_used)
    assert len(calls) == 1
    with get_cursor() as cur:
        assert load_run(cur, world["user_id"], first["run_id"])["diagnoses"]


def test_a_failed_diagnosis_fails_the_run_resumably(monkeypatch, world):
    monkeypatch.setattr(bullet_diagnosis, "ENABLED", True)

    def boom(*_a, **_k):
        raise RuntimeError("model down")

    monkeypatch.setattr(bullet_diagnosis, "request_diagnosis", boom)
    sent = editor(monkeypatch)

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert sent == []
    assert result["status"] == "failed"
    with get_cursor() as cur:
        cur.execute("SELECT error_code FROM tailoring_runs WHERE id = %s", (result["run_id"],))
        assert cur.fetchone()[0] == "model_call_failed"


HELPED = "Helped improve the checkout flow for the web store backend."


def test_a_bullet_the_regex_calls_strong_is_still_read(monkeypatch, world, _db):
    """"Helped improve the checkout flow" reads as strong to `bullet_quality_gaps` and used to
    be kept without anyone looking. Every bullet a met requirement cites is diagnosed now,
    and a decision that it needs work turns that `keep` requirement into real work."""
    from services.claim_check import bullet_is_already_strong

    assert bullet_is_already_strong(HELPED)          # the premise: the regex calls it done
    with _db.cursor() as cur:
        cur.execute("UPDATE resume_bullets SET text = %s WHERE id = %s", (HELPED, world["b"][SIBLING]))
    _db.commit()
    seen = diagnosis(monkeypatch, lambda task: (
        {"decision": "rewrite", "problem_type": "vague_wording", "span": "Helped improve",
         "specific_problem": "Improve the checkout flow is vaguer than the bullet needs to be."}
        if task["text"] == HELPED else {"decision": "keep"}
    ))
    sent = editor(monkeypatch, response(content="Stopping."))

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert HELPED in {task["text"] for task in seen[0]}
    assert "Decision: rewrite" in sent[0][1]["content"] and HELPED in sent[0][1]["content"]
    with get_cursor() as cur:
        rows = {item["normalized"]: item["status"]
                for item in candidates_state.load(cur, result["run_id"])}
    assert rows.get("backend") not in (None, "kept")


@pytest.mark.parametrize("span", [None, "", "the whole architecture", "vague phrasing overall"])
def test_a_rewrite_that_cannot_point_at_words_is_a_keep(span):
    """The second real run wanted to rewrite concise bullets with nothing in them to fix."""
    result = validate({"decision": "rewrite", "problem_type": "vague_wording",
                       "specific_problem": "It could read more smoothly overall.", "span": span}, TASK)
    assert result["decision"] == KEEP


def test_a_subject_copied_with_its_full_stop_still_reads_as_a_question():
    result = validate({**GOOD_ASK, "subject": "backend services for the ordering team."}, TASK)
    assert result["decision"] == ASK
    assert result["question"].endswith("ordering team?")


@pytest.mark.parametrize("problem_type, span, kept", [
    ("weak_result_claim", "Worked on backend services", True),     # no result in it
    ("weak_result_claim", "improving the ordering team", False),
    ("meaningless_metric", "backend services", True),              # no number in it
])
def test_the_category_must_be_true_of_the_words_it_quotes(problem_type, span, kept):
    """The third real run tagged four bullets that claim no result as weak_result_claim."""
    task = {**TASK, "text": "Worked on backend services, improving the ordering team 3x."}
    result = validate({"decision": "rewrite", "problem_type": problem_type, "span": span,
                       "specific_problem": "The quoted words are vaguer than they need to be."}, task)
    assert (result["decision"] == KEEP) is kept


def test_a_concrete_sibling_answers_what_they_built():
    """Asked three runs in a row: "Worked on the payments backend" beside "Built the refund
    service in Go…". No word overlap connects them; the sibling still answers the question."""
    task = {"text": "Worked on the payments backend.",
            "siblings": ["Built the refund service in Go that retries failed payouts."], "answers": []}
    ask = {**GOOD_ASK, "subject": "the payments backend",
           "missing_fact": "which part of the payments backend they personally built"}
    result = validate(ask, task)
    assert result["decision"] == KEEP and "sibling" in result["downgraded"]


def test_a_sibling_about_meetings_does_not_answer_it():
    task = {"text": "Worked on backend services for the ordering platform.",
            "siblings": ["Attended sprint planning and code reviews with the team."], "answers": []}
    assert validate({**GOOD_ASK, "missing_fact": "the service they personally built"}, task)["decision"] == ASK


def test_the_model_is_not_shown_the_regex_hints(world):
    job = ("Backend Engineer", "Globex", "Owns services", [])
    with get_cursor() as cur:
        tasks = build_tasks(cur, world["user_id"], _assessment(world, ["go"]), [world["b"][VAGUE]])
    shown = json.dumps(payload(job, tasks))
    assert "hint" not in shown and "outcome detail" not in shown


def test_the_editor_is_told_which_words_to_fix(monkeypatch, world):
    diagnosis(monkeypatch, lambda task: {
        "decision": "rewrite", "problem_type": "vague_wording", "span": "Worked on",
        "specific_problem": "Opens with worked on instead of the action.",
    } if task["text"] == VAGUE else {"decision": "keep"})
    sent = editor(monkeypatch, response(content="Stopping."))

    run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert 'Fix exactly these words: "Worked on"' in sent[0][1]["content"]


# ── confirmations: provenance, and a yes that must change something ──────────
# Run 0dc2d235 asked "Did you use data structures?" about a React messaging bullet and "Did you
# use front end frameworks?" about a React calendar bullet. Both bullets name React; the only
# link to "front end frameworks" was learned, and the model picked the alternative.

MESSAGING = ("Built an end-to-end encrypted messaging platform with React and Flask, keeping "
             "cryptographic operations in the browser so the backend stores only ciphertext.")
CALENDAR = ("Designed a FastAPI backend with Supabase and REST APIs for event creation, updates, "
            "and geocoded location storage; integrated it with a React/TypeScript calendar and "
            "Mapbox interface.")
LEARNED_IN_THAT_RUN = [
    ("arrays", "data structures"), ("linked lists", "data structures"),
    ("hash tables", "data structures"), ("trees", "data structures"), ("graphs", "data structures"),
    ("aws", "cloud infrastructure"), ("azure", "cloud infrastructure"), ("gcp", "cloud infrastructure"),
    ("nas", "storage systems"), ("san", "storage systems"), ("object storage", "storage systems"),
    ("react", "front end frameworks"), ("vue", "front end frameworks"), ("angular", "front end frameworks"),
]


def _job_with(world, _db, requirements, bullets, learned=()):
    from services import skill_relations

    with _db.cursor() as cur:
        cur.execute("DELETE FROM resume_bullets WHERE user_id = %s", (world["user_id"],))
        cur.execute("DELETE FROM resume_entries WHERE user_id = %s", (world["user_id"],))
        save_resume_evidence(cur, world["user_id"], ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", organization=f"Project {i}", title="Developer",
                                  bullets=[text])
            for i, text in enumerate(bullets)
        ]))
        cur.execute(
            "UPDATE jobs SET requirements = %s::jsonb, skills = '[]'::jsonb, match_detail = NULL WHERE id = %s",
            (json.dumps(requirements), world["job_id"]),
        )
        cur.execute("DELETE FROM skill_relations")
        for specific, general in learned:
            cur.execute("INSERT INTO skill_relations (specific, general) VALUES (%s, %s)",
                        (specific, general))
        skill_relations.reset()
        skill_relations.load(cur)
    _db.commit()


def _questions(run_id):
    with get_cursor() as cur:
        cur.execute("SELECT question, skill FROM tailoring_detail_requests WHERE run_id = %s", (run_id,))
        return cur.fetchall()


def test_run_0dc2d235_asks_nothing_about_either_react_bullet(monkeypatch, world, _db):
    """Regression for the real run. "front-end frameworks" now means frontend, which React
    implies through the hand-written table, so the group is met on both bullets before any task
    exists: no diagnosis, no editor step, no question — even with a diagnosis that would ask."""
    _job_with(world, _db, [{
        "condition": {"operator": "any_of", "minimum": 1, "items": [
            "data structures", "storage systems", "cloud infrastructure", "front-end frameworks",
        ]},
        "source_text": "data structures or storage systems or cloud infrastructure or front-end frameworks",
        "importance": "required", "type": "skill",
    }], [MESSAGING, CALENDAR], LEARNED_IN_THAT_RUN)
    calls = diagnosis(monkeypatch, lambda task: {"decision": "ask"})
    sent = editor(monkeypatch)

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert sent == []
    assert calls == []
    assert _questions(result["run_id"]) == []
    from services import skill_relations
    skill_relations.reset()


CELERY = "Built report generation with Celery workers in a Flask app."


def _celery_job(world, _db):
    # celery → task queues is a valid learned edge: specific tool to the general concept
    _job_with(world, _db, [{"skill": "task queues", "importance": "required", "type": "skill"}],
              [CELERY], [("celery", "task queues")])


def _confirm_editor(monkeypatch, world):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s", (world["user_id"],))
        bullet_id = str(cur.fetchone()[0])
    return editor(
        monkeypatch,
        response([call("request_detail", {
            "requirement": "task queues", "bullet_id": bullet_id, "intent": "establish_use",
            "question": "anything",
        }, "c1")]),
    )


def test_a_learned_skill_is_confirmed_when_a_yes_changes_the_bullet(monkeypatch, world, _db):
    _celery_job(world, _db)
    diagnosis(monkeypatch, lambda task: {
        "decision": "ask", "skill": "task queues",
        "job_relevance": "The job runs background jobs on a task queue.",
        "expected_improvement": "Built report generation on task queues with Celery workers in a Flask app.",
    })
    _confirm_editor(monkeypatch, world)

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert _questions(result["run_id"]) == [
        ("Did you use task queues in this project? If so, what did you use it for?", "task queues"),
    ]
    from services import skill_relations
    skill_relations.reset()


@pytest.mark.parametrize("improvement", [
    "Makes the bullet more relevant to the job.",
    "Improves visibility of the candidate's backend skills.",
    "A stronger bullet.",
    None,
])
def test_a_confirmation_that_changes_nothing_concrete_is_not_asked(monkeypatch, world, _db, improvement):
    _celery_job(world, _db)
    diagnosis(monkeypatch, lambda task: {
        "decision": "ask", "skill": "task queues",
        "job_relevance": "The job runs background jobs on a task queue.",
        "expected_improvement": improvement,
    })
    sent = editor(monkeypatch)

    result = run_tailoring(get_cursor, world["user_id"], world["job_id"])

    assert sent == [] and _questions(result["run_id"]) == []
    from services import skill_relations
    skill_relations.reset()
