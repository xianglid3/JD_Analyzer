"""Answer key for skill matching.

Every case is a requirement, the evidence a resume provides, and the state it should get.
Rows came from real runs: each false gap the owner reported is in here as a regression.
No model runs — this is deterministic code, so it costs nothing and can gate CI.
"""

import pytest

from services.claim_check import unsupported_claims
from services.skill_evidence import EXPLICIT, INFERRED, NONE, PARTIAL, evaluate_requirement


def sk(*names):
    return [{"bullet_id": f"s{i}", "kind": "skill", "text": n} for i, n in enumerate(names)]


def bl(*texts):
    return [{"bullet_id": f"b{i}", "kind": "project", "text": t} for i, t in enumerate(texts)]


# requirement, evidence, expected state, why it's here
GOLD = [
    # --- named outright ---
    ("React", sk("React"), EXPLICIT, "listed in skills"),
    ("Python", bl("Built an ingestion pipeline in Python"), EXPLICIT, "named in a bullet"),
    ("Git", sk("Java", "Python", "Git"), EXPLICIT, "skills line is evidence"),
    ("PostgreSQL", sk("Postgres"), EXPLICIT, "alias"),
    ("JavaScript", sk("JS"), EXPLICIT, "alias"),
    ("Node.js", sk("nodejs"), EXPLICIT, "alias"),

    # --- implied by a named technology ---
    ("CSS", bl("Built responsive interfaces using Tailwind"), INFERRED, "tailwind is css"),
    ("JavaScript", bl("Built the frontend in React"), INFERRED, "react is javascript"),
    ("HTML", sk("React"), INFERRED, "react implies html"),
    ("Python", sk("FastAPI"), INFERRED, "fastapi is python"),
    ("SQL", sk("PostgreSQL"), INFERRED, "postgres is sql"),
    ("SQL", sk("Supabase"), INFERRED, "two hops: supabase → postgres → sql"),
    ("software testing", bl("Validated with 15+ Google Test unit tests"), INFERRED, "google test"),
    ("testing", sk("pytest"), INFERRED, "pytest is testing"),
    ("version control", sk("GitHub"), INFERRED, "github implies git implies version control"),
    ("CI/CD", sk("GitHub Actions"), INFERRED, "gh actions is cicd"),
    ("containerization", sk("Docker"), INFERRED, "docker"),
    ("cloud", sk("AWS"), INFERRED, "aws is cloud"),
    ("AI", bl("Developed a gesture recognition system with FEAGI"), INFERRED, "gesture recognition"),
    ("machine learning", sk("PyTorch"), INFERRED, "pytorch"),
    ("API", sk("FastAPI"), INFERRED, "fastapi implies api"),
    ("backend", bl("Created REST APIs using Flask"), INFERRED, "flask is backend work"),
    ("frontend", sk("Vue"), INFERRED, "vue is frontend"),
    ("programming", sk("Java"), INFERRED, "any named language"),
    ("relational databases", sk("MySQL"), INFERRED, "phrasing + alias"),
    ("Artificial Intelligence", sk("Machine Learning"), INFERRED, "phrasing + edge"),

    # --- only the general skill is present ---
    ("AWS", bl("Deployed the app to the cloud"), PARTIAL, "cloud is weaker than aws"),
    ("Tailwind", sk("CSS"), PARTIAL, "reverse direction never reaches inferred"),
    ("PyTorch", sk("machine learning"), PARTIAL, "general to specific"),

    # --- genuinely absent ---
    ("Kubernetes", sk("Docker"), NONE, "docker does not imply k8s"),
    ("Terraform", bl("Deployed services to Kubernetes"), NONE, "different tool"),
    ("Rust", sk("Go", "Python"), NONE, "unrelated language"),
    ("Salesforce", bl("Built a calendar app with React"), NONE, "unrelated entirely"),
    ("C", bl("Wrote CSS and JavaScript for the dashboard"), NONE, "c must not match css"),
    ("Java", sk("JavaScript"), NONE, "java is not javascript"),
    ("R", bl("Built REST APIs"), NONE, "single letter must not match inside words"),

    # --- spellings and separators (found by probing, not by writing the code) ---
    ("C++", sk("Java, C/C++, Python"), EXPLICIT, "slash-joined list, and c++ normalizes to cpp"),
    ("C", sk("Java, C/C++, Python"), EXPLICIT, "the C in C/C++"),
    ("C#", sk("C# and .NET"), EXPLICIT, "hash survives normalization"),
    ("Node.js", sk("nodejs, express"), EXPLICIT, "dotted spelling"),
    ("Go", sk("Django"), NONE, "must not match inside a word"),
    ("Go", bl("Going to production weekly"), NONE, "must not match a prefix"),
    ("React", bl("Reacted to incidents on call"), NONE, "must not match a prefix"),
    ("SQL", sk("NoSQL"), NONE, "nosql is not sql"),
    ("AWS", sk("AWS Lambda"), EXPLICIT, "named inside a longer term"),

    # --- punctuation in ordinary prose (found in review, not by the code's own tests) ---
    ("Python", bl("Built services in Python."), EXPLICIT, "trailing period"),
    ("React", bl("React-based interfaces for the dashboard"), EXPLICIT, "hyphenated compound"),
    ("Go", bl("Used Go; shipped it weekly"), EXPLICIT, "semicolon"),
    ("Flask", bl("Wrote the backend (Flask) and the client"), EXPLICIT, "parentheses"),
    ("Python", bl("Python's ecosystem made it quick"), EXPLICIT, "possessive"),
    ("Docker", bl('Containerised everything, "Docker first"'), EXPLICIT, "quotes and comma"),
    (".NET", bl("Built with .NET Core on Windows"), EXPLICIT, "leading dot must survive"),
    ("Node.js", bl("Used node.js for the API layer"), EXPLICIT, "internal dot"),
    ("CI/CD", bl("Set up CI/CD pipelines in Actions"), EXPLICIT, "slash kept whole"),
    ("scikit-learn", bl("Trained models with scikit-learn"), EXPLICIT, "hyphen kept whole"),
    ("machine learning", bl("Applied machine learning to ranking"), EXPLICIT, "phrase"),
    ("machine learning", bl("Learning machines is fun"), NONE, "phrase order matters"),
    ("C", bl("Wrote CSS and JavaScript for the dashboard."), NONE, "still must not match css"),

    # --- list separators and smart quotes (second review pass) ---
    ("Python", sk("Python,React,SQL"), EXPLICIT, "comma with no space"),
    ("React", sk("Python,React,SQL"), EXPLICIT, "comma with no space, middle item"),
    ("React", sk("Python;React"), EXPLICIT, "semicolon separated"),
    ("C++", sk("Java,C/C++,Python"), EXPLICIT, "the real skills-line format"),
    ("Python", bl("Python\u2019s ecosystem made it quick"), EXPLICIT, "curly apostrophe"),
    ("machine learning", bl("Applied machine-learning to ranking"), EXPLICIT, "hyphen spelling of a phrase"),
    ("React", sk("Vue | React | Svelte"), EXPLICIT, "pipe separated"),
]


@pytest.mark.parametrize("requirement, evidence, expected, why", GOLD, ids=[
    f"{r}-{e}" for r, _, e, _ in GOLD
])
def test_skill_match_gold(requirement, evidence, expected, why):
    result = evaluate_requirement(requirement, evidence)
    assert result["state"] == expected, (
        f"{requirement} vs {[b['text'] for b in evidence]} → {result['state']}, "
        f"expected {expected} ({why})"
    )


# requirement inference is only half of it: what a rewrite is allowed to say
REWRITE_GOLD = [
    ("Developed RESTful backend services using Flask", ["Created REST APIs using Flask"], [], "rephrasing"),
    ("Built responsive CSS layouts", ["Styled the app with Tailwind"], [], "tailwind licenses css"),
    ("Wrote SQL queries against Postgres", ["Managed a PostgreSQL database"], [], "same tech"),
    ("Built Kubernetes-deployed Flask services", ["Created REST APIs using Flask"], ["kubernetes"], "invented tech"),
    ("Built Flask services on PostgreSQL", ["Created REST APIs using Flask"], ["postgresql"], "invented datastore"),
    ("Owned DevOps for the platform", ["Deployed to Kubernetes"], ["devops"], "discipline claim, not a tool"),
    ("Trained deep learning models in PyTorch", ["Used scikit-learn for classification"], ["deep learning", "pytorch"], "different stack"),
    ("Deployed with Kubernetes.", ["Built Flask APIs."], ["kubernetes"], "punctuation does not hide a claim"),
    ("Shipped React-based interfaces", ["Built the UI in React"], [], "hyphenated compound is supported"),

    # --- invented quantities ---
    ("Processed 2M events daily", ["Processed 2,000,000 events daily"], [], "same number, different notation"),
    ("Cut latency by 30%", ["Improved latency"], ["30%"], "invented metric"),
    ("Shipped 24 releases", ["Shipped releases through 2024"], ["24"], "24 must not match 2024"),
    ("Served 3 regions", ["Deployed across 3 regions"], [], "number is supported"),
    ("Improved throughput 3x", ["Handled 3 services"], ["3x"], "a multiple is not a count"),
    ("Reduced cost by $1.2k", ["Reduced cost"], ["1200"], "currency and units normalize"),

    # --- one phrase is one concept, not its pieces ---
    ("Applied object-oriented programming",
     ["Designed classes using object-oriented programming"], [],
     "OOP is one concept: reading it as oop + programming made the checker contradict itself"),
    ("Practised object-oriented programming", ["Wrote C++ classes"], ["oop"],
     "match credit is not permission to write OOP into a bullet"),
    ("Developed the frontend using JavaScript with React + TypeScript",
     ["Developed the frontend using React + TypeScript"], ["javascript"],
     "React and TypeScript earn JS match credit but do not justify keyword insertion"),
    ("Applied object-oriented programming principles", ["Tested C++ code with Google Test"], ["oop"],
     "Google Test to C++ to OOP is an unsafe transitive authorship claim"),
    ("Built machine learning pipelines", ["Trained models with machine learning"], [],
     "the phrase is supported, and 'learning' is not a separate claim"),
    ("Used natural language processing", ["Applied natural language processing to tickets"], [],
     "three-word phrase claims all three words"),
    ("Did general programming work", ["Deployed to Kubernetes"], ["programming"],
     "a bare piece with nothing behind it is still caught"),
]


@pytest.mark.parametrize("proposed, evidence, expected, why", REWRITE_GOLD, ids=[
    w for _, _, _, w in REWRITE_GOLD
])
def test_rewrite_claim_gold(proposed, evidence, expected, why):
    assert sorted(unsupported_claims(proposed, evidence)) == sorted(expected), why
