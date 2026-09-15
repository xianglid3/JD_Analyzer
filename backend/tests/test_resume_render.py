"""Rendering a tailored resume. The model produced structured edits; this file turns them
into a document, which is the only place markup is ever written."""

import pytest

from services.openai_services import ResumeEntryExtraction, ResumeHeader, ResumeStructure
from services.resume_evidence import save_resume_evidence, save_resume_header
from services.resume_render import build_document, latex_escape, render_html, render_latex


HEADER = ResumeHeader(
    full_name="Shawn Li", email="shl362@pitt.edu", phone="(412) 400-6088",
    location="Pittsburgh, PA", links=["github.com/shawn"],
)


@pytest.fixture
def resume(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, resume_headers, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('renderuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        save_resume_header(cur, user_id, HEADER)
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(
                kind="project", title="Calendar Map", organization=None,
                start_date="Jun 2024", end_date="Present",
                bullets=["Built a calendar app with React & Tailwind", "Designed a FastAPI backend"],
            ),
            ResumeEntryExtraction(
                kind="experience", title="IT & Sales", organization="Asian Granite",
                start_date="2019", end_date="2023", bullets=["Automated quoting workflows"],
            ),
        ], skills=["Python", "C++"]))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s", (user_id,))
        bullets = {text: str(bid) for bid, text in cur.fetchall()}
    _db.commit()
    return {"user_id": user_id, "bullets": bullets}


def make_run(cur, user_id, edits):
    cur.execute(
        "INSERT INTO jobs (user_id, raw_description, title) VALUES (%s, 'x', 'Role') RETURNING id",
        (user_id,),
    )
    job_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
        VALUES (%s, %s, 'test', 8, 'completed') RETURNING id
        """,
        (user_id, job_id),
    )
    run_id = cur.fetchone()[0]
    for bullet_id, text, status in edits:
        cur.execute(
            """
            INSERT INTO proposed_edits (run_id, user_id, bullet_id, requirement, proposed_text, status)
            VALUES (%s, %s, %s, 'req', %s, %s)
            """,
            (run_id, user_id, bullet_id, text, status),
        )
    return run_id


# ── the document ─────────────────────────────────────────────────────────────

def test_document_groups_entries_and_pulls_skills_out(_db, resume):
    with _db.cursor() as cur:
        document = build_document(cur, resume["user_id"])

    # education, then projects, then work — the page order, not the order they were entered
    assert [s["title"] for s in document["sections"]] == ["Projects", "Experience"]
    assert document["skills"] == ["Python", "C++"]
    assert document["header"]["full_name"] == "Shawn Li"


def test_accepted_edits_replace_their_bullets(_db, resume):
    original = "Designed a FastAPI backend"
    with _db.cursor() as cur:
        run_id = make_run(cur, resume["user_id"], [
            (resume["bullets"][original], "Designed and shipped a FastAPI backend serving REST APIs", "accepted"),
        ])
        document = build_document(cur, resume["user_id"], run_id)

    texts = [b["text"] for s in document["sections"] for e in s["entries"] for b in e["bullets"]]
    assert "Designed and shipped a FastAPI backend serving REST APIs" in texts
    assert original not in texts
    assert document["tailored_count"] == 1


def test_accepted_merge_replaces_primary_and_removes_consumed_bullet(_db, resume):
    first = resume["bullets"]["Built a calendar app with React & Tailwind"]
    second = resume["bullets"]["Designed a FastAPI backend"]
    merged = "Built a calendar app with React and a FastAPI backend"
    with _db.cursor() as cur:
        run_id = make_run(cur, resume["user_id"], [])
        cur.execute(
            """
            INSERT INTO proposed_edits (
                run_id, user_id, bullet_id, requirement, proposed_text, edit_type, status
            ) VALUES (%s, %s, %s, 'Full-stack development', %s, 'merge', 'accepted')
            RETURNING id
            """,
            (run_id, resume["user_id"], first, merged),
        )
        edit_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO tailoring_edit_bullets (edit_id, bullet_id, sort_order) VALUES (%s, %s, 1)",
            (edit_id, second),
        )
        document = build_document(cur, resume["user_id"], run_id)

    texts = [b["text"] for s in document["sections"] for e in s["entries"] for b in e["bullets"]]
    assert merged in texts
    assert "Built a calendar app with React & Tailwind" not in texts
    assert "Designed a FastAPI backend" not in texts
    assert document["tailored_count"] == 1


def test_undecided_and_rejected_edits_are_ignored(_db, resume):
    original = "Automated quoting workflows"
    with _db.cursor() as cur:
        run_id = make_run(cur, resume["user_id"], [
            (resume["bullets"][original], "Rejected rewrite", "rejected"),
            (resume["bullets"]["Built a calendar app with React & Tailwind"], "Undecided rewrite", "proposed"),
        ])
        document = build_document(cur, resume["user_id"], run_id)

    texts = [b["text"] for s in document["sections"] for e in s["entries"] for b in e["bullets"]]
    assert "Rejected rewrite" not in texts and "Undecided rewrite" not in texts
    assert original in texts
    assert document["tailored_count"] == 0


def test_the_underlying_bullet_is_never_rewritten(_db, resume):
    """Tailoring for one job must not change the evidence every other job cites."""
    original = "Automated quoting workflows"
    with _db.cursor() as cur:
        run_id = make_run(cur, resume["user_id"], [
            (resume["bullets"][original], "A tailored version", "accepted"),
        ])
        build_document(cur, resume["user_id"], run_id)
        cur.execute("SELECT text FROM resume_bullets WHERE id = %s", (resume["bullets"][original],))
        assert cur.fetchone()[0] == original


def test_a_run_belonging_to_someone_else_applies_nothing(_db, resume):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('renderother', 'x') RETURNING id")
        other_id = cur.fetchone()[0]
        run_id = make_run(cur, resume["user_id"], [
            (resume["bullets"]["Automated quoting workflows"], "Someone else's rewrite", "accepted"),
        ])
        document = build_document(cur, other_id, run_id)

    assert document["sections"] == [] and document["skills"] == []


# ── output ───────────────────────────────────────────────────────────────────

def test_html_contains_the_resume_and_escapes_user_text(_db, resume):
    with _db.cursor() as cur:
        html = render_html(build_document(cur, resume["user_id"]))

    assert "Shawn Li" in html and "Calendar Map" in html
    assert "React &amp; Tailwind" in html      # ampersand escaped, not raw


def test_html_escapes_markup_in_a_bullet(_db, resume):
    """Bullet text is user-controlled and reaches a file people open in a browser."""
    with _db.cursor() as cur:
        cur.execute(
            "UPDATE resume_bullets SET text = %s WHERE id = %s",
            ("<script>alert(1)</script>", resume["bullets"]["Automated quoting workflows"]),
        )
        html = render_html(build_document(cur, resume["user_id"]))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("raw, expected", [
    ("R&D", r"R\&D"),
    ("50% faster", r"50\% faster"),
    ("cost_savings", r"cost\_savings"),
    ("C# and $200", r"C\# and \$200"),
    ("\\input{/etc/passwd}", r"\textbackslash{}input\{/etc/passwd\}"),
])
def test_latex_escaping(raw, expected):
    assert latex_escape(raw) == expected


def test_latex_document_is_well_formed(_db, resume):
    with _db.cursor() as cur:
        tex = render_latex(build_document(cur, resume["user_id"]))

    assert tex.count(r"\begin{document}") == 1 and tex.count(r"\end{document}") == 1
    assert tex.count(r"\begin{itemize}") == tex.count(r"\end{itemize}")
    assert r"\section{Projects}" in tex and "Calendar Map" in tex
    assert r"React \& Tailwind" in tex


def test_an_empty_resume_still_renders(_db):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('emptyrender', 'x') RETURNING id")
        empty_id = cur.fetchone()[0]
        document = build_document(cur, empty_id)

    assert "Your name" in render_html(document)
    assert r"\end{document}" in render_latex(document)


def test_sections_render_education_then_projects_then_work(_db, resume):
    """The page order is fixed by KIND_TITLES, not by the order entries were saved."""
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resume_entries (user_id, kind, title, organization, sort_order)
            VALUES (%s, 'education', 'BSc Computer Science', 'University', 99)
            """,
            (resume["user_id"],),
        )
        document = build_document(cur, resume["user_id"])
        tex = render_latex(document)

    assert [s["title"] for s in document["sections"]] == ["Education", "Projects", "Experience"]
    assert (tex.index(r"\section{Education}")
            < tex.index(r"\section{Projects}")
            < tex.index(r"\section{Experience}")
            < tex.index(r"\section{Technical Skills}"))
