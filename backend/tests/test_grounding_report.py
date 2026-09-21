"""The grounding report: counting the rejections the agent's checks already record.

The checks themselves are tested in test_tailoring_agent.py. This is about whether the
numbers describing them are right — including the hand-check sample, which is the only part
that can tell you a rejection was *correct*.
"""

import json
import types

import pytest

from db import get_cursor
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.grounding_report import classify, report, samples
from services.resume_evidence import save_resume_evidence
from services import tailoring_agent
from services.tailoring_agent import run_tailoring


def call(name, arguments, call_id="c1"):
    return types.SimpleNamespace(
        id=call_id, function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def response(tool_calls=None, content=None):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def script(monkeypatch, *responses):
    queue = list(responses)
    monkeypatch.setattr(tailoring_agent, "complete", lambda _messages: queue.pop(0))


@pytest.mark.parametrize("message, expected", [
    ("bullet abc was not returned by a search in this run — search for it first", "uncited_bullet"),
    ("bullet abc is not part of your resume", "not_your_bullet"),
    ("kubernetes does not appear in the evidence you cited — rewrite using only...", "unsupported_claim"),
    ("a search for React in this run returned 3 bullet(s) — propose an edit citing them", "contradicted_gap"),
    ("evidence_bullet_ids is required — an edit with no evidence is not allowed", "no_evidence_cited"),
    ("requirement is required", "missing_fields"),
    ("proposed_text must be 500 characters or fewer", "too_long"),
    ("arguments were not valid JSON", "bad_arguments"),
    ("unknown tool delete_everything", "unknown_tool"),
    ("something nobody has seen before", "other"),
    (None, "none"),
])
def test_every_rejection_the_agent_can_raise_has_a_label(message, expected):
    assert classify(message) == expected


@pytest.fixture
def fixtures(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('reportuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills)
            VALUES (%s, 'x', 'Platform Engineer', '["kubernetes"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", bullets=[
                "Worked on Kubernetes deployments across three regions",
            ])
        ]))
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s", (user_id,))
        bullet_id = str(cur.fetchone()[0])
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "bullet_id": bullet_id}


def test_empty_history_reports_nothing_rather_than_dividing_by_zero(_db, fixtures):
    with _db.cursor() as cur:
        data = report(cur)

    assert data["runs"]["total"] == 0
    assert data["output"]["edits_per_run"] == 0.0
    assert data["by_reason"] == []


def test_a_clean_run_reports_no_rejections(monkeypatch, fixtures, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": fixtures["bullet_id"],
            "proposed_text": "Deployed Kubernetes services across three regions",
            "evidence_bullet_ids": [fixtures["bullet_id"]],
        }, "c2")]),
        response(content="Done."),
    )
    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        data = report(cur)

    assert data["runs"]["total"] == 1 and data["runs"]["completed"] == 1
    assert data["output"]["edits_proposed"] == 1
    assert data["by_tool"]["propose_edit"]["rejection_rate"] == 0.0
    assert data["by_reason"] == []


def test_an_invented_claim_is_counted_and_can_be_read_back(monkeypatch, fixtures, _db):
    """The number says the checker fired; the sample is what tells you it was right to."""
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": fixtures["bullet_id"],
            "proposed_text": "Ran Kubernetes on Terraform-provisioned infrastructure",
            "evidence_bullet_ids": [fixtures["bullet_id"]],
        }, "c2")]),
        response(content="Stopped."),
    )
    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        data = report(cur)
        rows = samples(cur, limit=10)

    assert data["by_tool"]["propose_edit"] == {
        "attempts": 1, "completed": 0, "failed": 1, "rejection_rate": 100.0,
    }
    assert data["by_reason"][0] == {"tool": "propose_edit", "reason": "unsupported_claim", "count": 1}

    assert rows[0]["reason"] == "unsupported_claim"
    assert "terraform" in rows[0]["proposed_text"].lower()   # the text a human needs to judge it
    assert "terraform" in rows[0]["error"].lower()


def test_samples_can_be_filtered_to_one_reason(monkeypatch, fixtures, _db):
    import uuid as _uuid

    # an id belonging to nobody. Ownership is checked before authorisation and before the
    # citation, so this stops at the first gate — and `uncited_bullet` now only arises for a
    # merge partner, which is authorised by entry but still has to have reached the run.
    stranger = str(_uuid.uuid4())
    script(
        monkeypatch,
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": stranger,
            "proposed_text": "Operated Kubernetes across three regions",
            "evidence_bullet_ids": [stranger],
        }, "c1")]),
        response(content="Stopped."),
    )
    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        assert samples(cur, limit=10, reason="not_your_bullet")
        assert samples(cur, limit=10, reason="unsupported_claim") == []


def test_scoping_to_one_user_excludes_everyone_else(monkeypatch, fixtures, _db):
    script(monkeypatch, response(content="Nothing to do."))
    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('otherreport', 'x') RETURNING id")
        other = cur.fetchone()[0]
        assert report(cur, user_id=fixtures["user_id"])["runs"]["total"] == 1
        assert report(cur, user_id=other)["runs"]["total"] == 0


# ── the classifier is coupled to the agent's wording ─────────────────────────
# Rejection messages live in `tailoring_agent`; the patterns that group them live in
# `grounding_report`. Reword one without the other and every rejection of that kind
# silently becomes "other", which is how the report quietly stops meaning anything.
# (That is exactly what happened when the search-first guard was added.)

EVERY_REJECTION = [
    # citation and provenance
    ("bullet 0f9c is not part of your resume", "not_your_bullet"),
    ("one or more selected bullets are not part of your resume", "not_your_bullet"),
    ("bullet 0f9c was not returned by a search in this run — search for it first", "uncited_bullet"),
    ("call search_resume first, then copy the exact bullet_id UUID it returns", "uncited_bullet"),
    ("call search_resume first, then copy the exact bullet_id UUID it returns; "
     "list positions such as 1, 2, or 3 are not bullet ids", "uncited_bullet"),
    # approval boundaries
    ("requirement is not an approved tailoring candidate", "unapproved_requirement"),
    ("the action does not name an approved target bullet", "unapproved_target"),
    ("the selected bullet is not an approved tailoring target", "unapproved_target"),
    ("gaps are determined by the fit engine, not the writing agent", "agent_overreach"),
    ("ask the user for the missing detail before proposing wording", "needs_user_answer"),
    ("confirm this requirement with the user before changing wording", "needs_user_answer"),
    # claims
    ("kubernetes does not appear in the evidence you cited — rewrite using only what "
     "those bullets say, or cite a bullet that supports it", "unsupported_claim"),
    ("a search for CSS in this run returned 2 bullet(s) — propose an edit citing them, "
     "or search again with different wording", "contradicted_gap"),
    # quality
    ("the rewrite only changes phrasing; strengthen the action or surface new supported "
     "evidence, otherwise leave the bullet unchanged", "cosmetic_rewrite"),
    ("the rewrite removes too much of the bullet's existing detail", "detail_loss"),
    ("the rewrite removes supported detail: flask", "detail_loss"),
    ("the rewrite removes a measurable result from the current bullet", "detail_loss"),
    # merges
    ("a merge requires at least two source bullets", "invalid_merge"),
    ("the merged bullet must open with a concrete action verb", "invalid_merge"),
    ("the merge mostly concatenates the source bullets instead of removing repetition", "invalid_merge"),
    ("merge between 2 and 3 bullets", "invalid_merge"),
    ("evidence_bullet_ids must exactly match the bullets being merged", "invalid_merge"),
    ("merged bullets must come from the same resume entry", "invalid_merge"),
    ("every merged bullet must belong to your resume", "invalid_merge"),
    # citation shape
    ("evidence_bullet_ids is required — an edit with no evidence is not allowed", "no_evidence_cited"),
    ("a one-bullet rewrite may cite only that bullet; use merge_bullets to combine facts",
     "wrong_citation_scope"),
    ("an edit may cite at most 5 bullets", "wrong_citation_scope"),
    ("bullet id must be a valid UUID", "bad_bullet_id"),
    ("every bullet id must be a valid UUID", "bad_bullet_id"),
    ("every evidence bullet id must be a valid UUID", "bad_bullet_id"),
    # argument shape
    ("at least one evidence bullet is required", "missing_fields"),
    ("requirement and proposed_text are both required", "missing_fields"),
    ("requirement is required", "missing_fields"),
    ("requirement, bullet_id, and question are required", "missing_fields"),
    ("proposed_text must be 500 characters or fewer", "too_long"),
    ("question must be 300 characters or fewer", "too_long"),
    ("question must be one line of plain text", "malformed_question"),
    ("arguments were not valid JSON", "bad_arguments"),
    ("tool arguments must be an object", "bad_arguments"),
    ("bullet_ids must be a list", "bad_arguments"),
    ("unknown tool wander_off", "unknown_tool"),
]


@pytest.mark.parametrize("message,expected", EVERY_REJECTION)
def test_every_rejection_is_classified(message, expected):
    assert classify(message) == expected


def test_an_unrecognised_rejection_is_visible_as_other():
    # the escape hatch has to keep working, or a new message would land in a real bucket
    assert classify("something nobody has written yet") == "other"
    assert classify(None) == "none"


@pytest.mark.parametrize("message,expected", [
    ("the proposed edit is the same as the current bullet", "unchanged_text"),
    ("the merged bullet is the same as one source bullet", "unchanged_text"),
    ("the merge removes too much of the source bullets' detail", "detail_loss"),
    ("the merge removes supported detail: pytest", "detail_loss"),
    ("the merge removes a measurable result from its source bullets", "detail_loss"),
    ("the bullet says contributing, and the rewrite says developed — that claims more of the "
     "work than the evidence does", "ownership_inflation"),
    ("this entry has no end date, so the work is still going on, and the rewrite puts it in "
     "the past", "tense_regression"),
    ('you are already waiting on an answer about this bullet ("What did you use?")',
     "duplicate_question"),
])
def test_quality_rejections_are_classified(message, expected):
    assert classify(message) == expected
