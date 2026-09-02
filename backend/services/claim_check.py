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
from services.skill_graph import implied_by, vocabulary
from services.text_match import index, mentions


def named_skills(text):
    """Known technologies mentioned in a piece of text, as canonical names.

    Canonical because the vocabulary holds every spelling: without this, one "Kubernetes"
    comes back as both kubernetes and k8s.
    """
    if not text:
        return []
    indexed = index(text)
    found = []
    for term in vocabulary():
        if mentions(indexed, term):
            canonical = normalize_skill(term)
            if canonical not in found:
                found.append(canonical)
    return found


def supported_skills(evidence_texts):
    """Everything the cited evidence names, plus what it lets a rewrite say."""
    allowed = set()
    for text in evidence_texts:
        for skill in named_skills(text):
            allowed.add(skill)
            allowed.update(implied_by(skill, for_rewrite=True))
    return allowed


# 30%, 2M, $1.2k, 15+, 3.5x — the shapes a resume bullet actually uses
NUMBER = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?\s*(?:%|k|m|b|x|\+|hrs?|hours?|ms|s|gb|mb|tb)?", re.I)
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


def unsupported_claims(proposed_text, evidence_texts):
    """Technologies and numbers in the rewrite that the evidence doesn't back."""
    allowed = supported_skills(evidence_texts)
    skills = [
        skill for skill in named_skills(proposed_text)
        if skill not in allowed and normalize_skill(skill) not in allowed
    ]
    return skills + unsupported_numbers(proposed_text, evidence_texts)
