import json
import importlib
import os
from pathlib import Path

import pytest

from evals.run_real_resume_v2_eval import (
    expected_action_problems,
    interactive_review,
    select_jobs,
)


def test_real_resume_runner_accepts_a_comma_separated_job_subset():
    selected = select_jobs('palantir,aerotech')
    assert [item[1] for item in selected] == ['palantir_swe', 'aerotech_software']


def test_importing_eval_helpers_does_not_change_runtime_configuration(monkeypatch):
    from evals import run_real_resume_v2_eval as real_runner
    from evals import run_tailoring_v2_eval as tailoring_runner

    monkeypatch.setenv('SUPABASE_URL', 'postgresql://example.invalid/application')
    monkeypatch.setenv('TAILORING_REVIEW_V2_ENABLED', 'sentinel')
    monkeypatch.delenv('TAILORING_REVIEW_V2_LOG_RAW', raising=False)

    importlib.reload(tailoring_runner)
    importlib.reload(real_runner)

    assert os.environ['SUPABASE_URL'] == 'postgresql://example.invalid/application'
    assert os.environ['TAILORING_REVIEW_V2_ENABLED'] == 'sentinel'
    assert 'TAILORING_REVIEW_V2_LOG_RAW' not in os.environ


def test_real_resume_runner_retries_transient_job_analysis(monkeypatch):
    from evals import run_real_resume_v2_eval as runner
    calls = []
    expected = object()

    def analyze(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("service_unavailable_error")
        if len(calls) == 2:
            raise ValueError("malformed model JSON")
        return expected

    delays = []
    monkeypatch.setattr(runner, "analyze_job_description", analyze)

    assert runner.analyze_with_retries("raw posting", sleep=delays.append) is expected
    assert len(calls) == 3
    assert delays == [1, 2]


def test_vague_resume_is_a_key_aligned_control_with_only_intended_text_changes():
    here = Path(__file__).resolve().parents[1] / 'evals'
    polished_path = here / 'real_resume.json'
    vague_path = here / 'real_resume_vague.json'
    if not polished_path.exists() or not vague_path.exists():
        pytest.skip('private real-resume fixtures are intentionally not committed')
    polished = json.loads(polished_path.read_text())
    vague = json.loads(vague_path.read_text())

    def bullets(fixture):
        return {bullet['key']: bullet['text']
                for entry in fixture['entries'] for bullet in entry['bullets']}

    polished_bullets, vague_bullets = bullets(polished), bullets(vague)
    assert vague['_paired_polished_fixture'] == 'real_resume.json'
    assert vague_bullets.keys() == polished_bullets.keys()
    assert {key for key in polished_bullets if polished_bullets[key] != vague_bullets[key]} == {
        'jobmatcha_grounding', 'jobmatcha_postgres', 'jobmatcha_worker',
        'jobmatcha_idempotency', 'jobmatcha_delivery',
    }
    assert vague['skills'] == polished['skills']
    assert polished['expected_actions_by_job']['google_swe_sre']['jobmatcha_grounding'] == 'keep'
    assert vague['expected_actions_by_job']['google_swe_sre']['jobmatcha_grounding'] == 'ask'


def test_owner_authored_action_controls_score_selected_questions():
    resume = {
        'expected_actions_by_job': {
            'google_swe_sre': {'strong': 'keep', 'vague': 'ask'},
        },
    }
    bullet_ids = {'strong': 'b1', 'vague': 'b2'}
    run = {
        'detail_requests': [{'bullet_id': 'b2', 'question': 'What did you build?'}],
        'reviews': {'b1': {'decision': 'KEEP'}, 'b2': {'decision': 'ASK'}},
    }

    assert expected_action_problems('google_swe_sre', resume, run, bullet_ids) == []
    assert expected_action_problems('palantir_swe', resume, run, bullet_ids) == []

    run['detail_requests'] = []
    assert expected_action_problems('google_swe_sre', resume, run, bullet_ids) == [
        'vague: expected ask, got keep',
    ]


def test_human_review_pauses_for_bullet_and_every_question(tmp_path):
    labels = tmp_path / "labels.json"
    run = {
        "id": "run-1",
        "reviews": {
            "bullet-1": {
                "decision_claimed": "ASK",
                "decision_reason": "The contribution is unclear.",
                "question_candidates": [
                    {"id": "c1", "question": "What did you build?"},
                    {"id": "c2", "question": "What challenges did you face?"},
                ],
            }
        },
    }
    fixtures = {"backend": {"key": "backend", "text": "Worked on the backend."}}
    answers = iter(["a", "u", "l"])
    prompts = []

    data = interactive_review(
        "real_job", run, fixtures, {"backend": "bullet-1"}, labels,
        coordinator={
            "selected_ids": ["bullet-1:c1"],
            "rejected": [{"id": "bullet-1:c2", "reason": "low_value"}],
        },
        input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1],
        print_fn=lambda *_args: None,
    )

    assert len(prompts) == 3
    assert data["bullet_reviews"][0]["human_action"] == "ask"
    assert [item["human_label"] for item in data["question_reviews"]] == [
        "useful", "low_value",
    ]
    assert json.loads(labels.read_text()) == data


def test_repeated_runs_keep_separate_labels_and_hide_model_judgment(tmp_path):
    labels = tmp_path / 'labels.json'
    fixtures = {'b': {'text': 'Built a worker with expiring leases.'}}
    output = []
    for run_id in ['first', 'second']:
        run = {'id': run_id, 'reviews': {'b-id': {
            'decision': 'ASK', 'decision_reason': 'SECRET model justification',
            'question_candidates': [{'id': 'c1', 'question': 'What did you build?',
                                     'why_it_matters_for_this_job': 'SECRET persuasion'}],
        }}}
        answers = iter(['k', 'd'])
        data = interactive_review(
            'google', run, fixtures, {'b': 'b-id'}, labels,
            coordinator={'selected_ids': ['b-id:c1']},
            input_fn=lambda _: next(answers), print_fn=output.append,
        )
    assert len(data['bullet_reviews']) == 2
    assert len(data['question_reviews']) == 2
    assert 'SECRET' not in '\n'.join(output)
    assert '[selected]' not in '\n'.join(output)
    assert '"duplicate_or_answered": 1' in output[-1]


def test_snapshot_can_be_reviewed_without_database_or_paid_calls(tmp_path, monkeypatch):
    from evals import run_real_resume_v2_eval as runner
    from evals.question_artifacts import save_artifact
    path = save_artifact('google', {'id': 'r', 'reviews': {}},
                         {'b': {'text': 'Built a thing.'}}, {'b': 'b-id'}, {},
                         {'job': {'raw_description': 'A real posting'}}, [], tmp_path)
    seen = []
    monkeypatch.setattr(runner, 'interactive_review', lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(runner, 'refuse_unless_test_database',
                        lambda *_: (_ for _ in ()).throw(AssertionError('DB reached')))
    monkeypatch.setattr('sys.argv', ['eval', '--review-snapshot', str(path)])
    assert runner.main() == 0
    assert seen[0][0][0] == 'google'
    assert seen[0][1]['job_context']['raw_description'] == 'A real posting'


def test_contract_selector_is_explicit_and_clears_other_rollouts(monkeypatch):
    from evals.run_real_resume_v2_eval import configure_contract
    for key in (
        'TAILORING_FOCUSED_REVIEW_USERS', 'TAILORING_FOCUSED_REVIEW_PERCENT',
        'TAILORING_REVIEW_V2_USERS', 'TAILORING_REVIEW_V2_PERCENT',
    ):
        monkeypatch.setenv(key, 'stale')

    configure_contract('focused_v1')
    assert __import__('os').environ['TAILORING_FOCUSED_REVIEW_ENABLED'] == '1'
    assert __import__('os').environ['TAILORING_REVIEW_V2_ENABLED'] == '0'
    assert __import__('os').environ['TAILORING_FOCUSED_REVIEW_USERS'] == ''

    configure_contract('v2')
    assert __import__('os').environ['TAILORING_FOCUSED_REVIEW_ENABLED'] == '0'
    assert __import__('os').environ['TAILORING_REVIEW_V2_ENABLED'] == '1'
