"""Who owns "is this run finished?".

Before this, nobody did. The planner handed the model a list of candidates, and any
plain-text reply ended the loop and wrote `completed`. A run that handled one of four and
stopped looked exactly like a run that genuinely had nothing to do, which is why empty runs
were impossible to diagnose (AE-02).

Now the assignment is state. The orchestrator hands out one candidate at a time, records what
happened to it, and refuses to call a run complete while any are still pending. The model's
opinion that it is done is a hint, not a decision.

    pending ─→ active ─→ handled       an edit or merge was recorded
                      ├→ kept          the model read it and judged the original better
                      ├→ skipped       no evidence could be retrieved, so there was nothing to do
                      └→ needs_review  it failed grounding twice, or the run ran out of budget

`kept` is deliberately separate from `handled`. Both are successful decisions, but "I improved
this" and "I judged this fine" are different facts about a run, and the ratio between them is
the only number that shows whether the decline is being used honestly or as a cheap exit.
"""

PENDING = "pending"
ACTIVE = "active"
HANDLED = "handled"
KEPT = "kept"
SKIPPED = "skipped"
NEEDS_REVIEW = "needs_review"

TERMINAL = (HANDLED, KEPT, SKIPPED, NEEDS_REVIEW)


def create(cur, user_id, run_id, candidates):
    """Write the planner's assignment down before the worker starts.

    Persisted at creation rather than derived later, so a run can always be compared against
    what it was actually asked to do — including after a restart, when the plan would
    otherwise be recomputed from a resume that may since have changed.
    """
    from services.match import normalize_skill

    for item in candidates:
        cur.execute(
            """
            INSERT INTO tailoring_candidates (
                run_id, user_id, position, requirement, normalized, action
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, position) DO NOTHING
            """,
            (
                # the posting's own wording for the user, the short handle for matching —
                # a candidate named by a whole sentence cannot be addressed reliably
                run_id, user_id, item["position"], item["requirement"],
                normalize_skill(item.get("agent_label") or item["requirement"]),
                item["action"],
            ),
        )
    return len(candidates)


def load(cur, run_id):
    cur.execute(
        """
        SELECT id, position, requirement, normalized, action, status, outcome, attempts
        FROM tailoring_candidates WHERE run_id = %s ORDER BY position
        """,
        (run_id,),
    )
    return [
        {
            "id": str(row[0]), "position": row[1], "requirement": row[2],
            "normalized": row[3], "action": row[4], "status": row[5],
            "outcome": row[6], "attempts": row[7],
        }
        for row in cur.fetchall()
    ]


def claim_next(cur, run_id):
    """Mark the next unfinished candidate active and return it, or None when all are done.

    `FOR UPDATE SKIP LOCKED` so two workers racing on the same run cannot both take the same
    candidate — which is exactly what a resumed run and a slow original worker can do.
    """
    cur.execute(
        """
        UPDATE tailoring_candidates SET status = 'active'
        WHERE id = (
            SELECT id FROM tailoring_candidates
            WHERE run_id = %s AND status IN ('pending', 'active')
            ORDER BY position
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, position, requirement, normalized, action, attempts
        """,
        (run_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]), "position": row[1], "requirement": row[2],
        "normalized": row[3], "action": row[4], "attempts": row[5],
    }


def resolve(cur, run_id, normalized, status, outcome=None):
    """Close out whichever candidate matches this requirement."""
    cur.execute(
        """
        UPDATE tailoring_candidates
        SET status = %s, outcome = %s, resolved_at = now()
        WHERE run_id = %s AND normalized = %s AND status NOT IN ('handled', 'kept', 'skipped')
        RETURNING id
        """,
        (status, outcome, run_id, normalized),
    )
    return [str(row[0]) for row in cur.fetchall()]


def is_finished(cur, run_id, normalized):
    """True when this candidate already reached a terminal state.

    The model re-sends its whole batch of candidates every step, so one that succeeded on an
    earlier step is offered again on the next one. Without this the second attempt is written
    as a second proposal, and the user reviews the same rewrite twice.

    `needs_review` is deliberately not counted. It means the run ran out of budget or the
    action failed twice — the work is still owed, and a resumed run must be able to pick it
    back up. Only `handled`, `kept` and `skipped` are decisions, which is the same line
    `resolve` draws when it declines to reopen a candidate.
    """
    cur.execute(
        """
        SELECT 1 FROM tailoring_candidates
        WHERE run_id = %s AND normalized = %s AND status IN %s
        """,
        (run_id, normalized, (HANDLED, KEPT, SKIPPED)),
    )
    return cur.fetchone() is not None


def record_attempt(cur, run_id, normalized):
    cur.execute(
        """
        UPDATE tailoring_candidates SET attempts = attempts + 1
        WHERE run_id = %s AND normalized = %s
        RETURNING attempts
        """,
        (run_id, normalized),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def unfinished(cur, run_id):
    """Candidates still owed work. An empty list is the only thing that justifies
    `completed`."""
    cur.execute(
        """
        SELECT requirement FROM tailoring_candidates
        WHERE run_id = %s AND status IN ('pending', 'active') ORDER BY position
        """,
        (run_id,),
    )
    return [row[0] for row in cur.fetchall()]


def close_out(cur, run_id, outcome):
    """Give every still-unfinished candidate a terminal state.

    Called when the loop stops for a reason that is not "everything was handled" — the step
    limit, the quota, a model failure. Without it those candidates stay `pending` forever and
    the run reads as though the work is still coming.
    """
    cur.execute(
        """
        UPDATE tailoring_candidates
        SET status = 'needs_review', outcome = %s, resolved_at = now()
        WHERE run_id = %s AND status IN ('pending', 'active')
        RETURNING requirement
        """,
        (outcome, run_id),
    )
    return [row[0] for row in cur.fetchall()]


def summary(cur, run_id):
    """What the run was asked to do and what became of it. This is what makes an empty run
    readable: `assigned 4, handled 1, kept 2, needs_review 1` says something that
    `completed` never did."""
    cur.execute(
        """
        SELECT status, count(*) FROM tailoring_candidates WHERE run_id = %s GROUP BY status
        """,
        (run_id,),
    )
    counts = dict(cur.fetchall())
    return {
        "assigned": sum(counts.values()),
        "handled": counts.get(HANDLED, 0),
        "kept": counts.get(KEPT, 0),
        "skipped": counts.get(SKIPPED, 0),
        "needs_review": counts.get(NEEDS_REVIEW, 0),
        "unfinished": counts.get(PENDING, 0) + counts.get(ACTIVE, 0),
    }
