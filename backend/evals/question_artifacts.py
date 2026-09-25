"""Local, private eval artifacts. Never imported by production code."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n')
    temporary.replace(path)


@contextmanager
def capture_calls(path=None):
    """Capture exact JSON-call inputs and provider output, including failures, across threads.

    An optional journal survives an interrupted comparison. Costs remain in the existing
    budget/usage records; this recorder never guesses them or captures API credentials.
    """
    from services import openai_services
    original = openai_services.complete_json
    calls, lock = [], threading.Lock()

    def call(messages, *args, **kwargs):
        record = {'messages': deepcopy(messages), 'model': kwargs.get('model', 'gpt-4o-mini'),
                  'kind': kwargs.get('kind'),
                  'settings': deepcopy({key: value for key, value in kwargs.items()
                                        if key not in {'budget', 'model', 'kind'}}),
                  'started_at': datetime.now(timezone.utc).isoformat()}
        with lock:
            calls.append(record)
            if path:
                write_json(path, calls)
        started = time.monotonic()
        outcome = {}
        try:
            response = original(messages, *args, **kwargs)
            outcome['raw_response'] = response.choices[0].message.content
            usage = getattr(response, 'usage', None)
            outcome['usage'] = usage.model_dump() if hasattr(usage, 'model_dump') else None
            return response
        except Exception as exc:
            outcome['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            with lock:
                record.update(outcome)
                record['elapsed_seconds'] = time.monotonic() - started
                if path:
                    write_json(path, calls)

    with patch.object(openai_services, 'complete_json', call):
        yield calls


def source_hashes():
    return {file: hashlib.sha256((HERE.parent / 'services' / file).read_bytes()).hexdigest()
            for file in (
                'bullet_review_v2.py',
                'focused_review.py',
                'question_coordinator_v2.py',
                'tailoring_review_trace.py',
            )}


def call_summary(calls):
    """Comparable local metrics for captured calls; cost is only valid for the priced mini model."""
    prompt = completion = 0
    elapsed = 0.0
    models = set()
    for call in calls or []:
        usage = call.get('usage') or {}
        prompt += int(usage.get('prompt_tokens') or 0)
        completion += int(usage.get('completion_tokens') or 0)
        elapsed += float(call.get('elapsed_seconds') or 0)
        if call.get('model'):
            models.add(call['model'])
    cost = None
    if models <= {'gpt-4o-mini'}:
        from services.openai_services import usd
        cost = usd(prompt, completion)
    return {
        'calls': len(calls or []), 'prompt_tokens': prompt,
        'completion_tokens': completion, 'tokens': prompt + completion,
        'elapsed_seconds': elapsed, 'cost_usd': cost, 'models': sorted(models),
    }


def save_artifact(name, run, fixtures, bullet_ids, coordinator, case, calls, directory=None,
                  review_stage_trace=None):
    hashes = source_hashes()
    path = (directory or HERE / 'artifacts') / f"{name}-{run['id']}.json"
    write_json(path, {
        'version': 1, 'job_name': name, 'job': case['job'], 'case': case,
        'run': run, 'fixtures': fixtures, 'bullet_ids': bullet_ids,
        'coordinator': coordinator, 'model_calls': calls, 'source_hashes': hashes,
        'review_stage_trace': list(review_stage_trace or []),
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    return path
