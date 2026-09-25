from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from evals.project_question_planner import (
    assemble_reviews,
    evidence_payload,
    plan_project,
    relevance_payload,
    validate_evidence,
    validate_relevance,
)
from evals.run_question_comparison import (
    frozen_inputs,
    generate_comparison,
    iterate_comparison,
    print_saved_trace,
    recover_coordination,
)
from services.bullet_review import ReviewUnavailable

TASK = {'bullet_id': 'b', 'text': 'Worked on background processing.', 'entry': 'Project',
        'siblings': [], 'answers': [], 'supported_explicit': [{'id': 'r1', 'label': 'backend'}],
        'related_inferred': [], 'resume_gaps': [{'id': 'r2', 'label': 'algorithms'}]}
WEAKNESS = {'id': 'w1', 'source_quote': 'Worked on background processing',
            'missing_clause': 'The recovery mechanism the candidate implemented.',
            'why_unresolved': 'The entry names no owned mechanism.',
            'recruiter_doubt_type': 'implementation'}
AUDIT_ROW = {'bullet_id': 'b', 'assessment': 'UNCERTAIN',
             'reason': 'The owned recovery mechanism is unclear.', 'established_facts': [],
             'rewrite_instruction': None, 'weaknesses': [WEAKNESS]}
RELEVANCE_ROW = {'bullet_id': 'b', 'weakness_id': 'w1', 'disposition': 'ASK',
                 'reason': 'Reliable jobs matter to this backend role.',
                 'question': 'What recovery mechanism did you personally implement?',
                 'why_it_matters_for_this_job': 'The role includes reliable backend systems.',
                 'priority': 'high', 'requirement_reference': None}


def project_reviews(tasks=(TASK,)):
    audits = validate_evidence({'bullets': [AUDIT_ROW]}, list(tasks))
    relevance = validate_relevance({'weaknesses': [RELEVANCE_ROW]}, list(tasks), audits)
    return assemble_reviews(list(tasks), audits, relevance)


def artifact():
    job = {'title': 'Engineer', 'company': 'Co', 'summary': 'Backend', 'requirements': ['python']}
    target = {'target_bullet': TASK['text'], 'entry_context': {'name': 'Project', 'sibling_bullets': []},
              'answers_given_in_this_run': [], 'fit_context': {}}
    request = {'job_description': job, 'target': target}
    alternative = {'job_description': job, 'target': {**target, 'bullet': 'b1'}}
    from evals.question_artifacts import source_hashes
    return {'source_hashes': source_hashes(), 'job_name': 'google', 'job': {'raw_description': 'real JD'},
            'fixtures': {'worker': {'text': TASK['text']}}, 'bullet_ids': {'worker': 'b'},
            'run': {'id': 'baseline', 'status': 'waiting_for_user',
                    'reviews': project_reviews()},
            'coordinator': {'selected_ids': ['b:c1'], 'rejected': []},
            'model_calls': [{'model': 'mock', 'messages': [{'role': 'user', 'content': json.dumps(body)}]}
                            for body in [request, alternative]]}


def test_evidence_audit_has_no_job_context_and_requires_an_exact_weak_phrase():
    payload = evidence_payload([TASK])
    assert 'job_description' not in payload
    assert 'fit_context' not in payload['bullets'][0]
    assert 'resume_gaps' not in json.dumps(payload)
    audit = validate_evidence({'bullets': [AUDIT_ROW]}, [TASK])
    assert audit['b']['weaknesses'][0]['missing_clause'].startswith('The recovery mechanism')
    with pytest.raises(ReviewUnavailable, match='source_quote'):
        validate_evidence({'bullets': [{**AUDIT_ROW, 'weaknesses': [
            {**WEAKNESS, 'source_quote': 'Led the team'}
        ]}]}, [TASK])
    with pytest.raises(ReviewUnavailable, match='contradictory'):
        validate_evidence({'bullets': [{**AUDIT_ROW, 'assessment': 'SUFFICIENT'}]}, [TASK])
    with pytest.raises(ReviewUnavailable, match='omitted'):
        validate_evidence({'bullets': []}, [TASK])


def test_relevance_can_only_rank_audited_weaknesses_and_reference_local_fit():
    audits = validate_evidence({'bullets': [AUDIT_ROW]}, [TASK])
    payload = relevance_payload(('Engineer', 'Co', 'Backend', ['algorithms']), [TASK], audits)
    assert payload['audited_targets'][0]['weaknesses'] == [WEAKNESS]
    assert 'resume_gaps' not in payload['audited_targets'][0]['fit_context']
    decision = validate_relevance({'weaknesses': [RELEVANCE_ROW]}, [TASK], audits)
    assert decision[('b', 'w1')]['disposition'] == 'ASK'
    for bad in [
        {**RELEVANCE_ROW, 'weakness_id': 'invented'},
        {**RELEVANCE_ROW, 'requirement_reference': 'r2'},
        {**RELEVANCE_ROW, 'disposition': 'NOT_MATERIAL'},
    ]:
        with pytest.raises(ReviewUnavailable):
            validate_relevance({'weaknesses': [bad]}, [TASK], audits)


def test_planner_enforces_evidence_then_relevance_and_skips_job_for_strong_bullets(monkeypatch):
    from services import openai_services
    calls = []

    def response(data):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(data))
        )])

    def provider(messages, **kwargs):
        calls.append(json.loads(messages[1]['content']))
        if len(calls) == 1:
            return response({'bullets': [AUDIT_ROW]})
        return response({'weaknesses': [RELEVANCE_ROW]})

    monkeypatch.setattr(openai_services, 'complete_json', provider)
    reviews = plan_project(('Engineer', 'Co', 'Backend', ['algorithms']), [TASK], model='mock')
    assert reviews['b']['decision_claimed'] == 'ASK'
    assert reviews['b']['question_candidates'][0]['source_quote'] == WEAKNESS['source_quote']
    assert len(calls) == 2
    assert 'job_description' not in calls[0]
    assert calls[1]['audited_targets'][0]['weaknesses'] == [WEAKNESS]

    calls.clear()
    sufficient = {**AUDIT_ROW, 'assessment': 'SUFFICIENT', 'weaknesses': [],
                  'reason': 'The owned implementation is concrete.'}
    monkeypatch.setattr(openai_services, 'complete_json',
                        lambda messages, **kwargs: (calls.append(messages) or response(
                            {'bullets': [sufficient]}
                        )))
    reviews = plan_project(('Engineer', 'Co', 'Backend', ['algorithms']), [TASK], model='mock')
    assert reviews['b']['decision_claimed'] == 'KEEP'
    assert len(calls) == 1


def test_multiple_questions_require_multiple_audited_weaknesses():
    task = {**TASK, 'text': 'Worked on background processing and monitoring.'}
    second = {
        'id': 'w2', 'source_quote': 'monitoring',
        'missing_clause': 'The monitoring behavior the candidate implemented.',
        'why_unresolved': 'No owned monitoring change is named.',
        'recruiter_doubt_type': 'implementation',
    }
    audit_row = {**AUDIT_ROW, 'weaknesses': [WEAKNESS, second]}
    audits = validate_evidence({'bullets': [audit_row]}, [task])
    second_relevance = {
        **RELEVANCE_ROW, 'weakness_id': 'w2',
        'question': 'What monitoring behavior did you personally implement?',
    }
    relevance = validate_relevance(
        {'weaknesses': [RELEVANCE_ROW, second_relevance]}, [task], audits,
    )
    review = assemble_reviews([task], audits, relevance)['b']
    assert review['decision_claimed'] == 'ASK'
    assert [item['uncertainty_id'] for item in review['question_candidates']] == ['w1', 'w2']
    assert len({item['expected_resume_change'] for item in review['question_candidates']}) == 2

    with pytest.raises(ReviewUnavailable, match='unknown or repeated weakness'):
        validate_relevance({'weaknesses': [RELEVANCE_ROW, second_relevance, {
            **second_relevance, 'weakness_id': 'invented'
        }]}, [task], audits)


def test_frozen_context_is_not_reanalyzed_and_detects_input_drift():
    saved = artifact()
    job, tasks = frozen_inputs(saved)
    assert job == ('Engineer', 'Co', 'Backend', ['python'])
    assert tasks[0]['text'] == TASK['text']
    assert tasks[0]['entry_id'] == 'Project'
    assert tasks[0]['sibling_bullets'] == []
    saved['model_calls'][1]['messages'][0]['content'] = saved['model_calls'][1]['messages'][0]['content'].replace('Backend', 'Different')
    with pytest.raises(ValueError, match='consistent'):
        frozen_inputs(saved)


def test_comparison_saves_partial_failures_without_relabeling_as_keep(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    saved = artifact()
    original = deepcopy(saved)
    def fail(*args, **kwargs):
        raise ReviewUnavailable('bad provider output')
    monkeypatch.setattr(runner, 'plan_project', fail)
    path = generate_comparison(saved, tmp_path / 'comparison')
    assert saved == original
    manifest = json.loads(path.read_text())
    project_arm = next(a for a in manifest['arms'].values()
                       if a['variant'] == 'evidence_first_project_planner')
    project = json.loads((path.parent / project_arm['file']).read_text())
    assert project['run']['status'] == 'failed'
    assert project['run']['reviews']['b']['decision'] == 'REVIEW_UNAVAILABLE'
    assert project_arm['failures'][0]['stage'] == 'project'
    assert (path.parent / 'partial.json').exists()


def test_call_journal_preserves_provider_output_and_failure(tmp_path, monkeypatch):
    from evals.question_artifacts import capture_calls
    from services import openai_services
    calls = []
    def provider(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 2:
            raise RuntimeError('provider failed')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
    monkeypatch.setattr(openai_services, 'complete_json', provider)
    path = tmp_path / 'calls.json'
    with capture_calls(path) as recorded:
        openai_services.complete_json([{'role': 'user', 'content': 'input'}], model='mock')
        with pytest.raises(RuntimeError):
            openai_services.complete_json([{'role': 'user', 'content': 'second'}], model='mock')
    assert json.loads(path.read_text()) == recorded
    assert recorded[0]['raw_response'] == '{"ok":true}'
    assert recorded[1]['error']['type'] == 'RuntimeError'
    assert openai_services.complete_json is provider


def test_changed_baseline_is_rejected_before_paying(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    saved = artifact()
    saved['source_hashes']['bullet_review_v2.py'] = 'old'
    monkeypatch.setattr(runner, 'plan_project', lambda *a, **k: pytest.fail('must not pay'))
    with pytest.raises(ValueError, match='fresh baseline'):
        generate_comparison(saved, tmp_path / 'comparison')
    assert not (tmp_path / 'comparison').exists()


def test_iteration_reuses_an_explicit_prior_baseline_with_provenance(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    prior_dir = tmp_path / 'prior'
    prior_dir.mkdir()
    saved = artifact()
    saved['source_hashes']['bullet_review_v2.py'] = 'recorded-old-version'
    (prior_dir / 'baseline.json').write_text(json.dumps(saved))
    prior = {
        'comparison_id': 'old-comparison',
        'arms': {'A': {'variant': 'recorded_per_bullet', 'file': 'baseline.json'}},
    }
    prior_path = prior_dir / 'comparison.json'
    prior_path.write_text(json.dumps(prior))
    monkeypatch.setattr(runner, 'plan_project', lambda job, tasks, **k: project_reviews(tasks))
    monkeypatch.setattr(runner.coordinator, 'request_selection',
                        lambda *a, **k: {'selected_ids': ['b:c1'], 'rejected': []})

    path = iterate_comparison(prior_path, tmp_path / 'next')

    manifest = json.loads(path.read_text())
    assert manifest['parent_comparison_id'] == 'old-comparison'
    assert manifest['source_hashes']['bullet_review_v2.py'] == 'recorded-old-version'
    assert manifest['experiment_source_hashes']['bullet_review_v2.py'] != 'recorded-old-version'


def test_focused_comparison_reuses_frozen_input_and_saves_every_stage(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    saved = artifact()
    seen = {}

    def focused(job, tasks, **kwargs):
        seen['job'] = job
        seen['tasks'] = deepcopy(tasks)
        kwargs['on_stage']({
            'contract': 'focused_v1', 'prompt_version': 'test', 'stage': 'clarity',
            'scope_id': 'Project', 'attempt': 1, 'model': 'mock', 'input': {},
            'messages': [], 'status': 'completed',
            'normalized': {'check': 'clarity', 'bullets': []},
            'validation': {'valid': True}, 'elapsed_ms': 1,
        })
        return project_reviews(tasks)

    monkeypatch.setattr(runner.focused_review, 'review_bullets', focused)
    monkeypatch.setattr(runner.coordinator, 'request_selection',
                        lambda *a, **k: {'selected_ids': ['b:c1'], 'rejected': []})

    path = generate_comparison(
        saved, tmp_path / 'focused', experiment='focused',
    )

    manifest = json.loads(path.read_text())
    focused_arm = next(a for a in manifest['arms'].values()
                       if a['variant'] == 'focused_review')
    result = json.loads((path.parent / focused_arm['file']).read_text())
    assert manifest['experiment'] == 'focused'
    assert seen['job'] == ('Engineer', 'Co', 'Backend', ['python'])
    assert seen['tasks'][0]['text'] == TASK['text']
    assert result['coordinator']['selected_ids'] == ['b:c1']
    assert result['review_stage_trace'][0]['stage'] == 'clarity'
    assert (path.parent / 'focused_stage_trace.json').exists()

    output = []
    monkeypatch.setattr('builtins.print', lambda *values, **_kwargs: output.append(
        ' '.join(str(value) for value in values)
    ))
    print_saved_trace(path)
    assert any('clarity scope=Project' in line for line in output)
    assert any('coordinator selected: b:c1' in line for line in output)


def test_focused_snapshot_can_use_old_baseline_with_explicit_provenance(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    saved = artifact()
    saved['source_hashes']['bullet_review_v2.py'] = 'recorded-old-version'
    monkeypatch.setattr(runner.focused_review, 'review_bullets',
                        lambda job, tasks, **kwargs: project_reviews(tasks))
    monkeypatch.setattr(runner.coordinator, 'request_selection',
                        lambda *a, **k: {'selected_ids': ['b:c1'], 'rejected': []})

    path = generate_comparison(
        saved, tmp_path / 'focused-old', experiment='focused',
        require_current_sources=False,
    )

    manifest = json.loads(path.read_text())
    assert manifest['source_hashes']['bullet_review_v2.py'] == 'recorded-old-version'
    assert manifest['experiment_source_hashes']['bullet_review_v2.py'] != 'recorded-old-version'


def test_both_arms_can_be_blindly_reviewed_from_saved_outputs(tmp_path, monkeypatch):
    from evals import run_question_comparison as runner
    monkeypatch.setattr(runner, 'plan_project', lambda job, tasks, **k: project_reviews(tasks))
    monkeypatch.setattr(runner.coordinator, 'request_selection',
                        lambda *a, **k: {'selected_ids': ['b:c1'], 'rejected': []})
    path = generate_comparison(artifact(), tmp_path / 'comparison')
    seen = []
    monkeypatch.setattr(runner, 'interactive_review', lambda *a, **k: seen.append((a, k)))
    runner.review_comparison(path, tmp_path / 'labels.json')
    assert len(seen) == 2
    assert seen[0][0][1]['id'] != seen[1][0][1]['id']
    assert all(args[0] == 'google' for args, kwargs in seen)
    assert all(kwargs['coordinator']['selected_ids'] == ['b:c1'] for args, kwargs in seen)


def test_failed_coordination_recovers_from_captured_response_without_model_call():
    frozen = artifact()
    project = deepcopy(frozen)
    project['run']['reviews'] = project_reviews()
    project['run']['status'] = 'failed'
    project['coordinator'] = {'selected_ids': [], 'rejected': []}
    project['failures'] = [{'stage': 'coordination', 'type': 'ContractViolation',
                            'error': 'old strict contract'}]
    project['model_calls'] = [{
        'messages': [
            {'role': 'system', 'content': __import__(
                'services.question_coordinator_v2', fromlist=['SYSTEM_PROMPT']
            ).SYSTEM_PROMPT},
            {'role': 'user', 'content': '{}'},
        ],
        'raw_response': json.dumps({'decisions': [{
            'id': 'b:c1', 'action': 'reject', 'reason': 'already_answered',
            'duplicate_of': 'invented-pointer',
        }]}),
    }]

    recovered = recover_coordination(project, frozen)

    assert recovered['run']['status'] == 'question_audit'
    assert recovered['failures'] == []
    assert recovered['coordinator']['selected_ids'] == []
    assert recovered['coordinator']['rejected'] == [
        {'id': 'b:c1', 'reason': 'already_answered', 'duplicate_of': None}
    ]
    assert recovered['coordination_recovery']['contract_repairs']
