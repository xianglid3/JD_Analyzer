"""Match a job's requirements against a resume's evidence.

Each requirement gets a state rather than a yes/no:

    EXPLICIT   named outright
    INFERRED   a named technology implies it (Tailwind → CSS)
    PARTIAL    only the general skill shows up ("cloud" for an AWS requirement)
    NONE       nothing

Scores come from those states with fixed weights, so the number is reproducible and every
part of it can be traced back to a bullet.
"""

from services.match import ALIASES, normalize_skill
from services.resume_evidence import evidence_is_stale
from services.skill_graph import evidence_for, implied_by
from services.text_match import index as token_index, mentions

EXPLICIT = "EXPLICIT"
INFERRED = "INFERRED"
PARTIAL = "PARTIAL"
NONE = "NONE"

STATE_WEIGHTS = {EXPLICIT: 1.0, INFERRED: 0.8, PARTIAL: 0.4, NONE: 0.0}

# how much a requirement counts, from how the posting framed it
IMPORTANCE_WEIGHTS = {"required": 3, "preferred": 2, "nice_to_have": 1}
DEFAULT_IMPORTANCE = "required"

# Fit and visibility are reported separately and never blended.
#
# Blending them meant inserting a keyword raised the score — the resume changed, the
# candidate did not, and the number said they had improved. Two questions, two answers:
#
#   fit         of what this job asks for, how much can they actually do?
#   visibility  of what they can do, how much does the resume say plainly?
#
# Only wording moves the second one. Only skills move the first.
#
# Named "visibility" rather than "communication": the extractor drops soft skills on purpose,
# so "communication" pointed at the one thing this score is definitely not about.


# canonical form → every spelling of it, so a resume writing "c++" still matches a
# requirement that normalizes to "cpp"
_SPELLINGS = {}
for _variant, _canonical in ALIASES.items():
    _SPELLINGS.setdefault(_canonical, set()).add(_variant)


def _spellings(term):
    return [term] + sorted(_SPELLINGS.get(term, ()))


def _bullet_index(bullets):
    """Each bullet tokenized once, plus the raw text for display."""
    indexed = []
    for bullet in bullets:
        text = bullet["text"] if isinstance(bullet, dict) else str(bullet)
        indexed.append({
            "bullet_id": bullet.get("bullet_id") if isinstance(bullet, dict) else None,
            "kind": bullet.get("kind") if isinstance(bullet, dict) else None,
            "source": bullet.get("source") if isinstance(bullet, dict) else None,
            "entry_id": bullet.get("entry_id") if isinstance(bullet, dict) else None,
            "entry_name": bullet.get("entry_name") if isinstance(bullet, dict) else None,
            "text": text,
            "normalized": normalize_skill(text),
            "tokens": token_index(text),
        })
    return indexed


def _mentions(entry, term):
    if entry["normalized"] == term:
        return True
    return mentions(entry["tokens"], term)


def evaluate_requirement(requirement, bullets):
    """One requirement against the evidence: state, the bullets behind it, and what it was
    inferred from."""
    term = normalize_skill(requirement)
    index = _bullet_index(bullets)

    explicit = [e for e in index if any(_mentions(e, spelling) for spelling in _spellings(term))]
    if explicit:
        return {
            "requirement": requirement,
            "state": EXPLICIT,
            "evidence": explicit[:3],
            "inferred_from": [],
        }

    for specific in evidence_for(term):
        hits = [e for e in index if any(_mentions(e, s) for s in _spellings(specific))]
        if hits:
            return {
                "requirement": requirement,
                "state": INFERRED,
                "evidence": hits[:3],
                "inferred_from": [specific],
            }

    # the reverse direction, allowed only here: they show the general skill where the job
    # wants a specific one. Weak on purpose — it never counts as matched.
    for general in implied_by(term):
        hits = [e for e in index if _mentions(e, general)]
        if hits:
            return {
                "requirement": requirement,
                "state": PARTIAL,
                "evidence": hits[:3],
                "inferred_from": [general],
            }

    return {"requirement": requirement, "state": NONE, "evidence": [], "inferred_from": []}


# ── requirement conditions ───────────────────────────────────────────────────
# "experience in one of Java, Python, JavaScript, HTML, SQL or C++" is ONE requirement with
# six alternatives, not six requirements. Flattening it meant someone who knows C++ scored
# 1/6 = 17% instead of satisfied — the denominator was wrong before matching even began.
#
# Two operators cover everything worth modelling: all_of, and any_of with a minimum (which
# subsumes "one of" at minimum=1 and "at least two of" at minimum=2). A bare skill is
# any_of over one item, so legacy rows and trees run through exactly the same resolver.

ALL_OF = "all_of"
ANY_OF = "any_of"
VALID_OPERATORS = {ALL_OF, ANY_OF}

STATE_ORDER = [NONE, PARTIAL, INFERRED, EXPLICIT]
SATISFYING = (EXPLICIT, INFERRED)


def _best(states):
    return max(states, key=STATE_ORDER.index) if states else NONE


def as_condition(item):
    """Any requirement shape → {operator, minimum, items}.

    A plain string, a legacy {"skill": ...}, and a tree node all end up here, so nothing
    downstream needs to know which one it started as.
    """
    if isinstance(item, dict) and isinstance(item.get("condition"), dict):
        condition = item["condition"]
        operator = condition.get("operator", ANY_OF)
        items = [str(i).strip() for i in condition.get("items", []) if str(i).strip()]
        minimum = condition.get("minimum", 1)
        if operator not in VALID_OPERATORS:
            operator = ANY_OF
        if not isinstance(minimum, int) or minimum < 1:
            minimum = 1
        return {"operator": operator, "minimum": min(minimum, len(items) or 1), "items": items}

    name = (item.get("skill") or item.get("requirement") or "") if isinstance(item, dict) else str(item)
    name = name.strip()
    return {"operator": ANY_OF, "minimum": 1, "items": [name] if name else []}


def short_condition_label(condition):
    """A name short enough to be addressed reliably.

    `condition_label` prefers the posting's own sentence, which is right on screen and wrong
    everywhere else: the agent has to echo it back exactly to say which requirement it is
    working on, and a model asked to repeat a hundred-character sentence does not. It also
    quotes more than the group covers — one posting's `source_text` named six skills for a
    group of four.
    """
    joiner = " and " if condition["operator"] == ALL_OF else " or "
    if condition["operator"] == ANY_OF and condition["minimum"] > 1:
        return f"{condition['minimum']} of: " + ", ".join(condition["items"])
    return joiner.join(condition["items"])


def condition_label(item, condition):
    """What to call this requirement on screen. The posting's own words when we captured
    them, otherwise the alternatives spelled out."""
    if isinstance(item, dict) and (item.get("source_text") or "").strip():
        return item["source_text"].strip()
    return short_condition_label(condition)


def evaluate_condition(condition, bullets):
    """Resolve one requirement's alternatives into a single state.

    `any_of` stops at the alternatives that are met: the rest are not gaps, and must never
    reach tailoring. Being asked for "Java or Python" and knowing Python is not a Java gap.
    """
    leaves = [evaluate_requirement(name, bullets) for name in condition["items"]]
    if not leaves:
        return None

    met = [leaf for leaf in leaves if leaf["state"] in SATISFYING]

    if condition["operator"] == ALL_OF:
        state = min((leaf["state"] for leaf in leaves), key=STATE_ORDER.index)
        satisfied_by = [leaf["requirement"] for leaf in met]
        unmet = [leaf["requirement"] for leaf in leaves if leaf["state"] not in SATISFYING]
    else:
        if len(met) >= condition["minimum"]:
            state = _best([leaf["state"] for leaf in met])
            satisfied_by = [leaf["requirement"] for leaf in met]
            unmet = []                     # satisfied: the other alternatives are not gaps
        else:
            state = PARTIAL if any(leaf["state"] == PARTIAL for leaf in leaves) else NONE
            satisfied_by = [leaf["requirement"] for leaf in met]
            unmet = [leaf["requirement"] for leaf in leaves if leaf["state"] not in SATISFYING]

    # the evidence shown is the evidence that carried the decision
    carrying = met if met else leaves
    evidence, inferred_from = [], []
    for leaf in carrying:
        evidence.extend(leaf["evidence"])
        inferred_from.extend(leaf["inferred_from"])

    return {
        "state": state,
        "satisfied_by": satisfied_by,
        "unmet_alternatives": unmet,
        "evidence": evidence[:3],
        "inferred_from": list(dict.fromkeys(inferred_from)),
    }


def eligibility_conditions(requirements):
    """The gates the user has to judge for themselves, kept out of every score."""
    return [
        (item.get("skill") or item.get("requirement") or "").strip()
        for item in requirements or []
        if isinstance(item, dict) and item.get("type") == "eligibility"
        and (item.get("skill") or item.get("requirement") or "").strip()
    ]


def evaluate_requirements(requirements, bullets):
    """Every scored requirement plus the two scores. Items can be strings or
    {"skill", "importance", "type"}. Eligibility conditions are skipped — see below."""
    if not requirements:
        return None

    results = []
    for item in requirements:
        importance = item.get("importance", DEFAULT_IMPORTANCE) if isinstance(item, dict) else DEFAULT_IMPORTANCE
        kind = item.get("type", "skill") if isinstance(item, dict) else "skill"
        # a clearance or work authorization is a gate on applying, not a wording problem:
        # scoring it would report a permanent gap, and tailoring it would invite a lie
        if kind == "eligibility":
            continue
        if importance not in IMPORTANCE_WEIGHTS:
            importance = DEFAULT_IMPORTANCE

        condition = as_condition(item)
        evaluated = evaluate_condition(condition, bullets)
        if evaluated is None:
            continue

        # one requirement, one weight — however many alternatives it offers
        evaluated["requirement"] = condition_label(item, condition)
        # what the agent is asked to name; never a sentence
        evaluated["agent_label"] = short_condition_label(condition)
        evaluated["importance"] = importance
        results.append(evaluated)

    if not results:
        return None

    total_weight = sum(IMPORTANCE_WEIGHTS[r["importance"]] for r in results)
    capability = sum(
        STATE_WEIGHTS[r["state"]] * IMPORTANCE_WEIGHTS[r["importance"]] for r in results
    ) / total_weight

    # Communication is conditional on capability: of the requirements there IS evidence for,
    # how much does the resume state outright? Dividing by every requirement instead would
    # let a gap drag this down, which is the conflation the split exists to remove.
    supported = [r for r in results if r["state"] != NONE]
    supported_weight = sum(IMPORTANCE_WEIGHTS[r["importance"]] for r in supported)
    explicit_weight = sum(
        IMPORTANCE_WEIGHTS[r["importance"]] for r in supported if r["state"] == EXPLICIT
    )
    communication = explicit_weight / supported_weight if supported_weight else None

    # what tailoring is for: real evidence the resume does not say plainly
    hidden = [r["requirement"] for r in supported if r["state"] != EXPLICIT]

    return {
        # the headline number is capability. Wording cannot move it.
        "score": round(capability * 100, 2),
        "fit_score": round(capability * 100, 2),
        "visibility_score": round(communication * 100, 2) if communication is not None else None,
        "hidden": hidden,
        # carried, never scored: the user decides these for themselves
        "eligibility": eligibility_conditions(requirements),
        "requirements": [
            {
                "requirement": r["requirement"],
                "agent_label": r.get("agent_label") or r["requirement"],
                "state": r["state"],
                "importance": r["importance"],
                "inferred_from": r["inferred_from"],
                # which alternative carried it, so the user can see why it counted
                "satisfied_by": r["satisfied_by"],
                "evidence": [
                    {
                        "bullet_id": e["bullet_id"], "text": e["text"],
                        "source": e.get("source"),
                        "entry_id": e.get("entry_id"), "entry_name": e.get("entry_name"),
                    }
                    for e in r["evidence"]
                ],
            }
            for r in results
        ],
        "matched": [r["requirement"] for r in results if r["state"] in (EXPLICIT, INFERRED)],
        "missing": [r["requirement"] for r in results if r["state"] == NONE],
    }


def load_evidence_bullets(cur, user_id):
    """Everything a requirement can match against: structured bullets, plus the plain skills
    list from the resume page so people who never built structured evidence still score.
    Only the structured ones carry ids, so only they can be cited.

    Stale evidence is left out entirely — scoring against a resume the user has replaced is
    the same lie as tailoring from it."""
    evidence = []
    if not evidence_is_stale(cur, user_id):
        cur.execute(
            """
            SELECT b.id, b.text, e.kind
            FROM resume_bullets AS b
            JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s
            ORDER BY b.sort_order
            """,
            (user_id,),
        )
        evidence = [{"bullet_id": str(r[0]), "text": r[1], "kind": r[2]} for r in cur.fetchall()]

        # An entry header carries technologies the bullets never repeat — a project called
        # "Motor Control System (C++, Google Test)" names two skills nothing underneath says
        # again. Those count as evidence, but carry no bullet id: the resume names them, and
        # no single bullet demonstrates them, so a rewrite may not cite one as if it did.
        cur.execute(
            """
            SELECT kind, organization, title
            FROM resume_entries
            WHERE user_id = %s AND (organization IS NOT NULL OR title IS NOT NULL)
            ORDER BY sort_order
            """,
            (user_id,),
        )
        for kind, organization, title in cur.fetchall():
            header = " ".join(part for part in (title, organization) if part).strip()
            if header:
                evidence.append({
                    "bullet_id": None, "text": header, "kind": kind, "source": "entry_header",
                })

        # Skills the user attached to an entry themselves. First-party evidence: the resume
        # was only ever a record of what they say about their own work, and this is the same
        # claim made directly. Carries the entry so a rewrite of a bullet in it can be
        # authorised to name the skill — see `tailoring_plan`.
        cur.execute(
            """
            SELECT s.entry_id, s.skill, e.kind, e.title, e.organization
            FROM resume_entry_skills AS s
            JOIN resume_entries AS e ON e.id = s.entry_id
            WHERE s.user_id = %s
            ORDER BY e.sort_order
            """,
            (user_id,),
        )
        for entry_id, skill, kind, title, organization in cur.fetchall():
            evidence.append({
                "bullet_id": None,
                "text": skill,
                "kind": kind,
                "source": "user_affirmed",
                "entry_id": str(entry_id),
                "entry_name": (title or organization or "an entry"),
            })

    cur.execute("SELECT skills FROM resumes WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    for skill in (row[0] if row and row[0] else []):
        evidence.append({"bullet_id": None, "text": skill, "kind": "skill"})

    return evidence


def requirements_for_job(requirements, skills):
    """Stored requirements if present, else the flat skills list — older jobs have no
    requirements and shouldn't drop to zero."""
    if requirements:
        return requirements
    return list(skills or [])


def warm_relations(requirements, user_id=None):
    """Teach the graph any requirement term it has never seen, before scoring reads it.

    **Call this before opening a transaction, never inside one.** It makes a model call, and
    holding a connection across that call is what starved the pool. Scoring itself does not
    call it — a caller that skips warming simply scores against the relations already known,
    which is a slightly staler answer rather than a wrong one.
    """
    from services.skill_graph import seed_vocabulary
    from services.skill_relations import resolve

    terms = []
    for item in requirements or []:
        terms.extend(as_condition(item)["items"])
    if terms:
        resolve(terms, seed_known=seed_vocabulary(), user_id=user_id)


def warm_relations_for_user(user_id, get_cursor):
    """Warm every term across a user's jobs. For the resume-save path, which rescores all of
    them; reads the job rows in its own short transaction, then warms outside it."""
    with get_cursor() as cur:
        cur.execute("SELECT requirements, skills FROM jobs WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
    warm_relations([
        item for requirements, skills in rows
        for item in requirements_for_job(requirements, skills)
    ], user_id=user_id)


def match_for_job(cur, user_id, requirements, skills):
    """Score one job against the user's current evidence. None when there is nothing to score.

    Scoring only reads the relation cache. Warming it is `warm_relations`, which the caller
    runs before opening this transaction.
    """
    items = requirements_for_job(requirements, skills)
    if not items:
        return None
    return evaluate_requirements(items, load_evidence_bullets(cur, user_id))


def recompute_job_match(cur, user_id, job_id):
    """Rescore one job. For the paths that change evidence for a single job the user is
    looking at — rescoring their whole list would be work nobody asked for."""
    import json

    cur.execute(
        "SELECT requirements, skills FROM jobs WHERE id = %s AND user_id = %s",
        (str(job_id), user_id),
    )
    row = cur.fetchone()
    if row is None:
        return None

    items = requirements_for_job(row[0], row[1])
    result = evaluate_requirements(items, load_evidence_bullets(cur, user_id)) if items else None
    cur.execute(
        "UPDATE jobs SET match_score = %s, match_detail = %s WHERE id = %s AND user_id = %s",
        (result["score"] if result else None,
         json.dumps(detail_for(result)) if result else None,
         str(job_id), user_id),
    )
    return result


def recompute_user_matches(cur, user_id):
    """Rescore every job after the resume changes: one read, one batched write."""
    import json

    bullets = load_evidence_bullets(cur, user_id)
    cur.execute("SELECT id, requirements, skills FROM jobs WHERE user_id = %s", (user_id,))
    rows = cur.fetchall()

    updates = []
    for job_id, requirements, skills in rows:
        items = requirements_for_job(requirements, skills)
        result = evaluate_requirements(items, bullets) if items else None
        updates.append({
            "id": str(job_id),
            "match_score": result["score"] if result else None,
            "match_detail": detail_for(result),
        })

    if updates:
        cur.execute(
            """
            UPDATE jobs AS job
            SET match_score = match.match_score,
                match_detail = match.match_detail
            FROM jsonb_to_recordset(%s::jsonb) AS match(
                id uuid,
                match_score numeric,
                match_detail jsonb
            )
            WHERE job.id = match.id AND job.user_id = %s
            """,
            (json.dumps(updates), user_id),
        )

    return len(updates)


def detail_for(result):
    """What lands in jobs.match_detail — the explanation, not just the number."""
    if not result:
        return None
    return {
        "matched": result["matched"],
        "missing": result["missing"],
        "fit_score": result["fit_score"],
        "visibility_score": result["visibility_score"],
        "hidden": result["hidden"],
        "eligibility": result["eligibility"],
        "requirements": result["requirements"],
    }
