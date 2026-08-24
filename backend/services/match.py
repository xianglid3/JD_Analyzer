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

    # common multi-word equivalents (surfaced by the eval)
    "systems design": "system design",
    "large language models": "llm",
    "large language model": "llm",
    "apis": "api",
    "restful apis": "api",
    "restful api": "api",
    "rest api": "api",
}


def normalize_skill(skill):
    s = skill.strip().lower().replace("_", " ").removesuffix(".js")   # model_distillation -> model distillation; React.js -> react
    return ALIASES.get(s, s)                                           # "js" -> "javascript", else unchanged


def compute_match(job_skills, resume_skills):
    if not job_skills:
        return None

    job_set = {normalize_skill(s) for s in job_skills}
    resume_set = {normalize_skill(s) for s in resume_skills}

    matched = sorted(job_set & resume_set)
    missing = sorted(job_set - resume_set)
    score = round(len(matched) / len(job_set) * 100, 2)

    return {
        "score": score,
        "matched": matched,
        "missing": missing,
    }

def compute_match_score(job_skills, resume_skills):
    result = compute_match(job_skills, resume_skills)
    return result["score"] if result else None