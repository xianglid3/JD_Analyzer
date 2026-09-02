"""Search a user's resume evidence — what the tailoring agent calls to find support for a
requirement. Expands through aliases and the skill graph so a search for CSS reaches a
Tailwind bullet, and scopes everything to one user."""

import re

from services.match import ALIASES, normalize_skill
from services.skill_graph import evidence_for

MAX_RESULTS = 10
MAX_QUERY_CHARS = 200

# canonical form → every spelling that maps to it ("kubernetes" → {"k8s", "kubernetes"})
_VARIANTS = {}
for _variant, _canonical in ALIASES.items():
    _VARIANTS.setdefault(_canonical, set()).add(_variant)

WORD = re.compile(r"[A-Za-z0-9+#./_-]+")


def expand_query(query):
    """The query, then its aliases, then skills that imply it. Raw phrase stays first."""
    query = query.strip()
    if not query:
        return []

    terms = [query]
    canonical = normalize_skill(query)
    if canonical != query.strip().lower():
        terms.append(canonical)
    for sibling in sorted(_VARIANTS.get(canonical, ())):
        terms.append(sibling)

    # skills that count as evidence of this one, never the reverse
    for specific in evidence_for(canonical):
        terms.append(specific)

    # also alias individual words, so a multi-word requirement still reaches its synonyms
    for word in WORD.findall(query)[:8]:
        word_canonical = normalize_skill(word)
        if word_canonical != word.lower():
            terms.append(word_canonical)
        for sibling in sorted(_VARIANTS.get(word_canonical, ())):
            terms.append(sibling)

    return list(dict.fromkeys(terms))


def search_resume_bullets(cur, user_id, query, limit=5):
    """Ranked full-text search over one user's bullets, with a substring fallback — stemming
    misses "c#" and short tokens, and a false miss turns into a gap the user doesn't have."""
    if not isinstance(query, str):
        return []
    query = query.strip()[:MAX_QUERY_CHARS]
    if not query:
        return []

    limit = max(1, min(int(limit), MAX_RESULTS))
    terms = expand_query(query)

    # one plainto_tsquery per term, OR'd together with ||
    tsquery = " || ".join(["plainto_tsquery('english', %s)"] * len(terms))
    cur.execute(
        f"""
        WITH q AS (SELECT ({tsquery}) AS query)
        SELECT b.id, b.text, e.kind, e.organization, e.title,
               ts_rank(b.search_vector, q.query) AS rank
        FROM resume_bullets AS b
        JOIN resume_entries AS e ON e.id = b.entry_id
        CROSS JOIN q
        WHERE b.user_id = %s AND b.search_vector @@ q.query
        ORDER BY rank DESC, b.sort_order
        LIMIT %s
        """,
        (*terms, user_id, limit),
    )
    rows = cur.fetchall()

    if not rows:
        patterns = [f"%{term}%" for term in terms]
        cur.execute(
            """
            SELECT b.id, b.text, e.kind, e.organization, e.title, 0 AS rank
            FROM resume_bullets AS b
            JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s AND b.text ILIKE ANY(%s)
            ORDER BY b.sort_order
            LIMIT %s
            """,
            (user_id, patterns, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "bullet_id": str(row[0]),
            "text": row[1],
            "kind": row[2],
            "organization": row[3],
            "entry_title": row[4],
            "rank": float(row[5]),
        }
        for row in rows
    ]
