ALIASES = {
    # languages
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "golang": "go",
    "c#": "csharp",
    "c sharp": "csharp",
    "c++": "cpp",
    "f#": "fsharp",
    "objective-c": "objectivec",
    "objective c": "objectivec",

    # frontend
    "reactjs": "react",
    "react js": "react",
    "nextjs": "next",
    "next js": "next",
    "vuejs": "vue",
    "vue js": "vue",
    "angularjs": "angular",
    "tailwindcss": "tailwind",

    # backend / runtime / frameworks
    "nodejs": "node",
    "node js": "node",
    "expressjs": "express",
    "nestjs": "nest",
    "dotnet": ".net",
    "dot net": ".net",
    ".net core": ".net",
    "ror": "rails",
    "ruby on rails": "rails",
    "spring boot": "spring",

    # databases
    "postgres": "postgresql",
    "postgre": "postgresql",
    "postgre sql": "postgresql",
    "psql": "postgresql",
    "mongo": "mongodb",
    "my sql": "mysql",
    "mssql": "sql server",
    "ms sql": "sql server",
    "microsoft sql server": "sql server",
    "dynamo": "dynamodb",

    # cloud / devops / dev tools
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "amazon web services": "aws",
    "k8s": "kubernetes",
    "ci/cd": "cicd",
    "ci-cd": "cicd",
    "gh actions": "github actions",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "intellij idea": "intellij",

    # data / ml / analytics
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tensor flow": "tensorflow",
    "py torch": "pytorch",
    "matlabs": "matlab",
    "power bi": "powerbi",
    "power query": "powerquery",
    "jupyter notebook": "jupyter",
    "jupyter notebooks": "jupyter",
    "open cv": "opencv",
    "hugging face": "huggingface",

    # office / design
    "ms excel": "excel",
    "microsoft excel": "excel",
    "power point": "powerpoint",
    "ms powerpoint": "powerpoint",
    "adobe photoshop": "photoshop",
    "adobe illustrator": "illustrator",
    "adobe xd": "xd",

    # generic phrasings of things the graph already knows
    "artificial intelligence": "ai",
    "genai": "ai",
    "generative ai": "ai",
    "ai-enabled": "ai",
    "ai enabled": "ai",
    "software testing": "testing",
    "test automation": "testing",
    "automated testing": "testing",
    "version control systems": "version control",
    "relational databases": "database",
    "relational database": "database",
    "object oriented programming": "oop",
    "cloud platforms": "cloud",
    "cloud computing": "cloud",
    "containers": "containerization",
    "infrastructure-as-code": "infrastructure as code",
    "continuous integration": "cicd",
    "continuous deployment": "cicd",
    "front end": "frontend",
    "front-end": "frontend",
    # reviewed category phrasing: a posting's "front-end frameworks" is the frontend the
    # hand-written table already derives from React, Vue, Angular and Svelte
    "front end framework": "frontend",
    "front end frameworks": "frontend",
    "front-end framework": "frontend",
    "front-end frameworks": "frontend",
    "frontend framework": "frontend",
    "frontend frameworks": "frontend",
    "back end": "backend",
    "back-end": "backend",
    "full stack": "fullstack",
    "full-stack": "fullstack",

    # common multi-word equivalents (surfaced by the eval)
    "systems design": "system design",
    "large language models": "llm",
    "large language model": "llm",
    "apis": "api",
    "restful apis": "api",
    "restful api": "api",
    "rest api": "api",
}


def flatten_punctuation(text):
    """Hyphens and underscores are punctuation, not meaning.

    "object-oriented design", "object oriented design" and "object_oriented_design" are one
    concept, and they used to normalize to three different keys. Everything downstream is
    keyed on this: the alias table, the skill graph, the learned-relation cache. Three keys
    meant the same term was looked up — and paid for — more than once, and a relation learned
    under one spelling was invisible to the others.
    """
    return " ".join(str(text).replace("_", " ").replace("-", " ").split())


# alias lookups have to be punctuation-insensitive too, or "objective-c" would stop resolving
# the moment the query spelled it "objective c"
_ALIAS_BY_FLAT = {
    flatten_punctuation(variant): flatten_punctuation(canonical)
    for variant, canonical in ALIASES.items()
}


def normalize_skill(skill):
    s = str(skill).strip().lower().removesuffix(".js")   # React.js -> react
    flat = flatten_punctuation(s)
    return _ALIAS_BY_FLAT.get(flat, flat)                # "js" -> "javascript", else unchanged


def compute_match(job_skills, resume_skills):
    if not job_skills:
        return None

    # Match on canonical keys, but keep the JD's first spelling for user-facing chips.
    job_display = {}
    for skill in job_skills:
        normalized = normalize_skill(skill)
        job_display.setdefault(normalized, skill.strip())

    job_set = set(job_display)
    resume_set = {normalize_skill(s) for s in resume_skills}

    matched = sorted((job_display[key] for key in job_set & resume_set), key=str.casefold)
    missing = sorted((job_display[key] for key in job_set - resume_set), key=str.casefold)
    score = round(len(matched) / len(job_set) * 100, 2)

    return {
        "score": score,
        "matched": matched,
        "missing": missing,
    }

def compute_match_score(job_skills, resume_skills):
    result = compute_match(job_skills, resume_skills)
    return result["score"] if result else None
