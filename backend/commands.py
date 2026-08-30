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
