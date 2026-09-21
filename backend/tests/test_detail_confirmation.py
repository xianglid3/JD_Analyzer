"""Which questions are worth asking, and what an answer settles.

The run of 2026-09-21 asked "did you use go or typescript or python?" about a bullet naming
TypeScript, and "did you use ai?" about an LLM bullet. These pin the rules that stop that: use
is judged per skill and per bullet, a requirement's alternatives keep their operator, and an
answer confirms (or denies) one skill on one bullet — never the whole group.
"""

import json
import types

import pytest

from db import get_cursor
from services import tailoring_agent
from services import tailoring_candidates as candidates_state
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.tailoring_agent import (
    GroundingError,
    _claim_evidence,
    _with_conditions,
    execute_tool,
    resolve_detail_request,
    tool_request_detail,
)


FASTAPI = (
    "Designed a FastAPI backend with Supabase and REST APIs for event creation, updates, and "
    "geocoded location storage; integrated it with a React/TypeScript calendar and Mapbox "
    "interface."
)
LLM = "Built an LLM-powered job analysis and resume tailoring platform with Flask."
MOBILE = "Built the ordering service for a campus food delivery app."
TOOLING = "Wrote internal tooling that the data team used for weekly reports."

ANY_GTP = {"operator": "any_of", "minimum": 1, "items": ["go", "typescript", "python"]}
ALL_TG = {"operator": "all_of", "minimum": 1, "items": ["typescript", "go"]}


@pytest.fixture
def world(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('confirmuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Engineer', 'Globex', 'Builds things', '["typescript"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", organization="Planner", title="Dev",
                                  bullets=[FASTAPI, LLM]),
            ResumeEntryExtraction(kind="project", organization="Campus Eats", title="Dev",
                                  bullets=[MOBILE]),
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern",
                                  bullets=[TOOLING]),
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
        # every bullet reached this run, so citations are not what these tests are about
        cur.execute(
            """
            INSERT INTO tool_calls (run_id, step_number, call_id, tool_name, arguments, result, status)
            VALUES (%s, 1, 'search', 'search_resume', '{}', %s, 'completed')
            """,
            (run_id, json.dumps({"results": [{"bullet_id": b} for b in bullets.values()],
                                 "count": len(bullets)})),
        )
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "run_id": run_id, "b": bullets}


def ask(world, bullet, requirement, intent, skill=None, condition=None, question="What did you build?",
        action=None):
    arguments = {"requirement": requirement, "bullet_id": world["b"][bullet],
                 "intent": intent, "question": question}
    if skill:
        arguments["skill"] = skill
    with get_cursor(commit=True) as cur:
        return tool_request_detail(cur, world["user_id"], world["run_id"], arguments,
                                   condition=condition, action=action)


def answer(world, bullet, requirement, skill, outcome, text="Yes.", intent="establish_use"):
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO tailoring_detail_requests (
                run_id, user_id, bullet_id, requirement, question, answer, status, intent,
                outcome, skill
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'answered', %s, %s, %s)
            """,
            (world["run_id"], world["user_id"], world["b"][bullet], requirement,
             f"q-{requirement}-{skill}-{intent}-{text}", text, intent, outcome, skill),
        )


def pending_questions(world):
    with get_cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tailoring_detail_requests WHERE run_id = %s AND status = 'pending'",
            (world["run_id"],),
        )
        return cur.fetchone()[0]


# ── redundant questions ──────────────────────────────────────────────────────

def test_a_grouped_requirement_the_bullet_meets_is_not_asked_about(world):
    with pytest.raises(GroundingError, match="already shows typescript"):
        ask(world, FASTAPI, "go or typescript or python", "establish_use",
            skill="typescript", condition=ANY_GTP)
    # the unused alternatives are not gaps: asking about Go would be asking for nothing
    with pytest.raises(GroundingError, match="already met here by"):
        ask(world, FASTAPI, "go or typescript or python", "establish_use",
            skill="go", condition=ANY_GTP)
    assert pending_questions(world) == 0


def test_llm_establishes_ai_so_nobody_asks(world):
    """The hand-written table says llm implies ai — the rule the fit engine used to call it
    INFERRED — so the gate may not disagree with it."""
    with pytest.raises(GroundingError, match="already shows ai"):
        ask(world, LLM, "ai", "establish_use")


def test_an_established_confirm_candidate_asks_nothing_at_all(world):
    """The post-patch run: establish_use was refused for LLM → AI, so the model asked "what
    improvements did the LLM-powered platform provide?" instead. The confirm candidate's only
    question was answered by the bullet; that is not a reason for another one."""
    with pytest.raises(GroundingError, match="nothing to confirm or ask"):
        ask(world, LLM, "ai", "implementation", action="confirm",
            question="Which part of the platform used AI?")
    assert pending_questions(world) == 0


def test_an_unestablished_confirm_candidate_can_still_ask(world):
    assert ask(world, MOBILE, "go", "establish_use", action="confirm")["status"] == "awaiting_user"


def test_a_group_question_must_name_one_of_its_alternatives(world):
    with pytest.raises(GroundingError, match="name the one"):
        ask(world, MOBILE, "go or typescript or python", "establish_use", condition=ANY_GTP)
    with pytest.raises(GroundingError, match="must be one of"):
        ask(world, MOBILE, "go or typescript or python", "establish_use",
            skill="rust", condition=ANY_GTP)


def test_a_genuinely_absent_requirement_can_still_be_asked(world):
    result = ask(world, MOBILE, "go or typescript or python", "establish_use",
                 skill="go", condition=ANY_GTP)
    assert result["status"] == "awaiting_user"
    with get_cursor() as cur:
        cur.execute("SELECT question, skill FROM tailoring_detail_requests WHERE run_id = %s",
                    (world["run_id"],))
        question, skill = cur.fetchone()
    assert skill == "go"
    assert question.startswith("Did you use go in this project?")


def test_typescript_on_another_project_does_not_establish_it_here(world):
    result = ask(world, MOBILE, "typescript", "establish_use")
    assert result["status"] == "awaiting_user"


def test_a_learned_edge_does_not_make_a_question_unnecessary(monkeypatch, world):
    """Learned edges score. They do not get to answer for the user."""
    from services import skill_graph

    real = skill_graph._learned
    monkeypatch.setattr(
        skill_graph, "_learned",
        lambda direction, skill: (["redux"] if direction == "implies" and skill == "react"
                                  else real(direction, skill)),
    )
    assert ask(world, FASTAPI, "redux", "establish_use")["status"] == "awaiting_user"


# ── all_of keeps its meaning ─────────────────────────────────────────────────

def test_all_of_asks_for_the_member_still_missing(world):
    with pytest.raises(GroundingError, match="Still unestablished for typescript and go: go"):
        ask(world, FASTAPI, "typescript and go", "establish_use",
            skill="typescript", condition=ALL_TG)
    # a detail question about the member that IS shown is fine on its own
    ask(world, FASTAPI, "typescript and go", "implementation", skill="typescript",
        condition=ALL_TG, question="Which calendar component did you write in TypeScript?")
    # and it does not use up the confirmation Go still needs
    result = ask(world, FASTAPI, "typescript and go", "establish_use", skill="go",
                 condition=ALL_TG)
    assert result["status"] == "awaiting_user"
    assert pending_questions(world) == 2


def test_a_question_that_assumes_an_unused_alternative_is_refused(world):
    """Met by TypeScript does not make "which Go service did you write?" answerable."""
    with pytest.raises(GroundingError, match="nothing establishes that this bullet involved go"):
        ask(world, FASTAPI, "go or typescript", "implementation", skill="go",
            condition={"operator": "any_of", "minimum": 1, "items": ["go", "typescript"]},
            question="Which Go service did you write?")


def test_rewording_is_still_a_repeat(world):
    answer(world, MOBILE, "go", "go", "yes")
    ask(world, MOBILE, "go", "implementation", question="What did you build?")
    # the same question again is the same row, not a second one
    ask(world, MOBILE, "go", "implementation", question="What did you build?")
    assert pending_questions(world) == 1
    with pytest.raises(GroundingError, match="already waiting"):
        ask(world, MOBILE, "go", "implementation", question="Which part was yours?")


def test_a_denied_skill_cannot_be_asked_again(world):
    answer(world, MOBILE, "go or typescript", "go", "no", text="No.")
    with pytest.raises(GroundingError, match="did not use go on this bullet"):
        ask(world, MOBILE, "go or typescript", "establish_use", skill="go",
            condition={"operator": "any_of", "minimum": 1, "items": ["go", "typescript"]})


def test_an_unclear_answer_is_not_handed_back_as_usable(world):
    """Answered, but not a yes: pointing the model at a rewrite would send it into the gate."""
    answer(world, MOBILE, "go", "go", "unclear", text="Hard to say.")
    with pytest.raises(GroundingError, match="still unestablished") as refused:
        ask(world, MOBILE, "go", "establish_use")
    assert "propose the rewrite" not in str(refused.value)
    assert "keep_original" in str(refused.value)


# ── answers confirm one skill ────────────────────────────────────────────────

def test_yes_to_typescript_does_not_license_go(world):
    answer(world, MOBILE, "go or typescript", "typescript", "yes")
    with get_cursor() as cur:
        texts, *_ = _claim_evidence(cur, world["user_id"], world["run_id"],
                                    {world["b"][MOBILE]: None}, "go or typescript")
    joined = " ".join(texts).lower()
    assert "typescript" in joined and "go or typescript" not in joined


def test_an_old_yes_to_a_group_label_supports_nothing(world):
    answer(world, MOBILE, "go or typescript", None, "yes")
    with get_cursor() as cur:
        texts, details, _ = _claim_evidence(cur, world["user_id"], world["run_id"],
                                            {world["b"][MOBILE]: None}, "go or typescript")
    assert details == []
    assert not any("typescript" in text.lower() for text in texts)


def test_an_old_yes_to_a_single_skill_still_counts(world):
    answer(world, MOBILE, "go", None, "yes")
    with get_cursor() as cur:
        texts, *_ = _claim_evidence(cur, world["user_id"], world["run_id"],
                                    {world["b"][MOBILE]: None}, "go")
    assert "go" in texts


def test_only_the_users_own_words_can_license_a_result(world):
    answer(world, MOBILE, "go", "go", "no", text="No, but it reduced latency elsewhere.")
    answer(world, MOBILE, "go", None, None, text="It reduced duplicate orders.",
           intent="impact")
    with get_cursor() as cur:
        _texts, _details, answers = _claim_evidence(
            cur, world["user_id"], world["run_id"], {world["b"][MOBILE]: None}, "go",
        )
    assert answers == ["It reduced duplicate orders."]


# ── the edit gate ────────────────────────────────────────────────────────────

def _call(name, arguments):
    return types.SimpleNamespace(
        id=f"c-{name}-{json.dumps(arguments, sort_keys=True)}",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def run_tool(world, name, arguments, action, condition, targets, key=None):
    key = key or arguments["requirement"]
    with get_cursor(commit=True) as cur:
        return execute_tool(
            cur, world["user_id"], world["run_id"], 2, _call(name, arguments),
            {key}, {key: {"edit": set(targets), "merge": set(targets)}}, {key: action},
            allowed_labels={key: key}, allowed_conditions={key: condition},
        )


def test_an_established_confirm_candidate_can_be_edited_without_an_answer(world):
    """The trap this closes: use was already shown, so establishing it was refused — and the
    edit was refused too, for want of an answer. Nothing was left but to ask something else."""
    result = run_tool(world, "propose_edit", {
        "requirement": "ai", "bullet_id": world["b"][LLM],
        "proposed_text": "Built an LLM-powered job analysis and resume tailoring platform in Flask.",
        "evidence_bullet_ids": [world["b"][LLM]],
    }, "confirm", {"operator": "any_of", "minimum": 1, "items": ["ai"]}, [world["b"][LLM]])
    assert "establishes the requirement" not in result.get("error", "")
    # and adding "AI" is still a claim the evidence has to carry, which LLM does not
    result = run_tool(world, "propose_edit", {
        "requirement": "ai", "bullet_id": world["b"][LLM],
        "proposed_text": "Built an AI-powered job analysis and resume tailoring platform with Flask.",
        "evidence_bullet_ids": [world["b"][LLM]],
    }, "confirm", {"operator": "any_of", "minimum": 1, "items": ["ai"]}, [world["b"][LLM]])
    assert "does not appear in the evidence" in result.get("error", "")


def test_an_unclear_or_no_answer_does_not_unlock_a_confirm_edit(world):
    condition = {"operator": "any_of", "minimum": 1, "items": ["go"]}
    for outcome in ("unclear", "no"):
        answer(world, MOBILE, "go", "go", outcome, text=f"{outcome} answer")
        result = run_tool(world, "propose_edit", {
            "requirement": "go", "bullet_id": world["b"][MOBILE],
            "proposed_text": "Built the Go ordering service for a campus food delivery app.",
            "evidence_bullet_ids": [world["b"][MOBILE]],
        }, "confirm", condition, [world["b"][MOBILE]])
        assert "establishes the requirement" in result["error"]


def test_strengthen_needs_no_answer(world):
    """A same-facts edit on a bullet whose requirement is already on the page. The all_of may
    be met across two bullets; this one alone need not carry Go."""
    result = run_tool(world, "propose_edit", {
        "requirement": "typescript and go", "bullet_id": world["b"][FASTAPI],
        "proposed_text": "Built a FastAPI backend with Supabase and REST APIs for event creation, "
                         "updates, and geocoded location storage, behind a React/TypeScript "
                         "calendar and Mapbox interface.",
        "evidence_bullet_ids": [world["b"][FASTAPI]],
    }, "strengthen", ALL_TG, [world["b"][FASTAPI]])
    assert "ask the user" not in result.get("error", "")
    assert "establishes the requirement" not in result.get("error", "")


def test_the_model_naming_an_alternative_is_read_as_the_skill(world):
    """Asked about "go or typescript or python", a model says "go". That names the skill; the
    requirement is still the group's, so bookkeeping matches the candidate."""
    label = "go or typescript or python"
    result = run_tool(world, "request_detail", {
        "requirement": "go", "bullet_id": world["b"][MOBILE],
        "intent": "establish_use", "question": "x",
    }, "confirm", ANY_GTP, [world["b"][MOBILE]], key=label)
    assert result["status"] == "awaiting_user"
    with get_cursor() as cur:
        cur.execute("SELECT requirement, skill FROM tailoring_detail_requests WHERE run_id = %s",
                    (world["run_id"],))
        assert cur.fetchone() == (label, "go")


# ── a "no" settles one skill on one bullet ───────────────────────────────────

def _plan(monkeypatch, world, condition, targets, label):
    item = {
        "position": 0, "requirement": label, "agent_label": label, "state": "inferred",
        "importance": "required", "action": "confirm", "reason": "", "inferred_from": [],
        "satisfied_by": [], "condition": condition, "evidence_count": 1,
        "targets": [{"bullet_id": world["b"][t], "text": t} for t in targets],
    }
    monkeypatch.setattr(tailoring_agent, "load_job_assessment", lambda *_a: (None, {}))
    monkeypatch.setattr(tailoring_agent, "build_tailoring_plan", lambda *_a, **_k: [item])
    with get_cursor(commit=True) as cur:
        candidates_state.create(cur, world["user_id"], world["run_id"], [item])


def _pending(world, bullet, label, skill):
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE tailoring_runs SET status = 'waiting_for_user' WHERE id = %s",
                    (world["run_id"],))
        cur.execute(
            """
            INSERT INTO tailoring_detail_requests (
                run_id, user_id, bullet_id, requirement, question, intent, skill
            )
            VALUES (%s, %s, %s, %s, %s, 'establish_use', %s) RETURNING id
            """,
            (world["run_id"], world["user_id"], world["b"][bullet], label,
             f"Did you use {skill} on {bullet[:10]}?", skill),
        )
        return cur.fetchone()[0]


def _status(world, label):
    with get_cursor() as cur:
        return {item["normalized"]: item["status"]
                for item in candidates_state.load(cur, world["run_id"])}[label]


def test_no_to_go_leaves_typescript_open(monkeypatch, world):
    label = "go or typescript"
    _plan(monkeypatch, world, {"operator": "any_of", "minimum": 1, "items": ["go", "typescript"]},
          [MOBILE], label)
    resolve_detail_request(get_cursor, world["user_id"], _pending(world, MOBILE, label, "go"),
                           answer="No.", used=False)
    assert _status(world, label) != "skipped"

    # then yes to TypeScript, and the confirm edit unlocks
    resolve_detail_request(get_cursor, world["user_id"],
                           _pending(world, MOBILE, label, "typescript"),
                           answer="Yes, the order API.", used=True)
    result = run_tool(world, "propose_edit", {
        "requirement": label, "bullet_id": world["b"][MOBILE],
        "proposed_text": "Built the TypeScript ordering service for a campus food delivery app.",
        "evidence_bullet_ids": [world["b"][MOBILE]],
    }, "confirm", {"operator": "any_of", "minimum": 1, "items": ["go", "typescript"]},
        [world["b"][MOBILE]])
    assert "establishes the requirement" not in result.get("error", "")


def test_no_to_a_required_member_closes_a_single_target(monkeypatch, world):
    _plan(monkeypatch, world, ALL_TG, [MOBILE], "typescript and go")
    resolve_detail_request(get_cursor, world["user_id"],
                           _pending(world, MOBILE, "typescript and go", "go"),
                           answer="No.", used=False)
    assert _status(world, "typescript and go") == "skipped"


def test_no_on_one_project_leaves_the_other_project_open(monkeypatch, world):
    label = "typescript and go"
    _plan(monkeypatch, world, ALL_TG, [MOBILE, TOOLING], label)
    resolve_detail_request(get_cursor, world["user_id"], _pending(world, MOBILE, label, "go"),
                           answer="No.", used=False)
    assert _status(world, label) != "skipped"
    # Go is settled on the first project and still askable on the second
    with pytest.raises(GroundingError, match="did not use go"):
        ask(world, MOBILE, label, "establish_use", skill="go", condition=ALL_TG)
    resolve_detail_request(get_cursor, world["user_id"], _pending(world, TOOLING, label, "go"),
                           answer="Yes, the report service.", used=True)
    assert _status(world, label) != "skipped"


def test_no_on_both_projects_closes_it(monkeypatch, world):
    label = "typescript and go"
    _plan(monkeypatch, world, ALL_TG, [MOBILE, TOOLING], label)
    for bullet in (MOBILE, TOOLING):
        resolve_detail_request(get_cursor, world["user_id"], _pending(world, bullet, label, "go"),
                               answer="No.", used=False)
    assert _status(world, label) == "skipped"


# ── assessments stored before conditions were ────────────────────────────────

def test_an_old_all_of_is_rebuilt_as_all_of(world):
    raw = [{"condition": ALL_TG, "importance": "required", "type": "skill"}]
    stored = {"requirements": [{"requirement": "typescript and go",
                                "agent_label": "typescript and go", "state": "inferred"}]}
    with get_cursor() as cur:
        rebuilt = _with_conditions(cur, world["user_id"], stored, raw, [])
    assert rebuilt["requirements"][0]["condition"]["operator"] == "all_of"


def test_an_unmatched_old_label_is_recomputed_rather_than_guessed(monkeypatch, world):
    raw = [{"condition": ALL_TG, "importance": "required", "type": "skill"}]
    stored = {"requirements": [{"requirement": "something else", "state": "inferred"}]}
    fresh = {"requirements": [{"requirement": "typescript and go", "condition": ALL_TG}]}
    monkeypatch.setattr(tailoring_agent, "match_for_job", lambda *_a: fresh)
    with get_cursor() as cur:
        assert _with_conditions(cur, world["user_id"], stored, raw, []) is fresh
