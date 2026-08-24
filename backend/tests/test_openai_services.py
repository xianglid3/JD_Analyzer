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
