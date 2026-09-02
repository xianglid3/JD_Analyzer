"""Structured resume evidence — the citable layer the tailoring agent reads.

The property under test is id stability: a bullet keeps its id across re-extraction,
because every future citation points at that id.
"""

import pytest

from services.openai_services import (
    ResumeEntryExtraction,
    ResumeStructure,
    enforce_structure_limits,
)
from services.resume_evidence import (
    content_hash,
    list_resume_evidence,
    save_resume_evidence,
)


def structure(*entries):
    return ResumeStructure(entries=list(entries), skills=[])


def experience(title="Backend Intern", org="Acme", bullets=()):
    return ResumeEntryExtraction(kind="experience", title=title, organization=org, bullets=list(bullets))


@pytest.fixture
def user_id(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE resume_bullets, resume_entries, resumes, users CASCADE")
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('evidenceuser', 'x') RETURNING id"
        )
        return cur.fetchone()[0]


def bullet_ids(cur, user_id):
    cur.execute("SELECT text, id FROM resume_bullets WHERE user_id = %s", (user_id,))
    return {text: str(bid) for text, bid in cur.fetchall()}


# ── content_hash: pure, no DB ────────────────────────────────────────────────

def test_hash_ignores_whitespace_and_case():
    assert content_hash("Built  the\n API") == content_hash("built the api")


def test_hash_separates_different_wording():
    assert content_hash("Built the API") != content_hash("Built the CLI")


# ── limits are enforced in code, not just requested in the prompt (BUG-062) ──

def test_bullets_capped_and_deduped():
    entry = experience(bullets=["dup"] * 3 + [f"bullet {i}" for i in range(20)])
    result = enforce_structure_limits(structure(entry))
    texts = [b.text for b in result.entries[0].bullets]
    assert len(texts) == 12
    assert texts.count("dup") == 1


def test_skills_capped_at_thirty():
    result = enforce_structure_limits(ResumeStructure(skills=[f"skill{i}" for i in range(50)]))
    assert len(result.skills) == 30


def test_blank_bullets_dropped():
    result = enforce_structure_limits(structure(experience(bullets=["  ", "real bullet", ""])))
    assert [b.text for b in result.entries[0].bullets] == ["real bullet"]


# ── id stability: the whole point ────────────────────────────────────────────

def test_reextraction_keeps_bullet_ids(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, structure(experience(bullets=["Shipped the API", "Wrote tests"])))
        before = bullet_ids(cur, user_id)

        # same resume, re-uploaded: entries are rebuilt, bullets are not
        stats = save_resume_evidence(cur, user_id, structure(experience(bullets=["Shipped the API", "Wrote tests"])))
        after = bullet_ids(cur, user_id)

    assert before == after
    assert stats["reused_bullets"] == 2


def test_reformatted_bullet_still_matches(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, structure(experience(bullets=["Shipped the API"])))
        before = bullet_ids(cur, user_id)
        save_resume_evidence(cur, user_id, structure(experience(bullets=["shipped   the  API"])))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()

    assert len(rows) == 1
    assert str(rows[0][0]) == before["Shipped the API"]   # same id
    assert rows[0][1] == "shipped   the  API"             # new text stored


def test_bullet_moving_between_entries_keeps_its_id(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, structure(experience(org="Acme", bullets=["Shipped the API"])))
        before = bullet_ids(cur, user_id)
        save_resume_evidence(cur, user_id, structure(experience(org="Globex", bullets=["Shipped the API"])))
        after = bullet_ids(cur, user_id)
        cur.execute(
            """
            SELECT e.organization
            FROM resume_bullets AS b JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s
            """,
            (user_id,),
        )
        org = cur.fetchone()[0]

    assert before == after
    assert org == "Globex"


def test_removed_bullets_and_entries_are_deleted(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(
            cur, user_id,
            structure(experience(org="Acme", bullets=["Kept", "Dropped"]), experience(org="Globex", bullets=["Gone"])),
        )
        save_resume_evidence(cur, user_id, structure(experience(org="Acme", bullets=["Kept"])))

        cur.execute("SELECT text FROM resume_bullets WHERE user_id = %s", (user_id,))
        texts = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM resume_entries WHERE user_id = %s", (user_id,))
        entry_count = cur.fetchone()[0]

    assert texts == ["Kept"]
    assert entry_count == 1


def test_evidence_is_scoped_to_its_owner(_db, user_id):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('otheruser', 'x') RETURNING id")
        other_id = cur.fetchone()[0]

        save_resume_evidence(cur, user_id, structure(experience(bullets=["Mine"])))
        save_resume_evidence(cur, other_id, structure(experience(bullets=["Theirs"])))

        mine = list_resume_evidence(cur, user_id)
        theirs = list_resume_evidence(cur, other_id)

    assert [b["text"] for b in mine[0]["bullets"]] == ["Mine"]
    assert [b["text"] for b in theirs[0]["bullets"]] == ["Theirs"]


def test_same_text_for_two_users_gets_two_ids(_db, user_id):
    """The unique index is (user_id, content_hash) — two people can share a sentence."""
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('twin', 'x') RETURNING id")
        twin_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, structure(experience(bullets=["Shipped the API"])))
        save_resume_evidence(cur, twin_id, structure(experience(bullets=["Shipped the API"])))
        cur.execute("SELECT count(DISTINCT id) FROM resume_bullets WHERE text = 'Shipped the API'")
        assert cur.fetchone()[0] == 2


def test_list_returns_bullets_in_resume_order(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, structure(experience(bullets=["first", "second", "third"])))
        entries = list_resume_evidence(cur, user_id)

    assert [b["text"] for b in entries[0]["bullets"]] == ["first", "second", "third"]
