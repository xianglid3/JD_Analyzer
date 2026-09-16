"""Does a SIGKILLed worker actually lose nothing?

Not a pytest test: it needs two real OS processes and a real SIGKILL, which is exactly the
thing in-process execution cannot simulate. Run it by hand after touching leasing:

    BACKEND=$(pwd) SUPABASE_URL=postgresql://postgres:postgres@localhost:5432/jd_test \
    JWT_SECRET=proof-secret-not-real-at-all-32chars OPENAI_API_KEY=dummy MAINTENANCE_SWEEP=0 \
    python tools/worker_kill_proof.py


Two real processes, one real `kill -9`. Everything here runs against the local jd_test
database — never Supabase.
"""
import os, signal, subprocess, sys, time

BACKEND = os.environ["BACKEND"]
sys.path.insert(0, BACKEND)

from db import get_cursor
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.tailoring_agent import start_run

BULLETS = [
    "Worked on Kubernetes deployments across three regions",
    "Built an ingestion pipeline in Python processing 2M events daily",
    "Helped with the on-call rotation and wrote runbooks",
]


def setup():
    with get_cursor(commit=True) as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('proofuser','x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Platform Engineer', 'Globex', 'Runs the deploy pipeline',
                    '["kubernetes", "terraform"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern",
                                  bullets=BULLETS)
        ]))
    return user_id, job_id


def run_state(run_id):
    with get_cursor() as cur:
        cur.execute(
            """SELECT status, steps_used, claim_count, claimed_by, claim_token IS NOT NULL
               FROM tailoring_runs WHERE id = %s""", (run_id,))
        return cur.fetchone()


def tool_steps(run_id):
    with get_cursor() as cur:
        cur.execute(
            "SELECT step_number, count(*) FROM tool_calls WHERE run_id = %s GROUP BY 1 ORDER BY 1",
            (run_id,))
        return cur.fetchall()


def worker(name):
    return subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "worker_kill_proof_worker.py"), name],
                            env={**os.environ}, stdout=sys.stdout, stderr=sys.stderr)


def wait_until(predicate, what, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    raise SystemExit(f"timed out waiting for {what}")


user_id, job_id = setup()
started = start_run(get_cursor, user_id, job_id)
run_id = started["run_id"]
print(f"run {run_id} created\n")

a = worker("worker-a")
wait_until(lambda: run_state(run_id)[1] >= 2, "worker-a to checkpoint two steps")
killed_at_steps = run_state(run_id)[1]
killed_tools = tool_steps(run_id)
print(f"\nkilling worker-a at steps_used={killed_at_steps}, tool steps {killed_tools}")
os.kill(a.pid, signal.SIGKILL)
a.wait()

state = run_state(run_id)
print(f"after SIGKILL: status={state[0]} steps={state[1]} claims={state[2]} owner={state[3]} leased={state[4]}")

# simulate the lease running out, rather than waiting two real minutes for it
with get_cursor(commit=True) as cur:
    cur.execute("UPDATE tailoring_runs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s",
                (run_id,))
print("lease expired\n")

b = worker("worker-b")
final = wait_until(lambda: (lambda s: s if s[0] != "running" else None)(run_state(run_id)),
                   "worker-b to finish the run", timeout=60)
b.wait(timeout=30)

steps = tool_steps(run_id)
print(f"\nfinal: status={final[0]} steps_used={final[1]} claims={final[2]}")
print(f"tool calls per step: {steps}")

problems = []
# `incomplete` is the right answer here, not a failure: the fake model only ever searches, so
# it stops with candidates still unhandled and AE-02 says so honestly. What would be wrong is
# `failed` or `abandoned` — either would mean the kill cost the run.
if final[0] not in ("completed", "incomplete"):
    problems.append(f"run ended {final[0]} — the kill cost it")
numbers = [step for step, _count in steps]
if numbers != sorted(set(numbers)):
    problems.append(f"steps repeated or out of order: {numbers}")
if numbers and numbers != list(range(1, max(numbers) + 1)):
    problems.append(f"a step was skipped: {numbers}")
if any(count > 1 for _step, count in steps):
    problems.append("a step's tool calls were written twice")
if max(numbers, default=0) > final[1]:
    problems.append("a tool call exists for a step the run never counted")
if final[2] != 0:
    problems.append(f"claim_count is {final[2]}, not reset by durable progress")
if final[4]:
    problems.append("the lease was not released when the run closed")

print("\n" + ("FAILED:\n  " + "\n  ".join(problems) if problems else "PASSED — the killed worker lost nothing"))
sys.exit(1 if problems else 0)
