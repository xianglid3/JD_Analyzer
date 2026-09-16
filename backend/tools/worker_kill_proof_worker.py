"""A real tailoring worker, with the model faked and slowed down.

Run as its own process so it can be killed with SIGKILL — which is the whole point. The model
call sleeps, so there is a window in which to kill it mid-step.
"""
import json, os, sys, time, types

sys.path.insert(0, os.environ["BACKEND"])

from services import tailoring_agent
from services.tailoring_agent import claim_run, execute_run
from db import get_cursor

STEP_SECONDS = float(os.environ.get("STEP_SECONDS", "2"))


def fake_complete(messages, max_tokens=None):
    time.sleep(STEP_SECONDS)                 # a model call long enough to be interrupted
    step = sum(1 for m in messages if m.get("role") == "tool") + 1
    if step > 4:
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="Done.", tool_calls=None))],
            usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=None, tool_calls=[
            types.SimpleNamespace(
                id=f"call-{os.getpid()}-{step}",
                function=types.SimpleNamespace(
                    name="search_resume", arguments=json.dumps({"query": f"kubernetes {step}"}),
                ),
            ),
        ]))],
        usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


tailoring_agent.complete = fake_complete

name = sys.argv[1]
print(f"[{name}] up", flush=True)
while True:
    with get_cursor(commit=True) as cur:
        claimed = claim_run(cur, worker_id=name)
    if claimed is None:
        time.sleep(0.5)
        continue
    print(f"[{name}] claimed {claimed['run_id']} at step {claimed['steps_used']}", flush=True)
    result = execute_run(
        get_cursor, claimed["user_id"], claimed["job_id"], claimed["run_id"],
        max_steps=claimed["max_steps"], resume_from=claimed["steps_used"],
        token=claimed["token"],
    )
    print(f"[{name}] finished {claimed['run_id']} → {result['status']}", flush=True)
    break
