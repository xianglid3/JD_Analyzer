"""What model calls cost, and a daily cap per user that actually holds.

Lives in Postgres rather than the rate limiter: the limiter's counters are per-process and
vanish on restart, which is fine for bursts and useless for money.

**Reserve before you spend.** The old shape was read-then-call-then-record: two requests could
both read the same total, both decide there was room, and both spend. A SUM() cannot enforce a
limit, because nothing serializes the readers. So the ceiling lives in `llm_daily_budgets`,
one row per user per day, and reserving takes a lock on that row:

    reserve()  → row locked, spent + reserved + this call's ceiling checked against the cap
    the call   → made outside any transaction, holding no connection
    finalize() → reservation released, real cost recorded

The reservation is priced at the call's *ceiling*, which is why every paid call passes a
`max_tokens`. Without one there is no ceiling to reserve and the cap is a suggestion.

A call that fails after its tokens were sent is charged, not forgiven: `outcome='error'` keeps
the reserved amount as spend when the provider may well have billed it, and `'unknown'` marks
the case where usage never came back at all.
"""

import logging
import os
import time
from contextlib import contextmanager

from config import env_float
from db import get_cursor
from services.openai_services import PRICE_PER_MTOK, usd

logger = logging.getLogger(__name__)

DAILY_LIMIT_USD = env_float("LLM_DAILY_USD", 1.00)
# The per-user cap bounds one account. Accounts are free and signup is open, so it does not
# bound the bill — a thousand accounts is a thousand times the per-user cap. This is the
# number that protects the card, and it is deliberately not a multiple of anything: it is what
# you are willing to lose in a day.
GLOBAL_DAILY_LIMIT_USD = env_float("LLM_GLOBAL_DAILY_USD", 10.00)

# What one call of each kind may generate. This is the reservation price, so it has to be a
# real ceiling passed to the provider — not an estimate of the typical case.
MAX_OUTPUT_TOKENS = {
    "job_analysis": 3000,        # the posting, its skills, and the long no-BS translation
    "resume_parse": 1000,
    "resume_upload": 1000,
    "resume_structure": 6000,    # every entry and bullet of a resume
    "skill_relations": 1500,
    "tailoring_step": 1500,
    "default": 2000,
}
# How large a prompt we are willing to promise for. Prompts are bounded upstream (5 MB upload
# limit, 10k-character postings), so this only has to be a number no real call exceeds.
MAX_PROMPT_TOKENS = 40_000


class QuotaExceeded(Exception):
    """The user has spent their allowance for the day."""


class SystemQuotaExceeded(QuotaExceeded):
    """Everyone together has spent the day's allowance.

    A subclass so every existing handler keeps working: callers that refuse on QuotaExceeded
    refuse on this too. It is separate because the user did nothing wrong and the message
    should not blame them.
    """


def ceiling_for(kind):
    return MAX_OUTPUT_TOKENS.get(kind, MAX_OUTPUT_TOKENS["default"])


def ceiling_cost(kind):
    """The most one call of this kind can cost, which is what gets reserved."""
    return usd(MAX_PROMPT_TOKENS, ceiling_for(kind))


def spent_today(cur, user_id):
    """What the day has cost so far, in-flight reservations included."""
    cur.execute(
        """
        SELECT COALESCE(spent_usd, 0) + COALESCE(reserved_usd, 0)
        FROM llm_daily_budgets WHERE user_id = %s AND budget_date = current_date
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def spent_today_globally(cur):
    cur.execute(
        """
        SELECT COALESCE(spent_usd, 0) + COALESCE(reserved_usd, 0)
        FROM llm_global_budget WHERE budget_date = current_date
        """,
    )
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def check_quota(user_id):
    """A cheap look before doing expensive setup. Not the enforcement point — `reserve` is.

    Kept because refusing early is kinder than refusing after a run row exists, but it is
    advisory: two callers can pass this at the same moment and only one will get a
    reservation.
    """
    with get_cursor() as cur:
        spent = spent_today(cur, user_id)
        system = spent_today_globally(cur)
    if system >= GLOBAL_DAILY_LIMIT_USD:
        raise SystemQuotaExceeded(
            "the service has reached its daily AI budget — try again tomorrow"
        )
    if spent >= DAILY_LIMIT_USD:
        raise QuotaExceeded(f"${spent:.2f} of ${DAILY_LIMIT_USD:.2f} used today")
    return spent


def reserve(user_id, kind, model, run_id=None):
    """Claim this call's maximum cost, or raise. Returns the reservation.

    The INSERT ... ON CONFLICT DO UPDATE takes a row lock, so concurrent reservations queue
    behind each other instead of reading the same stale total. The guard is on the *updated*
    row, so a second caller sees the first one's reservation.
    """
    price = ceiling_cost(kind)

    # Checked here rather than left to the guards below, because the first call of a day takes
    # the INSERT branch — which has no ON CONFLICT clause and therefore no guard on it. Without
    # this, a cap smaller than one call would let exactly one call through each morning.
    if price > GLOBAL_DAILY_LIMIT_USD:
        raise SystemQuotaExceeded(
            "the service has reached its daily AI budget — try again tomorrow"
        )
    if price > DAILY_LIMIT_USD:
        # nothing could ever satisfy this, and silently letting it through would make the cap
        # meaningless for exactly the most expensive call type
        raise QuotaExceeded(
            f"one {kind} call can cost up to ${price:.2f}, over the ${DAILY_LIMIT_USD:.2f} daily cap"
        )

    with get_cursor(commit=True) as cur:
        # Global first, always, so two reservations can never take these two row locks in
        # opposite orders and deadlock. If the user guard fails below, raising rolls the whole
        # transaction back and the global reservation goes with it.
        cur.execute(
            """
            INSERT INTO llm_global_budget (budget_date, reserved_usd, spent_usd)
            VALUES (current_date, %s, 0)
            ON CONFLICT (budget_date) DO UPDATE
               SET reserved_usd = llm_global_budget.reserved_usd + EXCLUDED.reserved_usd,
                   updated_at = now()
             WHERE llm_global_budget.reserved_usd
                 + llm_global_budget.spent_usd
                 + EXCLUDED.reserved_usd <= %s
            RETURNING reserved_usd
            """,
            (price, GLOBAL_DAILY_LIMIT_USD),
        )
        if cur.fetchone() is None:
            raise SystemQuotaExceeded(
                "the service has reached its daily AI budget — try again tomorrow"
            )

        cur.execute(
            """
            INSERT INTO llm_daily_budgets (user_id, budget_date, reserved_usd, spent_usd)
            VALUES (%s, current_date, %s, 0)
            ON CONFLICT (user_id, budget_date) DO UPDATE
               SET reserved_usd = llm_daily_budgets.reserved_usd + EXCLUDED.reserved_usd,
                   updated_at = now()
             WHERE llm_daily_budgets.reserved_usd
                 + llm_daily_budgets.spent_usd
                 + EXCLUDED.reserved_usd <= %s
            RETURNING reserved_usd, spent_usd
            """,
            (user_id, price, DAILY_LIMIT_USD),
        )
        if cur.fetchone() is None:
            # the ON CONFLICT guard refused: the day is full
            spent = spent_today(cur, user_id)
            raise QuotaExceeded(f"${spent:.2f} of ${DAILY_LIMIT_USD:.2f} used today")

        cur.execute(
            """
            INSERT INTO llm_calls (user_id, kind, run_id, model, outcome, reserved_usd, cost_usd)
            VALUES (%s, %s, %s, %s, 'reserved', %s, 0)
            RETURNING id
            """,
            (user_id, kind, run_id, model, price),
        )
        call_id = cur.fetchone()[0]

    return {"id": call_id, "user_id": user_id, "kind": kind, "model": model,
            "run_id": run_id, "reserved": price}


def finalize(reservation, prompt_tokens, completion_tokens, latency_ms, outcome="ok"):
    """Release the reservation and record what the call really cost.

    Never raises. A failure here would lose the run or the request over bookkeeping, and the
    reservation is reconciled by `release_stale_reservations` if this never runs at all.
    """
    if reservation is None:
        return
    try:
        cost = (usd(prompt_tokens, completion_tokens) if outcome in ("ok", "error")
                # the tokens went out but we never learned how many: charge the ceiling
                else reservation["reserved"])
        with get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE llm_calls
                   SET prompt_tokens = %s, completion_tokens = %s, cost_usd = %s,
                       latency_ms = %s, outcome = %s
                 WHERE id = %s AND outcome = 'reserved'
                """,
                (prompt_tokens, completion_tokens, cost, int(latency_ms), outcome,
                 reservation["id"]),
            )
            if cur.rowcount:
                _settle(cur, reservation, cost)
    except Exception:
        logger.exception("could not finalize llm reservation %s", reservation["id"])


def _settle(cur, reservation, cost):
    """Move money from reserved to spent, on both ledgers.

    Clamped at zero: a reservation released twice must not drive a total negative and hand out
    free calls.
    """
    cur.execute(
        """
        UPDATE llm_daily_budgets
           SET reserved_usd = GREATEST(reserved_usd - %s, 0),
               spent_usd = spent_usd + %s,
               updated_at = now()
         WHERE user_id = %s AND budget_date = current_date
        """,
        (reservation["reserved"], cost, reservation["user_id"]),
    )
    cur.execute(
        """
        UPDATE llm_global_budget
           SET reserved_usd = GREATEST(reserved_usd - %s, 0),
               spent_usd = spent_usd + %s,
               updated_at = now()
         WHERE budget_date = current_date
        """,
        (reservation["reserved"], cost),
    )


def release_stale_reservations(cur, older_than="15 minutes"):
    """Reservations whose caller died before finalizing.

    Charged at their ceiling rather than released free: the tokens may well have been sent,
    and a crash is not a reason to hand someone their budget back.
    """
    cur.execute(
        """
        UPDATE llm_calls
           SET outcome = 'unknown', cost_usd = reserved_usd
         WHERE outcome = 'reserved' AND created_at < now() - %s::interval
        RETURNING id, user_id, reserved_usd
        """,
        (older_than,),
    )
    stale = cur.fetchall()
    for _id, user_id, reserved in stale:
        _settle(cur, {"user_id": user_id, "reserved": reserved}, reserved)
    return [str(row[0]) for row in stale]


class Budget:
    """One user's permission to make one kind of paid call.

    Passed into the openai service functions so they can price and record a call without
    knowing who asked — the thing `recorder()` used to do, plus the reservation half.
    """

    def __init__(self, user_id, kind, model="gpt-4o-mini", run_id=None):
        self.user_id = user_id
        self.kind = kind
        self.model = model
        self.run_id = run_id

    @property
    def max_output_tokens(self):
        return ceiling_for(self.kind)

    @contextmanager
    def paid_call(self):
        """Reserve, yield, settle. The model call happens inside, holding no connection."""
        reservation = reserve(self.user_id, self.kind, self.model, self.run_id)
        started = time.perf_counter()
        record = {}
        try:
            yield record
        except Exception as exc:
            finalize(reservation, 0, 0, (time.perf_counter() - started) * 1000,
                     outcome=_outcome_for(exc))
            raise
        usage = record.get("usage")
        finalize(
            reservation,
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
            (time.perf_counter() - started) * 1000,
            outcome="ok" if usage else "unknown",
        )


def _outcome_for(exc):
    name = type(exc).__name__.lower()
    return "timeout" if "timeout" in name else "error"


def budget(user_id, kind, model="gpt-4o-mini", run_id=None):
    return Budget(user_id, kind, model=model, run_id=run_id)


def record(user_id, kind, model, prompt_tokens, completion_tokens, latency_ms,
           outcome="ok", run_id=None):
    """Log a call that was made without a reservation, and charge it to the day.

    Reserving is the better path — it is the one that can refuse. But anything that spends
    money has to reach the ledger, or the cap is enforced against a number that does not
    include it.
    """
    cost = usd(prompt_tokens, completion_tokens)
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
             cost, int(latency_ms), outcome),
        )
        charge(cur, user_id, cost)


def charge(cur, user_id, cost):
    """Add to today's spend. The ledger is what the cap is read from, so every path that
    spends has to pass through here."""
    cur.execute(
        """
        INSERT INTO llm_daily_budgets (user_id, budget_date, reserved_usd, spent_usd)
        VALUES (%s, current_date, 0, %s)
        ON CONFLICT (user_id, budget_date) DO UPDATE
           SET spent_usd = llm_daily_budgets.spent_usd + EXCLUDED.spent_usd,
               updated_at = now()
        """,
        (user_id, cost),
    )


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
