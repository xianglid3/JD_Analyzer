"""Turn the deterministic fit assessment into bounded tailoring work.

The fit engine owns capability and gaps. The model is only invited to rewrite requirements
that already have citable evidence; it never decides whether a requirement is missing.
"""

from services.claim_check import (
    bullet_is_already_strong,
    bullet_quality_gaps,
    numeric_claims,
    recruiter_doubt,
)
from services.match import normalize_skill
from services.skill_evidence import EXPLICIT, INFERRED, NONE, PARTIAL
from services.skill_graph import rewrite_implied_by


def _has_citable_evidence(item):
    return any(evidence.get("bullet_id") for evidence in item.get("evidence", []))


def _has_weak_citable_evidence(item):
    return any(
        evidence.get("bullet_id") and not bullet_is_already_strong(evidence.get("text") or "")
        for evidence in item.get("evidence", [])
    )


def _weakness(text, structural):
    """What to tell the model is wrong with a bullet.

    Structural facts ("opens with no action verb", "names no technology") describe the
    sentence; a model handed those asks a question about the sentence — "what technologies did
    you apply?" — whose honest answer restates the bullet. `recruiter_doubt` describes what a
    reader would not believe, which is a different question with a more useful answer.

    Both go in: the structural note still says what a rewrite may fix without asking anybody.
    """
    doubt = recruiter_doubt(text)
    parts = list(structural)
    if doubt:
        _name, reads_as, ask_for = doubt
        parts.append(f"{reads_as} — if you need to ask, {ask_for}")
    return "; ".join(part for part in parts if part) or "it could be clearer"


def _rewrite_targets(item, already_claimed=frozenset()):
    """Bullets this candidate may rewrite, each with the id the run will be handed."""
    targets = []
    for evidence in item.get("evidence", []):
        text = evidence.get("text") or ""
        bullet_id = evidence.get("bullet_id")
        if not bullet_id or bullet_is_already_strong(text):
            continue
        if str(bullet_id) in already_claimed:
            continue
        targets.append({
            "bullet_id": str(bullet_id),
            "text": text,
            "weakness": _weakness(text, bullet_quality_gaps(text)),
        })
    return targets


def _confirmation_targets(item, already_claimed=frozenset()):
    """Editable evidence the user can confirm without the model asserting the answer."""
    return [
        {
            "bullet_id": str(evidence["bullet_id"]),
            "text": evidence.get("text") or "",
            "weakness": _weakness(
                evidence.get("text") or "",
                [f"related evidence does not prove "
                 f"{item.get('requirement') or 'this requirement'}"],
            ),
        }
        for evidence in item.get("evidence", [])
        if evidence.get("bullet_id") and evidence.get("text")
        and str(evidence["bullet_id"]) not in already_claimed
    ]


def _strengthening_targets(item, already_selected):
    """Choose at most one strong, unmeasured bullet for a grounded detail question.

    The cap and cross-requirement de-duplication prevent one React/TypeScript bullet from
    generating several versions of the same question.
    """
    for evidence in item.get("evidence", []):
        text = evidence.get("text") or ""
        bullet_id = evidence.get("bullet_id")
        if (
            bullet_id
            and str(bullet_id) not in already_selected
            and bullet_is_already_strong(text)
            and not numeric_claims(text)
        ):
            return [{
                "bullet_id": str(bullet_id),
                "text": text,
                "weakness": _weakness(text, []),
            }]
    return []


def _can_surface_inference(item, approved=frozenset()):
    """Whether this inference may be stated plainly.

    Defaults to the hand-written table alone. A model-learned edge only counts once the user
    has approved it for themselves, so a wrong learned edge cannot quietly turn into a
    suggestion that they add a skill they never claimed (AE-03).
    """
    target = normalize_skill(item.get("requirement") or "")
    return bool(target) and any(
        target in rewrite_implied_by(normalize_skill(source), approved)
        for source in item.get("inferred_from", [])
    )


def _affirmed_entries(item):
    """Entries the user said this skill belongs to, if any."""
    return [
        evidence for evidence in item.get("evidence", [])
        if evidence.get("source") == "user_affirmed" and evidence.get("entry_id")
    ]


def _show_in_bullet_targets(item, bullets_by_entry):
    """Bullets that may now name the skill, because the user said it belongs to their entry.

    This is the payoff for asking. Until the user says where a skill was used, no bullet can
    be authorised to name it and the skill stays stranded in a keyword list. Once they have,
    a rewrite of a bullet in that entry is grounded in something they asserted — the same
    authority the resume itself carries.
    """
    targets = []
    for evidence in _affirmed_entries(item):
        for bullet in bullets_by_entry.get(evidence["entry_id"], []):
            targets.append({
                "bullet_id": str(bullet["id"]),
                "text": bullet["text"],
                # Unlike a rewrite candidate, this bullet is not weak — it was chosen
                # because the user said the skill belongs to its entry. So the job is purely
                # additive, and saying so is what stops the model rewriting it shorter and
                # being refused for dropping detail.
                "weakness": (
                    f"you said you used {item.get('requirement') or 'this'} on "
                    f"{evidence.get('entry_name') or 'this entry'}, but no bullet there says "
                    f"so. ADD it to this bullet: keep every existing fact, technology and "
                    f"number exactly as they are, and make the result longer, not shorter"
                ),
            })
    return targets[:3]


def _related_experience_reason(item):
    """Why a requirement with only general evidence is a gap, said without implying a match."""
    requirement = item.get("requirement") or "this requirement"
    general = ", ".join(item.get("inferred_from") or []) or "related work"
    return (
        f"Your resume shows {general}, but nothing names {requirement} itself. Related "
        "experience is not evidence of the tool — if you did use it somewhere, say where and "
        "it becomes something a bullet can show."
    )


def _outside_bullets_reason(item):
    """Where the match came from, when it did not come from a bullet.

    Worth distinguishing: a keyword list is the weakest possible evidence a recruiter reads,
    and moving it into a bullet is the highest-value change available — but it needs a fact
    only the candidate has, so the system can suggest it and must not write it.
    """
    where = "your skills list" if item.get("evidence") else "the resume"
    return (
        f"Matched from {where}, not from anything you did. No bullet demonstrates it, so "
        "there is nothing here to rewrite — adding it to the project you actually used it "
        "on is what would make this visible."
    )


def build_tailoring_plan(assessment, approved=frozenset(), bullets_by_entry=None):
    """Give every scored requirement one deterministic outcome.

    ``rewrite`` is optional wording work for an explicit, citable claim. ``surface_skill``
    is more restrictive: the exact inference must be in the authorship allowlist. Broad or
    transitive inferences are kept as match credit but never turned into resume prose.
    """
    plan = []
    bullets_by_entry = bullets_by_entry or {}
    # One bullet, one candidate — held as a set of bullet ids. A posting lists "java or golang
    # or python or c++…" and "c# or c++ or java" as separate requirements, and the same C++
    # bullet is the evidence for both, so the agent was sent to fight the same rewrite twice.
    # Cross-requirement de-duplication existed for strengthening targets only; the same
    # argument applies to every kind of target.
    claimed_targets = set()
    # Asking for every missing metric would turn one run into a questionnaire. One focused
    # strengthening opportunity is enough; partial requirements can still ask for confirmation.
    strengthening_slots = 1
    for position, item in enumerate((assessment or {}).get("requirements") or []):
        state = item.get("state")
        citable = _has_citable_evidence(item)
        targets = []

        affirmed_targets = _show_in_bullet_targets(item, bullets_by_entry)

        if state == NONE:
            action = "gap"
            reason = "No supporting evidence was found in the saved resume."
        elif affirmed_targets:
            action = "show_in_bullet"
            reason = "You confirmed where you used this; a bullet there can now say so."
            targets = affirmed_targets
        elif state == PARTIAL:
            # Related experience, not a partial match. The resume shows the general skill —
            # "database", "cloud" — where the posting wants a specific tool, which is not
            # evidence of the tool and must not become a question about it. A live run asked
            # "How did Redis improve this project?" about a bullet that names PostgreSQL and
            # nothing else; the premise came from here.
            #
            # No targets, so `agent_candidates` drops it, and no evidence attached: citing the
            # PostgreSQL bullet under a Redis heading is what made the run look broken. It
            # lands in the gaps list instead, where "I used this — where?" lets the person who
            # knows the answer volunteer it.
            action = "gap"
            reason = _related_experience_reason(item)
        elif state == INFERRED and citable and _can_surface_inference(item, approved):
            action = "surface_skill"
            reason = "A strict one-hop authorship rule allows this evidence to be stated more plainly."
        elif state == INFERRED and not citable:
            action = "only_in_skills"
            reason = _outside_bullets_reason(item)
        elif state == INFERRED:
            # Match permission is not writing permission, but silently keeping the bullet
            # leaves useful, ambiguous evidence stranded outside the agent. Ask the user to
            # confirm the specific claim; only their answer can unlock a rewrite.
            action = "confirm"
            reason = (
                "Specific evidence earns match credit, but the wording is not authorized yet; "
                "ask the user to confirm what they actually used before drafting a change."
            )
            targets = _confirmation_targets(item, claimed_targets)
        elif state == EXPLICIT and citable and _has_weak_citable_evidence(item):
            action = "rewrite"
            reason = "The requirement is explicit, but at least one supporting bullet could be clearer."
            targets = _rewrite_targets(item, claimed_targets)
        elif state == EXPLICIT and citable:
            detail_targets = (
                _strengthening_targets(item, claimed_targets)
                if strengthening_slots > 0 and item.get("importance", "required") == "required"
                else []
            )
            if detail_targets:
                action = "strengthen"
                reason = "The wording is already strong; ask for one real impact or scale detail."
                targets = detail_targets
                strengthening_slots -= 1
            else:
                action = "keep"
                reason = "The supporting bullet is already specific; automatic rewording would be cosmetic."
        else:
            # Met, but nothing demonstrates it. This used to be `keep`, which renders as
            # nothing at all — so the single most useful thing the fit engine knew about a
            # resume ("you prove Python with a keyword, not with work") was invisible, and
            # the run looked empty for no stated reason.
            action = "only_in_skills"
            reason = _outside_bullets_reason(item)

        # whatever this candidate took, no later one may take again. Keyed on bullet id, not
        # text: two entries can carry word-for-word identical bullets, and keying on the words
        # silently dropped the second one's candidate.
        claimed_targets.update(
            target["bullet_id"] for target in targets if target.get("bullet_id")
        )

        plan.append({
            "position": position,
            "requirement": item.get("requirement") or "Requirement",
            # short and stable: the agent addresses candidates by this, the user reads the
            # other one
            "agent_label": item.get("agent_label") or item.get("requirement") or "Requirement",
            "state": state,
            "importance": item.get("importance", "required"),
            "action": action,
            "reason": reason,
            "inferred_from": item.get("inferred_from") or [],
            "satisfied_by": item.get("satisfied_by") or [],
            "evidence_count": len(item.get("evidence") or []),
            "targets": targets,
        })
    return plan


def rewrite_candidates(plan):
    # ``surface_skill`` belongs in the Skills section and is shown to the user separately;
    # sending it to the bullet editor recreates the keyword-splicing bug this plan prevents.
    return [item for item in plan if item["action"] == "rewrite"]


def agent_candidates(plan):
    """Work that benefits from the bounded model, including questions—not only rewrites."""
    return [
        item for item in plan
        if item["action"] in {"rewrite", "strengthen", "confirm", "show_in_bullet"}
        and item.get("targets")
    ]


def deterministic_gaps(plan):
    return [item for item in plan if item["action"] == "gap"]


def keyword_only(plan):
    """Requirements the resume claims but never demonstrates."""
    return [item for item in plan if item["action"] == "only_in_skills"]
