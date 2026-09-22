"""Does experience with X count as evidence of Y?

Aliases in match.py handle "same thing, different spelling". This handles implication, and
only ever points from the specific to the general: Tailwind is evidence of CSS, never the
reverse. Hand-written so every inferred match can be explained to the user.
"""

from services.match import ALIASES, flatten_punctuation

# specific → the general skills it demonstrates
IMPLIES = {
    # css
    "tailwind": ["css", "html"],
    "bootstrap": ["css", "html"],
    "scss": ["css"],
    "sass": ["css"],
    "styled-components": ["css", "javascript"],
    "css modules": ["css"],
    "material ui": ["css", "react"],
    "shadcn": ["css", "react"],

    # javascript / frontend
    "react": ["javascript", "html", "css"],
    "next": ["react", "javascript"],
    "vue": ["javascript", "html"],
    "angular": ["typescript", "javascript", "html"],
    "svelte": ["javascript", "html"],
    "typescript": ["javascript"],
    "jquery": ["javascript"],
    "redux": ["javascript", "react"],
    "vite": ["javascript"],
    "webpack": ["javascript"],

    # node / backend js
    "express": ["node", "javascript"],
    "nest": ["node", "typescript"],
    "node": ["javascript"],

    # python
    "django": ["python", "api"],
    "flask": ["python", "api"],
    "fastapi": ["python", "api", "rest"],
    "pandas": ["python"],
    "numpy": ["python"],
    "scikit-learn": ["python", "machine learning"],
    "pytorch": ["python", "deep learning", "machine learning"],
    "tensorflow": ["python", "deep learning", "machine learning"],
    "machine learning": ["ai"],
    "deep learning": ["machine learning", "ai"],
    "llm": ["ai", "machine learning"],
    "rag": ["llm", "ai"],
    "prompt engineering": ["llm", "ai"],
    "computer vision": ["machine learning", "ai"],
    "opencv": ["computer vision", "python"],
    "natural language processing": ["machine learning", "ai"],
    "gesture recognition": ["computer vision", "machine learning", "ai"],
    "pypdf": ["python"],

    # java / jvm
    "spring": ["java"],
    "hibernate": ["java", "sql"],
    "kotlin": ["jvm"],

    # data
    "postgresql": ["sql", "database"],
    "mysql": ["sql", "database"],
    "sqlite": ["sql", "database"],
    "sql server": ["sql", "database"],
    "supabase": ["postgresql", "sql", "database"],
    "mongodb": ["database", "nosql"],
    "redis": ["database"],
    "dynamodb": ["database", "nosql", "aws"],
    "sqlalchemy": ["sql", "python"],
    "psycopg2": ["postgresql", "sql", "python"],

    # cloud / infra
    "kubernetes": ["containerization", "devops"],
    "docker": ["containerization", "devops"],
    "terraform": ["infrastructure as code", "devops"],
    "github actions": ["cicd", "devops"],
    "jenkins": ["cicd", "devops"],
    "vercel": ["deployment"],
    "railway": ["deployment"],
    "aws": ["cloud"],
    "gcp": ["cloud"],
    "azure": ["cloud"],
    "lambda": ["aws", "cloud"],

    # testing
    "pytest": ["testing", "python", "unit testing"],
    "vitest": ["testing", "javascript", "unit testing"],
    "jest": ["testing", "javascript", "unit testing"],
    "playwright": ["testing", "end-to-end testing"],
    "cypress": ["testing", "end-to-end testing"],
    "google test": ["testing", "cpp", "unit testing"],
    "junit": ["testing", "java", "unit testing"],
    "unit testing": ["testing"],
    "integration testing": ["testing"],

    # tooling / practice
    "github": ["git", "version control"],
    "gitlab": ["git", "version control"],
    "git": ["version control"],
    "rest": ["api"],
    "graphql": ["api"],
    "grpc": ["api"],
    "figma": ["ui design"],

    # languages implying paradigms used in JDs
    "cpp": ["c", "oop", "programming"],
    "arduino": ["cpp", "embedded"],

    # a named language answers "can you program"
    "python": ["programming"],
    "java": ["programming", "oop"],
    "javascript": ["programming"],
    "c": ["programming"],
    "go": ["programming"],
    "rust": ["programming"],
    "ruby": ["programming"],
    "php": ["programming"],
    "swift": ["programming"],
    "csharp": ["programming", "oop"],

    # frontend / backend framing used by postings
    "react": ["javascript", "html", "css", "frontend"],
    "vue": ["javascript", "html", "frontend"],
    "svelte": ["javascript", "html", "frontend"],
    "angular": ["typescript", "javascript", "html", "frontend"],
    "flask": ["python", "api", "backend"],
    "fastapi": ["python", "api", "rest", "backend"],
    "django": ["python", "api", "backend"],
    "express": ["node", "javascript", "backend"],
    "spring": ["java", "backend"],
}

# A scoring implication is not automatically permission to write a claim. Matching can be
# generous and transitive; authorship must be narrow and explicit. These one-hop relations
# are the small set of phrases that can be surfaced without changing the underlying fact.
# Deliberately absent: TypeScript/React -> JavaScript and C++/Java -> OOP.
REWRITE_IMPLIES = {
    "tailwind": ["css"],
    "bootstrap": ["css"],
    "scss": ["css"],
    "sass": ["css"],
    "postgresql": ["sql"],
    "mysql": ["sql"],
    "sqlite": ["sql"],
    "sql server": ["sql"],
    "pytest": ["unit testing"],
    "vitest": ["unit testing"],
    "jest": ["unit testing"],
    "google test": ["unit testing"],
    "junit": ["unit testing"],
    "rest": ["api"],
    "flask": ["backend"],
    "fastapi": ["backend"],
    "django": ["backend"],
    "express": ["backend"],
    "spring": ["backend"],
}

# Flattened views of the tables above. The literals keep their readable spelling; every
# lookup goes through these, so "styled-components" and "styled components" are one key.
_IMPLIES_FLAT = {
    flatten_punctuation(specific): [flatten_punctuation(g) for g in generals]
    for specific, generals in IMPLIES.items()
}
_REWRITE_FLAT = {
    flatten_punctuation(specific): [flatten_punctuation(g) for g in generals]
    for specific, generals in REWRITE_IMPLIES.items()
}

MAX_DEPTH = 3

# The tables above are the seed. Everything the model has been asked about since lives in
# `skill_relations` and is merged in here, so the graph grows past what someone typed by
# hand without any call site needing to know. Import is deferred: skill_relations imports
# match.py, and a module-level import both ways would be a cycle.


def _learned(direction, skill):
    from services import skill_relations

    if direction == "implies":
        return skill_relations.cached_implies(skill)
    if direction == "rewrite":
        return skill_relations.cached_rewrite_implies(skill)
    return skill_relations.cached_evidenced_by(skill)


def _parents(skill):
    """Seed edges first, then anything learned. Order is stable so scoring is reproducible."""
    parents = list(_IMPLIES_FLAT.get(flatten_punctuation(skill), ()))
    for general in _learned("implies", skill):
        if general not in parents:
            parents.append(general)
    return parents


def seed_implied_by(skill):
    """Generals reachable from the hand-written table alone, transitively.

    Deliberately excludes learned edges: this is used while *validating* a learned answer, and
    checking a new edge against edges of the same provenance would let one bad answer vouch
    for the next.
    """
    from services.match import normalize_skill

    skill = normalize_skill(skill)
    seen, frontier = [], [skill]
    for _ in range(MAX_DEPTH):
        nxt = []
        for item in frontier:
            for parent in _IMPLIES_FLAT.get(flatten_punctuation(item), ()):
                if parent not in seen and parent != skill:
                    seen.append(parent)
                    nxt.append(parent)
        if not nxt:
            break
        frontier = nxt
    return seen


def implied_by(skill):
    """Everything this skill is evidence of for matching. Transitive
    (supabase → postgresql → sql), depth-capped so a bad edge can't loop forever.

    Normalizes its own input. Every caller already did, but a function that silently returns
    nothing for "sklearn" while working for "scikit learn" is a trap, and the cost is one
    dictionary lookup.
    """
    from services.match import normalize_skill

    skill = normalize_skill(skill)
    seen = []
    frontier = [skill]
    for _ in range(MAX_DEPTH):
        nxt = []
        for item in frontier:
            for parent in _parents(item):
                if parent not in seen and parent != skill:
                    seen.append(parent)
                    nxt.append(parent)
        if not nxt:
            break
        frontier = nxt
    return seen


def rewrite_implied_by(skill, approved=frozenset()):
    """Claims a rewrite may surface from an explicitly named technology.

    Unlike ``implied_by``, this never traverses. A two-hop path such as
    Google Test -> C++ -> OOP is useful for search/scoring and unsafe for authorship.

    Only the hand-written table is authority here. A model-learned edge has to arrive in
    `approved` — the set of (specific, general) pairs this particular user has agreed to —
    because one wrong model answer would otherwise become every user's permission to write a
    claim they never made (AE-03). Scoring keeps using learned edges freely; that is a number,
    not a sentence in somebody's resume.
    """
    from services.match import normalize_skill

    canonical = normalize_skill(skill)
    allowed = list(_REWRITE_FLAT.get(canonical, ()))
    for general in _learned("rewrite", canonical):
        if general not in allowed and (canonical, general) in approved:
            allowed.append(general)
    return allowed


def proposed_rewrite_edges(skill):
    """Learned edges the model thinks are writeable but nobody has approved yet."""
    from services.match import normalize_skill

    canonical = normalize_skill(skill)
    seeded = set(_REWRITE_FLAT.get(canonical, ()))
    return [general for general in _learned("rewrite", canonical) if general not in seeded]


def seed_vocabulary():
    """Only the hand-written names. Used to decide what still needs asking about."""
    known = set(_IMPLIES_FLAT)
    for parents in _IMPLIES_FLAT.values():
        known.update(parents)
    known.update(flatten_punctuation(name) for name in ALIASES)
    known.update(flatten_punctuation(name) for name in ALIASES.values())
    return known


def vocabulary():
    """Every skill name the graph and alias table know, longest first so multi-word terms
    match before their pieces ("machine learning" before "learning")."""
    from services import skill_relations

    known = seed_vocabulary()
    known.update(skill_relations.every_known_name())
    return sorted(known, key=len, reverse=True)


def evidence_for(skill):
    """Which specific skills would count as evidence of this one. A search for CSS uses this
    to reach a Tailwind bullet."""
    skill = flatten_punctuation(skill)
    found = {
        specific for specific, parents in _IMPLIES_FLAT.items()
        if skill in parents or skill in implied_by(specific)
    }
    found.update(_learned("evidenced_by", skill))
    found.discard(skill)
    return sorted(found)
