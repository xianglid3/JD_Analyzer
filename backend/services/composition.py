"""Build a deterministic ordering plan for one resume and one posting.

Composition never adds, removes, or rewrites a claim. It only changes presentation order,
so inferred and partial evidence may safely influence relevance here without authorizing new
wording elsewhere.
"""

from services.skill_evidence import (
    DEFAULT_IMPORTANCE,
    condition_label,
    IMPORTANCE_WEIGHTS,
    STATE_WEIGHTS,
    as_condition,
    evaluate_requirement,
)


PROJECT_KIND = "project"
SKILL_KIND = "skill"
MAX_ENTRY_SIGNALS = 3
ENTRY_SIGNAL_WEIGHTS = (1.0, 0.5, 0.25)


def _bullet_id(bullet):
    return str(bullet.get("id") or bullet.get("bullet_id")) if isinstance(bullet, dict) else str(bullet)


def _bullet_text(bullet):
    if not isinstance(bullet, dict):
        return ""
    return str(bullet.get("source_text") or bullet.get("text") or "")


def _requirement_weight(item):
    importance = item.get("importance", DEFAULT_IMPORTANCE) if isinstance(item, dict) else DEFAULT_IMPORTANCE
    if importance not in IMPORTANCE_WEIGHTS:
        importance = DEFAULT_IMPORTANCE
    return IMPORTANCE_WEIGHTS[importance]


def _is_eligibility(item):
    return isinstance(item, dict) and item.get("type") == "eligibility"


def score_breakdown(requirements, bullet):
    """Which requirements this bullet speaks to, and what each one contributed.

    Conditions are deliberately evaluated leaf-by-leaf. A bullet supporting one part of an
    ``all_of`` condition is still useful ordering evidence even when another bullet supplies
    the other part. Taking the best leaf also prevents alternatives from multiplying one
    requirement's weight.

    This is also what the ordering explanation renders, so the score and the reason given for
    it come from the same pass — an explanation that could disagree with the number it
    explains is worse than no explanation.
    """
    text = _bullet_text(bullet)
    if not text:
        return []

    evidence = {"bullet_id": _bullet_id(bullet), "text": text}
    contributions = []
    for item in requirements or []:
        if _is_eligibility(item):
            continue
        condition = as_condition(item)
        best_state, best_term = None, None
        for term in condition["items"]:
            state = evaluate_requirement(term, [evidence])["state"]
            if best_state is None or STATE_WEIGHTS[state] > STATE_WEIGHTS[best_state]:
                best_state, best_term = state, term
        if best_state is None or not STATE_WEIGHTS[best_state]:
            continue
        weight = _requirement_weight(item)
        contributions.append({
            "requirement": condition_label(item, condition),
            "matched": best_term,
            "state": best_state,
            # both halves of the multiplication, so the explanation can show the arithmetic
            # instead of the reader having to divide the total back out
            "state_weight": STATE_WEIGHTS[best_state],
            "importance": item.get("importance", DEFAULT_IMPORTANCE) if isinstance(item, dict) else DEFAULT_IMPORTANCE,
            "importance_weight": weight,
            "points": round(STATE_WEIGHTS[best_state] * weight, 3),
        })
    return sorted(contributions, key=lambda c: -c["points"])


def _text_relevance(requirements, bullet):
    return sum(c["points"] for c in score_breakdown(requirements, bullet))


def bullet_relevance(requirements, bullets):
    """Return a score for every source bullet with relevant JD evidence.

    This intentionally receives the raw requirements and the full bullet list. The public fit
    report limits citations to three for readability; using that truncated list here caused
    valid fourth and later bullets to receive no composition score.
    """
    scores = {}
    for bullet in bullets or []:
        bullet_id = _bullet_id(bullet)
        score = _text_relevance(requirements, bullet)
        if bullet_id and score > 0:
            scores[bullet_id] = score
    return scores


def entry_relevance(bullet_ids, scores):
    """Weight the three strongest signals with diminishing returns.

    Only the top three bullets count. A focused entry with one strong bullet therefore cannot
    be buried by an arbitrarily long entry full of weak matches.
    """
    values = sorted(
        (scores.get(str(bullet_id), 0.0) for bullet_id in bullet_ids),
        reverse=True,
    )[:MAX_ENTRY_SIGNALS]
    return sum(value * weight for value, weight in zip(values, ENTRY_SIGNAL_WEIGHTS))


def _composition_entries(entries):
    return [
        {
            "id": str(entry["id"]),
            "kind": entry.get("kind"),
            "title": entry.get("title"),
            "organization": entry.get("organization"),
            "bullets": [
                {
                    "id": _bullet_id(bullet),
                    "text": _bullet_text(bullet),
                }
                for bullet in entry.get("bullets") or []
            ],
        }
        for entry in entries
    ]


def compose(entries, requirements):
    """Return stable entry and bullet order for one posting.

    Bullets may move inside every entry. Only projects move as whole entries; employment,
    education, certificates, Skills, and unknown future kinds retain the user's saved order.
    Stable sorting preserves that order whenever relevance ties.
    """
    entries = _composition_entries(entries)
    bullets = [bullet for entry in entries for bullet in entry["bullets"]]
    scores = bullet_relevance(requirements, bullets)

    bullet_order = {
        entry["id"]: sorted(
            (_bullet_id(bullet) for bullet in entry["bullets"]),
            key=lambda bullet_id: -scores.get(bullet_id, 0.0),
        )
        for entry in entries
    }

    ordered = list(entries)
    project_slots = [i for i, entry in enumerate(ordered) if entry.get("kind") == PROJECT_KIND]
    ranked_projects = sorted(
        (ordered[i] for i in project_slots),
        key=lambda entry: -entry_relevance(
            [_bullet_id(bullet) for bullet in entry["bullets"]], scores
        ),
    )
    for slot, entry in zip(project_slots, ranked_projects):
        ordered[slot] = entry

    return {
        "entry_order": [entry["id"] for entry in ordered],
        "bullet_order": bullet_order,
        "scores": scores,
    }


def scored_bullets(entries, requirements, plan):
    """Every bullet with its relevance score and the requirements behind it.

    This is the debug view for ordering. It exists because the ranking is otherwise
    invisible: a bullet naming no technology scores zero and sinks, which looks arbitrary
    until you can see that the score was zero and why.
    """
    rows = []
    for entry in _composition_entries(entries):
        name = entry.get("title") or entry.get("organization") or "Untitled entry"
        planned = plan["bullet_order"].get(entry["id"], [b["id"] for b in entry["bullets"]])
        position = {bullet_id: index for index, bullet_id in enumerate(planned)}
        for index, bullet in enumerate(entry["bullets"]):
            contributions = score_breakdown(requirements, bullet)
            rows.append({
                "bullet_id": bullet["id"],
                "entry": name,
                "kind": entry.get("kind"),
                "text": bullet["text"],
                "score": round(sum(c["points"] for c in contributions), 3),
                "from": index + 1,
                "to": position.get(bullet["id"], index) + 1,
                "contributions": contributions,
            })
    return sorted(rows, key=lambda row: -row["score"])


def summarize(entries, plan):
    """Turn an ordering plan into a small, user-facing explanation."""
    entries = _composition_entries(entries)
    original_entry_order = [entry["id"] for entry in entries]
    planned_entry_order = plan["entry_order"]

    original_projects = [entry["id"] for entry in entries if entry.get("kind") == PROJECT_KIND]
    planned_projects = [entry_id for entry_id in planned_entry_order if entry_id in original_projects]
    project_positions = {entry_id: index for index, entry_id in enumerate(original_projects)}
    entry_by_id = {entry["id"]: entry for entry in entries}
    promoted_projects = []
    for index, entry_id in enumerate(planned_projects):
        if index < project_positions[entry_id]:
            entry = entry_by_id[entry_id]
            promoted_projects.append(
                entry.get("title") or entry.get("organization") or "Untitled project"
            )

    moved_bullets = 0
    reordered_skills = 0
    promoted_bullets = []
    promoted_skills = []
    for entry in entries:
        original = [_bullet_id(bullet) for bullet in entry["bullets"]]
        planned = plan["bullet_order"].get(entry["id"], original)
        moved = sum(before != after for before, after in zip(original, planned))
        original_positions = {bullet_id: index for index, bullet_id in enumerate(original)}
        bullet_by_id = {_bullet_id(bullet): bullet for bullet in entry["bullets"]}
        promoted = [
            (bullet_id, original_positions[bullet_id], index)
            for index, bullet_id in enumerate(planned)
            if index < original_positions[bullet_id]
        ]
        if entry.get("kind") == SKILL_KIND:
            reordered_skills += moved
            promoted_skills.extend(
                bullet_by_id[bullet_id]["text"] for bullet_id, _before, _after in promoted
            )
        else:
            moved_bullets += moved
            entry_name = entry.get("title") or entry.get("organization") or "Untitled entry"
            promoted_bullets.extend({
                "entry": entry_name,
                "text": bullet_by_id[bullet_id]["text"],
                "from": before + 1,
                "to": after + 1,
            } for bullet_id, before, after in promoted)

    return {
        "changed": original_entry_order != planned_entry_order or moved_bullets > 0 or reordered_skills > 0,
        "moved_bullets": moved_bullets,
        "promoted_projects": promoted_projects,
        "promoted_bullets": promoted_bullets,
        "promoted_skills": promoted_skills,
        "reordered_skills": reordered_skills,
    }
