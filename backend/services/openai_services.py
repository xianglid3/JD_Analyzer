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
PROMPT_VERSION = "2026-08-31"

# gpt-4o-mini list price, USD per million tokens. Only used for logging, so being a little
# stale is fine — it turns token counts into a number you can reason about.
PRICE_PER_MTOK = {"input": 0.15, "output": 0.60}


def report_usage(model, usage, latency_ms, on_usage=None):
    """Log the call, and hand the numbers to whoever knows which user made it."""
    logger.info("llm model=%s prompt_version=%s latency_ms=%.0f prompt=%d completion=%d cost=$%.5f",
                model, PROMPT_VERSION, latency_ms,
                usage.prompt_tokens, usage.completion_tokens,
                usd(usage.prompt_tokens, usage.completion_tokens))
    if on_usage:
        on_usage(model, usage.prompt_tokens, usage.completion_tokens, latency_ms)


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

#   Declares the structure we expect back from LLM by:
#       1. field (title, summary ...)
#       2. its allowed type (Optional[str] -> a str or null)
#       3. Default ( None -> set to null if AI omits)
class JobRequirement(BaseModel):
    skill: str
    importance: Literal["required", "preferred", "nice_to_have"] = "required"

    @field_validator("importance", mode="before")
    @classmethod
    def coerce_importance(cls, v):
        # an unknown importance shouldn't discard a real requirement
        return v if v in ("required", "preferred", "nice_to_have") else "required"


class JobExtraction(BaseModel): 
    title: Optional[str] = None
    summary: Optional[str] = None
    no_bs_translation: Optional[str] = None
    skills: list[str] = []
    requirements: list[JobRequirement] = []
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
  "no_bs_translation": "<see the translation rules below>",
  "skills": ["<skill>", "..."],
  "requirements": [{"skill": "<same skill>", "importance": "<'required' | 'preferred' | 'nice_to_have'>"}, "..."],
  "company_name": "<company name or null>",
  "location": "<city/region or null>",
  "work_type": "<'remote' | 'hybrid' | 'in_person' | null>",
}
Rules for "skills": concrete technical skills only — named programming languages, tools, frameworks, libraries, platforms, AND technical concepts/engineering practices (e.g. data structures, algorithms, system design, distributed systems, unit testing, integration testing, ci/cd). Output each as its short canonical name ("aws" not "AWS cloud services", "c" not "C programming", "api" not "API development"). EXCLUDE soft skills and generic traits entirely (communication, teamwork, problem-solving, adaptability, leadership, collaboration, organization, etc.). Max 15. Only skills explicitly named in the text — do not infer or generalize. Use [] when the posting names no concrete hard skills.
Rules for "no_bs_translation": translate the posting into a direct, no-BS explanation of what the job is actually likely to be like. Return only 1-2 short paragraphs.
Explain:
- what the person will realistically spend most of their time doing
- which requirements are truly important versus wishlist items
- what level of independence and knowledge the company probably expects
- what the role will likely feel like day to day
- any hidden realities such as maintenance work, legacy systems, internal tooling, customer support, meetings, debugging, or production responsibility
- how the company's industry, size, product, and engineering environment change the interpretation of the posting
- whether the expectations make sense for an intern, new grad, or full-time experienced hire
Do not repeat or summarize the posting line by line. Translate corporate language into plain English.
Be willing to say things like "this is mostly backend CRUD work," "this looks more like internal enterprise software than product engineering," "they list AWS, but you probably won't be designing cloud infrastructure," or "the internship posting looks intimidating, but they likely expect fundamentals and the ability to learn rather than mastery of every listed tool."
Separate reasonable inference from fact, and never invent details about the company. The reader should finish knowing: what am I actually signing up to do, and what will they realistically expect from me?

"requirements" repeats every skill from "skills" with how the posting framed it: "required" for must-haves and core responsibilities, "preferred" for nice-to-haves and bonuses, "nice_to_have" for passing mentions. Same names, same order, same count as "skills". When the posting does not distinguish, use "required".
For all other fields: use null (not empty string) when unknown; only use facts explicitly in the text; do not guess.

"""


def analyze_job_description(text, on_usage=None):
    
    t0 = time.perf_counter() #get the time

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,          # deterministic extraction → reproducible + factual
        timeout=30,
    )

    latency_ms = (time.perf_counter() - t0) * 1000 #calculate latency
    report_usage("gpt-4o-mini", response.usage, latency_ms, on_usage)



    content = response.choices[0].message.content
    data = json.loads(content)

    job = JobExtraction(**data)
    # normalize first: the model writes software_development as often as software development
    job.skills = [
        skill for skill in dict.fromkeys(job.skills)
        if normalize_skill(skill) not in VAGUE_REQUIREMENTS
    ]

    # the two lists can drift; skills is the measured one, so it wins
    by_name = {r.skill.strip().lower(): r for r in job.requirements if r.skill and r.skill.strip()}
    job.requirements = [
        by_name.get(skill.strip().lower(), JobRequirement(skill=skill))
        for skill in job.skills
    ]
    return job


class ResumeExtraction(BaseModel):
    skills: list[str]= []


RESUME_PROMPT = """You extract skills from a resume. Return ONLY valid JSON with this exact shape:
{ "skills": ["<skill>", "..."] }
Rules: hard skills, tools, languages, and frameworks explicitly present in the text. Max 30 skills. No soft-skill fluff. Only skills actually in the text — do not infer."""

def analyze_resume(text, on_usage=None):
    t0 = time.perf_counter()

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": RESUME_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,          # deterministic extraction → reproducible + factual
        timeout=30,
    )

    latency_ms = (time.perf_counter() - t0) * 1000
    report_usage("gpt-4o-mini", response.usage, latency_ms, on_usage)

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


def analyze_resume_structure(text, on_usage=None):
    """Extract entries and verbatim bullets — the evidence tailoring cites."""
    t0 = time.perf_counter()

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": STRUCTURE_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=60,             # a whole resume is a bigger output than one posting
    )

    latency_ms = (time.perf_counter() - t0) * 1000
    report_usage("gpt-4o-mini", response.usage, latency_ms, on_usage)

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
