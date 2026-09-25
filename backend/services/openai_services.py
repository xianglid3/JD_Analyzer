import json
import logging
import time
from openai import OpenAI
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, Literal
from services.match import normalize_skill

logger = logging.getLogger(__name__)

# The SDK retries twice by default. Because each attempt has its own timeout, one 60-second
# extraction could otherwise leave the browser waiting for more than three minutes. Routes
# already return clear retryable errors, so keep interactive requests to one bounded attempt.
client = OpenAI(max_retries=0)

# bump on every prompt edit — an eval score only means something against the prompt that produced it
PROMPT_VERSION = "2026-09-14"

# gpt-4o-mini list price, USD per million tokens. Only used for logging, so being a little
# stale is fine — it turns token counts into a number you can reason about.
PRICE_PER_MTOK = {"input": 0.15, "output": 0.60}


def report_usage(model, usage, latency_ms):
    """Log the call. Recording it against a user is `usage.Budget`'s job, not this one's."""
    logger.info("llm model=%s prompt_version=%s latency_ms=%.0f prompt=%d completion=%d cost=$%.5f",
                model, PROMPT_VERSION, latency_ms,
                usage.prompt_tokens, usage.completion_tokens,
                usd(usage.prompt_tokens, usage.completion_tokens))


def usd(prompt_tokens, completion_tokens):
    return (prompt_tokens * PRICE_PER_MTOK["input"]
            + completion_tokens * PRICE_PER_MTOK["output"]) / 1_000_000

# nobody can show evidence of "debugging" or "documentation", so these must never become
# requirements — nothing could ever satisfy them and every run reports them as gaps
VAGUE_REQUIREMENTS = {
    "programming", "coding", "software development", "software engineering",
    "software design", "engineering design", "development", "debugging",
    "documentation", "technical documentation", "computer science",
    "problem solving", "problem-solving", "troubleshooting", "analytical skills",
    "attention to detail", "best practices", "code quality", "clean code",
    "software", "technology", "engineering", "programming languages",
}

MAX_ELIGIBILITY = 6
# A canonical skill name is a term. Anything longer is a sentence the model failed to reduce,
# and it poisons everything downstream: it never matches evidence, so it is a permanent gap,
# and it renders as a paragraph in a list of chips.
MAX_SKILL_NAME_CHARS = 60
# the prompt asks for 15; grouped alternatives can push the flat list past it, so it is
# enforced here too rather than trusted
MAX_SKILLS = 15


def usable_terms(terms):
    """Trim, drop vague filler, and de-duplicate case-insensitively, keeping first order."""
    seen, kept = set(), []
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            continue
        term = " ".join(term.replace("_", " ").split())
        key = term.casefold()
        if (
            key in seen
            or len(term) > MAX_SKILL_NAME_CHARS
            or normalize_skill(term) in VAGUE_REQUIREMENTS
        ):
            continue
        seen.add(key)
        kept.append(term)
    return kept

#   Declares the structure we expect back from LLM by:
#       1. field (title, summary ...)
#       2. its allowed type (Optional[str] -> a str or null)
#       3. Default ( None -> set to null if AI omits)
class RequirementCondition(BaseModel):
    """Alternatives inside one requirement: "one of Java, Python, or C++"."""

    operator: Literal["any_of", "all_of"] = "any_of"
    minimum: int = 1
    items: list[str] = []

    @field_validator("operator", mode="before")
    @classmethod
    def coerce_operator(cls, v):
        return v if v in ("any_of", "all_of") else "any_of"

    @field_validator("minimum", mode="before")
    @classmethod
    def coerce_minimum(cls, v):
        # a non-integer minimum shouldn't discard the group; "at least one" is the safe read
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else 1


class JobRequirement(BaseModel):
    # exactly one of these carries the requirement: `skill` for a single term, `condition`
    # when the posting offered alternatives. Asking for "Java or Python" is ONE requirement —
    # flattening it to two made the denominator wrong before matching even began.
    skill: Optional[str] = None
    condition: Optional[RequirementCondition] = None
    # the posting's own words, so the review screen can show what was actually written
    source_text: Optional[str] = None
    importance: Literal["required", "preferred", "nice_to_have"] = "required"
    # "skill" is scored against the resume; "eligibility" never is — see ELIGIBILITY below.
    # Rows written before this field existed have no type and read back as "skill".
    type: Literal["skill", "eligibility"] = "skill"

    @field_validator("importance", mode="before")
    @classmethod
    def coerce_importance(cls, v):
        # an unknown importance shouldn't discard a real requirement
        return v if v in ("required", "preferred", "nice_to_have") else "required"


class RequirementGroup(BaseModel):
    """One posting phrase offering alternatives, before it becomes a JobRequirement."""

    items: list[str] = []
    minimum: int = 1
    source_text: Optional[str] = None
    importance: Literal["required", "preferred", "nice_to_have"] = "required"

    @field_validator("importance", mode="before")
    @classmethod
    def coerce_importance(cls, v):
        return v if v in ("required", "preferred", "nice_to_have") else "required"

    @field_validator("minimum", mode="before")
    @classmethod
    def coerce_minimum(cls, v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else 1


# The translation's three sections, in order, with the label each is stored under. Stored as
# one labelled text so the column, drafts and edits stay as they are; the page splits it back
# into sections by these labels (frontend `NoBsTranslation`), and an older free-text
# translation, which has none of them, still reads as a paragraph.
TRANSLATION_SECTIONS = (
    ("role", "What the role is"),
    ("skills", "What skills they expect"),
    ("day_to_day", "Day to day"),
)


def join_translation(sections):
    parts = [
        f"{label}: {str(sections.get(key)).strip()}"
        for key, label in TRANSLATION_SECTIONS
        if sections.get(key) and str(sections.get(key)).strip()
    ]
    return "\n\n".join(parts) or None


class JobExtraction(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    no_bs_translation: Optional[str] = None

    @field_validator("no_bs_translation", mode="before")
    @classmethod
    def join_sections(cls, v):
        return join_translation(v) if isinstance(v, dict) else v
    skills: list[str] = []
    requirements: list[JobRequirement] = []
    # kept separate in the model's output: two flat lists are easier for it to fill
    # correctly than one list holding two different shapes. Merged in below.
    requirement_groups: list[RequirementGroup] = []
    eligibility: list[str] = []
    company_name: Optional[str] = None
    location: Optional[str] = None
    work_type: Optional[Literal["remote", "hybrid", "in_person"]] = None

    @field_validator("work_type", mode="before")
    @classmethod
    def coerce_work_type(cls, v):
        # unknown values (e.g. "onsite") → None instead of failing the whole analysis
        return v if v in ("remote", "hybrid", "in_person") else None


SYSTEM_PROMPT = """You are a job description analyst. Extract key information and return ONLY valid JSON with this exact shape:
{
  "title": "<actual role name, not marketing fluff>",
  "summary": "<2-3 neutral, factual sentences describing the role and responsibilities>",
  "no_bs_translation": {"role": "<1-3 sentences>", "skills": "<1-3 sentences>", "day_to_day": "<1-3 sentences>"},
  "skills": ["<skill>", "..."],
  "requirements": [{"skill": "<same skill>", "importance": "<'required' | 'preferred' | 'nice_to_have'>"}, "..."],
  "requirement_groups": [{"items": ["<skill>", "..."], "minimum": <int>, "source_text": "<the posting's own words>", "importance": "<same values>"}, "..."],
  "eligibility": ["<hard gate that is not a skill>", "..."],
  "company_name": "<company name or null>",
  "location": "<city/region or null>",
  "work_type": "<'remote' | 'hybrid' | 'in_person' | null>",
}
Rules for "skills": concrete technical skills only — named programming languages, tools, frameworks, libraries, platforms, AND technical concepts/engineering practices (e.g. data structures, algorithms, system design, distributed systems, unit testing, integration testing, ci/cd). Output each as its short canonical name in lowercase words, never snake_case ("message queue" not "message_queue", "aws" not "AWS cloud services", "c" not "C programming", "api" not "API development"). A skill name is a term, never a sentence or a clause — if it does not fit in a few words it is not a skill. EXCLUDE soft skills and generic traits entirely (communication, teamwork, problem-solving, adaptability, leadership, collaboration, organization, etc.). Max 15. Only skills explicitly named in the text — do not infer or generalize. Use [] when the posting names no concrete hard skills.
Rules for "no_bs_translation": translate the posting into concrete, candid language. Do not merely summarize it. Return an object with exactly these three keys, each 1-3 concise sentences.

For every statement:
- Ground it in the posting. Never invent responsibilities, technologies, systems, scale, ownership, or company details.
- Prefer named products, systems, technologies, users, and responsibilities over broad categories.
- Explain what a named technology or skill would be used for in this role when the posting says.
- Separate fact from reasonable inference. If the posting omits a useful detail, say so plainly instead of filling the gap.

"role":
- Classify the actual work: backend, frontend, full-stack, data, infrastructure, ML, or a mix.
- State what product or system the person would work on and what they would own.
- Distinguish production engineering from prototypes, support, or internal tooling when the posting provides that distinction.
- If the posting does not identify the system, code, or ownership boundary, say that it does not.

"skills":
- Name the concrete technical skills the posting actually requires; do not replace them with "programming knowledge", "computer science fundamentals", or another broad category.
- Separate core requirements from bonuses when the posting does.
- Explain what the important skills appear to be used for here. If the posting lists tools without saying how they are used, say that instead of guessing.

"day_to_day":
- Describe two or three concrete recurring activities supported by the posting.
- Name the likely code, services, data, tests, debugging, deployment, operational work, or user problems only when the posting supports them.
- Mention collaboration only when the posting identifies who the person works with or what they work together on.
- Treat AI-assisted development as a workflow detail unless building AI systems is central to the role.

Be direct and slightly blunt. Strip away recruiting language and say what the job actually is. Avoid generic phrases that could describe almost any engineering job, including "build software tools and systems", "collaborate with engineers", "enhance your workflow", and "work on exciting projects". Do not oversell the role or repeat the posting line by line.

Rules for "eligibility": conditions the candidate either meets or does not, which no resume wording can change — work authorization or visa sponsorship, citizenship or residency, security clearance, willingness to relocate, on-site attendance, a required licence, a background or drug check, a minimum age, a required degree level or field of study, graduation or enrolment timing ("completing or recently completed a Bachelor's"), and any commitment to a start or onboarding date. Quote the posting's own words, briefly. These belong here and NOT in "skills": they cannot be evidenced by experience, so scoring them as skills would report a permanent gap the candidate can do nothing about. A degree requirement is NOT a skill — "computer science" as a field of study belongs here, while "algorithms" as a thing you can do belongs in skills. Use [] when the posting states none.

"requirements" repeats each skill from "skills" that is NOT part of a requirement_group, with how the posting framed it: "required" for must-haves and core responsibilities, "preferred" for nice-to-haves and bonuses, "nice_to_have" for passing mentions. Same names and same order as "skills". When the posting does not distinguish, use "required". Between them, "requirements" and "requirement_groups" must account for every name in "skills" exactly once.

Rules for "requirement_groups": when the posting offers ALTERNATIVES — "experience in one of Java, Python, or C++", "familiarity with React, Vue, or Angular", "at least two of AWS, GCP, or Azure" — that is ONE requirement satisfied by any of the listed options, NOT one requirement per option. Emit it here instead.
- "items": the alternatives, using the same short canonical names as "skills".
- "minimum": how many the candidate needs. "one of" → 1. "at least two of" → 2. Default 1.
- "source_text": the posting's own phrasing, quoted briefly, so the reader recognises it.
- Every name in "items" must ALSO appear in "skills".
- Do NOT also repeat those names in "requirements" — a skill belongs either to a group or to "requirements", never both.
- Only group true alternatives. A list of things the candidate needs ALL of stays in "requirements" as separate entries. When in doubt, do not group.
Use [] when the posting offers no alternatives.
For all other fields: use null (not empty string) when unknown; only use facts explicitly in the text; do not guess.

"""


def _paid(budget, kind, *, model="gpt-4o-mini", timeout=30, **kwargs):
    """Every paid call in this module goes through here.

    `budget` carries who is paying and reserves the call's ceiling before it is made; the
    ceiling is also sent as `max_tokens`, so the reservation is a real bound rather than a
    guess. Calls with no budget (tests, scripts) still run — they simply are not metered.
    """
    from services.usage import ceiling_for

    max_tokens = budget.max_output_tokens if budget is not None else ceiling_for(kind)
    t0 = time.perf_counter()

    def make():
        return client.chat.completions.create(
            model=model, timeout=timeout, max_tokens=max_tokens, **kwargs
        )

    if budget is None:
        response = make()
        report_usage(model, response.usage, (time.perf_counter() - t0) * 1000)
        return response

    with budget.paid_call() as record:
        response = make()
        record["usage"] = response.usage
    report_usage(model, response.usage, (time.perf_counter() - t0) * 1000)
    return response


def analyze_job_description(text, budget=None):
    response = _paid(
        budget, "job_analysis",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,          # deterministic extraction → reproducible + factual
    )

    content = response.choices[0].message.content
    data = json.loads(content)

    job = JobExtraction(**data)
    # normalize first: the model writes software_development as often as software development
    job.skills = usable_terms(job.skills)

    # A group is one requirement however many alternatives it lists. Build them first so the
    # flat reconciliation below can skip the names they already cover — emitting those twice
    # would restore the bug this shape exists to fix.
    groups = []
    for group in job.requirement_groups:
        items = usable_terms(group.items)
        if len(items) < 2:
            # not actually a choice; fall through and let it become a plain requirement
            job.skills = usable_terms(job.skills + items)
            continue
        groups.append(JobRequirement(
            condition=RequirementCondition(
                operator="any_of", minimum=min(group.minimum, len(items)), items=items,
            ),
            source_text=group.source_text,
            importance=group.importance,
        ))

    # every alternative is a real skill, so it belongs in the flat list the chips render
    job.skills = usable_terms(job.skills + [
        item for group in groups for item in group.condition.items
    ])[:MAX_SKILLS]

    grouped = {}
    for group in groups:
        for item in group.condition.items:
            grouped.setdefault(item.casefold(), group)

    # the two lists can drift; skills is the measured one, so it wins
    by_name = {
        r.skill.strip().casefold(): r
        for r in job.requirements if r.skill and r.skill.strip()
    }
    job.requirements, emitted = [], set()
    for skill in job.skills:
        group = grouped.get(skill.casefold())
        if group is None:
            job.requirements.append(by_name.get(skill.casefold(), JobRequirement(skill=skill)))
        elif id(group) not in emitted:
            # the group takes the position of its first alternative
            emitted.add(id(group))
            job.requirements.append(group)

    # eligibility rides along in `requirements` so it reaches the job page, but typed so that
    # scoring and tailoring both skip it: a clearance is not a wording problem
    job.eligibility = [
        condition.strip() for condition in dict.fromkeys(job.eligibility)
        if condition and condition.strip()
    ][:MAX_ELIGIBILITY]
    job.requirements += [
        JobRequirement(skill=condition, type="eligibility", importance="required")
        for condition in job.eligibility
    ]
    return job


class ResumeExtraction(BaseModel):
    skills: list[str]= []


RESUME_PROMPT = """You extract skills from a resume. Return ONLY valid JSON with this exact shape:
{ "skills": ["<skill>", "..."] }
Rules: hard skills, tools, languages, and frameworks explicitly present in the text. Max 30 skills. No soft-skill fluff. Only skills actually in the text — do not infer."""

def analyze_resume(text, budget=None, kind="resume_parse"):
    response = _paid(
        budget, kind,
        messages=[
            {"role": "system", "content": RESUME_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,          # deterministic extraction → reproducible + factual
    )

    content = response.choices[0].message.content
    data = json.loads(content)

    resume = ResumeExtraction(**data)
    resume.skills = list(dict.fromkeys(resume.skills))
    return resume



# ── Structured resume extraction ─────────────────────────────────────────────
MAX_ENTRIES = 30
MAX_BULLETS_PER_ENTRY = 12
MAX_STRUCTURE_SKILLS = 30
MAX_HEADER_LINKS = 5


class ResumeBullet(BaseModel):
    """A bullet, optionally carrying the id it already has.

    Extraction produces bare strings; the review screen sends back the ids it was given so
    an edited bullet keeps its identity instead of becoming a new row.
    """
    id: Optional[str] = None
    text: str

    @model_validator(mode="before")
    @classmethod
    def accept_plain_text(cls, value):
        return {"text": value} if isinstance(value, str) else value


class ResumeEntryExtraction(BaseModel):
    kind: Literal["experience", "project", "education", "certificate", "skill"]
    organization: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    bullets: list[ResumeBullet] = []


class ResumeHeader(BaseModel):
    """Contact details. A rendered resume without these is anonymous."""
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    links: list[str] = []


class ResumeStructure(BaseModel):
    header: ResumeHeader = ResumeHeader()
    entries: list[ResumeEntryExtraction] = []
    skills: list[str] = []


STRUCTURE_PROMPT = """You convert a resume into structured records. Return ONLY valid JSON with this exact shape:
{
  "header": {
    "full_name": "<the candidate's name, or null>",
    "email": "<email address, or null>",
    "phone": "<phone number as written, or null>",
    "location": "<city, state/region, or null>",
    "links": ["<portfolio, GitHub, or LinkedIn URL as written>", "..."]
  },
  "entries": [
    {
      "kind": "<'experience' | 'project' | 'education' | 'certificate'>",
      "organization": "<company / school / issuer, or null>",
      "title": "<role / degree / project name / certificate name, or null>",
      "location": "<city, region, or null>",
      "start_date": "<as written in the resume, e.g. 'Jun 2024', or null>",
      "end_date": "<as written, e.g. 'Present', or null>",
      "bullets": ["<one accomplishment or responsibility, copied verbatim>", "..."]
    }
  ],
  "skills": ["<skill>", "..."]
}
Rules for "bullets": copy the resume's own wording VERBATIM. Do not rewrite, summarize, merge, split, or improve them. Do not invent bullets that are not in the text. One bullet per line item in the resume. Max 12 per entry.
Rules for "skills": hard skills, tools, languages, and frameworks explicitly present. Short canonical names. No soft skills. Max 30.
For every other field: use null when the resume does not state it. Never guess an employer, date, or title that is not written down.
Rules for "header": copy contact details exactly as written, including formatting. Use null for anything the resume does not state — never guess a name or invent an email. Max 5 links.
Max 30 entries, ordered as they appear in the resume."""


def analyze_resume_structure(text, budget=None):
    """Extract entries and verbatim bullets — the evidence tailoring cites."""
    response = _paid(
        budget, "resume_structure",
        messages=[
            {"role": "system", "content": STRUCTURE_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=45,             # stay inside the browser's 50-second deadline
    )

    structure = ResumeStructure(**json.loads(response.choices[0].message.content))
    return enforce_structure_limits(structure)


def enforce_structure_limits(structure):
    """The prompt asks for these caps; this is what enforces them."""
    structure.header.links = list(dict.fromkeys(
        link.strip() for link in structure.header.links if link and link.strip()
    ))[:MAX_HEADER_LINKS]
    structure.entries = structure.entries[:MAX_ENTRIES]
    for entry in structure.entries:
        cleaned = []
        seen = set()
        for bullet in entry.bullets:
            text = bullet.text.strip()
            if text and text not in seen:
                seen.add(text)
                cleaned.append(ResumeBullet(id=bullet.id, text=text))
        entry.bullets = cleaned[:MAX_BULLETS_PER_ENTRY]
    structure.skills = list(dict.fromkeys(s.strip() for s in structure.skills if s.strip()))[:MAX_STRUCTURE_SKILLS]
    return structure


SURFACE_PROMPT = """You add one skill the candidate has confirmed they used to the bullet it belongs on.

You are given the skill, the candidate's own sentence about what they did with it, and the
bullets of the entry they named. Return ONLY valid JSON:
{ "bullet_index": <0-based index of the bullet to rewrite>, "proposed_text": "<the rewrite>" }

Pick the bullet the skill most plausibly applies to. Then rewrite it so a reader can see the
skill in the work.

Rules:
- Keep every fact, technology, number and outcome the bullet already has. This is additive:
  the result should be longer than the original, never shorter.
- You may use the skill's name, and any fact contained in the candidate's own sentence. Nothing
  else — no invented scale, tools, or results.
- One sentence, in the register of the bullet you are rewriting.
- If the candidate's sentence adds a concrete detail, work it in; if it only confirms usage,
  name the skill and leave the rest alone."""


def surface_rewrite(skill, detail, bullets, budget=None):
    """One call: which bullet, and what it should say instead.

    The model never sees a bullet id and never returns one — it picks an index into the list
    it was given, and the caller maps that back. An id it cannot name is an id it cannot
    invent.
    """
    listed = "\n".join(f"{index}. {text}" for index, text in enumerate(bullets))
    response = _paid(
        budget, "surface_skill",
        messages=[
            {"role": "system", "content": SURFACE_PROMPT},
            {"role": "user", "content": json.dumps({
                "skill": skill,
                "what_they_did": detail,
                "bullets": listed,
            })},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(response.choices[0].message.content)
    return {
        "bullet_index": data.get("bullet_index"),
        "proposed_text": (data.get("proposed_text") or "").strip(),
    }


def complete_json(messages, model="gpt-4o-mini", timeout=30, budget=None,
                  kind="skill_relations", schema=None, schema_name="response"):
    """One JSON-mode call. Shared by the smaller enrichment callers that need a model but
    not a whole extraction pipeline."""
    response_format = {"type": "json_object"}
    if schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
    return _paid(
        budget, kind, model=model, timeout=timeout, messages=messages,
        response_format=response_format, temperature=0,
    )
