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
