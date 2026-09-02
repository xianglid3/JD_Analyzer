"""Does experience with X count as evidence of Y?

Aliases in match.py handle "same thing, different spelling". This handles implication, and
only ever points from the specific to the general: Tailwind is evidence of CSS, never the
reverse. Hand-written so every inferred match can be explained to the user.
"""

from services.match import ALIASES

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
    "flask": ["python", "api", "backend"],
    "fastapi": ["python", "api", "rest", "backend"],
    "django": ["python", "api", "backend"],
    "express": ["node", "javascript", "backend"],
    "spring": ["java", "backend"],
}

# Targets that describe a discipline rather than a thing you used. Fine for scoring —
# Kubernetes work is evidence of devops — but too strong to assert in a rewrite, where
# "DevOps experience" reads as a claim about the role you held.
DISCIPLINE_CLAIMS = {
    "devops", "ai", "machine learning", "deep learning", "programming",
    "cloud", "fullstack", "embedded", "computer vision",
    "natural language processing", "database", "testing",
}

MAX_DEPTH = 3


def implied_by(skill, for_rewrite=False):
    """Everything this skill is evidence of. Transitive (supabase → postgresql → sql),
    depth-capped so a bad edge can't loop forever.

    for_rewrite drops the discipline-level claims: scoring may count Kubernetes as devops
    experience, a rewritten bullet may not say so.
    """
    seen = []
    frontier = [skill]
    for _ in range(MAX_DEPTH):
        nxt = []
        for item in frontier:
            for parent in IMPLIES.get(item, ()):
                if parent not in seen and parent != skill:
                    seen.append(parent)
                    nxt.append(parent)
        if not nxt:
            break
        frontier = nxt
    if for_rewrite:
        return [s for s in seen if s not in DISCIPLINE_CLAIMS]
    return seen


def vocabulary():
    """Every skill name the graph and alias table know, longest first so multi-word terms
    match before their pieces ("machine learning" before "learning")."""
    known = set(IMPLIES)
    for parents in IMPLIES.values():
        known.update(parents)
    known.update(ALIASES)
    known.update(ALIASES.values())
    return sorted(known, key=len, reverse=True)


def evidence_for(skill):
    """Which specific skills would count as evidence of this one. A search for CSS uses this
    to reach a Tailwind bullet."""
    return sorted(
        specific for specific, parents in IMPLIES.items()
        if skill in parents or skill in implied_by(specific)
    )
