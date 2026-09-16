"""Store the structured resume as citable evidence.

A bullet's id has to survive a re-upload, or every citation written earlier points at a row
that no longer exists. Re-extraction matches on content_hash and reuses the row; a user edit
updates the row in place. Either way the id outlives the text.
"""

import hashlib
import json
import re

WHITESPACE = re.compile(r"\s+")


def content_hash(text):
    """Normalized first, so reindenting or double-spacing a bullet isn't a new bullet."""
    normalized = WHITESPACE.sub(" ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def save_resume_header(cur, user_id, header):
    """One per user, replaced whole — nothing cites a header, so no id to preserve."""
    cur.execute(
        """
        INSERT INTO resume_headers (user_id, full_name, email, phone, location, links)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            email = EXCLUDED.email,
            phone = EXCLUDED.phone,
            location = EXCLUDED.location,
            links = EXCLUDED.links,
            updated_at = now()
        """,
        (
            user_id, header.full_name, header.email, header.phone, header.location,
            json.dumps(header.links),
        ),
    )


def load_resume_header(cur, user_id):
    cur.execute(
        "SELECT full_name, email, phone, location, links FROM resume_headers WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"full_name": None, "email": None, "phone": None, "location": None, "links": []}
    return {
        "full_name": row[0], "email": row[1], "phone": row[2],
        "location": row[3], "links": row[4],
    }


from services.openai_services import ResumeBullet, ResumeEntryExtraction

SKILLS_ENTRY_TITLE = "Skills"


def with_skills_as_evidence(structure):
    """Fold the skills list in as its own entry. It's evidence like any bullet — leaving it
    out meant a requirement like Git came back as a gap while sitting on the resume."""
    if not structure.skills:
        return structure

    existing = next(
        (entry for entry in structure.entries
         if entry.kind == "skill" and entry.title == SKILLS_ENTRY_TITLE),
        None,
    )
    if existing is not None:
        known = {b.text for b in existing.bullets}
        existing.bullets = existing.bullets + [
            ResumeBullet(text=s) for s in structure.skills if s not in known
        ]
        return structure

    structure.entries.append(ResumeEntryExtraction(
        kind="skill",
        title=SKILLS_ENTRY_TITLE,
        bullets=list(structure.skills),
    ))
    return structure


def source_hash(text):
    """Identifies the resume text a structured extraction came from."""
    return content_hash(text or "")


def set_evidence_source(cur, user_id, resume_text):
    cur.execute(
        "UPDATE resumes SET evidence_source_hash = %s WHERE user_id = %s",
        (source_hash(resume_text) if resume_text else None, user_id),
    )


def evidence_is_stale(cur, user_id):
    """True when the resume text has changed since the evidence was extracted from it.

    Stale evidence describes a resume the user no longer has, so it must not be cited by
    tailoring or counted in a match score — either would quote experience that is gone.
    """
    cur.execute(
        "SELECT resume_text, evidence_source_hash FROM resumes WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False
    resume_text, stored = row
    cur.execute("SELECT count(*) FROM resume_bullets WHERE user_id = %s", (user_id,))
    if cur.fetchone()[0] == 0:
        return False              # nothing to be stale
    if not resume_text:
        return False              # no text to compare against
    return stored != source_hash(resume_text)


def save_resume_evidence(cur, user_id, structure):
    """Replace the user's structured resume, keeping bullet ids where we can.

    Ids are matched first by the id the client sends back (ownership-checked), then by
    content hash. Both matter: the first keeps an id alive when the user rewords a bullet,
    the second keeps it alive across a re-extraction that produces the same wording.

    Returns {"entries": n, "bullets": n, "reused_bullets": n}.
    """
    structure = with_skills_as_evidence(structure)
    cur.execute(
        "SELECT content_hash, id FROM resume_bullets WHERE user_id = %s",
        (user_id,),
    )
    rows = cur.fetchall()
    existing = {row[0]: row[1] for row in rows}
    owned_ids = {str(row[1]) for row in rows}

    entry_ids = []
    bullet_ids = []
    used_ids = set()
    reused = 0

    for entry_order, entry in enumerate(structure.entries):
        cur.execute(
            """
            INSERT INTO resume_entries (
                user_id, kind, organization, title, location, start_date, end_date, sort_order
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id, entry.kind, entry.organization, entry.title,
                entry.location, entry.start_date, entry.end_date, entry_order,
            ),
        )
        entry_id = cur.fetchone()[0]
        entry_ids.append(entry_id)

        for bullet_order, bullet in enumerate(entry.bullets):
            text, claimed_id = (bullet, None) if isinstance(bullet, str) else (bullet.text, bullet.id)
            digest = content_hash(text)

            # an id the client was given, still owned, not already used in this save
            bullet_id = None
            if claimed_id and claimed_id in owned_ids and claimed_id not in used_ids:
                bullet_id = claimed_id
                used_ids.add(claimed_id)
            elif digest in existing and str(existing[digest]) not in used_ids:
                bullet_id = existing[digest]
                used_ids.add(str(bullet_id))

            if bullet_id is not None:
                # same wording as before, so keep the id and re-point it
                cur.execute(
                    """
                    UPDATE resume_bullets
                    SET entry_id = %s, text = %s, content_hash = %s, sort_order = %s,
                        updated_at = now()
                    WHERE id = %s AND user_id = %s
                    """,
                    (entry_id, text, digest, bullet_order, bullet_id, user_id),
                )
                reused += 1
            else:
                cur.execute(
                    """
                    INSERT INTO resume_bullets (entry_id, user_id, text, content_hash, sort_order)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (entry_id, user_id, text, digest, bullet_order),
                )
                bullet_id = cur.fetchone()[0]

            bullet_ids.append(bullet_id)

    # bullets first: deleting entries cascades, and a reused bullet now hangs off a new one
    cur.execute(
        "DELETE FROM resume_bullets WHERE user_id = %s AND NOT (id = ANY(%s::uuid[]))",
        (user_id, [str(b) for b in bullet_ids]),
    )
    cur.execute(
        "DELETE FROM resume_entries WHERE user_id = %s AND NOT (id = ANY(%s::uuid[]))",
        (user_id, [str(e) for e in entry_ids]),
    )

    return {"entries": len(entry_ids), "bullets": len(bullet_ids), "reused_bullets": reused}


def list_resume_evidence(cur, user_id):
    """Entries with their bullets, for the review screen."""
    cur.execute(
        """
        SELECT e.id, e.kind, e.organization, e.title, e.location,
               e.start_date, e.end_date, e.sort_order
        FROM resume_entries AS e
        WHERE e.user_id = %s
        ORDER BY e.sort_order, e.id
        """,
        (user_id,),
    )
    entries = []
    index = {}
    for row in cur.fetchall():
        entry = {
            "id": str(row[0]),
            "kind": row[1],
            "organization": row[2],
            "title": row[3],
            "location": row[4],
            "start_date": row[5],
            "end_date": row[6],
            "bullets": [],
        }
        entries.append(entry)
        index[row[0]] = entry

    cur.execute(
        """
        SELECT id, entry_id, text
        FROM resume_bullets
        WHERE user_id = %s
        ORDER BY sort_order, id
        """,
        (user_id,),
    )
    for bullet_id, entry_id, text in cur.fetchall():
        entry = index.get(entry_id)
        if entry is not None:
            entry["bullets"].append({"id": str(bullet_id), "text": text})

    return entries


# ── evidence versioning (AE-01) ──────────────────────────────────────────────

STALE_EDIT_CONDITION = """
    e.cited_count <> (
        SELECT count(*)
        FROM evidence_links AS l
        JOIN resume_bullets AS b ON b.id = l.bullet_id
        WHERE l.edit_id = e.id AND b.text = l.bullet_text
    )
"""


def stale_edit_ids(cur, user_id, run_id=None, edit_id=None):
    """Accepted edits whose evidence no longer says what it said when they were made.

    Bullet ids survive a reword on purpose, so an id alone proves nothing about the text
    behind it. Counting citations that still match their snapshot catches both halves of the
    problem at once: an edited bullet stops matching, and a deleted one stops existing.
    """
    scope, params = "", [user_id]
    if run_id is not None:
        scope += " AND e.run_id = %s"
        params.append(run_id)
    if edit_id is not None:
        scope += " AND e.id = %s"
        params.append(edit_id)

    cur.execute(
        f"""
        SELECT e.id FROM proposed_edits AS e
        WHERE e.user_id = %s{scope} AND e.status = 'accepted' AND {STALE_EDIT_CONDITION}
        """,
        params,
    )
    return [str(row[0]) for row in cur.fetchall()]


# ── skills the user attached to an entry themselves ──────────────────────────
# The resume is a lossy artefact: it is what someone typed in one sitting, not the whole truth
# about them. When the fit engine reports a gap the user does not have, or a skill proven only
# by a keyword list, the missing piece is a fact only they hold — which project it belongs to.

def save_entry_skills(cur, user_id, skill, entry_ids):
    """Record that this user used `skill` on these entries. Replaces any previous answer for
    that skill, so unticking a project actually removes it."""
    from services.match import normalize_skill

    normalized = normalize_skill(skill)
    if not normalized:
        return 0

    cur.execute(
        "DELETE FROM resume_entry_skills WHERE user_id = %s AND normalized = %s",
        (user_id, normalized),
    )
    if not entry_ids:
        return 0

    cur.execute(
        """
        INSERT INTO resume_entry_skills (entry_id, user_id, skill, normalized)
        SELECT e.id, %s, %s, %s FROM resume_entries AS e
        WHERE e.user_id = %s AND e.id = ANY(%s::uuid[])
        ON CONFLICT (entry_id, normalized) DO NOTHING
        """,
        (user_id, skill.strip(), normalized, user_id, list(entry_ids)),
    )
    return cur.rowcount


def add_entry_skill(cur, user_id, skill, entry_id):
    """Attach one skill to one entry, leaving any other entries it was attached to alone.

    `save_entry_skills` replaces the whole set, which is right for a checkbox list and wrong
    for "I also used it here" — that would silently unattach every other project.
    """
    from services.match import normalize_skill

    normalized = normalize_skill(skill)
    if not normalized:
        return 0
    cur.execute(
        """
        INSERT INTO resume_entry_skills (entry_id, user_id, skill, normalized)
        SELECT e.id, %s, %s, %s FROM resume_entries AS e
        WHERE e.user_id = %s AND e.id = %s
        ON CONFLICT (entry_id, normalized) DO NOTHING
        """,
        (user_id, skill.strip(), normalized, user_id, str(entry_id)),
    )
    return cur.rowcount


def entry_bullets(cur, user_id, entry_id):
    """Every bullet of one entry, in resume order."""
    cur.execute(
        """
        SELECT b.id, b.text
        FROM resume_bullets AS b
        JOIN resume_entries AS e ON e.id = b.entry_id
        WHERE e.id = %s AND e.user_id = %s
        ORDER BY b.sort_order
        """,
        (str(entry_id), user_id),
    )
    return [{"bullet_id": str(row[0]), "text": row[1]} for row in cur.fetchall()]


def entry_skills(cur, user_id):
    """{entry_id: {normalized skill, ...}} for one user."""
    cur.execute(
        "SELECT entry_id, normalized FROM resume_entry_skills WHERE user_id = %s",
        (user_id,),
    )
    affirmed = {}
    for entry_id, normalized in cur.fetchall():
        affirmed.setdefault(str(entry_id), set()).add(normalized)
    return affirmed


def skills_affirmed_for_bullets(cur, user_id, bullet_ids):
    """Skills the user attached to the entries these bullets belong to.

    This is what lets a rewrite of one of those bullets name the skill: the claim traces to
    something the user asserted, which is the same authority the resume itself carries.
    """
    if not bullet_ids:
        return set()
    cur.execute(
        """
        SELECT DISTINCT s.normalized
        FROM resume_bullets AS b
        JOIN resume_entry_skills AS s ON s.entry_id = b.entry_id
        WHERE b.user_id = %s AND b.id = ANY(%s::uuid[])
        """,
        (user_id, [str(b) for b in bullet_ids]),
    )
    return {row[0] for row in cur.fetchall()}
