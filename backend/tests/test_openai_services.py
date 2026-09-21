import pytest
import json
from types import SimpleNamespace
from pydantic import ValidationError
import services.openai_services as svc

# fake OpenAI response: response.choices[0].message.content + response.usage.*
def fake_response(content):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )

def test_valid_extraction(monkeypatch):
    payload = '{"title": "Backend Engineer", "skills": ["Python", "Python", "SQL"]}'
    monkeypatch.setattr(svc.client.chat.completions, "create",
                        lambda *a, **k: fake_response(payload))

    job = svc.analyze_job_description("any text")

    assert job.title == "Backend Engineer"
    assert job.skills == ["Python", "SQL"]   # duplicates removed


def test_malformed_json(monkeypatch):
    # model returns something that isn't JSON → json.loads should blow up
    monkeypatch.setattr(svc.client.chat.completions, "create",
                        lambda *a, **k: fake_response("totally not json"))

    with pytest.raises(json.JSONDecodeError):
        svc.analyze_job_description("any text")


def test_invalid_work_type(monkeypatch):
    # unknown work_type coerces to None instead of killing the analysis (BUG-022)
    payload = '{"title": "X", "work_type": "onsite"}'
    monkeypatch.setattr(svc.client.chat.completions, "create",
                        lambda *a, **k: fake_response(payload))

    job = svc.analyze_job_description("any text")
    assert job.work_type is None
    assert job.title == "X"


# ── requirement groups ───────────────────────────────────────────────────────
# "one of Java, Python, or C++" is ONE requirement. Flattening it made the
# denominator wrong before matching started, so the shape has to survive extraction.

def _extract(monkeypatch, payload):
    monkeypatch.setattr(svc.client.chat.completions, "create",
                        lambda *a, **k: fake_response(json.dumps(payload)))
    return svc.analyze_job_description("any text")


def test_alternatives_become_one_grouped_requirement(monkeypatch):
    job = _extract(monkeypatch, {
        "title": "SWE",
        "skills": ["java", "python", "cpp", "git"],
        "requirements": [{"skill": "git", "importance": "required"}],
        "requirement_groups": [{
            "items": ["java", "python", "cpp"],
            "minimum": 1,
            "source_text": "experience in one of Java, Python, or C++",
            "importance": "required",
        }],
    })

    # four skills, but only two requirements: the group plus git
    assert len(job.requirements) == 2
    group = next(r for r in job.requirements if r.condition)
    assert group.condition.items == ["java", "python", "cpp"]
    assert group.condition.minimum == 1
    assert group.source_text == "experience in one of Java, Python, or C++"
    # the alternatives are not ALSO emitted as standalone requirements
    assert [r.skill for r in job.requirements if r.skill] == ["git"]


def test_group_alternatives_are_added_to_the_flat_skill_list(monkeypatch):
    # the chips render `skills`; an alternative the model forgot to list there
    # would otherwise be scored but never shown
    job = _extract(monkeypatch, {
        "skills": ["java"],
        "requirement_groups": [{"items": ["java", "python"], "minimum": 1}],
    })

    assert job.skills == ["java", "python"]


def test_a_group_with_one_usable_item_is_not_a_choice(monkeypatch):
    # "one of Python" is just Python; a condition here would render as a fake choice
    job = _extract(monkeypatch, {
        "skills": [],
        "requirement_groups": [{"items": ["python", "programming"], "minimum": 1}],
    })

    assert job.skills == ["python"]
    assert [r.skill for r in job.requirements] == ["python"]
    assert all(r.condition is None for r in job.requirements)


def test_minimum_cannot_exceed_the_alternatives_offered(monkeypatch):
    job = _extract(monkeypatch, {
        "skills": ["aws", "gcp"],
        "requirement_groups": [{"items": ["aws", "gcp"], "minimum": 9}],
    })

    assert job.requirements[0].condition.minimum == 2


def test_a_grouped_skill_is_never_emitted_twice(monkeypatch):
    # the model repeating an alternative in `requirements` is the exact drift that
    # would restore the flattening bug
    job = _extract(monkeypatch, {
        "skills": ["java", "python"],
        "requirements": [
            {"skill": "java", "importance": "required"},
            {"skill": "python", "importance": "required"},
        ],
        "requirement_groups": [{"items": ["java", "python"], "minimum": 1}],
    })

    assert len(job.requirements) == 1
    assert job.requirements[0].condition.items == ["java", "python"]


def test_extraction_without_groups_is_unchanged(monkeypatch):
    job = _extract(monkeypatch, {
        "skills": ["python", "sql"],
        "requirements": [{"skill": "python", "importance": "preferred"}],
    })

    assert [r.skill for r in job.requirements] == ["python", "sql"]
    assert job.requirements[0].importance == "preferred"
    assert all(r.condition is None for r in job.requirements)


def test_a_sentence_is_not_a_skill(monkeypatch):
    """A clause the model failed to reduce poisons everything downstream: it can never match
    evidence, so it is a permanent gap, and it renders as a paragraph among skill chips."""
    job = _extract(monkeypatch, {
        "skills": [
            "python",
            "Individuals who are completing or have recently completed a Bachelor's or "
            "above degree in computer science or a related discipline",
        ],
    })

    assert job.skills == ["python"]
    assert [r.skill for r in job.requirements] == ["python"]


def test_snake_case_skill_names_are_written_as_words(monkeypatch):
    # the model emits message_queue as often as "message queue"; matching normalizes both,
    # but only one of them is readable on screen
    job = _extract(monkeypatch, {"skills": ["message_queue", "data_governance"]})

    assert job.skills == ["message queue", "data governance"]


def test_a_long_alternative_does_not_survive_inside_a_group(monkeypatch):
    job = _extract(monkeypatch, {
        "skills": ["spark"],
        "requirement_groups": [{
            "items": [
                "spark",
                "Experience with big data systems and related technologies across a very "
                "large distributed production environment",
            ],
            "minimum": 1,
        }],
    })

    # one usable alternative left, so it is no longer a choice
    assert job.skills == ["spark"]
    assert all(r.condition is None for r in job.requirements)


def test_translation_sections_are_stored_as_labelled_text():
    """The model returns three sections; the column keeps one text, labelled so the page can
    split it back. Missing sections are dropped, and an older plain string passes through."""
    from services.openai_services import JobExtraction

    joined = JobExtraction(no_bs_translation={
        "role": "Backend CRUD in Go.", "skills": " APIs and SQL. ", "day_to_day": "",
    }).no_bs_translation
    assert joined == "What the role is: Backend CRUD in Go.\n\nWhat skills they expect: APIs and SQL."
    assert JobExtraction(no_bs_translation="Old free text.").no_bs_translation == "Old free text."
    assert JobExtraction(no_bs_translation={}).no_bs_translation is None
