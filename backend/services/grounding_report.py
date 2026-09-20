"""How often the grounding checks actually fire.

The checks in `tailoring_agent` reject an edit the model isn't entitled to make, and every
rejection is already written to `tool_calls` with the reason. Nothing read those rows back,
so the guarantee was a design claim with no number behind it. This counts them.

What it cannot tell you is whether a rejection was *correct* — a rewrite saying "deployed with
Docker" from evidence saying "containerized the service" is refused, and that refusal is
arguably wrong. `samples()` exists to pull those out for a human to read.
"""

import re

# error_message → what kind of failure it was. Ordered: first match wins.
#
# These patterns are coupled to the wording in `tailoring_agent`. When a rejection message
# changes there and nothing changes here, the rejection silently reclassifies as "other" and
# the report stops describing the thing it exists to measure. `test_every_rejection_is_classified`
# pins that coupling.
REASONS = [
    ("uncited_bullet", re.compile(
        r"(was not returned by a search in this run|call search_resume first)")),
    ("not_your_bullet", re.compile(r"(is|are) not part of your resume")),
    ("unsupported_claim", re.compile(r"does not appear in the evidence you cited")),
    ("cosmetic_rewrite", re.compile(r"only changes phrasing")),
    ("ownership_inflation", re.compile(r"claims more of the work")),
    ("tense_regression", re.compile(r"the work is still going on")),
    ("unchanged_text", re.compile(r"is the same as")),
    ("detail_loss", re.compile(r"(removes too much|removes supported detail|removes a measurable result)")),
    ("unapproved_target", re.compile(
        r"(not an approved (rewrite|tailoring) target|does not name an approved target bullet)")),
    ("unapproved_requirement", re.compile(r"not an approved tailoring candidate")),
    # the fit engine owns these decisions; the model asked to make one itself
    ("agent_overreach", re.compile(r"gaps are determined by the fit engine")),
    ("duplicate_question", re.compile(r"already (asked|waiting on an answer) about this bullet")),
    ("needs_user_answer", re.compile(
        r"(ask the user for the missing detail|confirm this requirement with the user)")),
    ("invalid_merge", re.compile(
        r"(merge requires|merged bullets? must|merge mostly concatenates|merge between|"
        r"must exactly match the bullets being merged|come from the same resume entry|"
        r"merged bullet must belong to your resume)")),
    ("contradicted_gap", re.compile(r"returned \d+ bullet")),
    ("no_evidence_cited", re.compile(r"evidence_bullet_ids is required")),
    ("wrong_citation_scope", re.compile(r"(may cite only that bullet|may cite at most)")),
    ("bad_bullet_id", re.compile(r"must be a valid UUID")),
    ("missing_fields", re.compile(r"(is required|are required|are both required)")),
    ("too_long", re.compile(r"characters or fewer")),
    ("bad_arguments", re.compile(r"(not valid JSON|must be an object|must be a list)")),
    ("malformed_question", re.compile(r"must be one line of plain text")),
    ("unknown_tool", re.compile(r"^unknown tool")),
]


def classify(error_message):
    """The rejection reason, as a stable label to group by."""
    if not error_message:
        return "none"
    for label, pattern in REASONS:
        if pattern.search(error_message):
            return label
    return "other"


def report(cur, days=30, user_id=None):
    """Attempts, rejections by reason, and what runs produced, over the last N days."""
    window = "r.started_at >= now() - make_interval(days => %(days)s)"
    scope = " AND r.user_id = %(user)s" if user_id else ""
    params = {"days": days, "user": user_id}

    cur.execute(
        f"""
        SELECT c.tool_name, c.status, count(*)
        FROM tool_calls AS c
        JOIN tailoring_runs AS r ON r.id = c.run_id
        WHERE {window}{scope}
        GROUP BY c.tool_name, c.status
        """,
        params,
    )
    by_tool = {}
    for tool, status, count in cur.fetchall():
        entry = by_tool.setdefault(tool, {"attempts": 0, "completed": 0, "failed": 0})
        entry["attempts"] += count
        entry[status] += count
    for entry in by_tool.values():
        entry["rejection_rate"] = round(entry["failed"] / entry["attempts"] * 100, 1) if entry["attempts"] else 0.0

    cur.execute(
        f"""
        SELECT c.tool_name, c.error_message
        FROM tool_calls AS c
        JOIN tailoring_runs AS r ON r.id = c.run_id
        WHERE c.status = 'failed' AND {window}{scope}
        """,
        params,
    )
    reasons = {}
    for tool, message in cur.fetchall():
        key = (tool, classify(message))
        reasons[key] = reasons.get(key, 0) + 1
    by_reason = sorted(
        ({"tool": tool, "reason": reason, "count": count} for (tool, reason), count in reasons.items()),
        key=lambda row: (-row["count"], row["tool"], row["reason"]),
    )

    cur.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE r.status = 'completed'),
               count(*) FILTER (WHERE r.status = 'waiting_for_user'),
               count(*) FILTER (WHERE r.status = 'limit_reached'),
               count(*) FILTER (WHERE r.status = 'failed'),
               COALESCE(avg(r.steps_used), 0),
               COALESCE(sum(e.edits), 0),
               COALESCE(sum(r.gap_count), 0),
               COALESCE(sum(e.accepted), 0)
        FROM tailoring_runs AS r
        LEFT JOIN (
            SELECT run_id, count(*) AS edits,
                   count(*) FILTER (WHERE status = 'accepted') AS accepted
            FROM proposed_edits GROUP BY run_id
        ) AS e ON e.run_id = r.id
        WHERE {window}{scope}
        """,
        params,
    )
    runs, completed, waiting, limited, failed, avg_steps, edits, gap_count, accepted = cur.fetchone()

    return {
        "days": days,
        "runs": {
            "total": runs,
            "completed": completed,
            "waiting_for_user": waiting,
            "limit_reached": limited,
            "failed": failed,
            "avg_steps": round(float(avg_steps), 1),
        },
        "output": {
            "edits_proposed": int(edits),
            "edits_accepted": int(accepted),
            "gaps_flagged": int(gap_count),
            "edits_per_run": round(int(edits) / runs, 2) if runs else 0.0,
        },
        "by_tool": by_tool,
        "by_reason": by_reason,
    }


def samples(cur, limit=30, days=30, reason=None):
    """Rejections with the text that caused them, for the hand-check.

    The counts say the checker fires. Only reading these says it fires *correctly*.
    """
    cur.execute(
        """
        SELECT c.tool_name, c.error_message, c.arguments, c.created_at
        FROM tool_calls AS c
        JOIN tailoring_runs AS r ON r.id = c.run_id
        WHERE c.status = 'failed' AND r.started_at >= now() - make_interval(days => %s)
        ORDER BY c.created_at DESC
        LIMIT %s
        """,
        (days, limit * 4 if reason else limit),
    )
    rows = [
        {
            "tool": tool,
            "reason": classify(message),
            "error": message,
            "requirement": (arguments or {}).get("requirement") or (arguments or {}).get("query"),
            "proposed_text": (arguments or {}).get("proposed_text"),
            "at": created_at.isoformat(),
        }
        for tool, message, arguments, created_at in cur.fetchall()
    ]
    if reason:
        rows = [row for row in rows if row["reason"] == reason]
    return rows[:limit]
