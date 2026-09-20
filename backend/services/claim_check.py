"""A rewrite may strengthen wording. It may not strengthen facts.

The citation check proves a proposal points at real bullets. It says nothing about whether
the new sentence stays inside them — cite "Built Flask APIs" and you can still write
"Kubernetes-deployed Flask services" and pass. This closes the part of that hole a machine
can close: every technology named in the rewrite has to appear in the cited evidence, or be
implied by something that does.

Numbers are checked too, by `unsupported_numbers`. What is still not caught is invented adjectives and
scope — "high-performance", "led the team" — which need a human or an entailment model.
"""

import re

from services.match import normalize_skill
from services.skill_graph import rewrite_implied_by, vocabulary
from services.text_match import covered_tokens, index


STRONG_ACTION_VERBS = {
    "achieved", "analyzed", "architected", "automated", "built", "created", "delivered",
    "deployed", "designed", "developed", "engineered", "implemented", "improved",
    "increased", "integrated", "launched", "led", "managed", "migrated", "optimized",
    "operated", "produced", "reduced", "shipped", "streamlined",
}
# The same verbs as they appear in work that has not finished. A bullet on a current role
# keeps the ongoing sense (see `tense_regression`), so without these "Contributing to X" ->
# "Building X" is a real improvement that reads as a phrasing change and gets refused, while
# the past-tense rewrite that would pass is refused for changing the tense. Both doors shut.
STRONG_ACTION_GERUNDS = {
    "achieving", "analyzing", "architecting", "automating", "building", "creating",
    "delivering", "deploying", "designing", "developing", "engineering", "implementing",
    "improving", "increasing", "integrating", "launching", "leading", "managing",
    "migrating", "optimizing", "operating", "producing", "reducing", "shipping",
    "streamlining",
}
MIN_STRONG_BULLET_WORDS = 10


def opens_with_action(words):
    """Whether a bullet's first word is a concrete action verb, in either tense."""
    return bool(words) and (words[0] in STRONG_ACTION_VERBS or words[0] in STRONG_ACTION_GERUNDS)


def _phrase_length(term):
    return len(term.split())


def named_skills(text):
    """Known technologies mentioned in a piece of text, as canonical names.

    Canonical because the vocabulary holds every spelling: without this, one "Kubernetes"
    comes back as both kubernetes and k8s.

    **Longest match wins.** "object-oriented programming" is one concept, not `oop` plus
    `programming` — a phrase claims its words, and a shorter term that only matches inside an
    already-claimed phrase is not a separate mention. Reading one phrase as two concepts made
    the claim checker contradict itself: a rewrite could name `programming` without any
    evidence naming it on its own.
    """
    if not text:
        return []

    indexed = index(text)
    claimed = set()
    found = []
    # longest phrase first, then longest string: "object-oriented programming" before
    # "programming", "machine learning" before "learning"
    for term in sorted(vocabulary(), key=lambda t: (_phrase_length(t), len(t)), reverse=True):
        covers = covered_tokens(indexed, term)
        if not covers or covers <= claimed:
            continue
        claimed |= covers
        canonical = normalize_skill(term)
        if canonical not in found:
            found.append(canonical)
    return found


def supported_skills(evidence_texts, approved=frozenset()):
    """Everything the cited evidence names, plus the strict claims it lets a rewrite say.

    This intentionally does not use the transitive scoring graph. Evidence can earn match
    credit for a broader concept without granting permission to write that concept into a
    specific accomplishment.

    `approved` carries the model-learned edges this user has agreed to. Without it only the
    hand-written table applies, which is the safe default.
    """
    allowed = set()
    for text in evidence_texts:
        for skill in named_skills(text):
            allowed.add(skill)
            allowed.update(rewrite_implied_by(skill, approved))
    return allowed


# 30%, 2M, $1.2k, 15+, 3.5x — the shapes a resume bullet actually uses
# The trailing lookahead is load-bearing: without it the unit alternation happily matched the "s" of
# "ROS 2 stack", turning a framework version into "2 seconds" — and a rewrite that kept "ROS 2"
# was then refused for dropping a measurable result. "Vue 3 app" and "Python 3 script" are the
# same shape, and all three are ordinary resume words. A lookahead rather than \b, because
# \b does not hold after "%" and the suffix would be dropped from "30%" — which is the one
# distinction this function exists to keep.
NUMBER = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?\s*(?:%|k|m|b|x|\+|hrs?|hours?|ms|s|gb|mb|tb)?(?![A-Za-z0-9])", re.I)
MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def numeric_claims(text):
    """Every quantity in the text, normalized to a comparable value.

    Normalized so 24 does not match evidence saying 2024, and so "2M" and "2,000,000" are
    the same number. Percentages and multiples stay distinct from bare counts, because
    "30%" and "30" are different claims.
    """
    claims = set()
    for raw in NUMBER.findall(text or ""):
        token = raw.strip().lower().replace(",", "").lstrip("$€£")
        suffix = token.lstrip("0123456789.").strip()
        digits = token[: len(token) - len(suffix)] if suffix else token
        if not digits:
            continue
        value = float(digits)
        if suffix in MULTIPLIERS:
            value *= MULTIPLIERS[suffix]
            suffix = ""
        claims.add((value, suffix))
    return claims


def unsupported_numbers(proposed_text, evidence_texts):
    """Quantities in the rewrite that appear in none of the cited evidence."""
    supported = set()
    for text in evidence_texts:
        supported |= numeric_claims(text)
    return sorted(
        f"{value:g}{suffix}" for value, suffix in numeric_claims(proposed_text) - supported
    )


def unsupported_claims(proposed_text, evidence_texts, approved=frozenset()):
    """Technologies and numbers in the rewrite that the evidence doesn't back."""
    allowed = supported_skills(evidence_texts, approved)
    skills = [
        skill for skill in named_skills(proposed_text)
        if skill not in allowed and normalize_skill(skill) not in allowed
    ]
    return skills + unsupported_numbers(proposed_text, evidence_texts)


def _words(text):
    return index(text)["whole"]


def bullet_is_already_strong(text):
    """Conservative no-op signal for polished technical bullets.

    A substantial bullet that already opens with a concrete action and names a technology or
    quantity should not be sent through an AI merely to swap synonyms. This does not attempt
    to grade the whole resume; it only identifies bullets where automatic rewriting has little
    safe upside without asking the user for new facts.
    """
    words = _words(text)
    return bool(
        len(words) >= MIN_STRONG_BULLET_WORDS
        and opens_with_action(words)
        and (named_skills(text) or numeric_claims(text))
    )


def bullet_quality_gaps(text):
    """Small, explainable reasons a bullet is eligible for automatic improvement."""
    words = _words(text)
    gaps = []
    if not words:
        return ["the bullet is empty"]
    if not opens_with_action(words):
        gaps.append("it does not open with a concrete action verb")
    if len(words) < MIN_STRONG_BULLET_WORDS:
        gaps.append("it gives very little context or outcome detail")
    if not named_skills(text) and not numeric_claims(text):
        gaps.append("it names no concrete technology or measurable result")
    return gaps


# Words that place the writer inside a larger effort rather than behind the whole of it.
SHARED_CREDIT = {
    "contributing", "contributed", "contribute", "assisted", "assisting", "helped", "helping",
    "supported", "supporting", "participated", "participating", "collaborated", "collaborating",
    "involved", "shadowed", "shadowing",
}
# Words that claim the whole of it.
SOLE_CREDIT = {
    "architected", "built", "created", "designed", "developed", "engineered", "founded",
    "implemented", "launched", "led", "owned", "rebuilt", "shipped",
}


def ownership_inflation(original_text, proposed_text):
    """"Contributing to X" rewritten as "Developed X", or nothing.

    The claim checker verifies technologies and numbers, which leaves the most common way a
    resume line becomes untrue completely uncovered: keeping every noun and quietly promoting
    the verb. "Contributing to a work-in-progress stack" and "Developed the stack" cite the same
    evidence and describe different people.

    Deliberately narrow — it fires only when the original says shared credit *and* the rewrite
    says sole credit *and* the original never used that stronger word itself. A rewrite that
    keeps the hedge, or one whose bullet already said "Developed", is left alone.
    """
    original = set(_words(original_text))
    proposed = set(_words(proposed_text))
    shared = original & SHARED_CREDIT
    if not shared or original & SOLE_CREDIT:
        return None
    claimed = proposed & SOLE_CREDIT
    if not claimed or proposed & SHARED_CREDIT:
        return None
    return (
        f"the bullet says {sorted(shared)[0]}, and the rewrite says {sorted(claimed)[0]} — "
        "that claims more of the work than the evidence does. Keep the original's level of "
        "involvement and improve the wording around it"
    )


# Phrases that say the work has not finished. Checked against the raw text, not the word
# list, because "work-in-progress" and "in progress" are multi-word.
ONGOING_MARKERS = ("work-in-progress", "work in progress", "in progress", "currently", "ongoing")


def _reads_as_ongoing(text):
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ONGOING_MARKERS):
        return True
    words = _words(text)
    # a bullet that opens "Contributing to…" / "Building…" is describing live work
    return bool(words) and words[0].endswith("ing")


def tense_regression(original_text, proposed_text, entry_is_ongoing):
    """Live work rewritten as finished work, or nothing.

    `ownership_inflation` covers *how much* of the work is claimed. This covers *whether it is
    over*, which is a separate axis and equally a factual change: "Contributing to" and
    "Contributed" describe different situations to a recruiter reading a current role.

    Gated on `entry_is_ongoing` — a fact from `resume_entries.end_date`, not a guess from the
    sentence. A participle word-list alone would reject "Implementing X" -> "Implemented X" on
    a project that genuinely shipped last year, which is a correct edit.
    """
    if not entry_is_ongoing:
        return None
    if not _reads_as_ongoing(original_text):
        return None
    if _reads_as_ongoing(proposed_text):
        return None
    return (
        "this entry has no end date, so the work is still going on, and the rewrite puts it in "
        "the past. Keep the ongoing sense the bullet already has"
    )


def rewrite_quality_issue(original_text, proposed_text, surfacing=None, entry_is_ongoing=False):
    """Explain why a grounded rewrite still adds no useful value, or return ``None``.

    Grounding answers "is it true?". This answers the separate question "is it better?".
    It runs for weak bullets too; exempting them let synonym swaps through because weak
    bullets are exactly the ones the planner sends to the model.

    `surfacing` is a skill the user has just told us they used on this entry. Its first
    appearance in the bullet counts as new evidence even when it is not a term the graph
    knows — otherwise the whole point of asking ("show this skill in the work") reads as a
    phrasing change and gets refused. It exempts the phrasing check ONLY; the factual checks
    above it run either way, because "the user confirmed this skill" is not permission to
    change who did the work or whether it is finished.
    """
    original_words = _words(original_text)
    proposed_words = _words(proposed_text)
    if original_words == proposed_words:
        return "the proposed edit is the same as the current bullet"

    inflated = ownership_inflation(original_text, proposed_text)
    if inflated:
        return inflated

    regressed = tense_regression(original_text, proposed_text, entry_is_ongoing)
    if regressed:
        return regressed

    if len(proposed_words) < len(original_words) * 0.8:
        return (
            f"the rewrite drops detail the bullet already had ({len(proposed_words)} words "
            f"against {len(original_words)}). Keep every fact, technology and number that is "
            "already there and add to it — a rewrite should not be shorter than what it replaces"
        )

    original_skills = set(named_skills(original_text))
    proposed_skills = set(named_skills(proposed_text))
    removed_skills = original_skills - proposed_skills
    if removed_skills:
        return f"the rewrite removes supported detail: {', '.join(sorted(removed_skills))}"

    original_numbers = numeric_claims(original_text)
    proposed_numbers = numeric_claims(proposed_text)
    missing_numbers = original_numbers - proposed_numbers
    if missing_numbers:
        return (
            "the rewrite drops a measurable result the bullet already had "
            f"({', '.join(f'{value:g}{suffix}' for value, suffix in sorted(missing_numbers))}). "
            "Numbers are the strongest thing on a resume — keep every one of them"
        )

    original_opens_strong = opens_with_action(original_words)
    proposed_opens_strong = opens_with_action(proposed_words)
    stronger_action = proposed_opens_strong and not original_opens_strong
    added_skills = proposed_skills - original_skills
    added_numbers = proposed_numbers - original_numbers
    named = (surfacing or "").strip().lower()
    surfaced = bool(named) and named in proposed_text.lower() and named not in original_text.lower()
    if not stronger_action and not added_skills and not added_numbers and not surfaced:
        return (
            "the rewrite only changes phrasing; strengthen the action or surface new supported "
            "evidence, otherwise leave the bullet unchanged"
        )
    return None


def merge_quality_issue(original_texts, proposed_text):
    """Reject concatenation or detail loss when several thin bullets become one."""
    originals = [text for text in original_texts if text]
    proposed_words = _words(proposed_text)
    if len(originals) < 2:
        return "a merge requires at least two source bullets"
    if any(proposed_words == _words(text) for text in originals):
        return "the merged bullet is the same as one source bullet"
    if not opens_with_action(proposed_words):
        return "the merged bullet must open with a concrete action verb"

    original_word_count = sum(len(_words(text)) for text in originals)
    if len(proposed_words) > original_word_count * 0.9:
        return "the merge mostly concatenates the source bullets instead of removing repetition"
    if len(proposed_words) < original_word_count * 0.45:
        return "the merge removes too much of the source bullets' detail"

    original_skills = set().union(*(set(named_skills(text)) for text in originals))
    removed_skills = original_skills - set(named_skills(proposed_text))
    if removed_skills:
        return f"the merge removes supported detail: {', '.join(sorted(removed_skills))}"

    original_numbers = set().union(*(numeric_claims(text) for text in originals))
    if original_numbers - numeric_claims(proposed_text):
        return "the merge removes a measurable result from its source bullets"
    return None
