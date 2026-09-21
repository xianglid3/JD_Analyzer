"""The skill graph, learned once per term and then remembered.

`skill_graph.IMPLIES` is hand-written, so it only knows the relations somebody thought to
type. Nobody was ever going to add `gesture recognition -> computer vision` by hand, and a
missing edge is a gap the user does not actually have.

This asks the model instead, once per term, and stores the answer. The cache *is* the graph:
after the first sighting a term costs nothing, and the same term always resolves the same
way, so scoring stays reproducible. The hand-written table stays as the seed layer — it is
still the fastest, and it is what the tests pin.

Two questions get asked about every term, because matching needs both directions:

    implies       what does experience with X demonstrate?   (X is the specific one)
    evidenced_by  what would demonstrate X?                  (X is the general one)

and separately, which of `implies` is safe to *write into a resume* — a much stricter
question than what counts for scoring. See `skill_graph.REWRITE_IMPLIES` for why.
"""

import json
import logging
import threading

from services.match import normalize_skill

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
MAX_TERMS_PER_CALL = 25
MAX_RELATIONS_PER_TERM = 8
MAX_TERM_CHARS = 60

# process-wide, because a relation is a fact about the world rather than about one user.
# Guarded by a lock: the tailoring worker is a thread, and so is every request.
_lock = threading.RLock()
_cache = {"implies": {}, "rewrite": {}, "evidenced_by": {}}
_known = set()          # terms already looked up, including ones with no relations at all
_loaded = False


PROMPT = """You map technical skills onto the more general skills they demonstrate.

For each term you are given, return:
- "implies": general skills that experience with the term genuinely demonstrates. Tailwind implies css. Flask implies python and backend. Pytest implies testing. Be willing to include the obvious ones. Use [] when the term is already general.
- "evidenced_by": specific technologies, tools, or frameworks whose use would demonstrate the term. For css: tailwind, scss, bootstrap. For testing: pytest, jest, junit. Use [] when nothing more specific exists. This is the exact mirror of "implies" and the same direction rule applies: every entry must be MORE specific than the term, never more general. Kafka is not evidenced_by "messaging", and postgresql is not evidenced_by "database" — those are the categories they belong to, not evidence of them. A named tool usually has nothing more specific, so [] is the common answer.
- "writeable": the subset of "implies" that is fair to state outright in a resume bullet. This is much stricter. Tailwind -> css is fair, because using Tailwind IS writing css. React -> javascript is NOT fair to write, even though it is true for scoring, because a resume claiming "JavaScript" asserts something the person did not say. When unsure, leave it out.

Rules:
- Use short canonical lowercase names ("postgresql" not "PostgreSQL", "cpp" not "C++").
- At most 8 entries per list.
- Only real, defensible relations. Do not invent tools.
- Never point from general to specific in "implies": python does NOT imply flask.
- Never point from specific to general in "evidenced_by": kafka is NOT evidenced_by messaging.
- The two lists must not overlap. If the term implies X, then X cannot be evidence of the term.
- Return ONLY valid JSON of this shape:
{"terms": [{"term": "<the term>", "implies": ["..."], "evidenced_by": ["..."], "writeable": ["..."]}]}"""


def _clean(names):
    seen, kept = [], set()
    for name in names or []:
        if not isinstance(name, str) or not name.strip():
            continue
        canonical = normalize_skill(name)
        if canonical and canonical not in kept and len(canonical) <= MAX_TERM_CHARS:
            kept.add(canonical)
            seen.append(canonical)
    return seen[:MAX_RELATIONS_PER_TERM]


def load(cur):
    """Read every cached relation into memory. Cheap, and only happens once per process."""
    global _loaded
    with _lock:
        cur.execute("SELECT specific, general, rewriteable FROM skill_relations")
        implies, rewrite, evidenced = {}, {}, {}
        for specific, general, rewriteable in cur.fetchall():
            implies.setdefault(specific, set()).add(general)
            evidenced.setdefault(general, set()).add(specific)
            if rewriteable:
                rewrite.setdefault(specific, set()).add(general)

        cur.execute("SELECT term FROM skill_relation_lookups")
        _known.clear()
        _known.update(row[0] for row in cur.fetchall())

        _cache["implies"], _cache["rewrite"], _cache["evidenced_by"] = implies, rewrite, evidenced
        _loaded = True


def ensure_loaded(cur):
    if not _loaded:
        load(cur)


def reset():
    """Drop the in-memory view. For tests, and after a bulk edit of the table."""
    global _loaded
    with _lock:
        _cache["implies"], _cache["rewrite"], _cache["evidenced_by"] = {}, {}, {}
        _known.clear()
        _loaded = False


def cached_implies(term):
    with _lock:
        return sorted(_cache["implies"].get(term, ()))


def cached_rewrite_implies(term):
    with _lock:
        return sorted(_cache["rewrite"].get(term, ()))


def cached_evidenced_by(term):
    with _lock:
        return sorted(_cache["evidenced_by"].get(term, ()))


def unknown_terms(terms, seed_known):
    """Terms nobody has asked the model about, and the seed table doesn't already cover."""
    with _lock:
        return [
            term for term in dict.fromkeys(normalize_skill(t) for t in terms if t)
            if term and term not in _known and term not in seed_known
            and len(term) <= MAX_TERM_CHARS
        ]


def seed_generals(term):
    """What the hand-written graph already says this term is a kind of."""
    from services.skill_graph import seed_implied_by

    return seed_implied_by(term)


def _ask(terms, complete, budget=None):
    response = complete(
        [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps({"terms": terms})},
        ],
        budget=budget,
    )
    data = json.loads(response.choices[0].message.content)
    answers = {}
    for item in data.get("terms") or []:
        if not isinstance(item, dict):
            continue
        term = normalize_skill(item.get("term") or "")
        if term not in terms:
            continue
        implies = _clean(item.get("implies"))
        # Everything this term is already a *kind of*, in this answer and in the seed graph.
        # Nothing in there can also be evidence OF the term — that inverts the relation, and
        # `_persist` stores `evidenced_by` as an edge pointing the other way. Asked what
        # demonstrates Kafka, a model answers "messaging"; stored unchecked, that makes an
        # encrypted-chat bullet count as Kafka experience.
        generals = set(implies) | set(seed_generals(term))
        answers[term] = {
            "implies": implies,
            "evidenced_by": [
                name for name in _clean(item.get("evidenced_by"))
                if name != term and name not in generals
            ],
            # a claim the model can't already justify as an implication is not writeable
            "writeable": [name for name in _clean(item.get("writeable")) if name in implies],
        }
    return answers


def _without_contradictions(rows):
    """Drop edges that assert the opposite of something already believed.

    An edge (S, G) says S is a kind of G. If the seed table says the reverse, or the same
    batch does, one of the two is wrong and neither is worth trusting — a graph that holds
    both directions makes every term evidence of every other term within three hops.

    The seed table wins outright, because it was written by a person.
    """
    from services.skill_graph import seed_implied_by

    asserted = {(specific, general) for specific, general, _ in rows}
    kept = []
    for specific, general, rewriteable in rows:
        if specific in seed_implied_by(general):
            logger.info("dropping learned edge %s -> %s: the seed table says the reverse",
                        specific, general)
            continue
        if (general, specific) in asserted:
            logger.info("dropping learned edge %s -> %s: this answer asserts both directions",
                        specific, general)
            continue
        kept.append((specific, general, rewriteable))
    return kept


def _persist(cur, terms, answers):
    rows = []
    for term in terms:
        answer = answers.get(term)
        if not answer:
            continue
        writeable = set(answer["writeable"])
        for general in answer["implies"]:
            if general != term:
                rows.append((term, general, general in writeable))
        # the reverse direction is stored as the same edge, read the other way round
        for specific in answer["evidenced_by"]:
            if specific != term:
                rows.append((specific, term, False))

    rows = _without_contradictions(rows)

    if rows:
        cur.executemany(
            """
            INSERT INTO skill_relations (specific, general, rewriteable, source)
            VALUES (%s, %s, %s, 'model')
            ON CONFLICT (specific, general) DO UPDATE
              SET rewriteable = skill_relations.rewriteable OR EXCLUDED.rewriteable
            """,
            rows,
        )
    # recorded even when the model returned nothing, so a genuinely unrelated term is asked
    # about once rather than on every scoring pass forever
    cur.executemany(
        "INSERT INTO skill_relation_lookups (term) VALUES (%s) ON CONFLICT (term) DO NOTHING",
        [(term,) for term in terms],
    )


def resolve(terms, complete=None, seed_known=frozenset(), get_cursor=None, user_id=None):
    """Make sure every term has been looked up, then refresh the in-memory view.

    **Takes no caller cursor on purpose.** This makes a model call that can take 30 seconds,
    and it used to run inside the caller's open transaction — on `confirm_job_draft` that
    meant holding a pooled connection *and* a `FOR UPDATE` lock on the draft row for the
    whole call, out of a pool of ten. It now borrows its own connections, briefly, on either
    side of the call, so the model call itself holds nothing.

    One model call covers the whole batch. Never raises: a failed lookup means scoring falls
    back to the seed table, which is exactly the old behaviour, and is far better than
    failing a match because an enrichment call timed out.

    `user_id` pays for the lookup. The edge it learns is cached globally and every later user
    reads it for free, which is a little unfair on whoever asks first — but the alternative is
    a paid call charged to nobody, and an unmetered path is exactly what the budget exists to
    close. A user out of budget still scores: the reservation is refused, the lookup is
    skipped, and the seed table answers.
    """
    if get_cursor is None:
        from db import get_cursor as get_cursor

    try:
        with get_cursor() as cur:
            ensure_loaded(cur)
    except Exception:
        logger.exception("could not read the skill relation cache")
        return 0

    pending = unknown_terms(terms, seed_known)[:MAX_TERMS_PER_CALL]
    if not pending:
        return 0

    if complete is None:
        from services.openai_services import complete_json
        complete = complete_json

    # no connection is held across this call
    try:
        from services.usage import budget as make_budget

        answers = _ask(
            pending, complete,
            budget=make_budget(user_id, "skill_relations") if user_id else None,
        )
    except Exception:
        logger.exception("could not resolve skill relations for %s", pending)
        return 0

    try:
        with get_cursor(commit=True) as cur:
            _persist(cur, pending, answers)
        with get_cursor() as cur:
            load(cur)
    except Exception:
        logger.exception("could not store skill relations for %s", pending)
        return 0

    return len(pending)


def every_known_name():
    """Every skill name the cache has seen, either side of a relation.

    `claim_check` uses this to spot the technologies a bullet names, so a learned skill that
    never reaches it would be invisible to the claim checker.
    """
    with _lock:
        names = set(_cache["implies"]) | set(_cache["evidenced_by"])
        for generals in _cache["implies"].values():
            names.update(generals)
        return names


def approved_rewrites(cur, user_id):
    """The learned edges this user has agreed may be written into their resume.

    Returned as a set of `(specific, general)` pairs, which is what
    `skill_graph.rewrite_implied_by` takes. Empty means only the hand-written table applies,
    and that is the correct default for a brand-new user — a learned edge starts inert.
    """
    cur.execute(
        """
        SELECT specific, general FROM skill_rewrite_approvals
        WHERE user_id = %s AND approved
        """,
        (user_id,),
    )
    return {(row[0], row[1]) for row in cur.fetchall()}


def record_rewrite_decision(cur, user_id, specific, general, approved):
    """Remember that this user did or did not allow one learned claim."""
    cur.execute(
        """
        INSERT INTO skill_rewrite_approvals (user_id, specific, general, approved)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, specific, general)
        DO UPDATE SET approved = EXCLUDED.approved, created_at = now()
        """,
        (user_id, normalize_skill(specific), normalize_skill(general), bool(approved)),
    )
