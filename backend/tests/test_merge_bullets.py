"""`tool_merge_bullets`, tested directly.

The model is not offered this tool any more: the review reads every experience and project
bullet, so every possible merge partner is either work another candidate owns or a bullet the
review kept, and both are excluded from every merge scope. Offering an action that must always
be refused only spends a candidate's attempt.

The implementation stays, and so does its coverage. When merging returns as a candidate that
owns both bullet ids and resolves them together, these are the guarantees it has to keep — and
they are exercised here rather than through a loop that can no longer reach them.
"""

import pytest

from db import get_cursor
from services.tailoring_agent import GroundingError, tool_merge_bullets
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence


ONE = "Built Flask APIs for calendar events"
TWO = "Built Flask endpoints for event updates"
OTHER_ENTRY = "Wrote the deployment scripts in Bash"


@pytest.fixture
def world(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('mergeuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('stranger', 'x') RETURNING id")
        other_user = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Backend Engineer', 'Globex', 'Owns services', '["flask"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", organization=None, title="Calendar",
                                  bullets=[ONE, TWO]),
            ResumeEntryExtraction(kind="project", organization=None, title="Infra",
                                  bullets=[OTHER_ENTRY]),
        ]))
        save_resume_evidence(cur, other_user, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", organization=None, title="Theirs",
                                  bullets=["Built Flask APIs for their own calendar"]),
        ]))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s", (user_id,))
        mine = {text: str(bid) for bid, text in cur.fetchall()}
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s", (other_user,))
        theirs = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
            VALUES (%s, %s, 'gpt-4o-mini', 12, 'running') RETURNING id
            """,
            (user_id, job_id),
        )
        run_id = cur.fetchone()[0]
    _db.commit()
    return {"user_id": user_id, "run_id": run_id, "b": mine, "theirs": theirs}


def _reached_this_run(cur, run_id, bullet_ids):
    """The record `verify_citation` reads: these bullets were handed to this run."""
    import json

    cur.execute(
        """
        INSERT INTO tool_calls (run_id, step_number, call_id, tool_name, arguments, result, status)
        VALUES (%s, 1, 'supplied', 'evidence_supplied', '{}'::jsonb, %s, 'completed')
        ON CONFLICT (run_id, call_id) DO NOTHING
        """,
        (run_id, json.dumps({"results": [{"bullet_id": b, "text": ""} for b in bullet_ids]})),
    )


def _merge(cur, world, ids, text, requirement="flask"):
    return tool_merge_bullets(cur, world["user_id"], world["run_id"], {
        "requirement": requirement,
        "bullet_ids": ids,
        "proposed_text": text,
        "evidence_bullet_ids": ids,
        "reason": "The two bullets repeat the same work.",
    })


# ── citation and ownership ───────────────────────────────────────────────────

def test_a_merge_partner_must_have_reached_this_run(world, _db):
    """Authorisation is not retrieval: a bullet the run was never handed has no record of
    reaching it, and citing it is refused."""
    with _db.cursor() as cur:
        with pytest.raises(GroundingError):
            _merge(cur, world, [world["b"][ONE], world["b"][TWO]],
                   "Built Flask APIs for calendar events and updates")


def test_a_merge_cannot_consume_another_users_bullet(world, _db):
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["theirs"]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError):
            _merge(cur, world, ids, "Built Flask APIs for calendar events and updates")
        cur.execute("SELECT count(*) FROM proposed_edits WHERE run_id = %s", (world["run_id"],))
        assert cur.fetchone()[0] == 0


def test_merged_bullets_must_share_an_entry(world, _db):
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][OTHER_ENTRY]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError) as exc:
            _merge(cur, world, ids, "Built Flask APIs and the deployment scripts in Bash")
        assert "same resume entry" in str(exc.value)


# ── the sources survive ──────────────────────────────────────────────────────

def test_a_merge_preserves_the_bullets_it_consumed(world, _db):
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        _merge(cur, world, ids, "Built Flask APIs for calendar event creation and updates")
        cur.execute("SELECT text FROM resume_bullets WHERE id = ANY(%s::uuid[]) ORDER BY text", (ids,))
        assert [row[0] for row in cur.fetchall()] == sorted([ONE, TWO]), \
            "a proposal changes nothing until the user accepts it"


def test_every_consumed_bullet_is_recorded_with_the_edit(world, _db):
    """Atomic: the edit and the list of what it consumed are written in one statement, so an
    accepted merge can never be applied without knowing which bullets it replaces."""
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        result = _merge(cur, world, ids, "Built Flask APIs for calendar event creation and updates")
        # the primary is the edit's own bullet_id; the extras it consumed are the join rows,
        # and together they must account for every bullet the merge swallowed
        cur.execute("SELECT bullet_id FROM proposed_edits WHERE id = %s", (result["edit_id"],))
        primary = str(cur.fetchone()[0])
        cur.execute(
            "SELECT bullet_id FROM tailoring_edit_bullets WHERE edit_id = %s ORDER BY sort_order",
            (result["edit_id"],),
        )
        extras = [str(row[0]) for row in cur.fetchall()]
    assert primary == ids[0]
    assert [primary] + extras == ids, "every bullet the merge consumes is accounted for"


def test_a_merge_records_one_edit_or_none(world, _db):
    """The edit row and its consumed bullets share a transaction: a refused merge leaves
    neither behind."""
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError):
            _merge(cur, world, ids, "Built Flask APIs for calendar events using Redis caching")
        cur.execute("SELECT count(*) FROM proposed_edits WHERE run_id = %s", (world["run_id"],))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM tailoring_edit_bullets AS m "
            "JOIN proposed_edits AS e ON e.id = m.edit_id WHERE e.run_id = %s",
            (world["run_id"],),
        )
        assert cur.fetchone()[0] == 0


# ── the claim has to be supported ────────────────────────────────────────────

def test_a_merge_may_not_invent_a_technology(world, _db):
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError) as exc:
            _merge(cur, world, ids, "Built Flask and Redis APIs for calendar events and updates")
        assert "redis" in str(exc.value).lower()


def test_a_merge_may_not_append_an_outcome_nobody_claimed(world, _db):
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError) as exc:
            _merge(cur, world, ids, "Built Flask APIs for events, improving reliability")
        assert "result" in str(exc.value).lower()


def test_a_merge_may_not_simply_concatenate(world, _db):
    """Compression is the point; gluing two bullets together is not a merge."""
    with _db.cursor() as cur:
        ids = [world["b"][ONE], world["b"][TWO]]
        _reached_this_run(cur, world["run_id"], ids)
        with pytest.raises(GroundingError) as exc:
            _merge(cur, world, ids, f"{ONE} and {TWO.lower()}")
        assert "concatenat" in str(exc.value).lower()
