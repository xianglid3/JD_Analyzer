from datetime import datetime, timedelta, timezone

from db import get_cursor
from services.supabase_storage import delete_resume_files, list_resume_files


def _parse_storage_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_resume_storage(storage_objects, referenced_paths, min_age_hours=24, now=None):
    """Separate safe-to-delete orphans from recent/unknown objects and broken DB links."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=min_age_hours)
    referenced_paths = set(referenced_paths)
    storage_paths = {item["path"] for item in storage_objects}

    eligible_orphans = []
    recent_orphans = []
    unknown_age_orphans = []

    for item in storage_objects:
        path = item["path"]
        if path in referenced_paths:
            continue

        timestamp = _parse_storage_timestamp(item.get("updated_at"))
        if timestamp is None:
            timestamp = _parse_storage_timestamp(item.get("created_at"))

        if timestamp is None:
            unknown_age_orphans.append(path)
        elif timestamp <= cutoff:
            eligible_orphans.append(path)
        else:
            recent_orphans.append(path)

    return {
        "storage_count": len(storage_paths),
        "database_reference_count": len(referenced_paths),
        "eligible_orphans": sorted(eligible_orphans),
        "recent_orphans": sorted(recent_orphans),
        "unknown_age_orphans": sorted(unknown_age_orphans),
        "missing_storage_objects": sorted(referenced_paths - storage_paths),
    }


def _get_referenced_paths():
    with get_cursor() as cur:
        cur.execute(
            "SELECT storage_path FROM resumes WHERE storage_path IS NOT NULL"
        )
        return {row[0] for row in cur.fetchall()}


def reconcile_resume_storage(delete=False, min_age_hours=24, now=None):
    storage_objects = list_resume_files()
    referenced_paths = _get_referenced_paths()
    report = classify_resume_storage(
        storage_objects,
        referenced_paths,
        min_age_hours=min_age_hours,
        now=now,
    )
    report["deleted"] = []
    report["protected_by_recheck"] = []

    if not delete or not report["eligible_orphans"]:
        return report

    # Re-read Postgres immediately before deletion in case a concurrent upload
    # committed after the first snapshot.
    current_references = _get_referenced_paths()
    paths_to_delete = []
    for path in report["eligible_orphans"]:
        if path in current_references:
            report["protected_by_recheck"].append(path)
        else:
            paths_to_delete.append(path)

    for start in range(0, len(paths_to_delete), 100):
        batch = paths_to_delete[start:start + 100]
        delete_resume_files(batch)
        report["deleted"].extend(batch)

    return report
