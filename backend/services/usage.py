"""What model calls cost, and a daily cap per user.

Lives in Postgres rather than the rate limiter: the limiter's counters are per-process and
vanish on restart, which is fine for bursts and useless for money.
"""

import os

from db import get_cursor
from services.openai_services import usd

DAILY_LIMIT_USD = float(os.environ.get("LLM_DAILY_USD", "1.00"))


class QuotaExceeded(Exception):
    """The user has spent their allowance for the day."""


def spent_today(cur, user_id):
    cur.execute(
        """
        SELECT COALESCE(sum(cost_usd), 0)
        FROM llm_calls
        WHERE user_id = %s AND created_at >= date_trunc('day', now())
        """,
        (user_id,),
    )
    return float(cur.fetchone()[0])


def check_quota(user_id):
    """Call before spending. Raises when the day's allowance is gone."""
    with get_cursor() as cur:
        spent = spent_today(cur, user_id)
    if spent >= DAILY_LIMIT_USD:
        raise QuotaExceeded(f"${spent:.2f} of ${DAILY_LIMIT_USD:.2f} used today")
    return spent


def record(user_id, kind, model, prompt_tokens, completion_tokens, latency_ms,
           outcome="ok", run_id=None):
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO llm_calls (
                user_id, kind, run_id, model, prompt_tokens, completion_tokens,
                cost_usd, latency_ms, outcome
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, kind, run_id, model, prompt_tokens, completion_tokens,
             usd(prompt_tokens, completion_tokens), int(latency_ms), outcome),
        )


def recorder(user_id, kind, run_id=None):
    """A callback for the openai service, which knows tokens but not who asked."""
    def on_usage(model, prompt_tokens, completion_tokens, latency_ms, outcome="ok"):
        record(user_id, kind, model, prompt_tokens, completion_tokens, latency_ms,
               outcome, run_id)
    return on_usage


def report(cur, days=30):
    """Spend per user and latency percentiles per kind, over the last N days."""
    cur.execute(
        """
        SELECT u.username, count(*), sum(c.cost_usd), sum(c.prompt_tokens + c.completion_tokens)
        FROM llm_calls AS c JOIN users AS u ON u.id = c.user_id
        WHERE c.created_at >= now() - make_interval(days => %s)
        GROUP BY u.username ORDER BY sum(c.cost_usd) DESC
        """,
        (days,),
    )
    by_user = [
        {"username": r[0], "calls": r[1], "cost_usd": float(r[2]), "tokens": int(r[3])}
        for r in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT kind, count(*),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),
               sum(cost_usd)
        FROM llm_calls
        WHERE created_at >= now() - make_interval(days => %s)
        GROUP BY kind ORDER BY count(*) DESC
        """,
        (days,),
    )
    by_kind = [
        {"kind": r[0], "calls": r[1], "p50_ms": float(r[2]), "p95_ms": float(r[3]),
         "cost_usd": float(r[4])}
        for r in cur.fetchall()
    ]

    return {"days": days, "by_user": by_user, "by_kind": by_kind}
