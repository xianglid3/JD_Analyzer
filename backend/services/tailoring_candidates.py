"""Who owns "is this run finished?".

Before this, nobody did. The planner handed the model a list of candidates, and any
plain-text reply ended the loop and wrote `completed`. A run that handled one of four and
stopped looked exactly like a run that genuinely had nothing to do, which is why empty runs
were impossible to diagnose (AE-02).

Now the assignment is state. The orchestrator hands out one candidate at a time, records what
happened to it, and refuses to call a run complete while any are still pending. The model's
opinion that it is done is a hint, not a decision.

    pending ─→ active ─→ handled       an edit or merge was recorded
              │       ├→ kept          the model read it and judged the original better
              │       ├→ skipped       no evidence could be retrieved, so there was nothing to do
              │       └→ needs_review  it failed grounding twice, or the run ran out of budget
              └→ waiting ─→ answer_ready ─→ active …   an ASK. `pending` is "its question is
                                       not filed yet", `waiting` is "filed, owed an answer",
                                       `answer_ready` is ordinary editing work again, and a
                                       dismissal ends it at `kept`. Each is named so none can
                                       be read as a decision about the bullet.

A candidate is owned by a BULLET or by a REQUIREMENT, never both. Recruiter-review work is
always the bullet's: deciding it from requirement evidence is what left a vague resume with
two reviewed bullets out of twelve. `show_in_bullet` stays requirement-owned, because the
skill the user affirmed is the point of that rewrite. `key()` is how either is addressed.

`kept` is deliberately separate from `handled`. Both are successful decisions, but "I improved
this" and "I judged this fine" are different facts about a run, and the ratio between them is
the only number that shows whether the decline is being used honestly or as a cheap exit.
"""

PENDING = "pending"
ACTIVE = "active"
WAITING = "waiting"
ANSWER_READY = "answer_ready"
HANDLED = "handled"
KEPT = "kept"
SKIPPED = "skipped"
NEEDS_REVIEW = "needs_review"

TERMINAL = (HANDLED, KEPT, SKIPPED, NEEDS_REVIEW)


def key(item):
    """How a candidate is addressed in memory. Never stored: `normalized` is a skill handle,
    and putting "bullet:<uuid>" in it would overload the column the tool boundary matches
    skills on."""
    bullet_id = item.get("bullet_id")
    return f"bullet:{bullet_id}" if bullet_id else (item.get("normalized") or "")


def create(cur, user_id, run_id, candidates):
    """Write the assignment down before the worker edits anything.

    Persisted rather than derived later, so a run can always be compared against what it was
    actually asked to do — including after a restart, when the plan would otherwise be
    recomputed from a resume that may since have changed.

    Positions are allocated here, under the run row's lock, from what is already stored. They
    used to be carried in on the item, computed from a freshly recomputed plan: two writers a
    few seconds apart could then choose the same number, and `ON CONFLICT (run_id, position)
    DO NOTHING` swallowed the loser without a trace. Identity is the bullet or the skill, so
    re-running the assignment on a resume inserts nothing twice and a position clash is now a
    bug that raises.
    """
    from services.match import normalize_skill

    # the run row, not the candidate rows: two workers allocating at once must queue, and
    # there may be no candidate rows yet to lock
    cur.execute("SELECT 1 FROM tailoring_runs WHERE id = %s FOR UPDATE", (run_id,))
    cur.execute(
        "SELECT COALESCE(max(position), -1) + 1 FROM tailoring_candidates WHERE run_id = %s",
        (run_id,),
    )
    position = cur.fetchone()[0]

    written = 0
    for item in candidates:
        bullet_id = item.get("bullet_id")
        requirement = None if bullet_id else item.get("requirement")
        normalized = None if bullet_id else normalize_skill(
            item.get("agent_label") or item.get("requirement") or ""
        )
        cur.execute(
            """
            SELECT position FROM tailoring_candidates
            WHERE run_id = %s AND (
                (%s::uuid IS NOT NULL AND bullet_id = %s::uuid)
                OR (%s::text IS NOT NULL AND normalized = %s::text)
            )
            """,
            (run_id, bullet_id, bullet_id, normalized, normalized),
        )
        existing = cur.fetchone()
        if existing:
            # Already assigned; a resume must not duplicate it — but it still carries its
            # stored position back, so anything ordering candidates in memory orders them the
            # same way on a resume as it did on the first attempt.
            item["position"] = existing[0]
            continue
        cur.execute(
            """
            INSERT INTO tailoring_candidates (
                run_id, user_id, position, requirement, normalized, bullet_id, action
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            # the posting's own wording for the user, the short handle for matching — a
            # candidate named by a whole sentence cannot be addressed reliably. Both are NULL
            # on a bullet-owned row, where the bullet owns the work and requirements are context.
            (run_id, user_id, position, requirement, normalized, bullet_id, item["action"]),
        )
        item["position"] = position
        position += 1
        written += 1
    return written


def load(cur, run_id):
    cur.execute(
        """
        SELECT c.id, c.position, c.requirement, c.normalized, c.action, c.status,
               c.outcome, c.attempts, c.bullet_id, b.text, e.title, e.organization
        FROM tailoring_candidates AS c
        LEFT JOIN resume_bullets AS b ON b.id = c.bullet_id
        LEFT JOIN resume_entries AS e ON e.id = b.entry_id
        WHERE c.run_id = %s ORDER BY c.position
        """,
        (run_id,),
    )
    return [
        {
            "id": str(row[0]), "position": row[1], "requirement": row[2],
            "normalized": row[3], "action": row[4], "status": row[5],
            "outcome": row[6], "attempts": row[7],
            "bullet_id": str(row[8]) if row[8] else None,
            "bullet_text": row[9],
            # What the user sees where a requirement-owned candidate shows its requirement.
            # The entry it came from is the honest label for work the bullet owns.
            "label": row[2] or " — ".join(p for p in (row[10], row[11]) if p) or "Resume bullet",
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
            WHERE run_id = %s AND (
                -- an ASK is not editable work until its answer is in: before that it is
                -- either waiting to be asked or waiting to be answered, and handing it to
                -- the editor would mean editing a bullet on a fact nobody has given yet
                (action <> 'ask' AND status IN ('pending', 'active'))
                OR (action = 'ask' AND status IN ('answer_ready', 'active'))
            )
            ORDER BY position
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, position, requirement, normalized, action, attempts, bullet_id
        """,
        (run_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]), "position": row[1], "requirement": row[2],
        "normalized": row[3], "action": row[4], "attempts": row[5],
        "bullet_id": str(row[6]) if row[6] else None,
    }


def resolve(cur, run_id, candidate_id, status, outcome=None):
    """Close out one candidate, by its own id.

    This used to take the normalized skill, which is why a bullet-owned candidate had nowhere
    to go: it has no skill, and inventing one — "bullet:<uuid>" in the `normalized` column —
    would have overloaded the column the tool boundary matches real skills on.
    """
    if not candidate_id:
        return []
    cur.execute(
        """
        UPDATE tailoring_candidates
        SET status = %s, outcome = %s, resolved_at = now()
        WHERE run_id = %s AND id = %s AND status NOT IN ('handled', 'kept', 'skipped')
        RETURNING id
        """,
        (status, outcome, run_id, candidate_id),
    )
    return [str(row[0]) for row in cur.fetchall()]


def await_answer(cur, run_id, candidate_id):
    """An ASK whose question is now filed. Not a decision and not a failure: the run is
    parked, and the answer puts this same candidate back in the editable queue."""
    return resolve(cur, run_id, candidate_id, WAITING)


def make_answerable(cur, run_id):
    """Settle every waiting candidate whose question is no longer pending.

    An answer makes it `answer_ready` — ordinary editing work, with a fact to use. A dismissal
    ends it at `kept`: they were asked, they declined, and the bullet stands as written. The
    two must not collapse into one state, or a dismissal would send the editor to rewrite a
    bullet on nothing.
    """
    cur.execute(
        """
        UPDATE tailoring_candidates AS c SET status = 'answer_ready'
        WHERE c.run_id = %s AND c.status = 'waiting'
          AND EXISTS (
            SELECT 1 FROM tailoring_detail_requests AS q
            WHERE q.run_id = c.run_id AND q.bullet_id = c.bullet_id
              AND q.status = 'answered' AND q.answer IS NOT NULL
          )
          AND NOT EXISTS (
            SELECT 1 FROM tailoring_detail_requests AS q
            WHERE q.run_id = c.run_id AND q.bullet_id = c.bullet_id
              AND q.status = 'pending'
          )
        RETURNING id
        """,
        (run_id,),
    )
    ready = [str(row[0]) for row in cur.fetchall()]
    cur.execute(
        """
        UPDATE tailoring_candidates AS c
        SET status = 'kept', resolved_at = now(),
            outcome = 'you chose not to answer, so the bullet is unchanged'
        WHERE c.run_id = %s AND c.status = 'waiting'
          AND NOT EXISTS (
            SELECT 1 FROM tailoring_detail_requests AS q
            WHERE q.run_id = c.run_id AND q.bullet_id = c.bullet_id AND q.status = 'pending'
          )
          AND NOT EXISTS (
            SELECT 1 FROM tailoring_detail_requests AS q
            WHERE q.run_id = c.run_id AND q.bullet_id = c.bullet_id
              AND q.status = 'answered' AND q.answer IS NOT NULL
          )
        """,
        (run_id,),
    )
    return ready


def reopen_for_resume(cur, run_id):
    """Hand `needs_review` work back to a resumed run.

    `needs_review` is not a decision — it means the budget ran out, or the editor produced
    nothing, with the work still owed. `claim_next` deliberately will not take it *within* a
    run, or a candidate that failed would be picked up again and burn every remaining step on
    the same refusal. A new attempt is a different matter: this is what makes "owed to a human"
    resumable rather than terminal.
    """
    cur.execute(
        """
        UPDATE tailoring_candidates SET status = 'pending', resolved_at = NULL
        WHERE run_id = %s AND status = 'needs_review'
        RETURNING id
        """,
        (run_id,),
    )
    return [str(row[0]) for row in cur.fetchall()]


def is_finished(cur, run_id, candidate_id):
    """True when this candidate already reached a terminal state.

    The model re-sends its whole batch of candidates every step, so one that succeeded on an
    earlier step is offered again on the next one. Without this the second attempt is written
    as a second proposal, and the user reviews the same rewrite twice.

    `needs_review` is deliberately not counted. It means the run ran out of budget or the
    action failed twice — the work is still owed, and a resumed run must be able to pick it
    back up. Only `handled`, `kept` and `skipped` are decisions, which is the same line
    `resolve` draws when it declines to reopen a candidate.
    """
    if not candidate_id:
        return False
    cur.execute(
        """
        SELECT 1 FROM tailoring_candidates
        WHERE run_id = %s AND id = %s AND status IN %s
        """,
        (run_id, candidate_id, (HANDLED, KEPT, SKIPPED)),
    )
    return cur.fetchone() is not None


def record_attempt(cur, run_id, candidate_id):
    if not candidate_id:
        return 0
    cur.execute(
        """
        UPDATE tailoring_candidates SET attempts = attempts + 1
        WHERE run_id = %s AND id = %s
        RETURNING attempts
        """,
        (run_id, candidate_id),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def unfinished(cur, run_id):
    """Candidates still owed work. An empty list is the only thing that justifies
    `completed`."""
    # `needs_review` counts as owed. It is terminal for the attempt that set it and unfinished
    # for the run: `reopen_for_resume` hands it straight back, so calling it settled here would
    # make a resumable run look finished to everything that asks this question.
    return [item["label"] for item in load(cur, run_id)
            if item["status"] in (PENDING, ACTIVE, WAITING, ANSWER_READY, NEEDS_REVIEW)]


def close_out(cur, run_id, outcome, untouched=None):
    """Give every still-unfinished candidate a terminal state.

    Called when the loop stops for a reason that is not "everything was handled" — the step
    limit, the quota, a model failure. Without it those candidates stay `pending` forever and
    the run reads as though the work is still coming.

    `untouched` is the outcome for a candidate that never got a turn at all (`attempts = 0`).
    "this failed twice" and "the run ran out of steps before reaching it" are different facts
    about a candidate, and only the second one is still worth retrying as-is.
    """
    # One statement, as it was: reading the list first and updating second left a window in
    # which a candidate could finish in between, and reported it as owed when it was not.
    cur.execute(
        """
        UPDATE tailoring_candidates AS c
        SET status = 'needs_review', resolved_at = now(),
            outcome = CASE WHEN c.attempts = 0 AND %s::text IS NOT NULL
                           THEN %s::text ELSE %s::text END
        FROM (
            SELECT c2.id,
                   coalesce(c2.requirement,
                            nullif(concat_ws(' — ', e.title, e.organization), ''),
                            'Resume bullet') AS label
            FROM tailoring_candidates AS c2
            LEFT JOIN resume_bullets AS b ON b.id = c2.bullet_id
            LEFT JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE c2.run_id = %s
              AND c2.status IN ('pending', 'active', 'waiting', 'answer_ready')
        ) AS owed
        WHERE c.id = owed.id
        RETURNING owed.label
        """,
        (untouched, untouched, outcome, run_id),
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
        "waiting": counts.get(WAITING, 0),
        "unfinished": (counts.get(PENDING, 0) + counts.get(ACTIVE, 0)
                       + counts.get(WAITING, 0) + counts.get(ANSWER_READY, 0)),
    }
