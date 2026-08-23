import pytest
import json
from types import SimpleNamespace
from pydantic import ValidationError
import services.openai_services as svc

# fake OpenAI response so response.choices[0].message.content == json string
def fake_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

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
    # 'onsite' isn't in the Literal[...] → Pydantic should reject it
    payload = '{"title": "X", "work_type": "onsite"}'
    monkeypatch.setattr(svc.client.chat.completions, "create",
                        lambda *a, **k: fake_response(payload))

    with pytest.raises(ValidationError):
        svc.analyze_job_description("any text")
