#!/usr/bin/env python3
"""Compare a frozen real-run baseline with an eval-only project planner.

--snapshot FILE pays only for the project arm; the recorded baseline is never rerun.
--review FILE and --report FILE use saved comparison artifacts without DB/model calls.
Both arms use the same extracted job and fit context. Raw JD remains available to human raters.
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import secrets
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.question_artifacts import call_summary, capture_calls, write_json, source_hashes
from evals.project_question_planner import plan_project
from evals.run_real_resume_v2_eval import interactive_review
from services import focused_review, question_coordinator_v2 as coordinator
from services.bullet_review_v2 import unavailable
from services.tailoring_agent import _coordinator_bullets


def frozen_inputs(artifact):
    """Recover the exact per-target requests, not a newly analyzed job or inferred matches."""
    targets, jobs = {}, []
    for call in artifact.get('model_calls') or []:
        for message in call.get('messages') or []:
            if message.get('role') != 'user':
                continue
            try:
                body = json.loads(message.get('content') or '{}')
            except (ValueError, TypeError):
                continue
            if not isinstance(body, dict) or not isinstance(body.get('target'), dict):
                continue
            target = {k: v for k, v in body['target'].items() if k != 'bullet'}
            text = target.get('target_bullet')
            if not text or not isinstance(body.get('job_description'), dict):
                continue
            if text in targets and targets[text] != target:
                raise ValueError('target context changed inside the recorded run')
            targets[text] = target
            jobs.append(body['job_description'])
    if not jobs or any(job != jobs[0] for job in jobs):
        raise ValueError('snapshot needs complete, consistent captured reviewer requests; run a fresh real audit')
    tasks, seen_text = [], set()
    for key, fixture in artifact['fixtures'].items():
        text = fixture['text']
        if text in seen_text or text not in targets:
            raise ValueError(f'cannot unambiguously recover frozen context for {key}')
        seen_text.add(text)
        target = targets[text]
        context = target.get('entry_context') or {}
        tasks.append({'bullet_id': artifact['bullet_ids'][key], 'text': text,
                      'entry': context.get('name') or '',
                      'entry_id': context.get('id') or context.get('name') or key,
                      'siblings': context.get('sibling_bullets') or [],
                      'answers': target.get('answers_given_in_this_run') or [],
                      **(target.get('fit_context') or {})})
    # Focused review cites sibling evidence by stable id. Older recorded V2 requests only saved
    # sibling text, so reconstruct those ids from the same frozen entry without re-reading the DB.
    for task in tasks:
        task['sibling_bullets'] = [
            {'bullet_id': other['bullet_id'], 'text': other['text']}
            for other in tasks
            if other is not task and other['entry'] == task['entry']
        ]
    jd = jobs[0]
    return (jd['title'], jd['company'], jd['summary'], jd['requirements']), tasks


def _run_project_arm(job, tasks, model, directory):
    reviews, failures = {}, []
    groups = defaultdict(list)
    for task in tasks:
        groups[task['entry']].append(task)
    trace = {'selected_ids': [], 'rejected': []}
    with capture_calls(directory / 'project_calls.json') as calls:
        for index, group in enumerate(groups.values(), start=1):
            print(f'Project planning {index}/{len(groups)}...', flush=True)
            try:
                reviews.update(plan_project(job, group, model=model))
            except Exception as exc:
                failures.append({'stage': 'project', 'bullet_ids': [t['bullet_id'] for t in group],
                                 'error': str(exc), 'type': type(exc).__name__})
                for task in group:
                    reviews[task['bullet_id']] = unavailable(str(exc))
            write_json(directory / 'partial.json', {'reviews': reviews, 'failures': failures})
        candidates = coordinator.collect_candidates(_coordinator_bullets(tasks, reviews))
        try:
            print('Coordinating project questions...', flush=True)
            trace = coordinator.request_selection(job, candidates, model=model)
        except Exception as exc:
            failures.append({'stage': 'coordination', 'error': str(exc), 'type': type(exc).__name__})
    return reviews, trace, calls, failures, []


def _run_focused_arm(job, tasks, model, directory):
    reviews, failures, events = {}, [], []
    trace = {'selected_ids': [], 'rejected': []}

    def stage(event):
        events.append(deepcopy(event))
        write_json(directory / 'focused_stage_trace.json', events)

    with capture_calls(directory / 'focused_calls.json') as calls:
        try:
            print('Running clarity, claim-support, and opportunity checks...', flush=True)
            reviews = focused_review.review_bullets(
                job, tasks, model=model, on_stage=stage,
            )
            write_json(directory / 'partial.json', {
                'reviews': reviews, 'failures': failures, 'review_stage_trace': events,
            })
            candidates = coordinator.collect_candidates(_coordinator_bullets(tasks, reviews))
            if candidates:
                print('Coordinating focused questions...', flush=True)
                trace = coordinator.request_selection(
                    job, candidates, model=model, trace_callback=stage,
                )
        except Exception as exc:
            failures.append({'stage': 'focused_pipeline', 'error': str(exc),
                             'type': type(exc).__name__})
            for task in tasks:
                reviews.setdefault(task['bullet_id'], unavailable(str(exc)))
    return reviews, trace, calls, failures, events


def generate_comparison(artifact, directory, *, require_current_sources=True,
                        parent_comparison_id=None, experiment='project'):
    current_sources = source_hashes()
    if require_current_sources and artifact.get("source_hashes") != current_sources:
        raise ValueError("reviewer/coordinator code changed or snapshot lacks provenance; record a fresh baseline")
    job, tasks = frozen_inputs(artifact)
    models = {c.get('model') for c in artifact['model_calls'] if c.get('model')}
    if len(models) != 1:
        raise ValueError('comparison requires one recorded model for both arms')
    model = next(iter(models))
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / 'frozen_input.json', artifact)
    if experiment == 'focused':
        reviews, trace, calls, failures, stage_trace = _run_focused_arm(
            job, tasks, model, directory,
        )
        variant = 'focused_review'
    elif experiment == 'project':
        reviews, trace, calls, failures, stage_trace = _run_project_arm(
            job, tasks, model, directory,
        )
        variant = 'evidence_first_project_planner'
    else:
        raise ValueError(f'unknown comparison experiment: {experiment}')
    project = {**artifact, 'source_hashes': current_sources,
               'run': {'id': '', 'status': 'failed' if failures else 'question_audit',
                                  'reviews': reviews},
               'coordinator': trace, 'model_calls': calls, 'failures': failures,
               'review_stage_trace': stage_trace}
    compare_id = str(uuid.uuid4())
    arms = [('recorded_per_bullet', artifact), (variant, project)]
    secrets.SystemRandom().shuffle(arms)
    manifest = {'comparison_id': compare_id, 'arms': {}, 'model': model,
                'scope': 'question quality only; no answers, edits, or deployment',
                'source_hashes': artifact.get('source_hashes'),
                'experiment_source_hashes': current_sources,
                'parent_comparison_id': parent_comparison_id,
                'experiment': experiment,
                'experiment_source': (
                    Path(focused_review.__file__).read_text()
                    if experiment == 'focused' else
                    Path(__file__).with_name('project_question_planner.py').read_text()
                )}
    for alias, (variant, source) in zip(('A', 'B'), arms):
        saved = deepcopy(source)
        saved['run']['id'] = f'{compare_id}:{alias}'
        saved['arm'] = alias
        write_json(directory / f'{alias}.json', saved)
        manifest['arms'][alias] = {'variant': variant, 'file': f'{alias}.json',
                                   'run_id': saved['run']['id'], 'failures': source.get('failures') or []}
    path = directory / 'comparison.json'
    write_json(path, manifest)
    return path


def iterate_comparison(path, directory, *, experiment='project'):
    """Pay only for the new planner while reusing a prior comparison's frozen baseline."""
    prior = json.loads(path.read_text())
    baseline = [arm for arm in prior.get('arms', {}).values()
                if arm.get('variant') == 'recorded_per_bullet']
    if len(baseline) != 1:
        raise ValueError('prior comparison must contain exactly one recorded_per_bullet arm')
    artifact = json.loads((path.parent / baseline[0]['file']).read_text())
    return generate_comparison(
        artifact,
        directory,
        require_current_sources=False,
        parent_comparison_id=prior.get('comparison_id'),
        experiment=experiment,
    )


def _successful_calls(calls, system_prompt):
    return [call for call in calls
            if call.get('raw_response') is not None
            and (call.get('messages') or [{}])[0].get('content') == system_prompt]


def recover_coordination(project, frozen):
    """Rebuild a failed coordinator result only from captured provider responses."""
    job, tasks = frozen_inputs(frozen)
    candidates = coordinator.collect_candidates(
        _coordinator_bullets(tasks, project['run']['reviews'])
    )
    calls = project.get('model_calls') or []
    compared = coordinator._overlap_subset(candidates)
    overlap_calls = _successful_calls(calls, coordinator.OVERLAP_PROMPT)
    if compared:
        if not overlap_calls:
            raise ValueError('captured comparison has no successful overlap response')
        overlaps = coordinator.validate_overlap(
            json.loads(overlap_calls[-1]['raw_response']), compared
        )
    else:
        overlaps = {}

    survivors = [candidate for candidate in candidates if candidate['id'] not in overlaps]
    selection_calls = _successful_calls(calls, coordinator.SYSTEM_PROMPT)
    if not selection_calls:
        raise ValueError('captured comparison has no successful selection response')
    chosen = coordinator.validate(
        json.loads(selection_calls[-1]['raw_response']),
        survivors,
        allow_missing=len(selection_calls) > 1,
    )
    combined = coordinator.combine_selection(candidates, chosen, overlaps)
    repairs = chosen.get('contract_repairs') or []
    if repairs:
        combined['contract_repairs'] = repairs
    project['coordinator'] = combined
    project['failures'] = [failure for failure in project.get('failures') or []
                           if failure.get('stage') != 'coordination']
    project['run']['status'] = 'failed' if project['failures'] else 'question_audit'
    project['coordination_recovery'] = {
        'source': 'captured_model_calls',
        'overlap_calls': len(overlap_calls),
        'selection_calls': len(selection_calls),
        'contract_repairs': repairs,
    }
    return project


def recover_comparison(path):
    """Repair failed coordination in place without revealing arm identity or calling a model."""
    manifest = json.loads(path.read_text())
    frozen = json.loads((path.parent / 'frozen_input.json').read_text())
    recovered = []
    for alias, arm in manifest['arms'].items():
        failures = arm.get('failures') or []
        if arm.get('variant') not in {
                'project_planner', 'evidence_first_project_planner', 'focused_review'} or not any(
                failure.get('stage') == 'coordination' for failure in failures):
            continue
        arm_path = path.parent / arm['file']
        project = recover_coordination(json.loads(arm_path.read_text()), frozen)
        write_json(arm_path, project)
        arm['failures'] = project['failures']
        recovered.append(alias)
    if not recovered:
        raise ValueError('comparison has no recoverable project coordination failure')
    write_json(path, manifest)
    return recovered


def review_comparison(path, labels):
    manifest = json.loads(path.read_text())
    for alias, arm in manifest['arms'].items():
        artifact = json.loads((path.parent / arm['file']).read_text())
        print(f'\nBlind review — set {alias}')
        interactive_review(artifact['job_name'], artifact['run'], artifact['fixtures'],
                           artifact['bullet_ids'], labels, coordinator=artifact['coordinator'],
                           job_context=artifact['job'])


def report_comparison(path, labels):
    manifest = json.loads(path.read_text())
    data = json.loads(labels.read_text())
    for alias, arm in manifest['arms'].items():
        artifact = json.loads((path.parent / arm['file']).read_text())
        questions = [q for q in data['question_reviews'] if q['run_id'] == arm['run_id']]
        selected = [q for q in questions if q['coordination'] == 'selected']
        bullet_ratings = [b for b in data['bullet_reviews'] if b['run_id'] == arm['run_id']]
        total = sum(len(r.get('question_candidates') or []) for r in artifact['run']['reviews'].values())
        desired_ask = {b['bullet_key'] for b in bullet_ratings if b['human_action'] == 'ask'}
        candidates_by_key = {
            key: len((artifact['run']['reviews'].get(artifact['bullet_ids'][key]) or {})
                     .get('question_candidates') or [])
            for key in artifact['fixtures']
        }
        reviewer_misses = sorted(key for key in desired_ask if not candidates_by_key.get(key))
        unnecessary = sorted(
            b['bullet_key'] for b in bullet_ratings
            if b['human_action'] != 'ask' and candidates_by_key.get(b['bullet_key'])
        )
        useful_labels = {'useful', 'useful_but_reword'}
        harmful_labels = {'duplicate_or_answered', 'low_value', 'unsupported_premise'}
        coordinator_losses = [q for q in questions
                              if q['human_label'] in useful_labels
                              and q['coordination'] != 'selected']
        bad_selected = [q for q in selected if q['human_label'] in harmful_labels]
        usage = call_summary(artifact.get('model_calls') or [])
        print(f"{alias}: {arm['variant']} ({artifact['run']['status']})")
        print(f"  rated bullets {len(bullet_ratings)}/{len(artifact['fixtures'])}; questions {len(questions)}/{total}")
        print('  generated labels:', dict(Counter(q['human_label'] for q in questions)))
        print('  selected labels:', dict(Counter(q['human_label'] for q in selected)))
        print('  reviewer misses:', reviewer_misses or 'none')
        print('  unnecessary question pools:', unnecessary or 'none')
        print('  useful coordinator losses:', len(coordinator_losses),
              'bad selected:', len(bad_selected))
        cost = 'unavailable' if usage['cost_usd'] is None else f"${usage['cost_usd']:.5f}"
        print(f"  calls: {usage['calls']} tokens: {usage['tokens']} cost: {cost} "
              f"latency: {usage['elapsed_seconds']:.1f}s failures: {len(arm['failures'])}")
    print('Counts are observations, not a semantic PASS. Unrated and unavailable outputs remain unresolved.')


def _print_artifact_trace(artifact, heading=None):
    if heading:
        print(f'\n{heading}')
    events = artifact.get('review_stage_trace') or []
    if not events:
        print('  no focused stage trace stored')
        return
    for event in events:
        normalized = event.get('normalized') or (event.get('result') or {}).get('normalized') or {}
        print(
            f"  {event.get('stage') or (event.get('arguments') or {}).get('stage')} "
            f"scope={event.get('scope_id') or (event.get('arguments') or {}).get('scope_id')} "
            f"attempt={event.get('attempt') or (event.get('arguments') or {}).get('attempt')} "
            f"{event.get('status')}"
        )
        for row in normalized.get('bullets') or []:
            print(f"    {row.get('bullet_id')}: {row.get('signal')} — {row.get('summary')}")
            for finding in row.get('findings') or []:
                print(
                    f"      finding {finding.get('finding_id')}: "
                    f"{finding.get('information_needed')} -> {finding.get('resume_change')}"
                )
        for candidate in normalized.get('candidates') or []:
            print(
                f"    question {candidate.get('candidate_id')}: {candidate.get('question')} "
                f"[{', '.join(candidate.get('finding_ids') or [])}]"
            )
        for disposition in normalized.get('finding_dispositions') or []:
            print(
                f"    disposition {disposition.get('finding_id')}: "
                f"{disposition.get('disposition')}"
            )
    trace = artifact.get('coordinator') or {}
    print('  coordinator selected:', ', '.join(trace.get('selected_ids') or []) or 'none')
    for rejected in trace.get('rejected') or []:
        print(f"    rejected {rejected.get('id')}: {rejected.get('reason')}")


def print_saved_trace(path):
    saved = json.loads(path.read_text())
    if 'arms' not in saved:
        _print_artifact_trace(saved, path.name)
        return
    for alias, arm in saved['arms'].items():
        artifact = json.loads((path.parent / arm['file']).read_text())
        _print_artifact_trace(artifact, f"{alias}: {arm['variant']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path, help='paid project arm against a recorded baseline')
    source.add_argument('--review', type=Path, help='offline blind review of comparison.json')
    source.add_argument('--report', type=Path, help='offline label summary; reveals arm identities')
    source.add_argument('--recover', type=Path,
                        help='offline recovery from captured comparison responses')
    source.add_argument('--trace', type=Path,
                        help='offline readable focused-stage trace for an artifact/comparison')
    source.add_argument('--iterate', type=Path,
                        help='paid new planner arm against a prior frozen comparison baseline')
    source.add_argument('--focused-snapshot', type=Path,
                        help='paid focused-review arm against a recorded baseline')
    source.add_argument('--focused-iterate', type=Path,
                        help='paid focused-review arm against a prior frozen baseline')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--labels', type=Path)
    args = parser.parse_args()
    if args.snapshot or args.focused_snapshot:
        snapshot = args.snapshot or args.focused_snapshot
        experiment = 'focused' if args.focused_snapshot else 'project'
        artifact = json.loads(snapshot.read_text())
        directory = args.output or snapshot.parent / f'comparison-{uuid.uuid4().hex[:12]}'
        path = generate_comparison(
            artifact, directory, experiment=experiment,
            # A focused experiment necessarily changes reviewer source. The frozen request and
            # each arm's distinct hashes remain in the manifest; frozen_inputs still verifies a
            # complete, internally consistent baseline before any paid call.
            require_current_sources=not bool(args.focused_snapshot),
        )
        print(f'Saved comparison: {path}\nReview offline: ./venv/bin/python evals/run_question_comparison.py --review {path}')
    elif args.iterate or args.focused_iterate:
        prior = args.iterate or args.focused_iterate
        experiment = 'focused' if args.focused_iterate else 'project'
        directory = args.output or prior.parent / f'comparison-{uuid.uuid4().hex[:12]}'
        path = iterate_comparison(prior, directory, experiment=experiment)
        print(f'Saved comparison: {path}\nReview offline: ./venv/bin/python evals/run_question_comparison.py --review {path}')
    elif args.review:
        review_comparison(args.review, args.labels or args.review.parent / 'labels.json')
    elif args.recover:
        aliases = recover_comparison(args.recover)
        print('Recovered coordination offline for: ' + ', '.join(aliases))
        print(f'Review offline: ./venv/bin/python evals/run_question_comparison.py --review {args.recover}')
    elif args.trace:
        print_saved_trace(args.trace)
    else:
        report_comparison(args.report, args.labels or args.report.parent / 'labels.json')
    print('\a', end='', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
