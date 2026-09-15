import click

from services.resume_storage_reconciliation import reconcile_resume_storage


@click.command("reconcile-resume-storage")
@click.option(
    "--delete",
    "delete_orphans",
    is_flag=True,
    help="Delete stale orphan files. Without this flag, the command is read-only.",
)
@click.option(
    "--min-age-hours",
    default=24.0,
    show_default=True,
    type=click.FloatRange(min=0),
    help="Protect orphan files newer than this age.",
)
def reconcile_resume_storage_command(delete_orphans, min_age_hours):
    """Find resume files that disagree with Postgres metadata."""
    report = reconcile_resume_storage(
        delete=delete_orphans,
        min_age_hours=min_age_hours,
    )

    mode = "DELETE" if delete_orphans else "DRY RUN"
    click.echo(f"Resume Storage reconciliation ({mode})")
    click.echo(f"Storage files: {report['storage_count']}")
    click.echo(f"Database references: {report['database_reference_count']}")
    click.echo(f"Stale orphans: {len(report['eligible_orphans'])}")
    click.echo(f"Recent orphans protected: {len(report['recent_orphans'])}")
    click.echo(f"Unknown-age orphans protected: {len(report['unknown_age_orphans'])}")
    click.echo(f"Missing Storage files: {len(report['missing_storage_objects'])}")

    for path in report["eligible_orphans"]:
        click.echo(f"  orphan: {path}")
    for path in report["missing_storage_objects"]:
        click.echo(f"  missing: {path}")

    if delete_orphans:
        click.echo(f"Deleted: {len(report['deleted'])}")
        click.echo(f"Protected after DB recheck: {len(report['protected_by_recheck'])}")
    elif report["eligible_orphans"]:
        click.echo("Dry run only. Re-run with --delete to remove stale orphans.")


def register_commands(app):
    app.cli.add_command(reconcile_resume_storage_command)


def register_usage_report(app):
    import click

    @app.cli.command("usage-report")
    @click.option("--days", default=30, help="How far back to look.")
    def usage_report(days):
        """What the model calls cost, and how long they took."""
        from db import get_cursor
        from services.usage import DAILY_LIMIT_USD, report

        with get_cursor() as cur:
            data = report(cur, days)

        click.echo(f"last {days} days · daily cap ${DAILY_LIMIT_USD:.2f} per user\n")

        if not data["by_kind"]:
            click.echo("no model calls recorded yet")
            return

        click.echo(f"{'kind':<20}{'calls':>7}{'p50 ms':>9}{'p95 ms':>9}{'cost':>10}")
        for row in data["by_kind"]:
            click.echo(f"{row['kind']:<20}{row['calls']:>7}{row['p50_ms']:>9.0f}"
                       f"{row['p95_ms']:>9.0f}{row['cost_usd']:>10.4f}")

        click.echo(f"\n{'user':<20}{'calls':>7}{'tokens':>10}{'cost':>10}")
        for row in data["by_user"]:
            click.echo(f"{row['username']:<20}{row['calls']:>7}{row['tokens']:>10}"
                       f"{row['cost_usd']:>10.4f}")


def register_grounding_report(app):
    import click

    @app.cli.command("grounding-report")
    @click.option("--days", default=30, help="How far back to look.")
    @click.option("--samples", "sample_count", default=0,
                  help="Also print N rejections with their text, for the hand-check.")
    @click.option("--reason", default=None, help="Only sample this rejection reason.")
    def grounding_report(days, sample_count, reason):
        """How often the tailoring agent was refused, and why."""
        from db import get_cursor
        from services.grounding_report import report, samples

        with get_cursor() as cur:
            data = report(cur, days)
            rows = samples(cur, sample_count, days, reason) if sample_count else []

        runs = data["runs"]
        if not runs["total"]:
            click.echo(f"no tailoring runs in the last {days} days")
            return

        click.echo(f"last {days} days · {runs['total']} run(s): "
                   f"{runs['completed']} completed, {runs['waiting_for_user']} awaiting input, "
                   f"{runs['limit_reached']} hit the step cap, {runs['failed']} failed · "
                   f"{runs['avg_steps']} steps on average\n")

        out = data["output"]
        click.echo(f"proposed {out['edits_proposed']} edit(s) ({out['edits_accepted']} accepted by the user) "
                   f"and flagged {out['gaps_flagged']} gap(s) · {out['edits_per_run']} edits per run\n")

        click.echo(f"{'tool':<16}{'attempts':>10}{'refused':>9}{'rate':>8}")
        for tool, entry in sorted(data["by_tool"].items()):
            click.echo(f"{tool:<16}{entry['attempts']:>10}{entry['failed']:>9}{entry['rejection_rate']:>7.1f}%")

        if data["by_reason"]:
            click.echo(f"\n{'reason':<20}{'tool':<16}{'count':>7}")
            for row in data["by_reason"]:
                click.echo(f"{row['reason']:<20}{row['tool']:<16}{row['count']:>7}")
        else:
            click.echo("\nno rejections recorded")

        if rows:
            click.echo(f"\n── {len(rows)} rejection(s) to read: was the checker right? ──")
            for row in rows:
                click.echo(f"\n[{row['reason']}] {row['tool']} · {row['requirement'] or '?'}")
                if row["proposed_text"]:
                    click.echo(f"  proposed: {row['proposed_text']}")
                click.echo(f"  refused:  {row['error']}")


def register_tailoring_worker(app):
    import click

    @app.cli.command("tailoring-worker")
    @click.option("--poll-seconds", default=2.0, help="How often to look for claimable work.")
    @click.option("--once", is_flag=True, help="Take at most one run, then exit.")
    def tailoring_worker(poll_seconds, once):
        """Run tailoring jobs. This is the process that does the agent's work.

        The web server only writes the run row; this claims it. That separation is the point:
        a deploy restarts the web server without killing anything mid-run, and a worker that
        dies loses nothing, because its lease expires and another worker resumes the run from
        the last step written to disk.
        """
        import logging
        import signal
        import time

        from db import get_cursor
        from services.tailoring_agent import (
            DEFAULT_MAX_STEPS, WORKER_ID, claim_run, execute_run,
        )

        logger = logging.getLogger("tailoring-worker")
        stopping = {"now": False}

        def stop(_signum, _frame):
            # finish the run in hand, then exit — the lease is what makes a hard kill safe,
            # but a clean shutdown should still not strand one
            stopping["now"] = True
            click.echo("shutting down after the current run")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        click.echo(f"tailoring worker {WORKER_ID} started")
        while not stopping["now"]:
            try:
                with get_cursor(commit=True) as cur:
                    claimed = claim_run(cur)
            except Exception:
                logger.exception("could not claim a run")
                time.sleep(poll_seconds)
                continue

            if claimed is None:
                if once:
                    break
                time.sleep(poll_seconds)
                continue

            click.echo(f"claimed {claimed['run_id']} from step {claimed['steps_used']}")
            try:
                result = execute_run(
                    get_cursor, claimed["user_id"], claimed["job_id"], claimed["run_id"],
                    max_steps=claimed["max_steps"] or DEFAULT_MAX_STEPS,
                    resume_from=claimed["steps_used"],
                    token=claimed["token"],
                )
                click.echo(f"  {claimed['run_id']} → {result['status']}")
            except Exception:
                # the lease expires on its own, so another worker will pick this up; never
                # let one bad run take the worker down
                logger.exception("run %s failed in the worker", claimed["run_id"])

            if once:
                break


def register_run_sweep(app):
    import click

    @app.cli.command("sweep-tailoring-runs")
    def sweep_runs():
        """Close out tailoring runs whose worker stopped reporting."""
        from db import get_cursor
        from services.tailoring_agent import sweep_abandoned_runs

        with get_cursor(commit=True) as cur:
            swept = sweep_abandoned_runs(cur)

        click.echo(f"closed {len(swept)} abandoned run(s)")
        for run_id in swept:
            click.echo(f"  {run_id}")

    @app.cli.command("release-stale-reservations")
    @click.option("--older-than", default="15 minutes", help="Reservation age to reconcile.")
    def release_reservations(older_than):
        """Settle budget reservations whose caller died before finishing.

        Charged at their ceiling, not released free: the tokens may have been sent, and a
        crashed process is not a refund.
        """
        from db import get_cursor
        from services.usage import release_stale_reservations

        with get_cursor(commit=True) as cur:
            settled = release_stale_reservations(cur, older_than)
        click.echo(f"settled {len(settled)} stale reservation(s)")


def register_skill_relations(app):
    import click

    @app.cli.command("skill-relations")
    @click.option("--term", default=None, help="Show only relations touching this skill.")
    @click.option("--source", default=None,
                  type=click.Choice(["model", "user", "seed"]),
                  help="Show only relations from this source.")
    def show_skill_relations(term, source):
        """List what the skill graph has learned beyond the hand-written seed table."""
        from db import get_cursor
        from services.match import normalize_skill

        clauses, params = [], []
        if term:
            clauses.append("(specific = %s OR general = %s)")
            params += [normalize_skill(term), normalize_skill(term)]
        if source:
            clauses.append("source = %s")
            params.append(source)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with get_cursor() as cur:
            cur.execute(
                f"""
                SELECT specific, general, rewriteable, source
                FROM skill_relations {where}
                ORDER BY specific, general
                """,
                params,
            )
            rows = cur.fetchall()
            cur.execute("SELECT count(*) FROM skill_relation_lookups")
            asked = cur.fetchone()[0]

        click.echo(f"{len(rows)} learned relation(s); {asked} term(s) looked up")
        for specific, general, rewriteable, row_source in rows:
            # "proposed" not "writeable": a learned edge grants nothing until a user approves
            # it for themselves. Only the hand-written table is authority (AE-03).
            mark = "writeable-if-approved" if rewriteable else "scoring"
            click.echo(f"  {specific} -> {general}  [{mark}, {row_source}]")

    @app.cli.command("forget-skill-relation")
    @click.argument("specific")
    @click.argument("general")
    def forget_skill_relation(specific, general):
        """Delete one learned edge the model got wrong.

        The term stays in the lookups table, so it is not simply re-learned on the next
        scoring pass — a correction has to stick to be worth making.
        """
        from db import get_cursor
        from services import skill_relations
        from services.match import normalize_skill

        with get_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM skill_relations WHERE specific = %s AND general = %s",
                (normalize_skill(specific), normalize_skill(general)),
            )
            removed = cur.rowcount
        skill_relations.reset()

        click.echo(f"removed {removed} relation(s)")


def register_rewrite_approvals(app):
    import click

    @app.cli.command("rewrite-approvals")
    @click.argument("username")
    def show_rewrite_approvals(username):
        """Show which learned claims one user has agreed may be written into their resume."""
        from db import get_cursor

        with get_cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                raise click.ClickException(f"no user named {username}")
            cur.execute(
                """
                SELECT specific, general, approved
                FROM skill_rewrite_approvals WHERE user_id = %s
                ORDER BY specific, general
                """,
                (row[0],),
            )
            decisions = cur.fetchall()

        if not decisions:
            click.echo("no decisions yet — only the hand-written table applies")
        for specific, general, approved in decisions:
            click.echo(f"  {specific} -> {general}  [{'approved' if approved else 'refused'}]")

    @app.cli.command("approve-rewrite")
    @click.argument("username")
    @click.argument("specific")
    @click.argument("general")
    @click.option("--refuse", is_flag=True, help="Record a refusal instead of an approval.")
    def approve_rewrite(username, specific, general, refuse):
        """Let one user's resume state GENERAL on the strength of SPECIFIC.

        This is the decision the product should eventually ask for in the flow. Until it
        does, this is how a learned edge becomes usable — deliberately per user, so a wrong
        one stays contained.
        """
        from db import get_cursor
        from services.skill_relations import record_rewrite_decision

        with get_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                raise click.ClickException(f"no user named {username}")
            record_rewrite_decision(cur, row[0], specific, general, approved=not refuse)

        verb = "refused" if refuse else "approved"
        click.echo(f"{verb}: {specific} -> {general} for {username}")


def register_relation_maintenance(app):
    import click

    @app.cli.command("collapse-skill-relations")
    @click.option("--apply", "apply_changes", is_flag=True, help="Write the change (default is a dry run).")
    def collapse_skill_relations(apply_changes):
        """Re-key learned relations through the current normalizer.

        Hyphens used to survive normalization, so one concept could be stored under several
        spellings — each looked up, and paid for, separately. This folds the existing rows
        onto one key. Dry run by default.
        """
        from db import get_cursor
        from services import skill_relations
        from services.match import normalize_skill

        with get_cursor() as cur:
            cur.execute("SELECT specific, general, rewriteable, source FROM skill_relations")
            rows = cur.fetchall()
            cur.execute("SELECT term FROM skill_relation_lookups")
            terms = [row[0] for row in cur.fetchall()]

        edges = {}
        for specific, general, rewriteable, source in rows:
            key = (normalize_skill(specific), normalize_skill(general))
            if key[0] == key[1]:
                continue                      # a self-edge the table would reject anyway
            keep_rewriteable, keep_source = edges.get(key, (False, source))
            edges[key] = (keep_rewriteable or rewriteable, keep_source)
        flat_terms = sorted({normalize_skill(term) for term in terms})

        click.echo(f"relations {len(rows)} -> {len(edges)}")
        click.echo(f"lookups   {len(terms)} -> {len(flat_terms)}")
        if not apply_changes:
            click.echo("dry run — pass --apply to write")
            return

        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM skill_relations")
            cur.executemany(
                """
                INSERT INTO skill_relations (specific, general, rewriteable, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (specific, general) DO UPDATE
                  SET rewriteable = skill_relations.rewriteable OR EXCLUDED.rewriteable
                """,
                [(spec, gen, rw, src) for (spec, gen), (rw, src) in edges.items()],
            )
            cur.execute("DELETE FROM skill_relation_lookups")
            cur.executemany(
                "INSERT INTO skill_relation_lookups (term) VALUES (%s) ON CONFLICT DO NOTHING",
                [(term,) for term in flat_terms],
            )
        skill_relations.reset()
        click.echo("collapsed")
