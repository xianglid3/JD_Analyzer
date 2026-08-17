import json
from openai import OpenAI
from pydantic import BaseModel
from typing import Optional, Literal

client = OpenAI()

#   Declares the structure we expect back from LLM by:
#       1. field (title, summary ...)
#       2. its allowed type (Optional[str] -> a str or null)
#       3. Default ( None -> set to null if AI omits)
class JobExtraction(BaseModel): 
    title: Optional[str] = None
    summary: Optional[str] = None
    no_bs_translation: Optional[str] = None
    skills: list[str] = []
    company_name: Optional[str] = None
    location: Optional[str] = None
    work_type: Optional[Literal["remote", "hybrid", "in_person"]] = None


SYSTEM_PROMPT = """You are a job description analyst. Extract key information and return ONLY valid JSON with this exact shape:
{
  "title": "<actual role name, not marketing fluff>",
  "summary": "<2-3 neutral, factual sentences describing the role and responsibilities>",
  "no_bs_translation": "<2-3 blunt sentences cutting through corporate speak: what this job actually is and what they really want>",
  "skills": ["<skill>", "..."],
  "company_name": "<company name or null>",
  "location": "<city/region or null>",
  "work_type": "<'remote' | 'hybrid' | 'in_person' | null>",
}
Rules: max 15 skills, hard skills + explicitly required soft skills only. Use null (not empty string) when a field is unknown. Only use facts explicitly in the text. Do not guess. Use null for unknown scalar fields and [] for unknown lists.

"""

def analyze_job_description(text):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        timeout=30,
    )

    content = response.choices[0].message.content
    data = json.loads(content)

    job = JobExtraction(**data)
    job.skills = list(dict.fromkeys(job.skills))
    return job


